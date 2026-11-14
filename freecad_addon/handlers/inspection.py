"""Object property and topological shape inspection handlers."""

from typing import Any

from handlers.common import (
    check_tnp_attachment,
    resolve_doc,
    safe_error_description,
    safe_is_valid,
    sanitize_json,
    vec_to_list,
)


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


def analyze_shape(name: str, doc_name: str = "") -> dict[str, Any]:
    """Detailed topological analysis of an object's Shape."""
    doc = resolve_doc(doc_name)
    obj = doc.getObject(name)
    if obj is None:
        raise ValueError(f"Object not found: {name}")
    if not hasattr(obj, "Shape") or obj.Shape is None or obj.Shape.isNull():
        raise ValueError(f"Object '{name}' has no shape")

    shape = obj.Shape
    result = _basic_shape_info(shape)

    result["faces"] = []
    for i, face in enumerate(shape.Faces):
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

        result["faces"].append(face_info)

    result["edges"] = [
        {
            "index": i + 1,
            "name": f"Edge{i + 1}",
            "length": edge.Length,
            "curve_type": type(edge.Curve).__name__,
        }
        for i, edge in enumerate(shape.Edges)
    ]
    return result


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
