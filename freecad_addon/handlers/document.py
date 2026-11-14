"""Document-level inspection and creation handlers."""

from typing import Any

import FreeCAD

from handlers.common import (
    check_tnp_attachment,
    get_ui_edit_state,
    resolve_doc,
    safe_error_description,
    safe_is_valid,
)
from json_utils import sanitize_json


# Compact property subset shown by default in get_document_graph so large models
# do not flood the AI's context window. Full properties live in inspect_object().
_COMPACT_PROPERTY_KEYS = (
    "Placement",
    "Length",
    "Width",
    "Height",
    "Radius",
    "Radius1",
    "Radius2",
    "Angle",
    "Type",
    "Midplane",
    "Reversed",
    "AttachmentSupport",
    "Support",
    "MapMode",
    "Scale",
    "Direction",
)


def list_documents() -> dict[str, Any]:
    """Return a consistent dictionary listing all open FreeCAD documents.

    Returning a dictionary wrapper (instead of a bare list) prevents MCP/FastMCP
    clients from unwrapping single-item lists into a flat document object while
    returning an empty list when zero documents are open.
    """
    active_name = FreeCAD.ActiveDocument.Name if FreeCAD.ActiveDocument else None
    documents: list[dict[str, Any]] = []
    for name, doc in FreeCAD.listDocuments().items():
        if hasattr(doc, "isSaved"):
            is_modified = not doc.isSaved()
        else:
            is_modified = getattr(doc, "Modified", None)

        documents.append({
            "name": name,
            "label": doc.Label,
            "file_name": doc.FileName or None,
            "object_count": len(doc.Objects),
            "is_active": name == active_name,
            "is_modified": is_modified,
        })
    return {
        "active_document": active_name,
        "count": len(documents),
        "documents": documents,
    }


def get_document_graph(
    doc_name: str = "",
    include_properties: bool = False,
) -> dict[str, Any]:
    """Return a structured feature tree, dependencies, validity, and TNP/UI hints.

    By default (`include_properties=False`), only key geometric/attachment
    properties are included to keep token usage small on real CAD models.
    Pass `include_properties=True` or call `inspect_object` for a full dump.
    """
    doc = resolve_doc(doc_name)
    graph: dict[str, Any] = {}
    tnp_warnings: list[dict[str, str]] = []

    for obj in doc.Objects:
        props: dict[str, Any] = {}
        prop_names = (
            obj.PropertiesList
            if include_properties
            else [p for p in _COMPACT_PROPERTY_KEYS if p in obj.PropertiesList]
        )
        for prop_name in prop_names:
            try:
                props[prop_name] = sanitize_json(getattr(obj, prop_name))
            except Exception:
                props[prop_name] = "<unreadable>"

        valid = safe_is_valid(obj)
        if valid is True:
            state_str = "valid"
        elif valid is False:
            state_str = "invalid"
        else:
            state_str = "unknown"

        entry: dict[str, Any] = {
            "type": obj.TypeId,
            "label": obj.Label,
            "state": state_str,
            "dependencies": [dep.Name for dep in obj.InList],
            "sources": [src.Name for src in obj.OutList],
            "properties": props,
        }

        try:
            entry["touch_state"] = "touched" if obj.isTouched() else "clean"
        except AttributeError:
            entry["touch_state"] = "unknown"

        err_desc = safe_error_description(obj)
        if err_desc:
            entry["error_description"] = err_desc

        tnp_msg = check_tnp_attachment(obj)
        if tnp_msg:
            entry["tnp_warning"] = tnp_msg
            tnp_warnings.append({"object": obj.Name, "warning": tnp_msg})

        graph[obj.Name] = entry

    return {
        "document": doc.Name,
        "label": doc.Label,
        "include_properties": include_properties,
        "ui_state": get_ui_edit_state(),
        "tnp_warnings": tnp_warnings,
        "objects": graph,
    }


def create_document(name: str = "Tutor_Demo", activate: bool = True) -> dict[str, Any]:
    """Create a new FreeCAD document (defaults to a safe 'Tutor_Demo' scratch file)."""
    doc_name = name.strip() or "Tutor_Demo"
    doc = FreeCAD.newDocument(doc_name)
    if activate and hasattr(FreeCAD, "setActiveDocument"):
        FreeCAD.setActiveDocument(doc.Name)
    return {
        "name": doc.Name,
        "label": doc.Label,
        "is_demo": doc.Name.startswith("Tutor_Demo"),
    }
