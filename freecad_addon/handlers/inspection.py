"""Object property and topological shape inspection handlers."""

import math
from typing import Any

import FreeCAD

from handlers.common import (
    check_tnp_attachment,
    resolve_doc,
    safe_error_description,
    safe_is_valid,
    sanitize_json,
    vec_to_list,
)
from handlers.error_kb import explain_freecad_error


def inspect_object(name: str, doc_name: str = "") -> dict[str, Any]:
    """Full property dump, validity, dependency info, and shape summary for one object."""
    doc = resolve_doc(doc_name)
    obj = doc.getObject(name)
    if obj is None:
        raise ValueError(f"Object not found: {name}")

    properties: dict[str, Any] = {}
    for prop_name in obj.PropertiesList:
        try:
            val = getattr(obj, prop_name)
            properties[prop_name] = {
                "value": sanitize_json(val),
                "type": obj.getTypeIdOfProperty(prop_name),
                "group": obj.getGroupOfProperty(prop_name),
                "doc": obj.getDocumentationOfProperty(prop_name),
            }
        except Exception as exc:
            properties[prop_name] = {"value": f"<error: {exc}>", "type": "unknown"}

    valid = safe_is_valid(obj)
    result: dict[str, Any] = {
        "name": obj.Name,
        "label": obj.Label,
        "type_id": obj.TypeId,
        "state": "valid" if valid is True else ("invalid" if valid is False else "unknown"),
        "error_description": safe_error_description(obj),
        "tnp_warning": check_tnp_attachment(obj),
        "dependencies": [d.Name for d in obj.InList],
        "sources": [s.Name for s in obj.OutList],
        "properties": properties,
    }
    try:
        result["touch_state"] = "touched" if obj.isTouched() else "clean"
    except AttributeError:
        result["touch_state"] = "unknown"

    if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
        result["shape"] = _basic_shape_info(obj.Shape)

    return result


def _edge_endpoints_and_direction(edge: Any) -> dict[str, Any]:
    """Extract start, end, midpoint, and normalized unit direction for an edge."""
    info: dict[str, Any] = {}
    verts = getattr(edge, "Vertexes", None) or []
    if len(verts) >= 2:
        p1 = vec_to_list(verts[0].Point)
        p2 = vec_to_list(verts[-1].Point)
        info["start"] = p1
        info["end"] = p2
        dx, dy, dz = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
        chord = math.sqrt(dx * dx + dy * dy + dz * dz)
        if chord > 1e-9:
            info["direction"] = [round(dx / chord, 6), round(dy / chord, 6), round(dz / chord, 6)]
    if hasattr(edge, "CenterOfMass"):
        try:
            info["midpoint"] = vec_to_list(edge.CenterOfMass)
        except Exception:
            pass
    elif "start" in info and "end" in info:
        p1, p2 = info["start"], info["end"]
        info["midpoint"] = [(p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0, (p1[2] + p2[2]) / 2.0]
    return info


def _build_face_info(i: int, face: Any) -> dict[str, Any]:
    """Serialize one face's surface geometry metadata."""
    face_info: dict[str, Any] = {
        "index": i + 1,
        "name": f"Face{i + 1}",
        "area": face.Area,
        "center_of_mass": vec_to_list(face.CenterOfMass),
    }
    surface = face.Surface
    surface_type = type(surface).__name__
    face_info["surface_type"] = surface_type

    if surface_type == "Plane":
        face_info["normal"] = vec_to_list(surface.Axis)
        face_info["position"] = vec_to_list(surface.Position)
    elif surface_type == "Cylinder":
        face_info["axis"] = vec_to_list(surface.Axis)
        face_info["center"] = vec_to_list(surface.Center)
        face_info["radius"] = surface.Radius
    elif surface_type == "Cone":
        face_info["axis"] = vec_to_list(surface.Axis)
        face_info["apex"] = vec_to_list(surface.Apex)
        face_info["semi_angle"] = surface.SemiAngle
    elif surface_type == "Sphere":
        face_info["center"] = vec_to_list(surface.Center)
        face_info["radius"] = surface.Radius
    elif surface_type == "Toroid":
        face_info["axis"] = vec_to_list(surface.Axis)
        face_info["center"] = vec_to_list(surface.Center)
        face_info["major_radius"] = surface.MajorRadius
        face_info["minor_radius"] = surface.MinorRadius
    return face_info


def _build_edge_info(i: int, edge: Any) -> dict[str, Any]:
    """Serialize one edge's length, curve type, endpoints, and unit direction."""
    edge_info: dict[str, Any] = {
        "index": i + 1,
        "name": f"Edge{i + 1}",
        "length": edge.Length,
        "curve_type": type(edge.Curve).__name__,
    }
    edge_info.update(_edge_endpoints_and_direction(edge))
    return edge_info


def _parse_axis_spec(spec: str | list[float] | tuple[float, ...]) -> tuple[list[float], bool] | None:
    """Parse an axis/direction string ('X', '+Z', '-Z') or 3-vector.

    Returns (unit_vec, signed) where signed=False for bare 'X'/'Y'/'Z' (matches +/- axis)
    and signed=True when an explicit '+'/'-' sign or 3-vector is given.
    """
    if spec is None or spec == "":
        return None
    if isinstance(spec, (list, tuple)):
        if len(spec) != 3:
            raise ValueError("Direction/normal vector must have 3 numeric components [x, y, z].")
        vx, vy, vz = float(spec[0]), float(spec[1]), float(spec[2])
        mag = math.sqrt(vx * vx + vy * vy + vz * vz)
        if mag < 1e-9:
            raise ValueError("Direction/normal vector cannot be zero length.")
        return ([vx / mag, vy / mag, vz / mag], True)

    text = str(spec).strip().upper()
    axis_table = {
        "X": ([1.0, 0.0, 0.0], False),
        "+X": ([1.0, 0.0, 0.0], True),
        "-X": ([-1.0, 0.0, 0.0], True),
        "Y": ([0.0, 1.0, 0.0], False),
        "+Y": ([0.0, 1.0, 0.0], True),
        "-Y": ([0.0, -1.0, 0.0], True),
        "Z": ([0.0, 0.0, 1.0], False),
        "+Z": ([0.0, 0.0, 1.0], True),
        "-Z": ([0.0, 0.0, -1.0], True),
    }
    if text not in axis_table:
        raise ValueError(f"Unsupported axis/direction '{spec}'. Use X, Y, Z, +Z, -Z, or [x, y, z].")
    return axis_table[text]


def _direction_matches(actual: list[float] | None, target: tuple[list[float], bool], tol: float = 1e-3) -> bool:
    if actual is None:
        return False
    tvec, signed = target
    dot = actual[0] * tvec[0] + actual[1] * tvec[1] + actual[2] * tvec[2]
    if signed:
        return dot >= (1.0 - tol)
    return abs(dot) >= (1.0 - tol)


def _select_subelements(obj: Any, sub_names: list[str]) -> bool:
    """Highlight matching sub-elements in FreeCAD's 3D viewport when GUI is available."""
    if not sub_names or not getattr(FreeCAD, "GuiUp", False):
        return False
    try:
        import FreeCADGui as Gui

        Gui.Selection.clearSelection()
        doc_name = obj.Document.Name if getattr(obj, "Document", None) is not None else ""
        for sub in sub_names:
            Gui.Selection.addSelection(doc_name, obj.Name, sub)
        return True
    except Exception:
        return False


def analyze_shape(name: str, deep_check: bool = False, doc_name: str = "") -> dict[str, Any]:
    """Detailed topological analysis of an object's Shape, with optional OCC BOPCheck."""
    doc = resolve_doc(doc_name)
    obj = doc.getObject(name)
    if obj is None:
        raise ValueError(f"Object not found: {name}")
    if not hasattr(obj, "Shape") or obj.Shape is None or obj.Shape.isNull():
        raise ValueError(f"Object '{name}' has no shape")

    shape = obj.Shape
    result = _basic_shape_info(shape)
    result["faces"] = [_build_face_info(i, face) for i, face in enumerate(shape.Faces)]
    result["edges"] = [_build_edge_info(i, edge) for i, edge in enumerate(shape.Edges)]

    if deep_check:
        if hasattr(shape, "check"):
            try:
                shape.check(True)
                result["bop_check_passed"] = True
                result["bop_check_error"] = None
            except Exception as exc:
                msg = str(exc)
                result["bop_check_passed"] = False
                result["bop_check_error"] = msg
                result["error_explanation"] = explain_freecad_error(msg)
        else:
            result["bop_check_passed"] = None
            result["bop_check_error"] = "Shape.check() unavailable in this environment."

    return result


def find_edges(
    object_name: str,
    direction: str | list[float] = "",
    length: float | None = None,
    min_length: float | None = None,
    max_length: float | None = None,
    curve_type: str = "",
    near_point: list[float] | None = None,
    max_distance: float | None = None,
    select_matches: bool = False,
    doc_name: str = "",
    tolerance: float = 1e-3,
) -> dict[str, Any]:
    """Find edges on an object's Shape matching direction, length, curve type, or proximity."""
    doc = resolve_doc(doc_name)
    obj = doc.getObject(object_name)
    if obj is None:
        raise ValueError(f"Object not found: {object_name}")
    if not hasattr(obj, "Shape") or obj.Shape is None or obj.Shape.isNull():
        raise ValueError(f"Object '{object_name}' has no shape")

    axis_target = _parse_axis_spec(direction)
    ctype_lower = (curve_type or "").strip().lower()
    matches: list[dict[str, Any]] = []

    for i, edge in enumerate(obj.Shape.Edges):
        info = _build_edge_info(i, edge)
        if axis_target is not None:
            # Restrict direction filter to straight lines unless tangent/chord is aligned.
            if "line" not in info["curve_type"].lower():
                continue
            if not _direction_matches(info.get("direction"), axis_target, tol=tolerance):
                continue
        if length is not None and abs(info["length"] - float(length)) > max(tolerance, 1e-3):
            continue
        if min_length is not None and info["length"] < float(min_length) - tolerance:
            continue
        if max_length is not None and info["length"] > float(max_length) + tolerance:
            continue
        if ctype_lower and ctype_lower not in info["curve_type"].lower():
            continue
        if near_point is not None:
            mid = info.get("midpoint")
            if mid is None:
                continue
            dist = math.sqrt(
                (mid[0] - float(near_point[0])) ** 2
                + (mid[1] - float(near_point[1])) ** 2
                + (mid[2] - float(near_point[2])) ** 2
            )
            info["distance_to_point"] = round(dist, 6)
            if max_distance is not None and dist > float(max_distance) + tolerance:
                continue
        matches.append(info)

    if near_point is not None and max_distance is None:
        matches.sort(key=lambda m: m.get("distance_to_point", float("inf")))

    edge_names = [m["name"] for m in matches]
    selected = _select_subelements(obj, edge_names) if select_matches else False

    return {
        "object": obj.Name,
        "count": len(matches),
        "edge_names": edge_names,
        "selected_in_gui": selected,
        "edges": matches,
    }


def find_faces(
    object_name: str,
    normal: str | list[float] = "",
    area: float | None = None,
    min_area: float | None = None,
    max_area: float | None = None,
    surface_type: str = "",
    near_point: list[float] | None = None,
    max_distance: float | None = None,
    select_matches: bool = False,
    doc_name: str = "",
    tolerance: float = 1e-3,
) -> dict[str, Any]:
    """Find faces on an object's Shape matching normal direction, area, surface type, or proximity."""
    doc = resolve_doc(doc_name)
    obj = doc.getObject(object_name)
    if obj is None:
        raise ValueError(f"Object not found: {object_name}")
    if not hasattr(obj, "Shape") or obj.Shape is None or obj.Shape.isNull():
        raise ValueError(f"Object '{object_name}' has no shape")

    normal_target = _parse_axis_spec(normal)
    stype_lower = (surface_type or "").strip().lower()
    matches: list[dict[str, Any]] = []

    for i, face in enumerate(obj.Shape.Faces):
        info = _build_face_info(i, face)
        if normal_target is not None:
            if not _direction_matches(info.get("normal"), normal_target, tol=tolerance):
                continue
        if area is not None and abs(info["area"] - float(area)) > max(tolerance, 1e-3):
            continue
        if min_area is not None and info["area"] < float(min_area) - tolerance:
            continue
        if max_area is not None and info["area"] > float(max_area) + tolerance:
            continue
        if stype_lower and stype_lower not in info["surface_type"].lower():
            continue
        if near_point is not None:
            com = info["center_of_mass"]
            dist = math.sqrt(
                (com[0] - float(near_point[0])) ** 2
                + (com[1] - float(near_point[1])) ** 2
                + (com[2] - float(near_point[2])) ** 2
            )
            info["distance_to_point"] = round(dist, 6)
            if max_distance is not None and dist > float(max_distance) + tolerance:
                continue
        matches.append(info)

    if near_point is not None and max_distance is None:
        matches.sort(key=lambda m: m.get("distance_to_point", float("inf")))

    face_names = [m["name"] for m in matches]
    selected = _select_subelements(obj, face_names) if select_matches else False

    return {
        "object": obj.Name,
        "count": len(matches),
        "face_names": face_names,
        "selected_in_gui": selected,
        "faces": matches,
    }


def measure_distance(
    object1: str,
    subelement1: str = "",
    object2: str = "",
    subelement2: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Measure the exact minimum distance between two objects or sub-elements (Face/Edge/Vertex)."""
    doc = resolve_doc(doc_name)
    obj1 = doc.getObject(object1)
    if obj1 is None or not hasattr(obj1, "Shape") or obj1.Shape is None or obj1.Shape.isNull():
        raise ValueError(f"First object '{object1}' not found or has no shape")

    target2_name = object2 or object1
    obj2 = doc.getObject(target2_name)
    if obj2 is None or not hasattr(obj2, "Shape") or obj2.Shape is None or obj2.Shape.isNull():
        raise ValueError(f"Second object '{target2_name}' not found or has no shape")

    shape1 = obj1.Shape.getElement(subelement1) if subelement1 else obj1.Shape
    shape2 = obj2.Shape.getElement(subelement2) if subelement2 else obj2.Shape

    min_dist, point_pairs, _support_info = shape1.distToShape(shape2)
    p1 = vec_to_list(point_pairs[0][0]) if point_pairs else None
    p2 = vec_to_list(point_pairs[0][1]) if point_pairs else None
    delta = [round(p2[k] - p1[k], 6) for k in range(3)] if (p1 and p2) else None

    return {
        "min_distance": float(min_dist),
        "point1": p1,
        "point2": p2,
        "delta": delta,
        "from": f"{obj1.Name}.{subelement1}" if subelement1 else obj1.Name,
        "to": f"{obj2.Name}.{subelement2}" if subelement2 else obj2.Name,
    }


def _basic_shape_info(shape: Any) -> dict[str, Any]:
    info: dict[str, Any] = {
        "shape_type": shape.ShapeType,
        "is_valid": shape.isValid(),
        "is_null": shape.isNull(),
        "solids": len(shape.Solids),
        "shells": len(shape.Shells),
        "faces_count": len(shape.Faces),
        "edges_count": len(shape.Edges),
        "vertices_count": len(shape.Vertexes),
    }
    try:
        info["volume"] = shape.Volume
    except Exception:
        info["volume"] = None
    try:
        info["area"] = shape.Area
    except Exception:
        info["area"] = None
    try:
        bb = shape.BoundBox
        info["bounding_box"] = {
            "x_min": bb.XMin,
            "x_max": bb.XMax,
            "y_min": bb.YMin,
            "y_max": bb.YMax,
            "z_min": bb.ZMin,
            "z_max": bb.ZMax,
            "size": [bb.XLength, bb.YLength, bb.ZLength],
        }
    except Exception:
        info["bounding_box"] = None
    return info
