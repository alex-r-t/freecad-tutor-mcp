"""Pure-Python JSON sanitization utilities for the FreeCAD Tutor MCP addon.

Kept strictly free of `import FreeCAD` so `rpc_server.py` can run and be unit-tested
outside of FreeCAD's embedded Python interpreter.
"""

import math
from typing import Any


def sanitize_json(value: Any, _depth: int = 0) -> Any:
    """Convert arbitrary Python or FreeCAD objects into strict JSON-safe values.

    Why this exists:
    - FreeCAD properties can contain non-finite floats (`NaN`, `inf`, `-inf`),
      which cause Python's `json.dumps(..., allow_nan=False)` to raise ValueError
      and crash JSON-RPC clients.
    - Console output or user labels may contain ASCII control characters.
    - FreeCAD exposes custom C++ wrapper types (`Vector`, `Placement`, `Rotation`,
      and `DocumentObject` links) that are not natively JSON serializable.
    """
    # Prevent infinite recursion on cyclic or deeply nested structures.
    if _depth > 20:
        return "<max-depth>"

    if value is None or isinstance(value, (bool, int)):
        return value

    if isinstance(value, float):
        # Replace NaN / Infinity with None so strict JSON parsing never fails.
        if math.isfinite(value):
            return value
        return None

    if isinstance(value, str):
        # Strip non-printable ASCII control chars except tab, newline, and carriage return.
        cleaned_chars = []
        for ch in value:
            if ch in ("\t", "\n", "\r") or ord(ch) >= 32:
                cleaned_chars.append(ch)
        return "".join(cleaned_chars)

    if isinstance(value, (list, tuple)):
        return [sanitize_json(item, _depth + 1) for item in value]

    if isinstance(value, dict):
        sanitized_dict: dict[str, Any] = {}
        for key, val in value.items():
            sanitized_dict[str(key)] = sanitize_json(val, _depth + 1)
        return sanitized_dict

    # FreeCAD.Vector / Base::Vector3d exposes .x, .y, .z attributes.
    if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
        return {
            "x": sanitize_json(float(value.x), _depth + 1),
            "y": sanitize_json(float(value.y), _depth + 1),
            "z": sanitize_json(float(value.z), _depth + 1),
        }

    # FreeCAD.Placement exposes .Base (Vector) and .Rotation (Rotation).
    if hasattr(value, "Base") and hasattr(value, "Rotation"):
        return {
            "base": sanitize_json(value.Base, _depth + 1),
            "rotation": sanitize_json(value.Rotation, _depth + 1),
        }

    # FreeCAD.Rotation exposes .Q (4-element quaternion).
    if hasattr(value, "Q"):
        return {"Q": [sanitize_json(float(q), _depth + 1) for q in value.Q]}

    # Link to another FreeCAD DocumentObject (avoid expanding the whole target object).
    if hasattr(value, "Name") and hasattr(value, "TypeId"):
        return f"<{value.TypeId} '{value.Name}'>"

    # Fallback for any other C++ wrapper object: bounded repr string.
    text = repr(value)
    if len(text) > 200:
        return text[:200] + "..."
    return text
