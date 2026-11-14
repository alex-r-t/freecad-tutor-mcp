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
    find_parent_body,
    resolve_doc,
    resolve_origin_feature,
    safe_error_description,
    safe_is_valid,
    set_attachment_support,
    undo_transaction,
    verify_object_valid,
)

# Preserve backward-compatible private aliases if any external script imports them.
_find_parent_body = find_parent_body
_verify_object_valid = verify_object_valid
_set_attachment_support = set_attachment_support


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
                plane_obj = resolve_origin_feature(body, plane)
                set_attachment_support(sketch, plane_obj, "")
            elif plane.startswith("Face"):
                set_attachment_support(sketch, body, plane)
            elif doc.getObject(plane) is not None:
                datum_target = doc.getObject(plane)
                set_attachment_support(sketch, datum_target, "")
            else:
                raise ValueError(
                    f"Unsupported plane '{plane}'. Use XY_Plane, XZ_Plane, YZ_Plane, FaceN, or a DatumPlane name."
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


_SKETCH_AUTO_TOL = 1e-6


def _point_2d_key(pt: Any) -> tuple[float, float]:
    """Normalize a FreeCAD Vector/tuple onto a 1e-6 mm grid key for endpoint matching."""
    if hasattr(pt, "x"):
        return (round(float(pt.x), 6), round(float(pt.y), 6))
    return (round(float(pt[0]), 6), round(float(pt[1]), 6))


def _dist_2d(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _snapshot_sketch_endpoints(sketch: Any) -> list[tuple[int, int, tuple[float, float]]]:
    """Collect (geo_idx, point_pos, (x, y)) for existing open-curve endpoints (StartPoint/EndPoint)."""
    endpoints: list[tuple[int, int, tuple[float, float]]] = []
    for idx, geo in enumerate(getattr(sketch, "Geometry", []) or []):
        if hasattr(geo, "StartPoint") and hasattr(geo, "EndPoint"):
            endpoints.append((idx, 1, _point_2d_key(geo.StartPoint)))
            endpoints.append((idx, 2, _point_2d_key(geo.EndPoint)))
    return endpoints


def _existing_coincident_pairs(sketch: Any) -> set[tuple[tuple[int, int], tuple[int, int]]]:
    """Return canonical pairs ((geoA, posA), (geoB, posB)) already joined by Coincident."""
    pairs: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for con in getattr(sketch, "Constraints", []) or []:
        if getattr(con, "Type", "") == "Coincident":
            a = (int(con.First), int(con.FirstPos))
            b = (int(con.Second), int(con.SecondPos))
            pairs.add((min(a, b), max(a, b)))
    return pairs


def _apply_auto_coincident(
    sketch: Any,
    Sketcher: Any,
    existing_endpoints: list[tuple[int, int, tuple[float, float]]],
    new_endpoints: list[tuple[int, int, tuple[float, float]]],
) -> list[dict[str, Any]]:
    """Add Coincident constraints between matching newly added and pre-existing endpoints.

    Uses a cluster-representative strategy so N edges meeting at one corner get
    N-1 Coincident constraints (a minimal spanning chain) instead of redundant cycles.
    """
    added: list[dict[str, Any]] = []
    seen_pairs = _existing_coincident_pairs(sketch)
    # Seed representatives with pre-existing endpoints first so new segments snap onto existing ones.
    clusters: list[tuple[int, int, tuple[float, float]]] = list(existing_endpoints)

    for geo_idx, pos, xy in new_endpoints:
        matched_rep: tuple[int, int, tuple[float, float]] | None = None
        for rep in clusters:
            if rep[0] == geo_idx and rep[1] == pos:
                continue
            if _dist_2d(xy, rep[2]) <= _SKETCH_AUTO_TOL:
                matched_rep = rep
                break

        if matched_rep is None:
            clusters.append((geo_idx, pos, xy))
            continue

        a = (matched_rep[0], matched_rep[1])
        b = (geo_idx, pos)
        if a[0] == b[0] and a[1] == b[1]:
            continue
        pair_key = (min(a, b), max(a, b))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        con_idx = sketch.addConstraint(Sketcher.Constraint("Coincident", a[0], a[1], b[0], b[1]))
        added.append({
            "index": con_idx,
            "type": "Coincident",
            "first": a[0],
            "first_pos": a[1],
            "second": b[0],
            "second_pos": b[1],
        })
    return added


def _resolve_constraint_index(sketch: Any, spec: dict[str, Any]) -> int:
    """Resolve a constraint index from either explicit 'index' or constraint 'name'."""
    if spec.get("index") is not None:
        return int(spec["index"])
    target_name = str(spec.get("name", "")).strip()
    if not target_name:
        raise ValueError("Constraint update requires either 'index' or 'name'")
    for idx, con in enumerate(getattr(sketch, "Constraints", []) or []):
        if getattr(con, "Name", "") == target_name:
            return idx
    raise ValueError(f"Constraint named '{target_name}' not found in sketch '{sketch.Name}'")


def _build_rectangle_geometry(
    sketch: Any,
    Part: Any,
    Sketcher: Any,
    geo_spec: dict[str, Any],
    construction: bool,
) -> list[int]:
    """Create 4 connected LineSegments forming a rectangle with Coincident/H/V constraints."""
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
    # Close the 4 corners and enforce axis alignment just like FreeCAD's Rectangle tool.
    sketch.addConstraint(Sketcher.Constraint("Coincident", i0, 2, i1, 1))
    sketch.addConstraint(Sketcher.Constraint("Coincident", i1, 2, i2, 1))
    sketch.addConstraint(Sketcher.Constraint("Coincident", i2, 2, i3, 1))
    sketch.addConstraint(Sketcher.Constraint("Coincident", i3, 2, i0, 1))
    sketch.addConstraint(Sketcher.Constraint("Horizontal", i0))
    sketch.addConstraint(Sketcher.Constraint("Horizontal", i2))
    sketch.addConstraint(Sketcher.Constraint("Vertical", i1))
    sketch.addConstraint(Sketcher.Constraint("Vertical", i3))
    return [i0, i1, i2, i3]


def _build_slot_geometry(
    sketch: Any,
    Part: Any,
    Sketcher: Any,
    geo_spec: dict[str, Any],
    construction: bool,
) -> list[int]:
    """Create a stadium slot along [start -> end] with radius (or width / 2)."""
    p1 = _to_vector(geo_spec["start"])
    p2 = _to_vector(geo_spec["end"])
    radius = float(geo_spec["radius"]) if "radius" in geo_spec else float(geo_spec["width"]) / 2.0
    if radius <= 0:
        raise ValueError("Slot radius/width must be positive")
    dx = p2.x - p1.x
    dy = p2.y - p1.y
    seg_len = math.hypot(dx, dy)
    if seg_len < 1e-9:
        raise ValueError("Slot start and end points must be distinct")

    ux, uy = dx / seg_len, dy / seg_len
    nx, ny = -uy * radius, ux * radius  # Left-hand perpendicular offset

    top_start = FreeCAD.Vector(p1.x + nx, p1.y + ny, 0.0)
    top_end = FreeCAD.Vector(p2.x + nx, p2.y + ny, 0.0)
    bot_end = FreeCAD.Vector(p2.x - nx, p2.y - ny, 0.0)
    bot_start = FreeCAD.Vector(p1.x - nx, p1.y - ny, 0.0)

    base_angle = math.atan2(dy, dx)
    arc1 = Part.ArcOfCircle(
        Part.Circle(p1, FreeCAD.Vector(0, 0, 1), radius),
        base_angle + math.pi / 2.0,
        base_angle + 3.0 * math.pi / 2.0,
    )
    arc2 = Part.ArcOfCircle(
        Part.Circle(p2, FreeCAD.Vector(0, 0, 1), radius),
        base_angle - math.pi / 2.0,
        base_angle + math.pi / 2.0,
    )

    i_top = sketch.addGeometry(Part.LineSegment(top_start, top_end), construction)
    i_arc2 = sketch.addGeometry(arc2, construction)
    i_bot = sketch.addGeometry(Part.LineSegment(bot_end, bot_start), construction)
    i_arc1 = sketch.addGeometry(arc1, construction)

    sketch.addConstraint(Sketcher.Constraint("Tangent", i_top, 2, i_arc2, 2))
    sketch.addConstraint(Sketcher.Constraint("Tangent", i_arc2, 1, i_bot, 1))
    sketch.addConstraint(Sketcher.Constraint("Tangent", i_bot, 2, i_arc1, 2))
    sketch.addConstraint(Sketcher.Constraint("Tangent", i_arc1, 1, i_top, 1))
    sketch.addConstraint(Sketcher.Constraint("Equal", i_arc1, i_arc2))
    return [i_top, i_arc2, i_bot, i_arc1]


def _build_polygon_geometry(
    sketch: Any,
    Part: Any,
    Sketcher: Any,
    geo_spec: dict[str, Any],
    construction: bool,
    auto_constrain: bool,
) -> list[int]:
    """Create an N-sided regular polygon centered at `center`."""
    sides = int(geo_spec.get("sides", 6))
    if sides < 3:
        raise ValueError("Polygon must have at least 3 sides")
    center = _to_vector(geo_spec.get("center", [0.0, 0.0]))
    radius = float(geo_spec["radius"])
    if geo_spec.get("inscribed", False):
        radius = radius / math.cos(math.pi / sides)
    angle0 = math.radians(float(geo_spec.get("angle_deg", 0.0)))

    pts = [
        FreeCAD.Vector(
            center.x + radius * math.cos(angle0 + 2.0 * math.pi * k / sides),
            center.y + radius * math.sin(angle0 + 2.0 * math.pi * k / sides),
            0.0,
        )
        for k in range(sides)
    ]
    indices: list[int] = []
    for k in range(sides):
        idx = sketch.addGeometry(Part.LineSegment(pts[k], pts[(k + 1) % sides]), construction)
        indices.append(idx)

    if auto_constrain:
        for k in range(sides):
            nxt = (k + 1) % sides
            sketch.addConstraint(Sketcher.Constraint("Coincident", indices[k], 2, indices[nxt], 1))
        for k in range(1, sides):
            sketch.addConstraint(Sketcher.Constraint("Equal", indices[0], indices[k]))
    return indices


def edit_sketch(
    sketch_name: str,
    geometries: list[dict[str, Any]] | None = None,
    constraints: list[dict[str, Any]] | None = None,
    auto_constrain: bool = True,
    update_constraints: list[dict[str, Any]] | None = None,
    delete_constraint_indices: list[int] | None = None,
    delete_geometry_indices: list[int] | None = None,
    external_geometries: list[dict[str, Any]] | None = None,
    toggle_construction: list[int] | None = None,
    fillet_corners: list[dict[str, Any]] | None = None,
    doc_name: str = "",
) -> dict[str, Any]:
    """Modify a Sketch: add/edit/delete geometry and constraints with optional auto-constraining.

    Args:
        sketch_name: Target Sketcher::SketchObject name.
        geometries: List of shapes (`line`, `polyline`, `rectangle`, `circle`, `arc`, `slot`, `polygon`).
        constraints: List of Sketcher constraints to add (`Distance`, `Coincident`, `Horizontal`, etc.).
        auto_constrain: When True (default), automatically adds Coincident constraints between
            matching endpoints and Horizontal/Vertical constraints on axis-aligned lines/polylines.
        update_constraints: List of dicts to modify existing constraints by `index` or `name`
            (`value` in mm or degrees for Angle, `new_name`, `driving`, `active`).
        delete_constraint_indices: Zero-based constraint indices to delete (sorted descending safely).
        delete_geometry_indices: Zero-based geometry indices to delete.
        external_geometries: List of `{"object": str, "subelement": str, "defining": bool?}` for `addExternal`.
        toggle_construction: List of geometry indices to toggle between construction (blue) and normal.
        fillet_corners: List of corner fillet specs `{"geo1", "geo2", "point1", "point2", "radius"}`.
        doc_name: Target document name (empty string uses ActiveDocument).
    """
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
    auto_added: list[dict[str, Any]] = []
    updated_con: list[int] = []

    with undo_transaction(doc, f"Edit {sketch.Name}"):
        # 1. External geometry references (before adding constraints that reference them)
        for ext in external_geometries or []:
            obj_ref = str(ext["object"])
            sub_ref = str(ext["subelement"])
            if "defining" in ext or "intersection" in ext:
                sketch.addExternal(
                    obj_ref,
                    sub_ref,
                    bool(ext.get("defining", False)),
                    bool(ext.get("intersection", False)),
                )
            else:
                sketch.addExternal(obj_ref, sub_ref)

        # 2. Delete requested geometry/constraints before adding new ones so indices stay predictable.
        if delete_constraint_indices:
            sorted_cons = sorted({int(i) for i in delete_constraint_indices}, reverse=True)
            if hasattr(sketch, "delConstraints"):
                try:
                    sketch.delConstraints(sorted_cons, True)
                except TypeError:
                    sketch.delConstraints(sorted_cons)
            else:
                for idx in sorted_cons:
                    sketch.delConstraint(idx)

        if delete_geometry_indices:
            sorted_geos = sorted({int(i) for i in delete_geometry_indices}, reverse=True)
            sketch.delGeometries(sorted_geos)

        # Snapshot pre-existing endpoints so new lines can auto-join to existing geometry.
        existing_endpoints = _snapshot_sketch_endpoints(sketch) if auto_constrain else []
        new_endpoints: list[tuple[int, int, tuple[float, float]]] = []

        # 3. Add geometries
        for geo_spec in geometries or []:
            kind = str(geo_spec.get("type", "")).lower()
            construction = bool(geo_spec.get("construction", False))

            if kind == "line":
                p1 = _to_vector(geo_spec["start"])
                p2 = _to_vector(geo_spec["end"])
                idx = sketch.addGeometry(Part.LineSegment(p1, p2), construction)
                added_geo.append(idx)
                if auto_constrain:
                    new_endpoints.append((idx, 1, _point_2d_key(p1)))
                    new_endpoints.append((idx, 2, _point_2d_key(p2)))
                    dx, dy = abs(p2.x - p1.x), abs(p2.y - p1.y)
                    if dy <= _SKETCH_AUTO_TOL and dx > _SKETCH_AUTO_TOL:
                        c_idx = sketch.addConstraint(Sketcher.Constraint("Horizontal", idx))
                        auto_added.append({"index": c_idx, "type": "Horizontal", "first": idx})
                    elif dx <= _SKETCH_AUTO_TOL and dy > _SKETCH_AUTO_TOL:
                        c_idx = sketch.addConstraint(Sketcher.Constraint("Vertical", idx))
                        auto_added.append({"index": c_idx, "type": "Vertical", "first": idx})

            elif kind == "polyline":
                raw_pts = geo_spec.get("points") or []
                if len(raw_pts) < 2:
                    raise ValueError("Polyline requires at least 2 points")
                pts = [_to_vector(p) for p in raw_pts]
                closed = bool(geo_spec.get("closed", False))
                seg_pairs = [(pts[k], pts[k + 1]) for k in range(len(pts) - 1)]
                if closed and len(pts) >= 3:
                    seg_pairs.append((pts[-1], pts[0]))

                seg_indices: list[int] = []
                for p1, p2 in seg_pairs:
                    idx = sketch.addGeometry(Part.LineSegment(p1, p2), construction)
                    added_geo.append(idx)
                    seg_indices.append(idx)
                    if auto_constrain:
                        new_endpoints.append((idx, 1, _point_2d_key(p1)))
                        new_endpoints.append((idx, 2, _point_2d_key(p2)))
                        dx, dy = abs(p2.x - p1.x), abs(p2.y - p1.y)
                        if dy <= _SKETCH_AUTO_TOL and dx > _SKETCH_AUTO_TOL:
                            c_idx = sketch.addConstraint(Sketcher.Constraint("Horizontal", idx))
                            auto_added.append({"index": c_idx, "type": "Horizontal", "first": idx})
                        elif dx <= _SKETCH_AUTO_TOL and dy > _SKETCH_AUTO_TOL:
                            c_idx = sketch.addConstraint(Sketcher.Constraint("Vertical", idx))
                            auto_added.append({"index": c_idx, "type": "Vertical", "first": idx})
                    else:
                        # Even when auto_constrain=False, a polyline's sequential vertices stay joined.
                        if len(seg_indices) > 1:
                            prev_idx = seg_indices[-2]
                            sketch.addConstraint(Sketcher.Constraint("Coincident", prev_idx, 2, idx, 1))
                if not auto_constrain and closed and len(seg_indices) >= 3:
                    sketch.addConstraint(Sketcher.Constraint("Coincident", seg_indices[-1], 2, seg_indices[0], 1))

            elif kind == "circle":
                center = _to_vector(geo_spec["center"])
                radius = float(geo_spec["radius"])
                circle = Part.Circle(center, FreeCAD.Vector(0, 0, 1), radius)
                idx = sketch.addGeometry(circle, construction)
                added_geo.append(idx)

            elif kind == "rectangle":
                added_geo.extend(_build_rectangle_geometry(sketch, Part, Sketcher, geo_spec, construction))

            elif kind == "arc":
                center = _to_vector(geo_spec["center"])
                radius = float(geo_spec["radius"])
                start_rad = math.radians(float(geo_spec["start_angle_deg"]))
                end_rad = math.radians(float(geo_spec["end_angle_deg"]))
                circle = Part.Circle(center, FreeCAD.Vector(0, 0, 1), radius)
                arc = Part.ArcOfCircle(circle, start_rad, end_rad)
                idx = sketch.addGeometry(arc, construction)
                added_geo.append(idx)
                if auto_constrain and hasattr(arc, "StartPoint") and hasattr(arc, "EndPoint"):
                    new_endpoints.append((idx, 1, _point_2d_key(arc.StartPoint)))
                    new_endpoints.append((idx, 2, _point_2d_key(arc.EndPoint)))

            elif kind == "slot":
                added_geo.extend(_build_slot_geometry(sketch, Part, Sketcher, geo_spec, construction))

            elif kind == "polygon":
                added_geo.extend(
                    _build_polygon_geometry(sketch, Part, Sketcher, geo_spec, construction, auto_constrain)
                )

            else:
                raise ValueError(f"Unsupported sketch geometry type: '{kind}'")

        # Auto-join touching endpoints for newly drawn lines/polylines/arcs.
        if auto_constrain and new_endpoints:
            auto_added.extend(
                _apply_auto_coincident(sketch, Sketcher, existing_endpoints, new_endpoints)
            )

        # 4. Toggle construction mode on requested geometry indices
        for idx in toggle_construction or []:
            sketch.toggleConstruction(int(idx))

        # 5. Corner fillets between sketch lines
        for fillet_spec in fillet_corners or []:
            g1 = int(fillet_spec["geo1"])
            g2 = int(fillet_spec["geo2"])
            pt1 = _to_vector(fillet_spec["point1"])
            pt2 = _to_vector(fillet_spec["point2"])
            radius = float(fillet_spec["radius"])
            sketch.fillet(g1, g2, pt1, pt2, radius)

        # 6. Add explicit new constraints
        for con_spec in constraints or []:
            ctype = con_spec["type"]
            args: list[Any] = [ctype]
            for key in ("first", "first_pos", "second", "second_pos", "third", "third_pos"):
                if key in con_spec and con_spec[key] is not None:
                    args.append(con_spec[key])
            if "value" in con_spec and con_spec["value"] is not None:
                val = float(con_spec["value"])
                if ctype == "Angle" and not con_spec.get("is_radians", False):
                    val = math.radians(val)
                args.append(val)
            idx = sketch.addConstraint(Sketcher.Constraint(*args))
            if con_spec.get("name") and hasattr(sketch, "renameConstraint"):
                sketch.renameConstraint(idx, str(con_spec["name"]))
            added_con.append(idx)

        # 7. Modify existing constraints (dimension datum, name, driving/reference, active)
        for upd in update_constraints or []:
            c_idx = _resolve_constraint_index(sketch, upd)
            updated_con.append(c_idx)
            con_obj = sketch.Constraints[c_idx]
            if upd.get("value") is not None:
                val = float(upd["value"])
                if getattr(con_obj, "Type", "") == "Angle" and not upd.get("is_radians", False):
                    val = math.radians(val)
                sketch.setDatum(c_idx, val)
            if upd.get("driving") is not None:
                sketch.setDriving(c_idx, bool(upd["driving"]))
            if upd.get("active") is not None:
                sketch.setActive(c_idx, bool(upd["active"]))
            if upd.get("new_name"):
                sketch.renameConstraint(c_idx, str(upd["new_name"]))

    dof = getattr(sketch, "DoF", None)
    fully_constrained = getattr(sketch, "FullyConstrained", None)
    if fully_constrained is None and isinstance(dof, int):
        fully_constrained = dof == 0

    return {
        "document": doc.Name,
        "sketch": sketch.Name,
        "added_geometry_indices": added_geo,
        "added_constraint_indices": added_con,
        "auto_constraints_added": auto_added,
        "updated_constraint_indices": updated_con,
        "degrees_of_freedom": dof,
        "fully_constrained": fully_constrained,
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
