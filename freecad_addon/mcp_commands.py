"""FreeCADGui commands and handler registry for FreeCAD Tutor MCP."""

import os
import sys

import FreeCAD
import FreeCADGui as Gui

from rpc_server import DEFAULT_HOST, DEFAULT_PORT, PROTOCOL_VERSION, RPCServer

_server: RPCServer | None = None


def _build_handler_map() -> dict:
    """Import and return all RPC handlers."""
    from handlers import document, execution, inspection, modeling, recompute, sketcher, ui_discovery, viewport

    return {
        # Document & inspection
        "list_documents": document.list_documents,
        "get_document_graph": document.get_document_graph,
        "create_document": document.create_document,
        "inspect_object": inspection.inspect_object,
        "analyze_shape": inspection.analyze_shape,
        "get_sketch_diagnostics": sketcher.get_sketch_diagnostics,
        "tracked_recompute": recompute.tracked_recompute,
        # UI discovery & visual context
        "find_ui_command": ui_discovery.find_ui_command,
        "list_workbenches": ui_discovery.list_workbenches,
        "activate_workbench": ui_discovery.activate_workbench,
        "get_selection": ui_discovery.get_selection,
        "set_selection": ui_discovery.set_selection,
        "get_console_output": ui_discovery.get_console_output,
        "get_view": viewport.get_view,
        # Undo / redo & confirmed modeling
        "undo": modeling.undo,
        "redo": modeling.redo,
        "get_undo_redo_status": modeling.get_undo_redo_status,
        "create_partdesign_body": modeling.create_partdesign_body,
        "create_sketch": modeling.create_sketch,
        "edit_sketch": modeling.edit_sketch,
        "pad_sketch": modeling.pad_sketch,
        "pocket_sketch": modeling.pocket_sketch,
        "create_part_primitive": modeling.create_part_primitive,
        "boolean_operation": modeling.boolean_operation,
        "fillet_edges": modeling.fillet_edges,
        "chamfer_edges": modeling.chamfer_edges,
        "create_techdraw_page": modeling.create_techdraw_page,
        "add_techdraw_view": modeling.add_techdraw_view,
        # Escape hatch
        "execute_script": execution.execute_script,
    }


def _reload_handlers() -> dict:
    """Hot-reload handler modules without restarting FreeCAD."""
    import importlib

    for name in list(sys.modules):
        if name == "handlers" or name.startswith("handlers."):
            importlib.reload(sys.modules[name])
    if _server is not None:
        _server.register_all(_build_handler_map())
    return {"reloaded": True, "protocol_version": PROTOCOL_VERSION}


def _set_status_bar(text: str) -> None:
    try:
        mw = Gui.getMainWindow()
        if mw is not None:
            mw.showMessage(text, 5000)
    except Exception:
        pass


def ensure_started(silent_if_running: bool = False) -> RPCServer | None:
    """Start the RPC server if not already running."""
    global _server
    if _server is not None and _server.is_running:
        if not silent_if_running:
            FreeCAD.Console.PrintMessage(
                f"FreeCAD Tutor MCP: RPC server already running on {_server.host}:{_server.port}\n"
            )
        return _server

    host = os.environ.get("FREECAD_MCP_HOST", DEFAULT_HOST)
    port = int(os.environ.get("FREECAD_MCP_PORT", str(DEFAULT_PORT)))

    srv = RPCServer(host=host, port=port)
    srv.register_all(_build_handler_map())
    srv.register("reload_handlers", _reload_handlers)

    try:
        srv.start()
        _server = srv
        msg = (
            f"FreeCAD Tutor MCP: RPC server auto-started on {host}:{port} "
            f"(PID {os.getpid()}, protocol {PROTOCOL_VERSION})\n"
        )
        FreeCAD.Console.PrintMessage(msg)
        _set_status_bar(f"Tutor MCP listening on {host}:{port}")
        return _server
    except OSError as e:
        FreeCAD.Console.PrintError(
            f"FreeCAD Tutor MCP: Failed to bind {host}:{port} ({e}). "
            "Is another FreeCAD MCP server already using this port?\n"
        )
        _set_status_bar(f"Tutor MCP failed to bind port {port}")
        return None


def stop_server() -> None:
    """Stop the running RPC server."""
    global _server
    if _server is None or not _server.is_running:
        FreeCAD.Console.PrintMessage("FreeCAD Tutor MCP: RPC server is not running\n")
        return
    _server.stop()
    _server = None
    FreeCAD.Console.PrintMessage("FreeCAD Tutor MCP: RPC server stopped\n")
    _set_status_bar("Tutor MCP stopped")


class StartTutorServerCommand:
    def GetResources(self):
        return {
            "MenuText": "Start Tutor RPC Server",
            "ToolTip": "Start the local TCP RPC server for Claude Desktop",
        }

    def Activated(self):
        ensure_started(silent_if_running=False)

    def IsActive(self):
        return _server is None or not _server.is_running


class StopTutorServerCommand:
    def GetResources(self):
        return {
            "MenuText": "Stop Tutor RPC Server",
            "ToolTip": "Stop the local TCP RPC server",
        }

    def Activated(self):
        stop_server()

    def IsActive(self):
        return _server is not None and _server.is_running


Gui.addCommand("StartTutorServer", StartTutorServerCommand())
Gui.addCommand("StopTutorServer", StopTutorServerCommand())
