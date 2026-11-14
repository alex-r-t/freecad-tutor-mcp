"""Document-level inspection and creation handlers."""

import os
from typing import Any

import FreeCAD

from handlers.common import (
    check_tnp_attachment,
    get_ui_edit_state,
    resolve_doc,
    safe_error_description,
    safe_is_valid,
)
from handlers.error_kb import explain_freecad_error
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


def _get_active_body_name() -> str | None:
    """Return the active PartDesign::Body name in the GUI view, if any."""
    if not getattr(FreeCAD, "GuiUp", False):
        return None
    try:
        import FreeCADGui as Gui

        gui_doc = Gui.ActiveDocument
        view = gui_doc.ActiveView if gui_doc is not None else None
        if view is not None and hasattr(view, "getActiveObject"):
            active_body = view.getActiveObject("pdbody")
            if active_body is not None:
                return getattr(active_body, "Name", None)
    except Exception:
        pass
    return None


def get_document_graph(
    doc_name: str = "",
    include_properties: bool = False,
) -> dict[str, Any]:
    """Return a structured feature tree, dependencies, validity, Body tips, and TNP/UI hints.

    By default (`include_properties=False`), only key geometric/attachment
    properties are included to keep token usage small on real CAD models.
    Pass `include_properties=True` or call `inspect_object` for a full dump.
    """
    doc = resolve_doc(doc_name)
    graph: dict[str, Any] = {}
    tnp_warnings: list[dict[str, str]] = []
    body_tips: dict[str, str | None] = {}

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

        if obj.TypeId == "PartDesign::Body":
            tip_obj = getattr(obj, "Tip", None)
            tip_name = getattr(tip_obj, "Name", None) if tip_obj is not None else None
            entry["tip"] = tip_name
            body_tips[obj.Name] = tip_name

        try:
            entry["touch_state"] = "touched" if obj.isTouched() else "clean"
        except AttributeError:
            entry["touch_state"] = "unknown"

        err_desc = safe_error_description(obj)
        if err_desc:
            entry["error_description"] = err_desc
            entry["error_explanation"] = explain_freecad_error(err_desc)

        tnp_msg = check_tnp_attachment(obj)
        if tnp_msg:
            entry["tnp_warning"] = tnp_msg
            tnp_warnings.append({"object": obj.Name, "warning": tnp_msg})

        graph[obj.Name] = entry

    return {
        "document": doc.Name,
        "label": doc.Label,
        "include_properties": include_properties,
        "active_body": _get_active_body_name(),
        "body_tips": body_tips,
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


def save_document(path: str = "", doc_name: str = "") -> dict[str, Any]:
    """Save an open FreeCAD document to its existing path or a new `.FCStd` path."""
    doc = resolve_doc(doc_name)
    target_path = path.strip() if path else ""
    if not target_path and not getattr(doc, "FileName", ""):
        raise ValueError(
            f"Document '{doc.Name}' has never been saved. Provide a destination `path` ending in '.FCStd'."
        )

    if target_path:
        if not target_path.lower().endswith(".fcstd"):
            target_path = f"{target_path}.FCStd"
        parent = os.path.dirname(os.path.abspath(target_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        doc.saveAs(target_path)
    else:
        doc.save()

    return {
        "saved": True,
        "document": doc.Name,
        "file_name": getattr(doc, "FileName", None) or target_path,
    }


def open_document(path: str, activate: bool = True) -> dict[str, Any]:
    """Open an existing `.FCStd` document (or import a `.step`/`.stp`/`.iges`/`.igs` file)."""
    abs_path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"CAD file not found: {abs_path}")

    lower = abs_path.lower()
    if lower.endswith((".step", ".stp", ".iges", ".igs")):
        import Part

        doc = FreeCAD.newDocument(os.path.splitext(os.path.basename(abs_path))[0])
        Part.insert(abs_path, doc.Name)
        doc.recompute()
    else:
        doc = FreeCAD.openDocument(abs_path)

    if activate and doc is not None and hasattr(FreeCAD, "setActiveDocument"):
        FreeCAD.setActiveDocument(doc.Name)

    return {
        "opened": True,
        "name": doc.Name,
        "label": doc.Label,
        "file_name": getattr(doc, "FileName", None) or abs_path,
        "object_count": len(getattr(doc, "Objects", [])),
    }


def close_document(
    doc_name: str = "",
    save_before_close: bool = False,
    discard_unsaved: bool = False,
) -> dict[str, Any]:
    """Close a FreeCAD document safely, refusing to discard unsaved changes unless confirmed."""
    doc = resolve_doc(doc_name)
    target_name = doc.Name
    is_modified = (not doc.isSaved()) if hasattr(doc, "isSaved") else bool(getattr(doc, "Modified", False))

    if is_modified:
        if save_before_close:
            if not getattr(doc, "FileName", ""):
                raise ValueError(
                    f"Cannot save '{target_name}' before closing because it has no FileName yet. "
                    "Call `save_document(path=...)` first or pass `discard_unsaved=True`."
                )
            doc.save()
        elif not discard_unsaved:
            raise RuntimeError(
                f"Document '{target_name}' has unsaved changes. Save it with `save_document`, pass "
                "`save_before_close=True`, or confirm with `discard_unsaved=True`."
            )

    FreeCAD.closeDocument(target_name)
    new_active = FreeCAD.ActiveDocument.Name if FreeCAD.ActiveDocument else None
    return {
        "closed": True,
        "document": target_name,
        "saved": bool(is_modified and save_before_close),
        "active_document": new_active,
    }


def _select_export_objects(doc: Any, object_names: list[str] | None) -> list[Any]:
    """Resolve explicit objects for export, or auto-pick top-level Bodies/solids."""
    if object_names:
        objs = []
        for obj_name in object_names:
            obj = doc.getObject(obj_name)
            if obj is None:
                raise ValueError(f"Export object not found: {obj_name}")
            objs.append(obj)
        return objs

    bodies = [
        o for o in doc.Objects
        if o.TypeId == "PartDesign::Body" and hasattr(o, "Shape") and not o.Shape.isNull()
    ]
    if bodies:
        return bodies

    solids = [
        o for o in doc.Objects
        if hasattr(o, "Shape")
        and not o.Shape.isNull()
        and not o.TypeId.startswith(("Sketcher::", "PartDesign::Plane", "App::"))
        and not getattr(o, "InList", [])
    ]
    if solids:
        return solids

    fallback = [o for o in doc.Objects if hasattr(o, "Shape") and not o.Shape.isNull()]
    if not fallback:
        raise ValueError(f"No exportable 3D shape objects found in document '{doc.Name}'.")
    return fallback


def export_document(
    path: str,
    object_names: list[str] | None = None,
    export_format: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Export document objects to STEP (`.step`/`.stp`), IGES (`.iges`), STL/OBJ/3MF, or BREP."""
    doc = resolve_doc(doc_name)
    abs_path = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    fmt = (export_format or os.path.splitext(abs_path)[1].lstrip(".")).strip().lower()
    if not fmt:
        raise ValueError("Specify a file extension in `path` (e.g. .step, .stl) or `export_format`.")

    objs = _select_export_objects(doc, object_names)
    exported_names = [o.Name for o in objs]

    if fmt in ("step", "stp", "iges", "igs", "brep"):
        import Part

        Part.export(objs, abs_path)
    elif fmt in ("stl", "obj", "3mf", "ply"):
        import Mesh

        Mesh.export(objs, abs_path)
    else:
        raise ValueError(
            f"Unsupported export format '{fmt}'. Supported: step, stp, stl, obj, 3mf, ply, iges, brep."
        )

    return {
        "exported": True,
        "document": doc.Name,
        "format": fmt,
        "path": abs_path,
        "object_names": exported_names,
        "file_size_bytes": os.path.getsize(abs_path) if os.path.exists(abs_path) else None,
    }
