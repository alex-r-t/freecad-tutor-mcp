"""Sketcher constraint and geometry diagnostics handler."""

from typing import Any

from handlers.common import check_tnp_attachment, resolve_doc, vec_to_list


def get_sketch_diagnostics(name: str, doc_name: str = "") -> dict[str, Any]:
    """Deep inspection of a sketch's constraint health.

    Reads `sketch.DoF` (avoiding the common `sketch.solve()` status-code pitfall),
    checks conflicting/redundant constraints, enumerates geometry using
    `sketch.Geometry`, and attaches beginner-friendly `tutor_hints`.
    """
    doc = resolve_doc(doc_name)
    sketch = doc.getObject(name)
    if sketch is None:
        raise ValueError(f"Object not found: {name}")
    if not hasattr(sketch, "ConstraintCount"):
        raise ValueError(f"Object '{name}' is not a sketch (TypeId: {sketch.TypeId})")

    redundant: list[int] = []
    conflicting: list[int] = []
    malformed: list[int] = []
    partially_redundant: list[int] = []
    try:
        redundant = list(sketch.getRedundantConstraints())
    except Exception:
        pass
    try:
        conflicting = list(sketch.getConflictingConstraints())
    except Exception:
        pass
    try:
        malformed = list(getattr(sketch, "MalformedConstraints", []))
    except Exception:
        pass
    try:
        partially_redundant = list(getattr(sketch, "PartiallyRedundantConstraints", []))
    except Exception:
        pass

    # Built-in C++ Sketcher validation checks (OpenVertices & missing constraints).
    open_vertices: list[list[float]] = []
    for pt in getattr(sketch, "OpenVertices", []) or []:
        try:
            open_vertices.append(vec_to_list(pt))
        except Exception:
            pass

    missing_coincident_count = 0
    missing_hv_count = 0
    missing_equality_count = 0
    try:
        if hasattr(sketch, "detectMissingPointOnPointConstraints"):
            missing_coincident_count = int(sketch.detectMissingPointOnPointConstraints())
    except Exception:
        pass
    try:
        if hasattr(sketch, "detectMissingVerticalHorizontalConstraints"):
            missing_hv_count = int(sketch.detectMissingVerticalHorizontalConstraints())
    except Exception:
        pass
    try:
        if hasattr(sketch, "detectMissingEqualityConstraints"):
            missing_equality_count = int(sketch.detectMissingEqualityConstraints())
    except Exception:
        pass

    # In FreeCAD, sketch.DoF reports remaining degrees of freedom, whereas
    # sketch.solve() returns a solver status code (0 = solved).
    dof = getattr(sketch, "DoF", None)
    fully_constrained = getattr(sketch, "FullyConstrained", None)
    state_flags = list(sketch.State) if hasattr(sketch, "State") else []

    constraints: list[dict[str, Any]] = []
    for i, c in enumerate(getattr(sketch, "Constraints", [])):
        info: dict[str, Any] = {
            "index": i,
            "type": c.Type,
            "name": c.Name if getattr(c, "Name", "") else None,
            "first": c.First,
            "first_pos": c.FirstPos,
            "second": c.Second,
            "second_pos": c.SecondPos,
            "third": c.Third,
            "third_pos": c.ThirdPos,
            "is_driving": getattr(c, "Driving", True),
            "is_in_virtual_space": getattr(c, "InVirtualSpace", False),
            "is_active": getattr(c, "IsActive", True),
            "is_redundant": i in redundant,
            "is_conflicting": i in conflicting,
        }
        if hasattr(c, "Value"):
            info["value"] = c.Value
        constraints.append(info)

    geometries: list[dict[str, Any]] = []
    try:
        geo_list = sketch.Geometry
    except AttributeError:
        geo_list = []

    for i, geo in enumerate(geo_list):
        try:
            geo_type = type(geo).__name__
            geo_info: dict[str, Any] = {
                "index": i,
                "type": geo_type,
                "construction": getattr(geo, "Construction", False),
            }
            if hasattr(geo, "StartPoint") and hasattr(geo, "EndPoint"):
                geo_info["start"] = vec_to_list(geo.StartPoint)
                geo_info["end"] = vec_to_list(geo.EndPoint)
            if hasattr(geo, "Center"):
                geo_info["center"] = vec_to_list(geo.Center)
            if hasattr(geo, "Radius"):
                geo_info["radius"] = geo.Radius
            if hasattr(geo, "FirstParameter") and hasattr(geo, "LastParameter"):
                geo_info["first_param"] = geo.FirstParameter
                geo_info["last_param"] = geo.LastParameter
            if geo_type == "Point" and hasattr(geo, "X"):
                geo_info["position"] = [geo.X, geo.Y, geo.Z]
            geometries.append(geo_info)
        except Exception as exc:
            geometries.append({"index": i, "error": str(exc)})

    external_count = 0
    try:
        external_count = sketch.ExternalGeometryCount
    except Exception:
        pass

    tutor_hints = _build_tutor_hints(
        sketch=sketch,
        dof=dof,
        fully_constrained=fully_constrained,
        redundant=redundant,
        conflicting=conflicting,
        open_vertices=open_vertices,
        missing_coincident_count=missing_coincident_count,
        missing_hv_count=missing_hv_count,
        geometries=geometries,
    )

    return {
        "name": sketch.Name,
        "label": sketch.Label,
        "state": state_flags,
        "constraint_count": sketch.ConstraintCount,
        "geometry_count": sketch.GeometryCount,
        "external_geometry_count": external_count,
        "degrees_of_freedom": dof,
        "fully_constrained": fully_constrained,
        "redundant_indices": redundant,
        "conflicting_indices": conflicting,
        "malformed_indices": malformed,
        "partially_redundant_indices": partially_redundant,
        "open_vertex_count": len(open_vertices),
        "open_vertices": open_vertices,
        "missing_coincident_count": missing_coincident_count,
        "missing_hv_count": missing_hv_count,
        "missing_equality_count": missing_equality_count,
        "constraints": constraints,
        "geometries": geometries,
        "tutor_hints": tutor_hints,
    }


def _build_tutor_hints(
    *,
    sketch: Any,
    dof: Any,
    fully_constrained: Any,
    redundant: list[int],
    conflicting: list[int],
    open_vertices: list[list[float]],
    missing_coincident_count: int,
    missing_hv_count: int,
    geometries: list[dict[str, Any]],
) -> list[str]:
    hints: list[str] = []
    if open_vertices:
        hints.append(
            f"Found {len(open_vertices)} open vertex/vertices (e.g., near {open_vertices[0]}): "
            "Pad/Pocket/Revolve require a closed loop—add Coincident constraints at unconnected endpoints."
        )
    if missing_coincident_count:
        hints.append(
            f"Detected {missing_coincident_count} endpoint pair(s) touching without a Coincident constraint; "
            "run Sketcher -> Validate sketch or constrain the corners together."
        )
    if missing_hv_count:
        hints.append(
            f"Detected {missing_hv_count} nearly horizontal/vertical line(s) missing Horizontal/Vertical constraints."
        )
    if conflicting:
        hints.append(
            f"Conflicting constraints at indices {conflicting}: delete or convert one to a reference (blue) constraint."
        )
    if redundant:
        hints.append(
            f"Redundant constraints at indices {redundant}: removing redundant constraints resolves solver warnings."
        )
    if isinstance(dof, int) and dof > 0:
        hints.append(
            f"Sketch has {dof} remaining Degree(s) of Freedom (DOF). Try dragging unconstrained points/lines or adding geometric/dimensional constraints until DOF = 0."
        )
    elif fully_constrained:
        hints.append("Sketch is fully constrained (DOF = 0) and ready for Pad/Pocket/Revolve.")

    if not geometries:
        hints.append("Sketch currently contains no geometry elements.")

    tnp = check_tnp_attachment(sketch)
    if tnp:
        hints.append(tnp)

    return hints
