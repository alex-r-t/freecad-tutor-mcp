"""Beginner-friendly translator for cryptic FreeCAD and OpenCASCADE error messages.

This module maps low-level C++ / OCCT exceptions (such as ``BRep_API: command not
done`` or ``Failed to validate broken face``) into plain-English explanations,
likely root causes, and concrete steps a student can take in the FreeCAD UI.
"""

from __future__ import annotations

from typing import Any


# Ordered from most specific to most general so detailed diagnostics win first.
_ERROR_PATTERNS: list[dict[str, Any]] = [
    {
        "code": "FILLET_CHAMFER_FAILED",
        "needles": (
            "fillet not possible",
            "chamfer not possible",
            "failed to create fillet",
            "failed to create chamfer",
            "chfi3d_builder",
        ),
        "summary": "The Fillet or Chamfer could not be built on the selected edge(s).",
        "likely_causes": [
            "The radius/size is equal to or larger than the adjacent face width or edge length.",
            "The new rounded corner would collide with a neighboring edge or hole.",
            "Selected edges mix internal/external corners or form an acute pinch point.",
            "A zero-radius or invalid edge index was referenced after an earlier edit (TNP).",
        ],
        "how_to_fix": [
            "Try a smaller Radius/Size first (for example 0.5 mm or 1.0 mm) and increase gradually.",
            "Apply internal and external fillets in separate PartDesign Fillet features.",
            "Move fillets and chamfers near the bottom of your Body tree (after Pads/Pockets).",
        ],
    },
    {
        "code": "MULTIPLE_SOLIDS",
        "needles": ("result has multiple solids", "multiple solids"),
        "summary": "PartDesign only allows a single connected solid inside each Body.",
        "likely_causes": [
            "A Pad, Revolution, or Pattern created a floating piece that does not touch the existing solid.",
            "A Pocket or Groove sliced the Body completely into two separate pieces.",
        ],
        "how_to_fix": [
            "Make sure your new sketch overlaps or attaches flush to the existing Body geometry.",
            "If you genuinely want two separate parts, create a second Body (PartDesign -> Create body).",
        ],
    },
    {
        "code": "UNCLOSED_OR_BROKEN_WIRE",
        "needles": (
            "failed to validate broken face",
            "wire is not closed",
            "not closed",
            "open wire",
            "self-intersecting",
            "overlapping wires",
        ),
        "summary": "The Sketch profile cannot form a closed 2D face for Pad, Pocket, or Revolve.",
        "likely_causes": [
            "Two endpoints look close on screen but do not have a Coincident constraint leaving a tiny gap.",
            "Lines cross over each other, or two duplicate lines lie directly on top of each other.",
            "A T-junction exists (three white lines meet at one corner, or a construction line was left solid).",
        ],
        "how_to_fix": [
            "Run Sketcher -> Validate sketch (or call get_sketch_diagnostics) to find OpenVertices.",
            "Select the open endpoints and press C (Coincident), or switch extra divider lines to Construction geometry (blue).",
        ],
    },
    {
        "code": "SKETCH_CONFLICT_OR_REDUNDANT",
        "needles": (
            "conflicting constraints",
            "over-constrained",
            "redundant constraints",
            "malformed constraints",
            "unsolved",
        ),
        "summary": "The Sketch solver has contradictory or duplicate constraints.",
        "likely_causes": [
            "Two dimensions or geometric rules try to force the same length, angle, or alignment in incompatible ways.",
            "For example, adding both Vertical and Horizontal on one line, or constraining all 4 corners plus both diagonals.",
        ],
        "how_to_fix": [
            "Check Sketcher Tasks panel -> Solver messages for the conflicting constraint numbers.",
            "Delete one of the conflicting/redundant constraints, or toggle an informational dimension to Reference (blue).",
        ],
    },
    {
        "code": "THICKNESS_OFFSET_FAILED",
        "needles": (
            "brepoffset",
            "thickness",
            "offset failed",
            "makeoffset",
        ),
        "summary": "The Thickness (shell/hollow) operation failed to offset the solid walls.",
        "likely_causes": [
            "The wall thickness exceeds half the width of a thin feature, rib, or tight fillet.",
            "Join type 'Arc' vs 'Intersection' struggled at a sharp corner.",
            "No open face was selected (or an internal face was chosen by mistake).",
        ],
        "how_to_fix": [
            "Reduce the Thickness value (or switch 'Make thickness inwards' on/off).",
            "Try changing Join type between 'Intersection' and 'Arc'.",
            "Apply Thickness *before* adding small decorative Fillets or Chamfers.",
        ],
    },
    {
        "code": "EMPTY_OR_NULL_SHAPE",
        "needles": (
            "linked shape object is empty",
            "tip shape is empty",
            "input shape is null",
            "base shape is empty",
            "shape is null",
            "no planar face",
        ),
        "summary": "The feature received an empty or missing input shape from an upstream object.",
        "likely_causes": [
            "An earlier Sketch contains only construction (blue) geometry and no solid white profile.",
            "An upstream feature in the Body failed to recompute or has its Tip unset.",
            "A sketch or datum plane was detached from its support.",
        ],
        "how_to_fix": [
            "Check the Model tree from top to bottom for the first object with a red exclamation mark.",
            "Inspect the referenced Sketch to ensure it has at least one closed loop of normal (white) lines.",
        ],
    },
    {
        "code": "TOPOLOGICAL_NAMING_OR_INDEX",
        "needles": (
            "index out of range",
            "list index out of range",
            "topods",
            "cannot find subshape",
            "sub-shape not found",
            "no face",
        ),
        "summary": "A referenced Face, Edge, or Vertex name no longer matches the geometry (Topological Naming Problem).",
        "likely_causes": [
            "An earlier sketch or feature was edited, which renumbered Face1/Edge1 names downstream.",
            "The feature points to an Edge or Face index that does not exist on the current solid.",
        ],
        "how_to_fix": [
            "Re-select the intended face or edge on the current solid (using find_edges / find_faces).",
            "Attach new Sketches to Origin planes or a DatumPlane instead of generated solid faces.",
        ],
    },
    {
        "code": "REVOLVE_OR_AXIS_INTERSECTION",
        "needles": (
            "revolve",
            "revolution",
            "axis intersects",
            "bRepPrimAPI_MakeRevol",
            "makeRevol",
        ),
        "summary": "The Revolution or Groove cannot sweep the sketch cleanly around the selected axis.",
        "likely_causes": [
            "The sketch crosses over the revolution axis, causing a self-intersecting solid.",
            "A sketch edge touches the axis only at a single corner point (zero-thickness pinch).",
        ],
        "how_to_fix": [
            "Keep the entire closed sketch profile on ONE side of the revolution axis (edges lying cleanly on the axis are fine; crossing it is not).",
        ],
    },
    {
        "code": "LOFT_OR_SWEEP_FAILED",
        "needles": (
            "not enough sections",
            "loft",
            "pipe",
            "sweep",
            "thrusections",
            "frenet",
        ),
        "summary": "The Loft or Sweep could not smoothly connect the profile sketches.",
        "likely_causes": [
            "Fewer than two profile sections were provided for Loft, or profile sketches have wildly different vertex counts/orientations.",
            "A Sweep path has corners sharper than the profile radius, causing the swept solid to fold through itself.",
        ],
        "how_to_fix": [
            "Order Loft sections sequentially along the build direction and keep profiles closed.",
            "For Sweep, add rounded arcs at sharp path corners or increase bend radius.",
        ],
    },
    {
        "code": "BREP_API_GENERIC",
        "needles": ("brep_api: command not done", "command not done", "std::exception"),
        "summary": "FreeCAD's OpenCASCADE geometry kernel could not compute a valid 3D solid from these inputs.",
        "likely_causes": [
            "Two faces touch at a razor-thin tangent edge or zero-thickness point (coplanar coincidence issue).",
            "Pocket/Cut depth is 0 mm or misses the solid completely.",
            "A boolean or pattern step produced self-intersecting faces.",
        ],
        "how_to_fix": [
            "Check that dimensions are positive and cuts clearly overlap or extend slightly past coplanar faces.",
            "Verify the base sketch is closed with no duplicate overlapping lines.",
            "Inspect the feature properties in the Data tab and test a simpler value first.",
        ],
    },
]


def explain_freecad_error(error_message: str) -> dict[str, Any]:
    """Translate a FreeCAD/OCC error string into beginner-friendly causes and fixes."""
    text = (error_message or "").strip()
    lowered = text.lower()

    if not text:
        return {
            "matched": False,
            "code": "EMPTY_ERROR",
            "original_message": text,
            "summary": "No error message was provided.",
            "likely_causes": [],
            "how_to_fix": [],
        }

    for entry in _ERROR_PATTERNS:
        if any(needle in lowered for needle in entry["needles"]):
            return {
                "matched": True,
                "code": entry["code"],
                "original_message": text,
                "summary": entry["summary"],
                "likely_causes": list(entry["likely_causes"]),
                "how_to_fix": list(entry["how_to_fix"]),
            }

    return {
        "matched": False,
        "code": "UNKNOWN_FREECAD_ERROR",
        "original_message": text,
        "summary": "FreeCAD reported an error that does not match a built-in pattern.",
        "likely_causes": [
            "An upstream object in the Model tree failed first and cascaded downward.",
            "A property value or attachment reference became invalid after an edit.",
        ],
        "how_to_fix": [
            "Check the earliest object in the Model tree showing a red exclamation mark.",
            "Open the feature or Sketch in the Tasks panel to inspect solver/preview warnings, or check Report View.",
        ],
    }
