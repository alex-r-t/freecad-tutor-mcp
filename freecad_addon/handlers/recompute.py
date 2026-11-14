"""Tracked recompute handler — recompute with before/after error diff."""

from typing import Any

from handlers.common import (
    get_ui_edit_state,
    resolve_doc,
    safe_error_description,
    safe_is_valid,
)


def tracked_recompute(doc_name: str = "") -> dict[str, Any]:
    """Recompute a document and report new, resolved, and persistent errors."""
    ui_state = get_ui_edit_state()
    if ui_state["active_dialog"]:
        raise RuntimeError(
            f"Cannot recompute while a FreeCAD Task panel is open (editing: {ui_state['in_edit'] or 'unknown'}). "
            "Ask the user to close/OK/Cancel the Tasks panel first."
        )

    doc = resolve_doc(doc_name)
    before: dict[str, bool | None] = {obj.Name: safe_is_valid(obj) for obj in doc.Objects}

    doc.recompute()

    new_errors: list[dict[str, Any]] = []
    resolved: list[str] = []
    persistent_errors: list[dict[str, Any]] = []
    still_valid: list[str] = []

    for obj in doc.Objects:
        was_valid = before.get(obj.Name, True)
        is_valid = safe_is_valid(obj)
        if is_valid is False and was_valid is not False:
            new_errors.append(_obj_summary(obj))
        elif is_valid is False and was_valid is False:
            persistent_errors.append(_obj_summary(obj))
        elif is_valid is True and was_valid is False:
            resolved.append(obj.Name)
        else:
            still_valid.append(obj.Name)

    new_objects = [obj.Name for obj in doc.Objects if obj.Name not in before]

    return {
        "document": doc.Name,
        "total_objects": len(doc.Objects),
        "new_errors": new_errors,
        "resolved": resolved,
        "persistent_errors": persistent_errors,
        "valid_count": len(still_valid),
        "new_objects": new_objects,
    }


def _obj_summary(obj: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "name": obj.Name,
        "label": obj.Label,
        "type": obj.TypeId,
        "state": list(obj.State) if hasattr(obj, "State") and obj.State else [],
    }
    err = safe_error_description(obj)
    if err:
        summary["error_description"] = err
    return summary
