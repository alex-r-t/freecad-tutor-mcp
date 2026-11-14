"""Undo-transaction-wrapped Python execution escape hatch."""

import contextlib
import io
import traceback
from typing import Any

import FreeCAD

from handlers.common import resolve_doc, undo_transaction
from handlers.error_kb import explain_freecad_error

try:
    import FreeCADGui
except ImportError:
    FreeCADGui = None


def execute_script(
    script: str,
    doc_name: str = "",
    wrap_transaction: bool = True,
) -> dict[str, Any]:
    """Execute Python inside FreeCAD's main thread, optionally wrapped in an undo transaction."""
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()

    doc = None
    if doc_name or FreeCAD.ActiveDocument is not None:
        doc = resolve_doc(doc_name)

    namespace: dict[str, Any] = {
        "FreeCAD": FreeCAD,
        "App": FreeCAD,
        "FreeCADGui": FreeCADGui,
        "Gui": FreeCADGui,
        "doc": doc,
        "__builtins__": __builtins__,
    }

    try:
        with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
            if wrap_transaction and doc is not None:
                with undo_transaction(doc, "MCP Execute Script"):
                    exec(script, namespace)
            else:
                exec(script, namespace)

        return {
            "success": True,
            "stdout": stdout_capture.getvalue(),
            "stderr": stderr_capture.getvalue(),
            "error": None,
        }
    except Exception as exc:
        return {
            "success": False,
            "stdout": stdout_capture.getvalue(),
            "stderr": stderr_capture.getvalue(),
            "error": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            "error_explanation": explain_freecad_error(str(exc)),
        }
