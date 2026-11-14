"""Shared helper functions used across all FreeCAD addon handlers.

These helpers centralize document lookup, UI edit-state checks, JSON
sanitization, Topological Naming Problem (TNP) warnings, and undo-safe
transaction handling so individual handler files stay simple and readable.
"""

from contextlib import contextmanager
from typing import Any, Iterator

import FreeCAD

from json_utils import sanitize_json

try:
    import FreeCADGui
except ImportError:
    FreeCADGui = None


def resolve_doc(doc_name: str = "") -> Any:
    """Return the requested FreeCAD document, or ActiveDocument when `doc_name` is empty."""
    if doc_name:
        doc = FreeCAD.getDocument(doc_name)
        if doc is None:
            raise ValueError(f"Document not found: {doc_name}")
        return doc

    doc = FreeCAD.ActiveDocument
    if doc is None:
        raise ValueError("No active document is open in FreeCAD")
    return doc


def require_gui() -> Any:
    """Return the FreeCADGui module, or raise a clear error if running headless."""
    if FreeCADGui is None or not getattr(FreeCAD, "GuiUp", False):
        raise RuntimeError("FreeCAD GUI is not available (running in headless mode?)")
    return FreeCADGui


def get_ui_edit_state() -> dict[str, Any]:
    """Check whether FreeCAD currently has a Task panel open or an object in edit mode.

    Beginners frequently leave a Sketch or PartDesign Task panel open while
    asking why buttons are greyed out or why edits fail. Exposing this state
    lets the AI diagnose the UI state immediately.
    """
    if FreeCADGui is None or not getattr(FreeCAD, "GuiUp", False):
        return {"gui_up": False, "active_dialog": False, "in_edit": None}

    active_dialog = False
    try:
        control = getattr(FreeCADGui, "Control", None)
        if control is not None and hasattr(control, "activeDialog"):
            active_dialog = bool(control.activeDialog())
    except Exception:
        pass

    in_edit = None
    try:
        gui_doc = FreeCADGui.ActiveDocument
        if gui_doc is not None and hasattr(gui_doc, "getInEdit"):
            edit_view_provider = gui_doc.getInEdit()
            if (
                edit_view_provider is not None
                and hasattr(edit_view_provider, "Object")
                and edit_view_provider.Object is not None
            ):
                in_edit = edit_view_provider.Object.Name
    except Exception:
        pass

    return {
        "gui_up": True,
        "active_dialog": active_dialog,
        "in_edit": in_edit,
    }


def vec_to_list(vec: Any) -> list[float]:
    """Convert a FreeCAD Vector/Point (supporting lowercase .x or uppercase .X) to [x, y, z]."""
    if hasattr(vec, "x"):
        return [float(vec.x), float(vec.y), float(vec.z)]
    return [float(vec.X), float(vec.Y), float(vec.Z)]


def check_tnp_attachment(obj: Any) -> str | None:
    """Warn if an object is attached directly to a generated Face/Edge/Vertex.

    Even with FreeCAD 1.0+ TNP mitigations, attaching sketches to generated
    solid faces is less stable than attaching to Origin planes or DatumPlanes.
    """
    support = None
    if hasattr(obj, "AttachmentSupport"):
        # FreeCAD 1.0+ property name
        support = getattr(obj, "AttachmentSupport", None)
    elif hasattr(obj, "Support"):
        # FreeCAD 0.21 legacy property name
        support = getattr(obj, "Support", None)

    if not support:
        return None

    items = list(support) if isinstance(support, (list, tuple)) else [support]
    # Legacy Support can be a single 2-tuple: (target_obj, ["Face1"])
    if len(items) == 2 and hasattr(items[0], "TypeId") and isinstance(items[1], (list, tuple, str)):
        items = [items]

    for item in items:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        target, sub_refs = item[0], item[1]
        sub_list = list(sub_refs) if isinstance(sub_refs, (list, tuple)) else [sub_refs]
        for sub_name in sub_list:
            if not isinstance(sub_name, str):
                continue
            if sub_name.startswith(("Face", "Edge", "Vertex")):
                target_name = getattr(target, "Name", str(target))
                return (
                    f"Attached to generated sub-element '{target_name}.{sub_name}', which is "
                    "vulnerable to the Topological Naming Problem (TNP) if earlier features change. "
                    "Prefer attaching to an Origin plane or DatumPlane when possible."
                )
    return None


def safe_is_valid(obj: Any) -> bool | None:
    """Return obj.isValid() if supported, or None if the object type lacks isValid()."""
    try:
        return bool(obj.isValid())
    except AttributeError:
        return None


def safe_error_description(obj: Any) -> str | None:
    """Return FreeCAD's human-readable error description for an object, if any.

    In FreeCAD's C++ Python bindings (DocumentObjectPyImp.cpp), `obj.getStatusString()`
    returns `Document::getErrorDescription(obj)` when the object is in error state,
    or 'Valid' / 'Touched' / 'Freezed' otherwise.
    """
    if hasattr(obj, "getStatusString"):
        try:
            status = str(obj.getStatusString())
            if status and status not in ("Valid", "Touched", "Freezed"):
                return status
        except Exception:
            pass

    # Fallback for mocks or custom objects providing getErrorDescription()
    if hasattr(obj, "getErrorDescription"):
        try:
            desc = obj.getErrorDescription()
            if desc:
                return str(desc)
        except Exception:
            pass
    return None


@contextmanager
def undo_transaction(doc: Any, label: str, *, allow_during_dialog: bool = False) -> Iterator[None]:
    """Wrap a document mutation in a FreeCAD Ctrl+Z undo transaction.

    If an exception occurs (including post-recompute validation errors), the
    transaction is aborted cleanly so no half-broken objects remain in the tree.
    """
    if not allow_during_dialog:
        ui_state = get_ui_edit_state()
        if ui_state["active_dialog"]:
            editing = ui_state["in_edit"] or "unknown"
            raise RuntimeError(
                f"A FreeCAD Task panel is currently open (editing: {editing}). "
                "Ask the user to click OK or Cancel in the Tasks panel before modifying the model."
            )

    doc.openTransaction(label)
    try:
        yield
        doc.recompute()
        doc.commitTransaction()
    except Exception:
        try:
            doc.abortTransaction()
        except Exception:
            pass
        raise
