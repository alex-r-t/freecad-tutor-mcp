"""Confirmed, undo-transaction-wrapped modeling & TechDraw demonstration handlers.

All functions in this module:
- Wrap mutations in FreeCAD `openTransaction` / `commitTransaction` blocks so the
  student can undo any demonstration step with Ctrl+Z or `undo()`.
- Use FreeCAD 1.0+ `AttachmentSupport` (falling back to legacy `Support` if absent).
- Verify post-recompute object validity (`isValid()` / `State`) and roll back
  automatically if an operation produces invalid geometry.
"""

import math
import os
from typing import Any

import FreeCAD

from handlers.common import (
    resolve_doc,
    safe_error_description,
    safe_is_valid,
    undo_transaction,
)


def _find_parent_body(doc: Any, obj: Any) -> Any:
    """Find the PartDesign::Body that owns `obj`, or return None."""
    for candidate in doc.Objects:
        if candidate.TypeId == "PartDesign::Body" and obj in getattr(candidate, "Group", []):
            return candidate
    return None


def _verify_object_valid(obj: Any, action_label: str) -> None:
    """Confirm a created/edited feature recomputed cleanly; raise to trigger rollback if invalid."""
    state = list(obj.State) if hasattr(obj, "State") else []
    valid = safe_is_valid(obj)
    err_desc = safe_error_description(obj)
    if valid is False or "Invalid" in state or "Error" in state:
        detail = err_desc or f"state={state}"
        raise RuntimeError(
            f"{action_label} produced an invalid feature '{obj.Name}' ({detail}); rolled back."
        )


def _set_attachment_support(obj: Any, target: Any, sub: str = "") -> None:
    """Attach a Sketch or Feature using FreeCAD 1.x AttachmentSupport (or 0.21 Support)."""
    if hasattr(obj, "AttachmentSupport"):
        obj.AttachmentSupport = [(target, sub)]
    else:
        obj.Support = (target, [sub])
    obj.MapMode = "FlatFace"


# ── Undo / Redo ──────────────────────────────────────────────────────────

def undo(doc_name: str = "") -> dict[str, Any]:
    """Undo the most recent transaction in the document."""
    doc = resolve_doc(doc_name)
    if not doc.UndoCount:
        return {
            "undone": False,
            "message": "Nothing to undo",
            "undo_count": 0,
            "redo_count": doc.RedoCount,
        }
    doc.undo()
    doc.recompute()
    return {"undone": True, "undo_count": doc.UndoCount, "redo_count": doc.RedoCount}


def redo(doc_name: str = "") -> dict[str, Any]:
    """Redo the most recently undone transaction in the document."""
    doc = resolve_doc(doc_name)
    if not doc.RedoCount:
        return {
            "redone": False,
            "message": "Nothing to redo",
            "undo_count": doc.UndoCount,
            "redo_count": 0,
        }
    doc.redo()
    doc.recompute()
    return {"redone": True, "undo_count": doc.UndoCount, "redo_count": doc.RedoCount}


def get_undo_redo_status(doc_name: str = "") -> dict[str, Any]:
    """Return counts and transaction names available for undo/redo."""
    doc = resolve_doc(doc_name)
    return {
        "document": doc.Name,
        "undo_count": getattr(doc, "UndoCount", 0),
        "redo_count": getattr(doc, "RedoCount", 0),
        "undo_names": list(getattr(doc, "UndoNames", [])),
        "redo_names": list(getattr(doc, "RedoNames", [])),
    }


# ── PartDesign & Sketcher ────────────────────────────────────────────────

def create_partdesign_body(name: str = "", doc_name: str = "") -> dict[str, Any]:
    """Create a PartDesign::Body container for feature-based solid modeling."""
    doc = resolve_doc(doc_name)
    with undo_transaction(doc, "Create PartDesign Body"):
        body = doc.addObject("PartDesign::Body", name or "Body")
    return {
        "document": doc.Name,
        "name": body.Name,
        "label": body.Label,
        "type_id": body.TypeId,
    }


def create_sketch(
    body_name: str = "",
    plane: str = "XY_Plane",
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Create a Sketcher::SketchObject attached to an Origin plane or face."""
    doc = resolve_doc(doc_name)
    sketch_name = name or "Sketch"

    with undo_transaction(doc, "Create Sketch"):
        if body_name:
            body = doc.getObject(body_name)
            if body is None:
                raise ValueError(f"Body not found: {body_name}")
            sketch = body.newObject("Sketcher::SketchObject", sketch_name)
            if plane in ("XY_Plane", "XZ_Plane", "YZ_Plane"):
                plane_obj = body.Origin.getObject(plane)
                _set_attachment_support(sketch, plane_obj, "")
            elif plane.startswith("Face"):
                _set_attachment_support(sketch, body, plane)
            else:
                raise ValueError(
                    f"Unsupported plane '{plane}'. Use XY_Plane, XZ_Plane, YZ_Plane, or FaceN."
                )
        else:
            sketch = doc.addObject("Sketcher::SketchObject", sketch_name)
            rotations = {
                "XY_Plane": FreeCAD.Rotation(0, 0, 0, 1),
                "XZ_Plane": FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), -90),
                "YZ_Plane": FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), 90),
            }
            if plane not in rotations:
                raise ValueError(
                    f"Standalone sketch plane must be XY_Plane, XZ_Plane, or YZ_Plane (got '{plane}')"
                )
            sketch.Placement = FreeCAD.Placement(FreeCAD.Vector(0, 0, 0), rotations[plane])

    return {
        "document": doc.Name,
        "name": sketch.Name,
        "label": sketch.Label,
        "body": body_name or None,
        "plane": plane,
    }


def _to_vector(pt: list[float] | tuple[float, ...]) -> Any:
    """Convert a 2D [x, y] or 3D [x, y, z] list into a FreeCAD.Vector."""
    if len(pt) == 2:
        return FreeCAD.Vector(float(pt[0]), float(pt[1]), 0.0)
    return FreeCAD.Vector(float(pt[0]), float(pt[1]), float(pt[2]))


def edit_sketch(
    sketch_name: str,
    geometries: list[dict[str, Any]] | None = None,
    constraints: list[dict[str, Any]] | None = None,
    doc_name: str = "",
) -> dict[str, Any]:
    """Add geometry elements (line, rectangle, circle, arc) and constraints to a Sketch."""
    import Part
    import Sketcher

    doc = resolve_doc(doc_name)
    sketch = doc.getObject(sketch_name)
    if sketch is None:
        raise ValueError(f"Sketch not found: {sketch_name}")
    if not hasattr(sketch, "ConstraintCount"):
        raise ValueError(f"Object '{sketch_name}' is not a Sketch")

    added_geo: list[int] = []
    added_con: list[int] = []

    with undo_transaction(doc, f"Edit {sketch.Name}"):
        for geo_spec in geometries or []:
            kind = str(geo_spec.get("type", "")).lower()
            construction = bool(geo_spec.get("construction", False))

            if kind == "line":
                p1 = _to_vector(geo_spec["start"])
                p2 = _to_vector(geo_spec["end"])
                idx = sketch.addGeometry(Part.LineSegment(p1, p2), construction)
                added_geo.append(idx)

            elif kind == "circle":
                center = _to_vector(geo_spec["center"])
                radius = float(geo_spec["radius"])
                circle = Part.Circle(center, FreeCAD.Vector(0, 0, 1), radius)
                idx = sketch.addGeometry(circle, construction)
                added_geo.append(idx)

            elif kind == "rectangle":
                x = float(geo_spec.get("x", 0.0))
                y = float(geo_spec.get("y", 0.0))
                width = float(geo_spec["width"])
                height = float(geo_spec["height"])
                corners = [
                    FreeCAD.Vector(x, y, 0.0),
                    FreeCAD.Vector(x + width, y, 0.0),
                    FreeCAD.Vector(x + width, y + height, 0.0),
                    FreeCAD.Vector(x, y + height, 0.0),
                ]
                i0 = sketch.addGeometry(Part.LineSegment(corners[0], corners[1]), construction)
                i1 = sketch.addGeometry(Part.LineSegment(corners[1], corners[2]), construction)
                i2 = sketch.addGeometry(Part.LineSegment(corners[2], corners[3]), construction)
                i3 = sketch.addGeometry(Part.LineSegment(corners[3], corners[0]), construction)
                added_geo.extend([i0, i1, i2, i3])
                # Close the 4 corners and enforce axis alignment just like FreeCAD's Rectangle tool.
                sketch.addConstraint(Sketcher.Constraint("Coincident", i0, 2, i1, 1))
                sketch.addConstraint(Sketcher.Constraint("Coincident", i1, 2, i2, 1))
                sketch.addConstraint(Sketcher.Constraint("Coincident", i2, 2, i3, 1))
                sketch.addConstraint(Sketcher.Constraint("Coincident", i3, 2, i0, 1))
                sketch.addConstraint(Sketcher.Constraint("Horizontal", i0))
                sketch.addConstraint(Sketcher.Constraint("Horizontal", i2))
                sketch.addConstraint(Sketcher.Constraint("Vertical", i1))
                sketch.addConstraint(Sketcher.Constraint("Vertical", i3))

            elif kind == "arc":
                center = _to_vector(geo_spec["center"])
                radius = float(geo_spec["radius"])
                start_rad = math.radians(float(geo_spec["start_angle_deg"]))
                end_rad = math.radians(float(geo_spec["end_angle_deg"]))
                circle = Part.Circle(center, FreeCAD.Vector(0, 0, 1), radius)
                arc = Part.ArcOfCircle(circle, start_rad, end_rad)
                idx = sketch.addGeometry(arc, construction)
                added_geo.append(idx)

            else:
                raise ValueError(f"Unsupported sketch geometry type: '{kind}'")

        for con_spec in constraints or []:
            ctype = con_spec["type"]
            args: list[Any] = [ctype]
            for key in ("first", "first_pos", "second", "second_pos", "third", "third_pos"):
                if key in con_spec and con_spec[key] is not None:
                    args.append(con_spec[key])
            if "value" in con_spec and con_spec["value"] is not None:
                args.append(float(con_spec["value"]))
            idx = sketch.addConstraint(Sketcher.Constraint(*args))
            added_con.append(idx)

    return {
        "document": doc.Name,
        "sketch": sketch.Name,
        "added_geometry_indices": added_geo,
        "added_constraint_indices": added_con,
        "degrees_of_freedom": getattr(sketch, "DoF", None),
        "fully_constrained": getattr(sketch, "FullyConstrained", None),
    }


def pad_sketch(
    sketch_name: str,
    length: float = 10.0,
    symmetric: bool = False,
    reversed: bool = False,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Extrude a Sketch inside a PartDesign Body using Pad."""
    doc = resolve_doc(doc_name)
    sketch = doc.getObject(sketch_name)
    if sketch is None:
        raise ValueError(f"Sketch not found: {sketch_name}")
    body = _find_parent_body(doc, sketch)
    if body is None:
        raise ValueError(f"Sketch '{sketch_name}' is not inside a PartDesign::Body")

    with undo_transaction(doc, f"Pad {sketch.Name}"):
        pad = body.newObject("PartDesign::Pad", name or "Pad")
        pad.Profile = sketch
        pad.Length = float(length)
        # FreeCAD 1.1+ replaces Pad.Midplane with SideType ('One side' / 'Symmetric').
        if hasattr(pad, "SideType"):
            pad.SideType = "Symmetric" if symmetric else "One side"
        else:
            pad.Midplane = bool(symmetric)
        pad.Reversed = bool(reversed)
        doc.recompute()
        _verify_object_valid(pad, "Pad")

    return {"document": doc.Name, "name": pad.Name, "body": body.Name, "length": float(length)}


def pocket_sketch(
    sketch_name: str,
    length: float = 10.0,
    through_all: bool = False,
    reversed: bool = False,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Cut material from a PartDesign Body using Pocket."""
    doc = resolve_doc(doc_name)
    sketch = doc.getObject(sketch_name)
    if sketch is None:
        raise ValueError(f"Sketch not found: {sketch_name}")
    body = _find_parent_body(doc, sketch)
    if body is None:
        raise ValueError(f"Sketch '{sketch_name}' is not inside a PartDesign::Body")

    with undo_transaction(doc, f"Pocket {sketch.Name}"):
        pocket = body.newObject("PartDesign::Pocket", name or "Pocket")
        pocket.Profile = sketch
        # Use named enum strings verified in FeaturePocket.cpp (`Length`, `ThroughAll`).
        if through_all:
            pocket.Type = "ThroughAll"
        else:
            pocket.Type = "Length"
            pocket.Length = float(length)
        pocket.Reversed = bool(reversed)
        doc.recompute()
        _verify_object_valid(pocket, "Pocket")

    return {"document": doc.Name, "name": pocket.Name, "body": body.Name, "through_all": through_all}


# ── Part Workbench (CSG) & Dress-Up Features ─────────────────────────────

def create_part_primitive(
    shape_type: str,
    dimensions: dict[str, float] | None = None,
    position: list[float] | None = None,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Create a Part CSG primitive ('Box', 'Cylinder', 'Sphere', or 'Cone')."""
    type_map = {
        "box": "Part::Box",
        "cube": "Part::Box",
        "cylinder": "Part::Cylinder",
        "sphere": "Part::Sphere",
        "cone": "Part::Cone",
    }
    key = shape_type.strip().lower()
    type_id = type_map.get(key)
    if type_id is None:
        raise ValueError(
            f"Unsupported Part primitive '{shape_type}'. Use Box, Cylinder, Sphere, or Cone."
        )

    doc = resolve_doc(doc_name)
    dims = dimensions or {}
    default_name = type_id.split("::", 1)[1]

    with undo_transaction(doc, f"Create {default_name}"):
        obj = doc.addObject(type_id, name or default_name)
        if type_id == "Part::Box":
            obj.Length = float(dims.get("length", 10.0))
            obj.Width = float(dims.get("width", 10.0))
            obj.Height = float(dims.get("height", 10.0))
        elif type_id == "Part::Cylinder":
            obj.Radius = float(dims.get("radius", 5.0))
            obj.Height = float(dims.get("height", 10.0))
            if "angle" in dims:
                obj.Angle = float(dims["angle"])
        elif type_id == "Part::Sphere":
            obj.Radius = float(dims.get("radius", 5.0))
        elif type_id == "Part::Cone":
            obj.Radius1 = float(dims.get("radius1", 5.0))
            obj.Radius2 = float(dims.get("radius2", 2.0))
            obj.Height = float(dims.get("height", 10.0))

        if position is not None:
            if len(position) != 3:
                raise ValueError("position must be [x, y, z]")
            obj.Placement.Base = FreeCAD.Vector(
                float(position[0]), float(position[1]), float(position[2])
            )

        doc.recompute()
        _verify_object_valid(obj, f"Create {default_name}")

    return {"document": doc.Name, "name": obj.Name, "type_id": obj.TypeId}


def boolean_operation(
    operation: str,
    base_name: str,
    tool_name: str,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Perform a Part CSG Boolean ('Fuse', 'Cut', or 'Common')."""
    op_map = {"fuse": "Part::Fuse", "cut": "Part::Cut", "common": "Part::Common"}
    type_id = op_map.get(operation.strip().lower())
    if type_id is None:
        raise ValueError(f"Unsupported boolean operation '{operation}'. Use Fuse, Cut, or Common.")

    doc = resolve_doc(doc_name)
    base_obj = doc.getObject(base_name)
    tool_obj = doc.getObject(tool_name)
    if base_obj is None:
        raise ValueError(f"Base object not found: {base_name}")
    if tool_obj is None:
        raise ValueError(f"Tool object not found: {tool_name}")

    with undo_transaction(doc, f"Boolean {operation}"):
        feat = doc.addObject(type_id, name or operation.capitalize())
        feat.Base = base_obj
        feat.Tool = tool_obj
        doc.recompute()
        _verify_object_valid(feat, f"Boolean {operation}")

    return {"document": doc.Name, "name": feat.Name, "operation": operation}


def fillet_edges(
    object_name: str,
    edges: list[str],
    radius: float = 1.0,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Round selected edges (e.g., ['Edge1', 'Edge2']) on a feature or shape."""
    return _edge_dressup("PartDesign::Fillet", "Fillet", object_name, edges, radius, name, doc_name)


def chamfer_edges(
    object_name: str,
    edges: list[str],
    size: float = 1.0,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Bevel selected edges (e.g., ['Edge1']) on a feature or shape."""
    return _edge_dressup("PartDesign::Chamfer", "Chamfer", object_name, edges, size, name, doc_name)


def _edge_dressup(
    type_id: str,
    default_label: str,
    object_name: str,
    edges: list[str],
    dimension: float,
    name: str,
    doc_name: str,
) -> dict[str, Any]:
    if not edges:
        raise ValueError("edges must be a non-empty list of edge names (e.g., ['Edge1'])")
    doc = resolve_doc(doc_name)
    base = doc.getObject(object_name)
    if base is None:
        raise ValueError(f"Object not found: {object_name}")

    body = _find_parent_body(doc, base)
    with undo_transaction(doc, f"{default_label} {base.Name}"):
        if body is not None:
            feat = body.newObject(type_id, name or default_label)
            feat.Base = (base, edges)
        else:
            part_type = "Part::Fillet" if default_label == "Fillet" else "Part::Chamfer"
            feat = doc.addObject(part_type, name or default_label)
            feat.Base = base
            edge_indices = [int(e.replace("Edge", "")) for e in edges]
            feat.Edges = [(idx, float(dimension), float(dimension)) for idx in edge_indices]

        if hasattr(feat, "Radius"):
            feat.Radius = float(dimension)
        elif hasattr(feat, "Size"):
            feat.Size = float(dimension)

        doc.recompute()
        _verify_object_valid(feat, default_label)

    return {"document": doc.Name, "name": feat.Name, "edges": edges, "dimension": float(dimension)}


# ── TechDraw ─────────────────────────────────────────────────────────────

def _resolve_techdraw_template(template_name: str) -> str:
    """Locate a TechDraw SVG template inside FreeCAD's resource directory (1.0 & 1.1+ layouts)."""
    filename = template_name if template_name.endswith(".svg") else f"{template_name}.svg"
    stem = filename[:-4]
    res_dir = FreeCAD.getResourceDir()
    tpl_roots = [
        os.path.join(res_dir, "Mod", "TechDraw", "Templates"),
        os.path.join(res_dir, "share", "Mod", "TechDraw", "Templates"),
    ]

    # FreeCAD 1.1 reorganized Templates/ into Default_Template_*.svg + ISO/ and ANSI/ subdirs.
    alias_filenames = [filename]
    if stem.lower() in ("a4_landscape", "default_a4_landscape"):
        alias_filenames.extend([
            "Default_Template_A4_Landscape.svg",
            "A4_Landscape_TD.svg",
            "A4_Landscape_blank.svg",
        ])
    elif stem.lower() in ("a4_portrait", "a3_landscape", "usletter_landscape"):
        alias_filenames.extend([
            f"Default_Template_{stem}.svg",
            f"{stem}_TD.svg",
            f"{stem}_blank.svg",
        ])

    for root in tpl_roots:
        for candidate_name in alias_filenames:
            direct = os.path.join(root, candidate_name)
            if os.path.exists(direct):
                return direct

    # Recursive fallback scan inside Templates/ (preferring non-localized paths first).
    target_names_lower = [name.lower() for name in alias_filenames]
    for root in tpl_roots:
        if not os.path.isdir(root):
            continue
        non_local: list[str] = []
        localized: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in filenames:
                if not fname.lower().endswith(".svg"):
                    continue
                full = os.path.join(dirpath, fname)
                if "localized" in dirpath.lower():
                    localized.append(full)
                else:
                    non_local.append(full)
        for pool in (non_local, localized):
            for wanted in target_names_lower:
                for full in pool:
                    if os.path.basename(full).lower() == wanted:
                        return full
            for full in pool:
                if stem.lower() in os.path.basename(full).lower():
                    return full

    raise FileNotFoundError(
        f"TechDraw template '{filename}' not found under {tpl_roots}."
    )


def create_techdraw_page(
    template: str = "A4_Landscape",
    name: str = "Page",
    doc_name: str = "",
) -> dict[str, Any]:
    """Create a TechDraw 2D drawing page using a standard FreeCAD SVG template."""
    doc = resolve_doc(doc_name)
    tpl_path = _resolve_techdraw_template(template)

    with undo_transaction(doc, "Create TechDraw Page"):
        page = doc.addObject("TechDraw::DrawPage", name or "Page")
        tpl = doc.addObject("TechDraw::DrawSVGTemplate", f"{page.Name}_Template")
        tpl.Template = tpl_path
        page.Template = tpl

    return {"document": doc.Name, "page": page.Name, "template": template}


def add_techdraw_view(
    page_name: str,
    object_name: str,
    direction: str = "Front",
    scale: float = 1.0,
    projection_group: bool = False,
    views: list[str] | None = None,
    doc_name: str = "",
) -> dict[str, Any]:
    """Add an orthographic view or Projection Group of a 3D object to a TechDraw page."""
    doc = resolve_doc(doc_name)
    page = doc.getObject(page_name)
    if page is None:
        raise ValueError(f"TechDraw page not found: {page_name}")
    source = doc.getObject(object_name)
    if source is None:
        raise ValueError(f"Source object not found: {object_name}")

    dir_map = {
        "Front": FreeCAD.Vector(0, -1, 0),
        "Back": FreeCAD.Vector(0, 1, 0),
        "Top": FreeCAD.Vector(0, 0, 1),
        "Bottom": FreeCAD.Vector(0, 0, -1),
        "Left": FreeCAD.Vector(-1, 0, 0),
        "Right": FreeCAD.Vector(1, 0, 0),
        "Isometric": FreeCAD.Vector(1, -1, 1),
    }

    with undo_transaction(doc, "Add TechDraw View"):
        if projection_group:
            group = doc.addObject("TechDraw::DrawProjGroup", "ProjGroup")
            page.addView(group)
            group.Source = [source]
            group.Scale = float(scale)
            group.addProjection("Front")
            for view_dir in views or ["Top", "Right"]:
                if view_dir != "Front":
                    group.addProjection(view_dir)
            created_name = group.Name
        else:
            if direction not in dir_map:
                raise ValueError(f"Invalid direction '{direction}'. Valid: {list(dir_map.keys())}")
            view = doc.addObject("TechDraw::DrawViewPart", "View")
            view.Source = [source]
            view.Direction = dir_map[direction]
            view.Scale = float(scale)
            page.addView(view)
            created_name = view.Name

    return {
        "document": doc.Name,
        "page": page.Name,
        "view": created_name,
        "projection_group": projection_group,
    }
