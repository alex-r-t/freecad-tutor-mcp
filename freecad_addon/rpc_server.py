"""Hardened TCP JSON-RPC 2.0 server dispatching calls onto FreeCAD's main GUI thread.

Design combines:
- theosib/FreeCAD-MCP-Server: newline-delimited TCP JSON-RPC 2.0 framing (up to 16 MB).
- neka-nat/freecad-mcp (gui_dispatch.py): per-call response queue isolation, immediate
  Qt QueuedConnection wake signal + fallback heartbeat timer, bounded mouse-drag guard
  (MOUSE_DEFER_MAX_S) so missed mouse-release events on Windows cannot deadlock dispatch,
  stuck-task fail-fast, and non-GUI health probes (`ping`, `get_rpc_status`).
"""

import hmac
import json
import logging
import os
import queue
import secrets
import socket
import threading
import time
import traceback
from typing import Any, Callable

from json_utils import sanitize_json

logger = logging.getLogger("freecad_tutor_mcp.rpc_server")

PROTOCOL_VERSION = "0.2.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
MSG_DELIMITER = b"\n"
MAX_MSG_SIZE = 16 * 1024 * 1024
MAIN_THREAD_TIMEOUT = 60.0
POLL_INTERVAL_MS = 250
MOUSE_DEFER_MAX_S = 5.0
AUTH_ERROR_CODE = -32003


def get_token_file_path() -> str:
    """Return the path where the local TCP RPC shared secret is stored.

    Precedence:
    1. FREECAD_MCP_TOKEN_FILE environment override (useful in automated tests).
    2. %APPDATA%\\FreeCAD\\mcp_auth_token on Windows.
    3. ~/.freecad_mcp_token fallback on POSIX or stripped environments.
    """
    override = os.environ.get("FREECAD_MCP_TOKEN_FILE", "").strip()
    if override:
        return override
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return os.path.join(appdata, "FreeCAD", "mcp_auth_token")
    return os.path.join(os.path.expanduser("~"), ".freecad_mcp_token")


def read_auth_token() -> str | None:
    """Read the shared RPC auth token from FREECAD_MCP_TOKEN or the token file."""
    env_token = os.environ.get("FREECAD_MCP_TOKEN", "").strip()
    if env_token:
        return env_token
    token_path = get_token_file_path()
    try:
        with open(token_path, "r", encoding="utf-8") as fh:
            token = fh.read().strip()
            return token or None
    except OSError:
        return None


def get_or_create_auth_token() -> str:
    """Return the shared local auth token, creating and persisting one if absent.

    FreeCAD generates this secret once on server startup so other unprivileged local
    processes cannot invoke execute_script or mutate models over 127.0.0.1 without
    reading the current user's token file.
    """
    existing = read_auth_token()
    if existing:
        return existing

    token = secrets.token_urlsafe(32)
    token_path = get_token_file_path()
    parent = os.path.dirname(token_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as fh:
        fh.write(token + "\n")
    try:
        # Restrict permissions where supported (no-op for POSIX bits on Windows ACLs,
        # where %APPDATA% already inherits user-only NTFS ownership).
        os.chmod(token_path, 0o600)
    except OSError:
        pass
    return token


def _import_qt():
    """Import QtCore and QtWidgets across PySide / PySide6 / PySide2."""
    for mod in ("PySide", "PySide6", "PySide2"):
        try:
            pkg = __import__(mod, fromlist=["QtCore", "QtWidgets"])
            return pkg.QtCore, pkg.QtWidgets
        except (ImportError, AttributeError):
            continue
    return None, None


class _DispatchHealth:
    """Thread-safe tracker for stuck GUI-thread tasks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_method: str | None = None
        self._started_at: float | None = None
        self._stuck_method: str | None = None
        self._stuck_since: float | None = None

    def mark_started(self, method: str) -> None:
        with self._lock:
            self._current_method = method
            self._started_at = time.monotonic()

    def mark_finished(self) -> None:
        with self._lock:
            self._current_method = None
            self._started_at = None
            self._stuck_method = None
            self._stuck_since = None

    def mark_stuck(self, method: str) -> None:
        with self._lock:
            if self._stuck_method is None:
                self._stuck_method = method
                self._stuck_since = time.monotonic()

    def check_stuck(self) -> str | None:
        with self._lock:
            if self._stuck_method is None:
                return None
            elapsed = time.monotonic() - (self._stuck_since or time.monotonic())
            return (
                f"FreeCAD GUI thread is still blocked by an earlier '{self._stuck_method}' "
                f"call ({elapsed:.1f}s ago). Wait for FreeCAD to finish or restart FreeCAD."
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            return {
                "busy": self._current_method is not None,
                "current_method": self._current_method,
                "running_for_s": round(now - self._started_at, 2) if self._started_at else 0.0,
                "stuck": self._stuck_method is not None,
                "stuck_method": self._stuck_method,
            }


class RPCServer:
    """TCP JSON-RPC 2.0 server dispatching GUI work safely onto the main thread."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        use_qt: bool = True,
        main_thread_timeout: float = MAIN_THREAD_TIMEOUT,
        auth_token: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.use_qt = use_qt
        self.main_thread_timeout = main_thread_timeout
        self.auth_token = auth_token

        self._handlers: dict[str, Callable[..., Any]] = {}
        self._direct_handlers: dict[str, Callable[..., Any]] = {}
        self._server_socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._work_queue: queue.Queue[tuple[str, Callable, dict, queue.Queue]] = queue.Queue()
        self._poll_timer: Any = None
        self._waker: Any = None
        self._processing = False
        self._mouse_defer_since: float | None = None
        self._health = _DispatchHealth()

        # Non-GUI handlers respond even if the main GUI thread is busy or wedged.
        self.register_direct("ping", self._handle_ping)
        self.register_direct("get_rpc_status", self._handle_status)

    def register(self, method: str, handler: Callable[..., Any]) -> None:
        self._handlers[method] = handler

    def register_direct(self, method: str, handler: Callable[..., Any]) -> None:
        self._direct_handlers[method] = handler

    def register_all(self, handlers: dict[str, Callable[..., Any]]) -> None:
        self._handlers.update(handlers)

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.5)
        try:
            sock.bind((self.host, self.port))
            # Update port if bound to ephemeral port 0 (used in tests)
            self.port = sock.getsockname()[1]
            sock.listen(8)
        except Exception:
            sock.close()
            raise

        self._server_socket = sock
        self._running = True
        if self.use_qt:
            self._init_qt_dispatch()

        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        logger.info("RPC server started on %s:%d", self.host, self.port)

    def stop(self) -> None:
        self._running = False
        self._stop_qt_dispatch()
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass
            self._server_socket = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    # ── Qt main-thread dispatch ──────────────────────────────────────────

    def _init_qt_dispatch(self) -> None:
        QtCore, _ = _import_qt()
        if QtCore is None:
            return

        class _WakeSignal(QtCore.QObject):
            wake = QtCore.Signal()

            def __init__(self, callback: Callable[[], None]):
                super().__init__()
                self.wake.connect(callback, QtCore.Qt.QueuedConnection)

        self._waker = _WakeSignal(self.drain_work_queue)
        self._poll_timer = QtCore.QTimer()
        self._poll_timer.timeout.connect(self.drain_work_queue)
        self._poll_timer.start(POLL_INTERVAL_MS)

    def _stop_qt_dispatch(self) -> None:
        if self._poll_timer is not None:
            try:
                self._poll_timer.stop()
            except Exception:
                pass
            self._poll_timer = None
        self._waker = None

    def _should_defer_for_mouse_drag(self, now: float) -> bool:
        """Defer briefly while user orbits/drags, but never wedge past MOUSE_DEFER_MAX_S."""
        if not self.use_qt:
            return False
        _, QtWidgets = _import_qt()
        if QtWidgets is None:
            return False
        try:
            QtCore, _ = _import_qt()
            app = QtWidgets.QApplication.instance()
            if app is None:
                return False
            buttons = app.mouseButtons()
            mouse_down = buttons != QtCore.Qt.NoButton
        except Exception:
            return False

        if not mouse_down:
            self._mouse_defer_since = None
            return False
        if self._mouse_defer_since is None:
            self._mouse_defer_since = now
            return True
        return (now - self._mouse_defer_since) < MOUSE_DEFER_MAX_S

    def drain_work_queue(self) -> int:
        """Drain pending main-thread tasks. Safe to call directly in tests."""
        if self._processing or not self._running:
            return 0
        if self._should_defer_for_mouse_drag(time.monotonic()):
            return 0

        self._processing = True
        processed = 0
        try:
            while not self._work_queue.empty():
                try:
                    method, func, params, reply_queue = self._work_queue.get_nowait()
                except queue.Empty:
                    break

                self._health.mark_started(method)
                try:
                    result = func(**params) if params else func()
                    reply_queue.put({"result": result})
                except Exception as exc:
                    reply_queue.put({"error": f"{exc}\n{traceback.format_exc()}"})
                finally:
                    self._health.mark_finished()
                    processed += 1
        finally:
            self._processing = False
        return processed

    # ── Socket accept & per-client loop ──────────────────────────────────

    def _accept_loop(self) -> None:
        while self._running and self._server_socket is not None:
            try:
                client, _addr = self._server_socket.accept()
                t = threading.Thread(target=self._client_loop, args=(client,), daemon=True)
                t.start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _client_loop(self, client: socket.socket) -> None:
        buf = b""
        try:
            with client:
                while self._running:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) > MAX_MSG_SIZE:
                        self._send_json(
                            client,
                            self._error_response(None, -32600, "Request exceeds 16 MB limit"),
                        )
                        break

                    while MSG_DELIMITER in buf:
                        line, buf = buf.split(MSG_DELIMITER, 1)
                        line = line.strip()
                        if not line:
                            continue
                        response = self._handle_line(line)
                        self._send_json(client, response)
        except OSError:
            pass

    def _handle_line(self, raw: bytes) -> dict[str, Any]:
        req_id = None
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            return self._error_response(None, -32700, f"Parse error: {exc}")

        if not isinstance(req, dict):
            return self._error_response(None, -32600, "Invalid Request: expected JSON object")

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return self._error_response(req_id, -32600, "Invalid Request: method/params type error")

        # Enforce shared local token on all methods except unauthenticated `ping`.
        if self.auth_token is not None and method != "ping":
            provided = req.get("auth_token")
            if not isinstance(provided, str) or not hmac.compare_digest(provided, self.auth_token):
                return self._error_response(
                    req_id,
                    AUTH_ERROR_CODE,
                    "Unauthorized: missing or invalid FreeCAD Tutor MCP local auth token.",
                )

        if method in self._direct_handlers:
            try:
                result = self._direct_handlers[method](**params) if params else self._direct_handlers[method]()
                return {"jsonrpc": "2.0", "id": req_id, "result": sanitize_json(result)}
            except Exception as exc:
                return self._error_response(req_id, -32000, str(exc))

        handler = self._handlers.get(method)
        if handler is None:
            return self._error_response(req_id, -32601, f"Method not found: {method}")

        stuck_msg = self._health.check_stuck()
        if stuck_msg is not None:
            return self._error_response(req_id, -32002, stuck_msg)

        # Per-call reply queue prevents timed-out responses from leaking into later calls.
        reply_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        self._work_queue.put((method, handler, params, reply_queue))
        if self._waker is not None:
            try:
                self._waker.wake.emit()
            except Exception:
                pass

        try:
            outcome = reply_queue.get(timeout=self.main_thread_timeout)
        except queue.Empty:
            self._health.mark_stuck(method)
            return self._error_response(
                req_id,
                -32001,
                f"Timed out waiting {self.main_thread_timeout:.0f}s for '{method}' on FreeCAD's GUI thread",
            )

        if "error" in outcome:
            return self._error_response(req_id, -32000, outcome["error"])
        return {"jsonrpc": "2.0", "id": req_id, "result": sanitize_json(outcome["result"])}

    def _send_json(self, client: socket.socket, payload: dict[str, Any]) -> None:
        safe_payload = sanitize_json(payload)
        encoded = json.dumps(safe_payload, allow_nan=False).encode("utf-8") + MSG_DELIMITER
        client.sendall(encoded)

    @staticmethod
    def _error_response(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    # ── Built-in direct handlers ─────────────────────────────────────────

    def _handle_ping(self) -> dict[str, Any]:
        return {
            "pong": True,
            "protocol_version": PROTOCOL_VERSION,
            "pid": os.getpid(),
            "host": self.host,
            "port": self.port,
            "auth_required": self.auth_token is not None,
        }

    def _handle_status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "protocol_version": PROTOCOL_VERSION,
            "pid": os.getpid(),
            "host": self.host,
            "port": self.port,
            "handlers_count": len(self._handlers),
            "dispatch": self._health.snapshot(),
        }
