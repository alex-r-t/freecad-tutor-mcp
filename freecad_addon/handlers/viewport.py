"""Crash-safe camera control, multi-angle viewport, and full-window capture handlers."""

import base64
import os
import tempfile
from typing import Any

import FreeCAD

from handlers.common import require_gui, resolve_doc

MAX_AUTO_SCREENSHOT_EDGE = 1024
MAX_WINDOW_SCREENSHOT_EDGE = 1280

_VIEW_ALIASES = {
    "current": "Current",
    "iso": "Isometric",
    "isometric": "Isometric",
    "front": "Front",
    "top": "Top",
    "right": "Right",
    "back": "Back",
    "rear": "Back",
    "left": "Left",
    "bottom": "Bottom",
    "dimetric": "Dimetric",
    "trimetric": "Trimetric",
}

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


def _import_qt_gui():
    """Import QtCore, QtGui, QtWidgets across PySide / PySide6 / PySide2."""
    for mod in ("PySide", "PySide6", "PySide2"):
        try:
            pkg = __import__(mod, fromlist=["QtCore", "QtGui", "QtWidgets"])
            return pkg.QtCore, pkg.QtGui, pkg.QtWidgets
        except (ImportError, AttributeError):
            continue
    return None, None, None


def _normalize_view_name(view_name: str) -> str:
    key = (view_name or "Current").strip().lower()
    if key in _VIEW_ALIASES:
        return _VIEW_ALIASES[key]
    raise ValueError(
        f"Invalid view_name '{view_name}'. Valid options: Current, {', '.join(_VIEW_DISPATCH.keys())}"
    )


def _apply_camera_view(
    gui: Any,
    view: Any,
    *,
    view_name: str = "Current",
    fit_all: bool = True,
    zoom_to_selection: bool = False,
    focus_object: str = "",
) -> str:
    """Orient an active 3D viewport and optionally fit or zoom to selection."""
    canonical = _normalize_view_name(view_name)
    if canonical != "Current":
        method_name = _VIEW_DISPATCH[canonical]
        if hasattr(view, method_name):
            getattr(view, method_name)()
        else:
            cmd = _STD_COMMAND_DISPATCH.get(canonical)
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
    elif zoom_to_selection:
        sel = gui.Selection.getSelectionEx()
        if not sel:
            raise ValueError("zoom_to_selection=True requires at least one selected object.")
        gui.runCommand("Std_ViewFitSelection")
    elif fit_all and hasattr(view, "fitAll"):
        view.fitAll()

    return canonical


def set_view(
    view_name: str = "Isometric",
    fit_all: bool = True,
    zoom_to_selection: bool = False,
    focus_object: str = "",
) -> dict[str, Any]:
    """Change the active 3D viewport camera orientation and zoom without taking a screenshot."""
    gui = require_gui()
    try:
        view = gui.activeView()
    except Exception:
        view = None

    if view is None or not hasattr(view, "saveImage"):
        raise RuntimeError(
            "The active tab is not a 3D viewport (for example, a TechDraw or Start page is in front)."
        )

    canonical = _apply_camera_view(
        gui,
        view,
        view_name=view_name,
        fit_all=fit_all,
        zoom_to_selection=zoom_to_selection,
        focus_object=focus_object,
    )
    return {
        "view_name": canonical,
        "fit_all": bool(fit_all),
        "zoom_to_selection": bool(zoom_to_selection),
        "focus_object": focus_object or None,
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


def _capture_main_window(
    gui: Any,
    width: int | None,
    height: int | None,
    view_name: str,
) -> dict[str, Any]:
    """Capture the entire FreeCAD main window (including Task panels, tree, toolbars, dialogs)."""
    QtCore, QtGui, _QtWidgets = _import_qt_gui()
    if QtCore is None or QtGui is None:
        raise RuntimeError("Qt bindings are unavailable for full-window capture.")

    main_window = gui.getMainWindow() if hasattr(gui, "getMainWindow") else None
    if main_window is None:
        raise RuntimeError("FreeCAD main window handle is unavailable.")

    pixmap = None
    screen = QtGui.QGuiApplication.primaryScreen() if hasattr(QtGui, "QGuiApplication") else None
    if screen is not None and hasattr(main_window, "winId"):
        try:
            pixmap = screen.grabWindow(int(main_window.winId()))
        except Exception:
            pixmap = None
    if pixmap is None or (hasattr(pixmap, "isNull") and pixmap.isNull()):
        pixmap = main_window.grab()

    if pixmap is None or (hasattr(pixmap, "isNull") and pixmap.isNull()):
        raise RuntimeError("Failed to capture FreeCAD main window pixmap.")

    orig_w, orig_h = int(pixmap.width()), int(pixmap.height())
    if width is not None or height is not None:
        target_w = int(width) if width is not None else orig_w
        target_h = int(height) if height is not None else orig_h
        pixmap = pixmap.scaled(
            max(1, target_w),
            max(1, target_h),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
    elif max(orig_w, orig_h) > MAX_WINDOW_SCREENSHOT_EDGE:
        scale = MAX_WINDOW_SCREENSHOT_EDGE / max(orig_w, orig_h)
        pixmap = pixmap.scaled(
            max(1, int(orig_w * scale)),
            max(1, int(orig_h * scale)),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )

    byte_array = QtCore.QByteArray()
    qbuf = QtCore.QBuffer(byte_array)
    qbuf.open(QtCore.QIODevice.WriteOnly)
    pixmap.save(qbuf, "PNG")
    qbuf.close()
    png_bytes = bytes(byte_array)

    return {
        "base64_png": base64.b64encode(png_bytes).decode("ascii"),
        "width": int(pixmap.width()),
        "height": int(pixmap.height()),
        "view_name": view_name,
        "capture_scope": "window",
        "format": "png",
    }


def get_view(
    view_name: str = "Current",
    width: int | None = None,
    height: int | None = None,
    focus_object: str = "",
    capture_scope: str = "viewport",
) -> dict[str, Any]:
    """Capture the 3D viewport (`capture_scope='viewport'`) or full FreeCAD window (`'window'`)."""
    gui = require_gui()
    scope = (capture_scope or "viewport").strip().lower()
    if scope not in ("viewport", "window"):
        raise ValueError(f"Invalid capture_scope '{capture_scope}'. Use 'viewport' or 'window'.")

    if scope == "window":
        # Optionally orient 3D camera first only if a 3D viewport is currently active.
        canonical = _normalize_view_name(view_name)
        if canonical != "Current" or focus_object:
            try:
                view = gui.activeView()
            except Exception:
                view = None
            if view is not None and hasattr(view, "saveImage"):
                _apply_camera_view(
                    gui,
                    view,
                    view_name=canonical,
                    fit_all=(canonical != "Current"),
                    focus_object=focus_object,
                )
        return _capture_main_window(gui, width, height, canonical)

    # Use activeView() rather than ActiveDocument.ActiveView to avoid capturing a
    # hidden 3D view while a TechDraw/Start/Spreadsheet tab is active (which can crash).
    try:
        view = gui.activeView()
    except Exception:
        view = None

    if view is None or not hasattr(view, "saveImage"):
        raise RuntimeError(
            "The active tab is not a 3D viewport (for example, a TechDraw page or Start page is in front). "
            "Use capture_scope='window' to see non-3D tabs/dialogs, or switch to the 3D model tab."
        )

    canonical = _apply_camera_view(
        gui,
        view,
        view_name=view_name,
        fit_all=(view_name.strip().lower() != "current"),
        focus_object=focus_object,
    )

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
            "view_name": canonical,
            "capture_scope": "viewport",
            "format": "png",
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
