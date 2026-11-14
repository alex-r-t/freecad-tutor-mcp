"""Tests for RPCServer framing, dispatch health, sanitization, and FreeCADBridge."""

import threading
import time

import pytest

from freecad_tutor_mcp.bridge import FreeCADBridge, RPCError
from rpc_server import (
    AUTH_ERROR_CODE,
    PROTOCOL_VERSION,
    RPCServer,
    get_or_create_auth_token,
)


def _start_pumped_server(timeout: float = 2.0, auth_token: str | None = None):
    srv = RPCServer(
        host="127.0.0.1",
        port=0,
        use_qt=False,
        main_thread_timeout=timeout,
        auth_token=auth_token,
    )
    stop_pump = threading.Event()

    def _pump():
        while not stop_pump.is_set():
            srv.drain_work_queue()
            time.sleep(0.01)

    srv.start()
    pump_thread = threading.Thread(target=_pump, daemon=True)
    pump_thread.start()
    return srv, stop_pump, pump_thread


def test_ping_status_and_large_payload():
    srv, stop_pump, pump_thread = _start_pumped_server()
    try:
        srv.register("echo_big", lambda size=150000: {"blob": "x" * size, "nan": float("nan")})
        bridge = FreeCADBridge(host="127.0.0.1", port=srv.port)

        ping = bridge.call("ping")
        assert ping["pong"] is True
        assert ping["protocol_version"] == PROTOCOL_VERSION

        res = bridge.call("echo_big", {"size": 150000})
        assert len(res["blob"]) == 150000
        assert res["nan"] is None
        assert bridge.version_warning is None

        status = bridge.call("get_rpc_status")
        assert status["running"] is True
        assert status["dispatch"]["stuck"] is False
    finally:
        bridge.disconnect()
        stop_pump.set()
        pump_thread.join(timeout=2)
        srv.stop()


def test_timeout_marks_stuck_and_recovers_after_completion():
    srv = RPCServer(host="127.0.0.1", port=0, use_qt=False, main_thread_timeout=0.15)
    release = threading.Event()

    def slow_op():
        release.wait(timeout=2.0)
        return {"done": True}

    srv.register("slow_op", slow_op)
    srv.register("quick_op", lambda: {"ok": True})
    srv.start()

    pump_thread = threading.Thread(target=srv.drain_work_queue, daemon=True)
    pump_thread.start()

    bridge = FreeCADBridge(host="127.0.0.1", port=srv.port)
    try:
        with pytest.raises(RPCError) as exc_info:
            bridge.call("slow_op")
        assert exc_info.value.code == -32001

        # Direct ping still works while GUI thread is blocked
        assert bridge.call("ping")["pong"] is True

        # Subsequent GUI call fails fast (-32002) while slow_op is still running
        with pytest.raises(RPCError) as stuck_exc:
            bridge.call("quick_op")
        assert stuck_exc.value.code == -32002

        # Once slow_op finishes, dispatch clears stuck state
        release.set()
        pump_thread.join(timeout=2)
        stop2 = threading.Event()

        def _pump2():
            while not stop2.is_set():
                srv.drain_work_queue()
                time.sleep(0.01)

        pump2 = threading.Thread(target=_pump2, daemon=True)
        pump2.start()
        try:
            assert bridge.call("quick_op") == {"ok": True}
        finally:
            stop2.set()
            pump2.join(timeout=2)
    finally:
        release.set()
        bridge.disconnect()
        srv.stop()


def test_version_mismatch_sets_warning():
    srv, stop_pump, pump_thread = _start_pumped_server()
    try:
        srv.register_direct("ping", lambda: {"pong": True, "protocol_version": "0.0.1-stale"})
        srv.register("list_documents", lambda: [])
        bridge = FreeCADBridge(host="127.0.0.1", port=srv.port)

        assert bridge.call("list_documents") == []
        assert bridge.version_warning is not None
        assert "Version mismatch" in bridge.version_warning
    finally:
        bridge.disconnect()
        stop_pump.set()
        pump_thread.join(timeout=2)
        srv.stop()


def test_rpc_server_and_json_utils_import_without_freecad(monkeypatch: pytest.MonkeyPatch):
    import sys

    monkeypatch.delitem(sys.modules, "FreeCAD", raising=False)
    monkeypatch.delitem(sys.modules, "FreeCADGui", raising=False)

    from json_utils import sanitize_json

    cleaned = sanitize_json({"nan": float("nan"), "inf": float("inf"), "ninf": float("-inf"), "ok": [1, 2.5]})
    assert cleaned == {"nan": None, "inf": None, "ninf": None, "ok": [1, 2.5]}


def test_auth_token_enforcement_and_reload(monkeypatch: pytest.MonkeyPatch, tmp_path):
    token_file = tmp_path / "mcp_auth_token"
    monkeypatch.setenv("FREECAD_MCP_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("FREECAD_MCP_TOKEN", raising=False)

    created_token = get_or_create_auth_token()
    assert token_file.exists()
    assert get_or_create_auth_token() == created_token

    srv, stop_pump, pump_thread = _start_pumped_server(auth_token=created_token)
    srv.register("secret_op", lambda: {"allowed": True})
    try:
        # Wrong explicit token is rejected with -32003, while unauthenticated ping still works
        bad_bridge = FreeCADBridge(host="127.0.0.1", port=srv.port, auth_token="wrong-token")
        ping = bad_bridge.call("ping")
        assert ping["pong"] is True
        assert ping["auth_required"] is True

        with pytest.raises(RPCError) as exc_info:
            bad_bridge.call("secret_op")
        assert exc_info.value.code == AUTH_ERROR_CODE
        bad_bridge.disconnect()

        # Bridge without explicit token picks up the file token, and transparently reloads if rotated
        good_bridge = FreeCADBridge(host="127.0.0.1", port=srv.port)
        assert good_bridge.call("secret_op") == {"allowed": True}

        new_token = "rotated-token-value"
        srv.auth_token = new_token
        token_file.write_text(new_token + "\n", encoding="utf-8")
        assert good_bridge.call("secret_op") == {"allowed": True}
        good_bridge.disconnect()
    finally:
        stop_pump.set()
        pump_thread.join(timeout=2)
        srv.stop()
