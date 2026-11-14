"""Crash-safe multi-angle viewport screenshot handler."""

import base64
import os
import tempfile
from typing import Any

import FreeCAD

from handlers.common import require_gui, resolve_doc

MAX_AUTO_SCREENSHOT_EDGE = 1024

_VIEW_DISPATCH = {
    "Isometric": "viewIsometric",
    "Front": "viewFront",
    "Top": "viewTop",
    "Right": "viewRight",
    "Back": "viewBack",
    "Left": "viewLeft",
    "Bottom": "viewBottom",
    "Dimetric": "viewDimetric",
    "Trimetric": "viewTrimetric",
}

_STD_COMMAND_DISPATCH = {
    "Isometric": "Std_ViewIsometric",
    "Front": "Std_ViewFront",
    "Top": "Std_ViewTop",
    "Right": "Std_ViewRight",
    "Back": "Std_ViewRear",
    "Left": "Std_ViewLeft",
    "Bottom": "Std_ViewBottom",
    "Dimetric": "Std_ViewDimetric",
    "Trimetric": "Std_ViewTrimetric",
}


def _get_view_size(view: Any) -> tuple[int, int]:
    try:
        size = view.getSize()
        if isinstance(size, (list, tuple)) and len(size) >= 2:
            return max(1, int(size[0])), max(1, int(size[1]))
        return max(1, int(size.width())), max(1, int(size.height()))
    except Exception:
        return 1024, 768


def _resolve_size(view: Any, width: int | None, height: int | None) -> tuple[int, int]:
    vw, vh = _get_view_size(view)
    if width is None and height is None:
        longest = max(vw, vh)
        if longest <= MAX_AUTO_SCREENSHOT_EDGE:
            return vw, vh
        scale = MAX_AUTO_SCREENSHOT_EDGE / longest
        return max(1, int(vw * scale)), max(1, int(vh * scale))
    return (vw if width is None else max(1, int(width))), (vh if height is None else max(1, int(height)))


def get_view(
    view_name: str = "Current",
    width: int | None = None,
    height: int | None = None,
    focus_object: str = "",
) -> dict[str, Any]:
    """Capture the displayed 3D viewport safely (guarding against non-3D active tabs)."""
    gui = require_gui()

    # Use activeView() rather than ActiveDocument.ActiveView to avoid capturing a
    # hidden 3D view while a TechDraw/Start/Spreadsheet tab is active (which can crash).
    try:
        view = gui.activeView()
    except Exception:
        view = None

    if view is None or not hasattr(view, "saveImage"):
        raise RuntimeError(
            "The active tab is not a 3D viewport (for example, a TechDraw page or Start page is in front). "
            "Switch to the 3D model tab to take a viewport screenshot."
        )

    if view_name != "Current":
        method_name = _VIEW_DISPATCH.get(view_name)
        if method_name is None:
            raise ValueError(
                f"Invalid view_name '{view_name}'. Valid options: Current, {', '.join(_VIEW_DISPATCH.keys())}"
            )
        if hasattr(view, method_name):
            getattr(view, method_name)()
        else:
            cmd = _STD_COMMAND_DISPATCH.get(view_name)
            if cmd:
                gui.runCommand(cmd)

    if focus_object:
        doc = resolve_doc()
        obj = doc.getObject(focus_object)
        if obj is None:
            raise ValueError(f"Object not found for focus_object: {focus_object}")
        gui.Selection.clearSelection()
        gui.Selection.addSelection(obj)
        gui.runCommand("Std_ViewFitSelection")
    elif view_name != "Current" and hasattr(view, "fitAll"):
        view.fitAll()

    resolved_w, resolved_h = _resolve_size(view, width, height)

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        view.saveImage(tmp_path, resolved_w, resolved_h, "Current")
        with open(tmp_path, "rb") as f:
            image_bytes = f.read()
        return {
            "base64_png": base64.b64encode(image_bytes).decode("ascii"),
            "width": resolved_w,
            "height": resolved_h,
            "view_name": view_name,
            "format": "png",
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
