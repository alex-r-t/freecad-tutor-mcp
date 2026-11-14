"""Visual Qt spotlight overlay to help students locate UI elements in FreeCAD.

This module creates a non-blocking, click-through translucent Qt overlay window
over FreeCAD's live main window so the AI tutor can point directly at a toolbar
button, menu item, dock panel, or the workbench switcher without interfering
with the student's mouse clicks or keyboard focus.
"""

from typing import Any

import FreeCAD

from handlers.common import require_gui
from handlers.ui_discovery import _suggest_unloaded_workbenches


# Keep references to the active overlay and timer so a new highlight cleanly
# replaces the previous one and garbage collection never deletes an active timer.
_active_overlay: Any = None
_active_timer: Any = None

# Map beginner-friendly panel names to FreeCAD's Qt widget objectNames / titles.
_PANEL_ALIASES: dict[str, tuple[str, ...]] = {
    "tasks": ("Tasks", "Task panel", "Task List"),
    "task panel": ("Tasks", "Task panel", "Task List"),
    "tasks panel": ("Tasks", "Task panel", "Task List"),
    "model": ("Model", "Combo View", "Tree view"),
    "tree": ("Tree view", "Model", "Combo View"),
    "tree view": ("Tree view", "Model", "Combo View"),
    "combo view": ("Combo View", "Model"),
    "report": ("Report view",),
    "report view": ("Report view",),
    "python": ("Python console",),
    "python console": ("Python console",),
    "property": ("Property view",),
    "property view": ("Property view",),
    "selection": ("Selection view",),
    "selection view": ("Selection view",),
    "dag view": ("DAG View",),
}

_WORKBENCH_SWITCHER_ALIASES = {
    "workbench",
    "workbench switcher",
    "workbench selector",
    "workbench dropdown",
    "workbench combobox",
}


def _qt_modules() -> tuple[Any, Any, Any] | None:
    """Import QtCore, QtGui, QtWidgets across FreeCAD's PySide / PySide6 / PySide2 shims."""
    for mod_name in ("PySide", "PySide6", "PySide2"):
        try:
            pkg = __import__(mod_name, fromlist=["QtCore", "QtGui", "QtWidgets"])
            qt_core = getattr(pkg, "QtCore", None)
            qt_gui = getattr(pkg, "QtGui", None)
            qt_widgets = getattr(pkg, "QtWidgets", None) or qt_gui
            if qt_core is not None and qt_widgets is not None:
                return qt_core, qt_gui, qt_widgets
        except ImportError:
            continue
    return None


def clear_ui_highlight() -> dict[str, Any]:
    """Hide and dispose of any currently visible Qt UI spotlight overlay."""
    global _active_overlay, _active_timer

    had_overlay = _active_overlay is not None
    if _active_timer is not None:
        try:
            _active_timer.stop()
            _active_timer.deleteLater()
        except Exception:
            pass
        _active_timer = None

    if _active_overlay is not None:
        try:
            _active_overlay.hide()
            _active_overlay.deleteLater()
        except Exception:
            pass
        _active_overlay = None

    return {"cleared": True, "was_active": had_overlay}


def _global_rect_for_widget(widget: Any, qt_core: Any) -> tuple[int, int, int, int] | None:
    """Return (x, y, width, height) in global screen coordinates for a visible widget."""
    try:
        if hasattr(widget, "isVisible") and not widget.isVisible():
            return None
        w = int(widget.width())
        h = int(widget.height())
        if w <= 0 or h <= 0:
            return None
        if hasattr(widget, "mapToGlobal") and qt_core is not None:
            top_left = widget.mapToGlobal(qt_core.QPoint(0, 0))
            return int(top_left.x()), int(top_left.y()), w, h
        # Fallback for lightweight mock widgets without QtCore.QPoint.
        return int(widget.x()), int(widget.y()), w, h
    except Exception:
        return None


def _global_rect_for_subrect(widget: Any, local_rect: Any, qt_core: Any) -> tuple[int, int, int, int] | None:
    """Return global screen rect (x, y, w, h) for a sub-rectangle inside a widget."""
    try:
        w = int(local_rect.width())
        h = int(local_rect.height())
        if w <= 0 or h <= 0:
            return None
        if hasattr(widget, "mapToGlobal") and qt_core is not None:
            top_left = widget.mapToGlobal(qt_core.QPoint(int(local_rect.x()), int(local_rect.y())))
            return int(top_left.x()), int(top_left.y()), w, h
        return int(local_rect.x()), int(local_rect.y()), w, h
    except Exception:
        return None


def _find_workbench_switcher_widget(main_window: Any, qt_widgets: Any) -> Any:
    """Locate FreeCAD's Workbench selector combo box or Workbench toolbar."""
    if qt_widgets is None:
        return None

    if hasattr(qt_widgets, "QComboBox"):
        try:
            for combo in main_window.findChildren(qt_widgets.QComboBox):
                cls_name = type(combo).__name__.lower()
                obj_name = (combo.objectName() or "").lower()
                parent = combo.parentWidget() if hasattr(combo, "parentWidget") else None
                parent_name = (parent.objectName() or "").lower() if parent is not None else ""
                if (
                    "workbench" in cls_name
                    or "workbench" in obj_name
                    or "workbench" in parent_name
                ) and combo.isVisible():
                    return combo
        except Exception:
            pass

    if hasattr(qt_widgets, "QToolBar"):
        try:
            for tb in main_window.findChildren(qt_widgets.QToolBar):
                title = (tb.windowTitle() or tb.objectName() or "").lower()
                if "workbench" in title and tb.isVisible():
                    return tb
        except Exception:
            pass

    return None


def _find_panel_widget(main_window: Any, query: str, qt_widgets: Any) -> tuple[Any, str] | None:
    """Match a named dock panel (e.g. 'Model', 'Tasks', 'Report view')."""
    if qt_widgets is None:
        return None

    if query in _WORKBENCH_SWITCHER_ALIASES:
        wb_widget = _find_workbench_switcher_widget(main_window, qt_widgets)
        if wb_widget is not None:
            return wb_widget, "Workbench selector"
        return None

    candidate_names = _PANEL_ALIASES.get(query)
    if candidate_names is None:
        return None

    names_lower = [n.lower() for n in candidate_names]
    widget_types = [
        getattr(qt_widgets, cls_name)
        for cls_name in ("QDockWidget", "QWidget")
        if hasattr(qt_widgets, cls_name)
    ]
    for wtype in widget_types:
        try:
            for child in main_window.findChildren(wtype):
                obj_name = (child.objectName() or "").strip()
                win_title = (child.windowTitle() or "").strip() if hasattr(child, "windowTitle") else ""
                if (obj_name.lower() in names_lower or win_title.lower() in names_lower) and child.isVisible():
                    display = win_title or obj_name or candidate_names[0]
                    return child, display
        except Exception:
            continue

    return None


def _find_toolbar_button(
    main_window: Any,
    query: str,
    qt_widgets: Any,
    qt_core: Any,
) -> dict[str, Any] | None:
    """Search visible QToolBars for a matching QAction button (or its overflow '>>' button)."""
    if qt_widgets is None or not hasattr(qt_widgets, "QToolBar"):
        return None

    best: dict[str, Any] | None = None
    best_score = -1

    try:
        toolbars = main_window.findChildren(qt_widgets.QToolBar)
    except Exception:
        return None

    for toolbar in toolbars:
        if hasattr(toolbar, "isVisible") and not toolbar.isVisible():
            continue
        tb_title = (toolbar.windowTitle() or toolbar.objectName() or "Toolbar").strip()

        for act in toolbar.actions():
            cmd_name = (act.objectName() or "").strip()
            act_text = (act.text() or "").replace("&", "").strip()
            if not cmd_name and not act_text:
                continue

            score = 0
            if query == cmd_name.lower() or query == act_text.lower():
                score = 100
            elif act_text.lower().startswith(query) or cmd_name.lower().startswith(query):
                score = 60
            elif query in cmd_name.lower() or query in act_text.lower():
                score = 30
            else:
                continue

            widget = toolbar.widgetForAction(act) if hasattr(toolbar, "widgetForAction") else None
            if widget is None or (hasattr(widget, "isVisible") and not widget.isVisible()):
                continue

            # Check whether the button is clipped beyond the toolbar's '>>' overflow chevron.
            overflow_btn = None
            try:
                tb_rect = toolbar.rect()
                btn_center = widget.geometry().center()
                in_toolbar = bool(tb_rect.contains(btn_center))
            except Exception:
                in_toolbar = True

            if not in_toolbar and hasattr(qt_widgets, "QToolButton"):
                for tb_child in toolbar.findChildren(qt_widgets.QToolButton):
                    if (tb_child.objectName() or "") == "qt_toolbutton_ext" and tb_child.isVisible():
                        overflow_btn = tb_child
                        break

            target_widget = overflow_btn if overflow_btn is not None else widget
            rect = _global_rect_for_widget(target_widget, qt_core)
            if rect is None:
                continue

            if overflow_btn is not None:
                score -= 10  # Prefer directly visible buttons if command appears in multiple toolbars.

            if score > best_score:
                best_score = score
                best = {
                    "target_kind": "toolbar_overflow" if overflow_btn is not None else "toolbar_button",
                    "command": cmd_name or None,
                    "display_name": act_text or cmd_name,
                    "toolbar": tb_title,
                    "overflowed": overflow_btn is not None,
                    "rect": rect,
                }

    return best


def _find_menubar_item(
    main_window: Any,
    query: str,
    qt_core: Any,
) -> dict[str, Any] | None:
    """Search QMenuBar actions recursively and locate the top-level menu bar tab."""
    try:
        menu_bar = main_window.menuBar()
    except Exception:
        menu_bar = None
    if menu_bar is None:
        return None

    def _walk_menu(menu: Any, prefix: str, top_act: Any, top_title: str) -> tuple[int, dict[str, Any]] | None:
        best_local: tuple[int, dict[str, Any]] | None = None
        for act in menu.actions():
            text = (act.text() or "").replace("&", "").strip()
            cmd = (act.objectName() or "").strip()
            sub = act.menu() if hasattr(act, "menu") else None
            if sub is not None:
                next_prefix = f"{prefix} -> {text}" if prefix else text
                candidate = _walk_menu(sub, next_prefix, top_act, top_title)
                if candidate is not None and (best_local is None or candidate[0] > best_local[0]):
                    best_local = candidate
                continue

            if not text and not cmd:
                continue
            score = 0
            if query == cmd.lower() or query == text.lower():
                score = 100
            elif text.lower().startswith(query) or cmd.lower().startswith(query):
                score = 60
            elif query in cmd.lower() or query in text.lower():
                score = 30
            else:
                continue

            if best_local is None or score > best_local[0]:
                full_path = f"{prefix} -> {text}" if prefix else text
                best_local = (
                    score,
                    {
                        "target_kind": "menu_bar_tab",
                        "command": cmd or None,
                        "display_name": text or cmd,
                        "menu_path": full_path,
                        "top_menu": top_title,
                        "top_action": top_act,
                    },
                )
        return best_local

    overall: tuple[int, dict[str, Any]] | None = None
    for top_act in menu_bar.actions():
        top_title = (top_act.text() or "").replace("&", "").strip()
        sub = top_act.menu() if hasattr(top_act, "menu") else None
        if sub is None:
            continue
        found = _walk_menu(sub, top_title, top_act, top_title)
        if found is not None and (overall is None or found[0] > overall[0]):
            overall = found

    if overall is None:
        return None

    info = overall[1]
    top_act = info.pop("top_action")
    if hasattr(menu_bar, "actionGeometry"):
        rect = _global_rect_for_subrect(menu_bar, menu_bar.actionGeometry(top_act), qt_core)
        if rect is not None:
            info["rect"] = rect
            return info
    return None


def _show_spotlight_overlay(
    main_window: Any,
    rect: tuple[int, int, int, int],
    callout_text: str,
    duration_s: float,
    qt_core: Any,
    qt_widgets: Any,
) -> bool:
    """Create and display a click-through Qt highlight frame + callout badge."""
    global _active_overlay, _active_timer

    clear_ui_highlight()

    # Only construct a real top-level Qt window when real PySide QtWidgets are present.
    if qt_core is None or qt_widgets is None or not hasattr(qt_widgets, "QWidget") or not hasattr(qt_widgets, "QFrame"):
        return False

    x, y, w, h = rect
    pad = 6
    box_x = x - pad
    box_y = y - pad
    box_w = w + (pad * 2)
    box_h = h + (pad * 2)

    flags = qt_core.Qt.Tool | qt_core.Qt.FramelessWindowHint | qt_core.Qt.WindowStaysOnTopHint
    if hasattr(qt_core.Qt, "WindowTransparentForInput"):
        flags = flags | qt_core.Qt.WindowTransparentForInput
    if hasattr(qt_core.Qt, "WindowDoesNotAcceptFocus"):
        flags = flags | qt_core.Qt.WindowDoesNotAcceptFocus

    overlay = qt_widgets.QWidget(main_window, flags)
    overlay.setAttribute(qt_core.Qt.WA_TranslucentBackground, True)
    overlay.setAttribute(qt_core.Qt.WA_TransparentForMouseEvents, True)
    overlay.setAttribute(qt_core.Qt.WA_ShowWithoutActivating, True)

    label_widget = qt_widgets.QLabel(callout_text, overlay)
    label_widget.setStyleSheet(
        "QLabel {"
        "  background-color: #1F2937;"
        "  color: #FFFFFF;"
        "  border: 2px solid #FF7A00;"
        "  border-radius: 6px;"
        "  padding: 4px 10px;"
        "  font-weight: bold;"
        "  font-size: 12px;"
        "}"
    )
    label_widget.adjustSize()
    lbl_w = label_widget.width()
    lbl_h = label_widget.height()

    gap = 6
    canvas_w = max(box_w, lbl_w) + 8
    canvas_h = box_h + gap + lbl_h + 8

    # Place badge below the highlighted element unless near bottom of FreeCAD window.
    place_above = False
    try:
        mw_bottom = main_window.mapToGlobal(qt_core.QPoint(0, main_window.height())).y()
        if box_y + box_h + gap + lbl_h > mw_bottom:
            place_above = True
    except Exception:
        pass

    if place_above:
        overlay_y = box_y - gap - lbl_h
        label_local_y = 0
        frame_local_y = lbl_h + gap
    else:
        overlay_y = box_y
        frame_local_y = 0
        label_local_y = box_h + gap

    # Shift container horizontally only if needed so the badge stays visible near right edge.
    overlay_x = box_x
    frame_local_x = 0
    try:
        mw_left = main_window.mapToGlobal(qt_core.QPoint(0, 0)).x()
        mw_right = mw_left + main_window.width()
        if overlay_x + canvas_w > mw_right:
            shift = (overlay_x + canvas_w) - mw_right
            overlay_x = max(mw_left, overlay_x - shift)
            frame_local_x = max(0, box_x - overlay_x)
    except Exception:
        pass

    frame = qt_widgets.QFrame(overlay)
    frame.setGeometry(frame_local_x, frame_local_y, box_w, box_h)
    frame.setStyleSheet(
        "QFrame {"
        "  background-color: rgba(255, 122, 0, 38);"
        "  border: 3px solid #FF7A00;"
        "  border-radius: 8px;"
        "}"
    )

    label_local_x = max(0, min(canvas_w - lbl_w, frame_local_x))
    label_widget.move(label_local_x, label_local_y)

    overlay.setGeometry(overlay_x, overlay_y, canvas_w, canvas_h)
    overlay.show()
    overlay.raise_()

    timer = qt_core.QTimer(overlay)
    timer.setSingleShot(True)
    timer.timeout.connect(clear_ui_highlight)
    timer.start(int(duration_s * 1000))

    _active_overlay = overlay
    _active_timer = timer
    return True


def highlight_ui_element(
    target: str = "",
    label: str = "",
    duration_s: float = 6.0,
    clear: bool = False,
) -> dict[str, Any]:
    """Highlight a live FreeCAD toolbar button, menu item, panel, or workbench selector.

    Args:
        target: Command name (e.g. 'PartDesign_Pad'), button label ('Pad'),
            or panel name ('Model', 'Tasks', 'Report view', 'Workbench').
        label: Optional short callout text shown next to the highlight frame.
        duration_s: Seconds before the highlight automatically disappears (1.0-20.0).
        clear: If True, removes any active highlight without creating a new one.
    """
    require_gui()
    if clear:
        return clear_ui_highlight()

    q = target.strip()
    if not q:
        raise ValueError("target must be a non-empty command, button, or panel name (or set clear=True)")

    clamped_duration = max(1.0, min(float(duration_s), 20.0))
    gui = require_gui()
    main_win = gui.getMainWindow()
    if main_win is None:
        return {"highlighted": False, "target": target, "reason": "FreeCAD MainWindow is not available."}

    qt_pair = _qt_modules()
    qt_core = qt_pair[0] if qt_pair else None
    qt_widgets = qt_pair[2] if qt_pair else None
    q_lower = q.lower()

    resolved: dict[str, Any] | None = None

    # 1. Check named dock panels or the Workbench selector first.
    panel_hit = _find_panel_widget(main_win, q_lower, qt_widgets)
    if panel_hit is not None:
        widget, display_name = panel_hit
        rect = _global_rect_for_widget(widget, qt_core)
        if rect is not None:
            resolved = {
                "target_kind": "workbench_switcher" if q_lower in _WORKBENCH_SWITCHER_ALIASES else "panel",
                "display_name": display_name,
                "rect": rect,
            }

    # 2. Check visible toolbars (matching QAction button or toolbar '>>' overflow chevron).
    if resolved is None:
        resolved = _find_toolbar_button(main_win, q_lower, qt_widgets, qt_core)

    # 3. Check the top-level menu bar if not on a visible toolbar.
    if resolved is None:
        resolved = _find_menubar_item(main_win, q_lower, qt_core)

    # 4. If a lazy-loaded workbench isn't active yet, highlight the Workbench switcher instead.
    if resolved is None:
        hints = _suggest_unloaded_workbenches(q_lower, gui.Command.listAll())
        wb_widget = _find_workbench_switcher_widget(main_win, qt_widgets)
        if hints and wb_widget is not None:
            rect = _global_rect_for_widget(wb_widget, qt_core)
            if rect is not None:
                wb_menu = hints[0]["menu_name"]
                resolved = {
                    "target_kind": "workbench_switcher_hint",
                    "display_name": f"Workbench ({wb_menu})",
                    "suggested_workbench": hints[0]["workbench"],
                    "note": hints[0]["note"],
                    "rect": rect,
                }

    if resolved is None:
        return {
            "highlighted": False,
            "target": target,
            "reason": (
                f"Could not locate a currently visible UI widget matching '{target}'. "
                "Try `find_ui_command` first, or switch to the command's workbench."
            ),
        }

    default_callout = resolved["display_name"]
    if resolved["target_kind"] == "toolbar_overflow":
        default_callout = f"Click '>>' in {resolved['toolbar']} for {resolved['display_name']}"
    elif resolved["target_kind"] == "menu_bar_tab":
        default_callout = f"Menu: {resolved['menu_path']}"
    elif resolved["target_kind"] == "workbench_switcher_hint":
        default_callout = f"Switch workbench to {resolved['display_name'].replace('Workbench (', '').rstrip(')')}"

    callout = label.strip() or default_callout
    shown_overlay = _show_spotlight_overlay(
        main_win,
        resolved["rect"],
        callout,
        clamped_duration,
        qt_core,
        qt_widgets,
    )

    x, y, w, h = resolved["rect"]
    return {
        "highlighted": True,
        "overlay_rendered": shown_overlay,
        "target": target,
        "target_kind": resolved["target_kind"],
        "display_name": resolved["display_name"],
        "callout": callout,
        "duration_s": clamped_duration,
        "screen_rect": {"x": x, "y": y, "width": w, "height": h},
        **{k: v for k, v in resolved.items() if k not in ("rect", "target_kind", "display_name")},
    }
