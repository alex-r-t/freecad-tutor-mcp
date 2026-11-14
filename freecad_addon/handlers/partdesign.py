"""1-to-1 PartDesign feature, datum, parametric VarSet, property, and appearance handlers.

Each tool maps directly to a FreeCAD workbench command or Data-tab property action
so the AI tutor can teach the exact workflow the student sees in the FreeCAD UI.
All mutations run inside FreeCAD ``undo_transaction`` blocks and roll back
cleanly if post-recompute validation fails.
"""

from typing import Any

import FreeCAD

from handlers.common import (
    find_parent_body,
    resolve_doc,
    resolve_origin_feature,
    set_attachment_support,
    undo_transaction,
    verify_object_valid,
)
from json_utils import sanitize_json

_VARSET_TYPE_MAP = {
    "length": "App::PropertyLength",
    "distance": "App::PropertyDistance",
    "float": "App::PropertyFloat",
    "int": "App::PropertyInteger",
    "integer": "App::PropertyInteger",
    "angle": "App::PropertyAngle",
    "string": "App::PropertyString",
    "bool": "App::PropertyBool",
}


def _resolve_object_and_body(doc: Any, object_name: str, label: str = "Object") -> tuple[Any, Any]:
    """Look up an object and its owning PartDesign::Body (raising a clear error if missing)."""
    obj = doc.getObject(object_name)
    if obj is None:
        raise ValueError(f"{label} not found: {object_name}")
    body = find_parent_body(doc, obj)
    if body is None:
        raise ValueError(f"{label} '{object_name}' is not inside a PartDesign::Body")
    return obj, body


def _resolve_reference_tuple(
    doc: Any,
    body: Any,
    ref_spec: str,
    *,
    sketch: Any = None,
) -> tuple[Any, list[str]]:
    """Resolve an axis/plane specification into FreeCAD's ``(Object, [SubElement])`` tuple.

    Supports:
    - Sketch axes: ``V_Axis``, ``H_Axis`` (also ``ConstructionLineN`` / ``AxisN``)
    - Body Origin features: ``X_Axis``, ``Y_Axis``, ``Z_Axis``, ``XY_Plane``, ``XZ_Plane``, ``YZ_Plane``
    - Explicit object/subelement: ``"Pad:Edge1"``, ``"Pad.Face2"``, or a Datum object name ``"DatumPlane"``
    """
    spec = (ref_spec or "").strip()
    if not spec:
        raise ValueError("Axis/plane reference cannot be empty")

    if sketch is not None and spec in ("V_Axis", "H_Axis"):
        return (sketch, [spec])

    origin_feat = resolve_origin_feature(body, spec)
    if origin_feat is not None:
        return (origin_feat, [""])

    if ":" in spec or "." in spec:
        sep = ":" if ":" in spec else "."
        obj_name, sub_name = spec.split(sep, 1)
        target = doc.getObject(obj_name.strip())
        if target is None:
            raise ValueError(f"Referenced object not found: {obj_name}")
        return (target, [sub_name.strip()])

    target = doc.getObject(spec)
    if target is not None:
        return (target, [""])

    if sketch is not None:
        return (sketch, [spec])

    raise ValueError(
        f"Could not resolve axis/plane reference '{ref_spec}'. "
        "Use a Body Origin name (e.g. X_Axis, YZ_Plane), sketch axis (V_Axis, H_Axis), "
        "Datum name, or 'ObjectName:Face1'."
    )


def _create_revolved_feature(
    *,
    type_id: str,
    action_label: str,
    default_name: str,
    sketch_name: str,
    angle: float,
    axis: str,
    reversed: bool,
    midplane: bool,
    name: str,
    doc_name: str,
) -> dict[str, Any]:
    """Shared implementation for PartDesign::Revolution (additive) and PartDesign::Groove (subtractive)."""
    import PartDesign  # noqa: F401 - ensures PartDesign types are registered

    doc = resolve_doc(doc_name)
    sketch, body = _resolve_object_and_body(doc, sketch_name, label="Sketch")
    ref_tuple = _resolve_reference_tuple(doc, body, axis or "V_Axis", sketch=sketch)

    with undo_transaction(doc, f"{action_label} {sketch.Name}"):
        feat = body.newObject(type_id, name or default_name)
        feat.Profile = sketch
        feat.ReferenceAxis = ref_tuple
        feat.Angle = float(angle)
        feat.Reversed = bool(reversed)
        if hasattr(feat, "Midplane"):
            feat.Midplane = bool(midplane)
        doc.recompute()
        verify_object_valid(feat, action_label)

    return {
        "document": doc.Name,
        "name": feat.Name,
        "body": body.Name,
        "type_id": type_id,
        "angle": float(angle),
        "axis": axis,
    }


def partdesign_revolution(
    sketch_name: str,
    angle: float = 360.0,
    axis: str = "V_Axis",
    reversed: bool = False,
    midplane: bool = False,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Revolve a Sketch around an axis to add solid material (PartDesign::Revolution)."""
    return _create_revolved_feature(
        type_id="PartDesign::Revolution",
        action_label="Revolution",
        default_name="Revolution",
        sketch_name=sketch_name,
        angle=angle,
        axis=axis,
        reversed=reversed,
        midplane=midplane,
        name=name,
        doc_name=doc_name,
    )


def partdesign_groove(
    sketch_name: str,
    angle: float = 360.0,
    axis: str = "V_Axis",
    reversed: bool = False,
    midplane: bool = False,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Cut material by revolving a Sketch around an axis (PartDesign::Groove)."""
    return _create_revolved_feature(
        type_id="PartDesign::Groove",
        action_label="Groove",
        default_name="Groove",
        sketch_name=sketch_name,
        angle=angle,
        axis=axis,
        reversed=reversed,
        midplane=midplane,
        name=name,
        doc_name=doc_name,
    )


def partdesign_thickness(
    object_name: str,
    faces: list[str],
    thickness: float = 2.0,
    mode: str = "Skin",
    join: str = "Intersection",
    inside: bool = True,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Hollow out a PartDesign solid by opening selected faces and offsetting walls (Thickness)."""
    import PartDesign  # noqa: F401

    if not faces:
        raise ValueError("Provide at least one open face (e.g. ['Face6']) to hollow out.")

    doc = resolve_doc(doc_name)
    base_obj, body = _resolve_object_and_body(doc, object_name, label="Base feature")

    # Normalize Mode and Join casing gracefully against FreeCAD's exact enums.
    mode_map = {"skin": "Skin", "pipe": "Pipe", "rectoverso": "RectoVerso"}
    join_map = {"arc": "Arc", "intersection": "Intersection"}
    mode_val = mode_map.get(mode.strip().lower(), mode)
    join_val = join_map.get(join.strip().lower(), join)

    with undo_transaction(doc, f"Thickness {base_obj.Name}"):
        feat = body.newObject("PartDesign::Thickness", name or "Thickness")
        feat.Base = (base_obj, list(faces))
        feat.Value = float(thickness)
        feat.Mode = mode_val
        feat.Join = join_val
        if hasattr(feat, "Intersection"):
            feat.Intersection = True
        feat.Reversed = bool(inside)
        doc.recompute()
        verify_object_valid(feat, "Thickness")

    return {
        "document": doc.Name,
        "name": feat.Name,
        "body": body.Name,
        "faces": list(faces),
        "thickness": float(thickness),
        "inside": bool(inside),
    }


def partdesign_hole(
    sketch_name: str,
    diameter: float = 6.0,
    depth: float = 10.0,
    through_all: bool = False,
    reversed: bool = False,
    hole_cut_type: str = "None",
    hole_cut_diameter: float | None = None,
    hole_cut_depth: float | None = None,
    threaded: bool = False,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Drill parametric holes from sketch circles/arcs/points (PartDesign::Hole)."""
    import PartDesign  # noqa: F401

    doc = resolve_doc(doc_name)
    sketch, body = _resolve_object_and_body(doc, sketch_name, label="Sketch")

    with undo_transaction(doc, f"Hole {sketch.Name}"):
        hole = body.newObject("PartDesign::Hole", name or "Hole")
        hole.Profile = sketch
        hole.Diameter = float(diameter)
        if hasattr(hole, "DepthType"):
            hole.DepthType = "ThroughAll" if through_all else "Dimension"
        if not through_all:
            hole.Depth = float(depth)
        if hasattr(hole, "Reversed"):
            hole.Reversed = bool(reversed)
        if hasattr(hole, "HoleCutType") and hole_cut_type:
            hole.HoleCutType = hole_cut_type
        if hole_cut_diameter is not None and hasattr(hole, "HoleCutDiameter"):
            hole.HoleCutDiameter = float(hole_cut_diameter)
        if hole_cut_depth is not None and hasattr(hole, "HoleCutDepth"):
            hole.HoleCutDepth = float(hole_cut_depth)
        if hasattr(hole, "ModelThread"):
            hole.ModelThread = bool(threaded)
        doc.recompute()
        verify_object_valid(hole, "Hole")

    return {
        "document": doc.Name,
        "name": hole.Name,
        "body": body.Name,
        "diameter": float(diameter),
        "through_all": bool(through_all),
    }


def partdesign_loft(
    profile_names: list[str],
    ruled: bool = False,
    closed: bool = False,
    subtractive: bool = False,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Loft smoothly through two or more sketches (AdditiveLoft or SubtractiveLoft)."""
    import PartDesign  # noqa: F401

    if len(profile_names) < 2:
        raise ValueError("PartDesign Loft requires at least 2 profile sketch names.")

    doc = resolve_doc(doc_name)
    profiles = []
    body = None
    for prof_name in profile_names:
        prof, prof_body = _resolve_object_and_body(doc, prof_name, label="Profile sketch")
        if body is None:
            body = prof_body
        elif prof_body is not body:
            raise ValueError(f"All loft profiles must belong to the same Body ('{body.Name}').")
        profiles.append(prof)

    assert body is not None
    type_id = "PartDesign::SubtractiveLoft" if subtractive else "PartDesign::AdditiveLoft"
    default_label = "SubtractiveLoft" if subtractive else "AdditiveLoft"

    with undo_transaction(doc, f"Loft {profiles[0].Name}"):
        loft = body.newObject(type_id, name or default_label)
        loft.Profile = profiles[0]
        loft.Sections = profiles[1:]
        if hasattr(loft, "Ruled"):
            loft.Ruled = bool(ruled)
        if hasattr(loft, "Closed"):
            loft.Closed = bool(closed)
        doc.recompute()
        verify_object_valid(loft, default_label)

    return {
        "document": doc.Name,
        "name": loft.Name,
        "body": body.Name,
        "type_id": type_id,
        "profiles": list(profile_names),
    }


def partdesign_sweep(
    profile_name: str,
    spine_name: str,
    spine_subelements: list[str] | None = None,
    subtractive: bool = False,
    orientation: str = "Standard",
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Sweep a profile sketch along a spine sketch/edge path (AdditivePipe or SubtractivePipe)."""
    import PartDesign  # noqa: F401

    doc = resolve_doc(doc_name)
    profile, body = _resolve_object_and_body(doc, profile_name, label="Profile sketch")
    spine = doc.getObject(spine_name)
    if spine is None:
        raise ValueError(f"Spine object not found: {spine_name}")

    type_id = "PartDesign::SubtractivePipe" if subtractive else "PartDesign::AdditivePipe"
    default_label = "SubtractivePipe" if subtractive else "AdditivePipe"
    subs = list(spine_subelements) if spine_subelements else [""]

    with undo_transaction(doc, f"Sweep {profile.Name}"):
        pipe = body.newObject(type_id, name or default_label)
        pipe.Profile = profile
        pipe.Spine = (spine, subs)
        if hasattr(pipe, "Mode") and orientation:
            pipe.Mode = orientation
        doc.recompute()
        verify_object_valid(pipe, default_label)

    return {
        "document": doc.Name,
        "name": pipe.Name,
        "body": body.Name,
        "type_id": type_id,
        "profile": profile.Name,
        "spine": spine.Name,
    }


def _resolve_originals(doc: Any, feature_names: list[str]) -> tuple[list[Any], Any]:
    if not feature_names:
        raise ValueError("Provide at least one PartDesign feature name in `feature_names`.")
    originals = []
    body = None
    for feat_name in feature_names:
        feat, feat_body = _resolve_object_and_body(doc, feat_name, label="Feature")
        if body is None:
            body = feat_body
        elif feat_body is not body:
            raise ValueError(f"All patterned/mirrored features must share Body '{body.Name}'.")
        originals.append(feat)
    assert body is not None
    return originals, body


def partdesign_mirrored(
    feature_names: list[str],
    mirror_plane: str = "YZ_Plane",
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Mirror one or more PartDesign features across a plane (PartDesign::Mirrored)."""
    import PartDesign  # noqa: F401

    doc = resolve_doc(doc_name)
    originals, body = _resolve_originals(doc, feature_names)
    plane_ref = _resolve_reference_tuple(doc, body, mirror_plane)

    with undo_transaction(doc, f"Mirrored {originals[0].Name}"):
        mir = body.newObject("PartDesign::Mirrored", name or "Mirrored")
        mir.Originals = originals
        mir.MirrorPlane = plane_ref
        doc.recompute()
        verify_object_valid(mir, "Mirrored")

    return {
        "document": doc.Name,
        "name": mir.Name,
        "body": body.Name,
        "originals": [o.Name for o in originals],
        "mirror_plane": mirror_plane,
    }


def partdesign_linear_pattern(
    feature_names: list[str],
    direction: str = "X_Axis",
    length: float = 50.0,
    occurrences: int = 3,
    reversed: bool = False,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Repeat PartDesign feature(s) along a linear direction (PartDesign::LinearPattern)."""
    import PartDesign  # noqa: F401

    if occurrences < 2:
        raise ValueError("LinearPattern requires occurrences >= 2.")

    doc = resolve_doc(doc_name)
    originals, body = _resolve_originals(doc, feature_names)
    dir_ref = _resolve_reference_tuple(doc, body, direction)

    with undo_transaction(doc, f"LinearPattern {originals[0].Name}"):
        lp = body.newObject("PartDesign::LinearPattern", name or "LinearPattern")
        lp.Originals = originals
        lp.Direction = dir_ref
        lp.Length = float(length)
        lp.Occurrences = int(occurrences)
        if hasattr(lp, "Reversed"):
            lp.Reversed = bool(reversed)
        doc.recompute()
        verify_object_valid(lp, "LinearPattern")

    return {
        "document": doc.Name,
        "name": lp.Name,
        "body": body.Name,
        "originals": [o.Name for o in originals],
        "occurrences": int(occurrences),
        "length": float(length),
    }


def partdesign_polar_pattern(
    feature_names: list[str],
    axis: str = "Z_Axis",
    angle: float = 360.0,
    occurrences: int = 4,
    reversed: bool = False,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Repeat PartDesign feature(s) radially around an axis (PartDesign::PolarPattern)."""
    import PartDesign  # noqa: F401

    if occurrences < 2:
        raise ValueError("PolarPattern requires occurrences >= 2.")

    doc = resolve_doc(doc_name)
    originals, body = _resolve_originals(doc, feature_names)
    axis_ref = _resolve_reference_tuple(doc, body, axis)

    with undo_transaction(doc, f"PolarPattern {originals[0].Name}"):
        pp = body.newObject("PartDesign::PolarPattern", name or "PolarPattern")
        pp.Originals = originals
        pp.Axis = axis_ref
        pp.Angle = float(angle)
        pp.Occurrences = int(occurrences)
        if hasattr(pp, "Reversed"):
            pp.Reversed = bool(reversed)
        doc.recompute()
        verify_object_valid(pp, "PolarPattern")

    return {
        "document": doc.Name,
        "name": pp.Name,
        "body": body.Name,
        "originals": [o.Name for o in originals],
        "occurrences": int(occurrences),
        "angle": float(angle),
    }


def partdesign_draft(
    object_name: str,
    faces: list[str],
    angle: float = 5.0,
    neutral_plane: str = "",
    pull_direction: str = "",
    reversed: bool = False,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Apply a taper/draft angle to selected solid faces (PartDesign::Draft)."""
    import PartDesign  # noqa: F401

    if not faces:
        raise ValueError("Provide at least one face (e.g. ['Face1']) to draft.")

    doc = resolve_doc(doc_name)
    base_obj, body = _resolve_object_and_body(doc, object_name, label="Base feature")

    with undo_transaction(doc, f"Draft {base_obj.Name}"):
        draft = body.newObject("PartDesign::Draft", name or "Draft")
        draft.Base = (base_obj, list(faces))
        draft.Angle = float(angle)
        if neutral_plane:
            draft.NeutralPlane = _resolve_reference_tuple(doc, body, neutral_plane)
        if pull_direction:
            draft.PullDirection = _resolve_reference_tuple(doc, body, pull_direction)
        if hasattr(draft, "Reversed"):
            draft.Reversed = bool(reversed)
        doc.recompute()
        verify_object_valid(draft, "Draft")

    return {
        "document": doc.Name,
        "name": draft.Name,
        "body": body.Name,
        "faces": list(faces),
        "angle": float(angle),
    }


# ── Datum Planes, Parametric VarSets, Properties & Appearance ────────────


def create_datum_plane(
    body_name: str,
    attachment_support: str = "XY_Plane",
    subelements: list[str] | None = None,
    map_mode: str = "FlatFace",
    offset: list[float] | None = None,
    rotation_deg: list[float] | None = None,
    name: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Create a PartDesign::Plane Datum Plane inside a Body to avoid face-TNP fragility."""
    import PartDesign  # noqa: F401 - required before instantiating PartDesign::Plane in 1.1+

    doc = resolve_doc(doc_name)
    body = doc.getObject(body_name)
    if body is None:
        raise ValueError(f"Body not found: {body_name}")

    origin_target = resolve_origin_feature(body, attachment_support)
    if origin_target is not None:
        target = origin_target
        sub_str = ""
    else:
        target = doc.getObject(attachment_support)
        if target is None:
            raise ValueError(f"Attachment support object not found: {attachment_support}")
        sub_str = (subelements[0] if subelements else "")

    with undo_transaction(doc, "Create Datum Plane"):
        plane = body.newObject("PartDesign::Plane", name or "DatumPlane")
        set_attachment_support(plane, target, sub_str, map_mode=map_mode)
        if (offset or rotation_deg) and hasattr(plane, "AttachmentOffset"):
            ox, oy, oz = (float(v) for v in (offset or [0.0, 0.0, 0.0]))
            rx, ry, rz = (float(v) for v in (rotation_deg or [0.0, 0.0, 0.0]))
            plane.AttachmentOffset = FreeCAD.Placement(
                FreeCAD.Vector(ox, oy, oz),
                FreeCAD.Rotation(rz, ry, rx),
            )
        doc.recompute()
        verify_object_valid(plane, "DatumPlane")

    return {
        "document": doc.Name,
        "name": plane.Name,
        "body": body.Name,
        "attachment_support": getattr(target, "Name", attachment_support),
        "map_mode": map_mode,
    }


def _infer_varset_type(value: Any, explicit_type: str = "") -> str:
    """Resolve shorthand variable types ('Length', 'Float', 'Angle', etc.) to App::Property*."""
    if explicit_type:
        text = explicit_type.strip()
        if text.startswith("App::Property"):
            return text
        mapped = _VARSET_TYPE_MAP.get(text.lower())
        if mapped:
            return mapped
        raise ValueError(
            f"Unsupported VarSet property_type '{explicit_type}'. "
            f"Use one of: {', '.join(_VARSET_TYPE_MAP.keys())} or an App::Property* name."
        )
    if isinstance(value, bool):
        return "App::PropertyBool"
    if isinstance(value, int):
        return "App::PropertyInteger"
    if isinstance(value, float):
        return "App::PropertyLength"
    if isinstance(value, str):
        return "App::PropertyString"
    return "App::PropertyLength"


def create_varset(
    name: str = "VarSet",
    variables: list[dict[str, Any]] | None = None,
    doc_name: str = "",
) -> dict[str, Any]:
    """Create a FreeCAD 1.0/1.1 App::VarSet parametric variable table object."""
    doc = resolve_doc(doc_name)
    added_vars: list[str] = []

    with undo_transaction(doc, f"Create {name or 'VarSet'}"):
        vs = doc.addObject("App::VarSet", name or "VarSet")
        for var in variables or []:
            prop_name = str(var["name"]).strip()
            if not prop_name:
                raise ValueError("VarSet variable entry requires a non-empty 'name'.")
            val = var.get("value")
            full_type = _infer_varset_type(val, str(var.get("type", "")))
            group = str(var.get("group", "Dimensions") or "Dimensions")
            vs.addProperty(full_type, prop_name, group)
            if val is not None:
                setattr(vs, prop_name, val)
            added_vars.append(prop_name)
        doc.recompute()

    return {
        "document": doc.Name,
        "name": vs.Name,
        "label": vs.Label,
        "variables": added_vars,
    }


def set_property(
    object_name: str,
    property_name: str,
    value: Any = None,
    expression: str = "",
    clear_expression: bool = False,
    property_type: str = "",
    group: str = "Dimensions",
    doc_name: str = "",
) -> dict[str, Any]:
    """Modify a Data-tab property, bind a parametric expression, or add a VarSet variable."""
    doc = resolve_doc(doc_name)
    obj = doc.getObject(object_name)
    if obj is None:
        raise ValueError(f"Object not found: {object_name}")

    with undo_transaction(doc, f"Set {obj.Name}.{property_name}"):
        if property_name not in getattr(obj, "PropertiesList", []):
            if obj.TypeId == "App::VarSet" and hasattr(obj, "addProperty"):
                full_type = _infer_varset_type(value, property_type)
                obj.addProperty(full_type, property_name, group or "Dimensions")
            else:
                raise ValueError(f"Object '{obj.Name}' has no property '{property_name}'.")

        if clear_expression and hasattr(obj, "clearExpression"):
            obj.clearExpression(property_name)
        elif expression:
            if not hasattr(obj, "setExpression"):
                raise ValueError(f"Object '{obj.Name}' does not support setExpression().")
            obj.setExpression(property_name, expression)

        if value is not None:
            current = getattr(obj, property_name, None)
            if isinstance(value, (list, tuple)) and len(value) == 3 and hasattr(current, "x"):
                setattr(obj, property_name, FreeCAD.Vector(float(value[0]), float(value[1]), float(value[2])))
            else:
                setattr(obj, property_name, value)

        doc.recompute()
        verify_object_valid(obj, f"set_property({obj.Name}.{property_name})")

    new_val = getattr(obj, property_name, None)
    return {
        "document": doc.Name,
        "object": obj.Name,
        "property": property_name,
        "value": sanitize_json(new_val),
        "expression": expression or None,
    }


def set_appearance(
    object_name: str,
    visible: bool | None = None,
    transparency: int | None = None,
    color: list[float] | None = None,
    doc_name: str = "",
) -> dict[str, Any]:
    """Update object visibility, transparency (0-100), or RGB shape color in the View tab."""
    doc = resolve_doc(doc_name)
    obj = doc.getObject(object_name)
    if obj is None:
        raise ValueError(f"Object not found: {object_name}")

    vobj = getattr(obj, "ViewObject", None)
    if vobj is None:
        raise RuntimeError(f"ViewObject is unavailable for '{obj.Name}' (headless mode?).")

    with undo_transaction(doc, f"Appearance {obj.Name}", allow_during_dialog=True):
        if visible is not None and hasattr(vobj, "Visibility"):
            vobj.Visibility = bool(visible)
        if transparency is not None and hasattr(vobj, "Transparency"):
            clamped = max(0, min(100, int(transparency)))
            vobj.Transparency = clamped
        if color is not None and hasattr(vobj, "ShapeColor"):
            if len(color) not in (3, 4):
                raise ValueError("Color must be [r, g, b] or [r, g, b, a] with floats in 0.0-1.0 (or 0-255).")
            vals = [float(c) / 255.0 if max(color) > 1.0 else float(c) for c in color]
            vobj.ShapeColor = tuple(vals[:3])

    return {
        "document": doc.Name,
        "object": obj.Name,
        "visible": getattr(vobj, "Visibility", None),
        "transparency": getattr(vobj, "Transparency", None),
    }
