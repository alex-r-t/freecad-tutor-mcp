"""TCP JSON-RPC 2.0 client connecting to the FreeCAD Tutor MCP addon."""

import json
import os
import socket
import threading
from typing import Any

from freecad_tutor_mcp import PROTOCOL_VERSION

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
MSG_DELIMITER = b"\n"
RECV_BUFFER = 65536
CONNECT_TIMEOUT = 5.0
CALL_TIMEOUT = 120.0


class BridgeError(Exception):
    """Raised on socket/transport failures with FreeCAD."""


class RPCError(Exception):
    """Raised when the FreeCAD addon returns a JSON-RPC error."""

    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(f"FreeCAD RPC error {code}: {message}")


class FreeCADBridge:
    """Thread-safe TCP JSON-RPC client for FreeCADTutorMCP."""

    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self.host = host or os.environ.get("FREECAD_MCP_HOST", DEFAULT_HOST)
        self.port = port or int(os.environ.get("FREECAD_MCP_PORT", str(DEFAULT_PORT)))
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._checked_version = False
        self._version_warning: str | None = None

    def connect(self) -> None:
        if self._socket is not None:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect((self.host, self.port))
            sock.settimeout(CALL_TIMEOUT)
            self._socket = sock
        except (ConnectionRefusedError, OSError) as exc:
            raise BridgeError(
                f"Cannot connect to FreeCAD Tutor MCP at {self.host}:{self.port}. "
                f"Make sure FreeCAD is running and the FreeCAD Tutor MCP server is started ({exc})."
            ) from exc

    def disconnect(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def is_connected(self) -> bool:
        return self._socket is not None

    @property
    def version_warning(self) -> str | None:
        return self._version_warning

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a JSON-RPC call, reconnecting once if the socket dropped."""
        with self._lock:
            for attempt in range(2):
                try:
                    result = self._do_call(method, params or {})
                    if not self._checked_version and method not in ("ping", "get_rpc_status"):
                        self._check_protocol_version_locked()
                    return result
                except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                    self.disconnect()
                    if attempt == 1:
                        raise BridgeError(
                            f"Lost connection to FreeCAD during '{method}': {exc}"
                        ) from exc

    def _check_protocol_version_locked(self) -> None:
        self._checked_version = True
        try:
            ping_info = self._do_call("ping", {})
            addon_ver = ping_info.get("protocol_version") if isinstance(ping_info, dict) else None
            if addon_ver != PROTOCOL_VERSION:
                self._version_warning = (
                    f"Version mismatch: MCP server protocol={PROTOCOL_VERSION}, "
                    f"FreeCAD addon protocol={addon_ver or 'unknown'}. "
                    "Re-run install.ps1 and restart FreeCAD."
                )
            else:
                self._version_warning = None
        except Exception:
            pass

    def _do_call(self, method: str, params: dict[str, Any]) -> Any:
        self.connect()
        assert self._socket is not None
        self._request_id += 1
        req_id = self._request_id

        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}
        raw = json.dumps(payload, allow_nan=False).encode("utf-8") + MSG_DELIMITER
        self._socket.sendall(raw)

        buffer = b""
        while MSG_DELIMITER not in buffer:
            chunk = self._socket.recv(RECV_BUFFER)
            if not chunk:
                raise BrokenPipeError("Connection closed by FreeCAD")
            buffer += chunk

        line, _ = buffer.split(MSG_DELIMITER, 1)
        response = json.loads(line.decode("utf-8"))

        if "error" in response:
            err = response["error"]
            raise RPCError(err.get("code", -1), err.get("message", "Unknown RPC error"))
        return response.get("result")
