"""Live FreeCAD Qt UI discovery, workbench, selection, and console handlers."""

from typing import Any

import FreeCAD

from handlers.common import get_ui_edit_state, require_gui, resolve_doc


# FreeCAD lazy-loads workbenches: commands like `PartDesign_Pad` or
# `TechDraw_PageDefault` are not registered in `Gui.Command.listAll()` until
# that workbench has been activated at least once in the current session.
# When a live search returns no matches, this table hints which workbench to
# activate so the AI never reports a real tool as "missing".
_LAZY_WORKBENCH_COMMAND_HINTS: list[dict[str, str]] = [
    {
        "keywords": "pad pocket thickness revolution groove loft sweep fillet chamfer hole mirror linear polar pattern draft body datum plane sketch partdesign",
        "workbench": "PartDesignWorkbench",
        "menu_name": "Part Design",
        "note": "PartDesign commands load when PartDesignWorkbench is activated.",
    },
    {
        "keywords": "sketch constraint coincident horizontal vertical tangent equal radius diameter trim construction",
        "workbench": "SketcherWorkbench",
        "menu_name": "Sketcher",
        "note": "Sketcher commands load when SketcherWorkbench is activated or a Sketch is edited.",
    },
    {
        "keywords": "box cube cylinder sphere cone torus fuse cut common boolean compound part",
        "workbench": "PartWorkbench",
        "menu_name": "Part",
        "note": "Part CSG commands load when PartWorkbench is activated.",
    },
    {
        "keywords": "techdraw drawing page template view projection group dimension section export svg pdf",
        "workbench": "TechDrawWorkbench",
        "menu_name": "TechDraw",
        "note": "TechDraw commands load when TechDrawWorkbench is activated.",
    },
]


def _qt_widgets_module() -> Any:
    """Import QtWidgets (PySide6 / FreeCAD PySide shim) or QtGui (PySide2 fallback)."""
    for mod_name in ("PySide", "PySide6", "PySide2"):
        try:
            pkg = __import__(mod_name, fromlist=["QtWidgets", "QtGui"])
            if hasattr(pkg, "QtWidgets"):
                return pkg.QtWidgets
            if hasattr(pkg, "QtGui"):
                return pkg.QtGui
        except (ImportError, AttributeError):
            continue
    return None


def _collect_menu_toolbar_locations(main_window: Any) -> dict[str, dict[str, list[str]]]:
    """Walk Qt's QMenuBar and QToolBars to map command names/labels to UI paths."""
    locations: dict[str, dict[str, list[str]]] = {}
    if main_window is None:
        return locations

    def _record(key: str, kind: str, path: str) -> None:
        if not key:
            return
        entry = locations.setdefault(key.lower(), {"menus": [], "toolbars": []})
        if path not in entry[kind]:
            entry[kind].append(path)

    # 1. Walk the top-level menu bar recursively to build paths like "Part Design -> Pad".
    try:
        menu_bar = main_window.menuBar()
        if menu_bar is not None:
            def _walk_menu(menu: Any, prefix: str) -> None:
                for act in menu.actions():
                    text = (act.text() or "").replace("&", "").strip()
                    sub_menu = act.menu() if hasattr(act, "menu") else None
                    if sub_menu is not None:
                        next_prefix = f"{prefix} -> {text}" if prefix else text
                        _walk_menu(sub_menu, next_prefix)
                    elif text:
                        path = f"{prefix} -> {text}" if prefix else text
                        _record(text, "menus", path)
                        if act.objectName():
                            _record(act.objectName(), "menus", path)

            for top_act in menu_bar.actions():
                sub_menu = top_act.menu() if hasattr(top_act, "menu") else None
                title = (top_act.text() or "").replace("&", "").strip()
                if sub_menu is not None:
                    _walk_menu(sub_menu, title)
    except Exception:
        pass

    # 2. Walk every QToolBar in the main window to collect toolbar titles.
    qt_widgets = _qt_widgets_module()
    if qt_widgets is not None and hasattr(qt_widgets, "QToolBar"):
        try:
            for toolbar in main_window.findChildren(qt_widgets.QToolBar):
                tb_title = (toolbar.windowTitle() or toolbar.objectName() or "Toolbar").strip()
                for act in toolbar.actions():
                    text = (act.text() or "").replace("&", "").strip()
                    if text:
                        _record(text, "toolbars", tb_title)
                    if act.objectName():
                        _record(act.objectName(), "toolbars", tb_title)
        except Exception:
            pass

    return locations


def _infer_disabled_reason(cmd_name: str, ui_state: dict[str, Any]) -> str:
    """Provide a beginner-friendly explanation for why a FreeCAD command is greyed out."""
    if ui_state.get("active_dialog"):
        editing = ui_state.get("in_edit") or "active task"
        return (
            f"A Task panel is currently open (editing: {editing}). "
            "Close/OK/Cancel it in the Tasks tab before running another command."
        )
    if FreeCAD.ActiveDocument is None:
        return "No document is open yet (use File -> New or Ctrl+N)."
    if cmd_name.startswith("PartDesign_"):
        doc = FreeCAD.ActiveDocument
        has_body = any(obj.TypeId == "PartDesign::Body" for obj in doc.Objects)
        if not has_body:
            return "No PartDesign::Body exists in the document yet (create a Body first)."
        return "Requires an active PartDesign::Body and/or a selected Sketch, Face, or Edge."
    if cmd_name.startswith("Sketcher_"):
        if not ui_state.get("in_edit"):
            return "Sketcher geometry/constraint tools are only enabled while editing a Sketch."
    if cmd_name.startswith("TechDraw_"):
        doc = FreeCAD.ActiveDocument
        has_page = any(obj.TypeId.startswith("TechDraw::DrawPage") for obj in doc.Objects)
        if not has_page and cmd_name != "TechDraw_PageDefault":
            return "Insert a TechDraw Page first, or select a 3D Body/Part to project."
    return "Required context or selection for this command is not currently active."


def _suggest_unloaded_workbenches(query: str, loaded_cmds: list[str]) -> list[dict[str, str]]:
    """Suggest workbenches to activate when a lazy-loaded command isn't registered yet."""
    words = [w for w in query.lower().split() if w]
    suggestions: list[dict[str, str]] = []
    for hint in _LAZY_WORKBENCH_COMMAND_HINTS:
        if any(w in hint["keywords"] for w in words):
            wb_prefix = hint["menu_name"].replace(" ", "") + "_"
            already_loaded = any(cmd.startswith(wb_prefix) for cmd in loaded_cmds)
            if not already_loaded:
                suggestions.append({
                    "workbench": hint["workbench"],
                    "menu_name": hint["menu_name"],
                    "note": hint["note"],
                })
    return suggestions


def find_ui_command(query: str, workbench_filter: str = "", limit: int = 15) -> dict[str, Any]:
    """Search live FreeCAD commands for menus, toolbars, shortcuts, and enabled state."""
    gui = require_gui()
    q = query.strip().lower()
    if not q:
        raise ValueError("query must be a non-empty string")

    wb_filter = workbench_filter.strip().lower()
    all_cmds = gui.Command.listAll()
    main_win = gui.getMainWindow()
    ui_locations = _collect_menu_toolbar_locations(main_win)
    ui_state = get_ui_edit_state()

    active_wb = None
    try:
        active_wb = gui.activeWorkbench().name()
    except Exception:
        pass

    matches: list[dict[str, Any]] = []
    for cmd_name in all_cmds:
        if wb_filter and wb_filter not in cmd_name.lower():
            continue

        cmd_obj = gui.Command.get(cmd_name)
        if cmd_obj is None:
            continue
        try:
            info = cmd_obj.getInfo() or {}
        except Exception:
            info = {}

        menu_text = str(info.get("menuText", "")).replace("&", "").strip()
        tooltip = str(info.get("toolTipText", "")).strip()
        status_tip = str(info.get("statusTip", "")).strip()
        shortcut = str(info.get("shortcut", "")).strip()

        haystack = " ".join([cmd_name, menu_text, tooltip, status_tip]).lower()
        if q not in haystack:
            continue

        try:
            is_active = bool(cmd_obj.isActive())
        except Exception:
            is_active = None

        loc = (
            ui_locations.get(cmd_name.lower())
            or ui_locations.get(menu_text.lower())
            or {"menus": [], "toolbars": []}
        )

        # Rank exact matches and items visible in menus/toolbars highest.
        score = 0
        if q == cmd_name.lower() or q == menu_text.lower():
            score += 100
        elif menu_text.lower().startswith(q) or cmd_name.lower().startswith(q):
            score += 50
        if loc["menus"] or loc["toolbars"]:
            score += 20

        matches.append({
            "command": cmd_name,
            "menu_text": menu_text or cmd_name,
            "tooltip": tooltip,
            "shortcut": shortcut or None,
            "menus": loc["menus"],
            "toolbars": loc["toolbars"],
            "is_active": is_active,
            "disabled_reason": _infer_disabled_reason(cmd_name, ui_state) if is_active is False else None,
            "_score": score,
        })

    matches.sort(key=lambda item: (-item["_score"], item["command"]))
    trimmed = matches[: max(1, min(limit, 50))]
    for item in trimmed:
        item.pop("_score", None)

    unloaded_hints = _suggest_unloaded_workbenches(q, all_cmds) if not trimmed else []

    return {
        "query": query,
        "active_workbench": active_wb,
        "ui_state": ui_state,
        "total_matches": len(matches),
        "matches": trimmed,
        "unloaded_workbench_hints": unloaded_hints,
    }


def list_workbenches() -> dict[str, Any]:
    """List all registered FreeCAD workbenches and highlight the active one."""
    gui = require_gui()
    active_name = None
    try:
        active_name = gui.activeWorkbench().name()
    except Exception:
        pass

    workbenches = []
    for name, wb in sorted(gui.listWorkbenches().items()):
        workbenches.append({
            "name": name,
            "menu_text": getattr(wb, "MenuText", name),
            "tooltip": getattr(wb, "ToolTip", ""),
            "is_active": name == active_name,
        })
    return {"active_workbench": active_name, "workbenches": workbenches}


def activate_workbench(name: str) -> dict[str, Any]:
    """Switch FreeCAD's active workbench (also loads its commands/menus into the UI)."""
    gui = require_gui()
    available = gui.listWorkbenches()
    if name not in available:
        lower = name.lower()
        for wb_key, wb_obj in available.items():
            menu_text = str(getattr(wb_obj, "MenuText", "")).lower()
            if wb_key.lower() == lower or menu_text == lower:
                name = wb_key
                break
        else:
            raise ValueError(f"Unknown workbench '{name}'. Available: {sorted(available.keys())}")

    gui.activateWorkbench(name)
    return {"active_workbench": name}


def get_selection() -> dict[str, Any]:
    """Return currently selected objects and sub-elements (Face1, Edge2, etc.)."""
    gui = require_gui()
    items = []
    for sel in gui.Selection.getSelectionEx():
        obj = sel.Object
        items.append({
            "document": sel.DocumentName,
            "object": obj.Name if obj else sel.ObjectName,
            "label": obj.Label if obj else None,
            "type_id": obj.TypeId if obj else None,
            "sub_elements": list(sel.SubElementNames) if sel.SubElementNames else [],
        })
    return {"count": len(items), "selection": items}


def set_selection(
    names: list[str],
    doc_name: str = "",
    clear_first: bool = True,
) -> dict[str, Any]:
    """Select/highlight objects or 'Obj.Face1' sub-elements in FreeCAD's viewport."""
    gui = require_gui()
    doc = resolve_doc(doc_name)
    if clear_first:
        gui.Selection.clearSelection()

    selected: list[str] = []
    for spec in names:
        if "." in spec:
            obj_name, sub_name = spec.split(".", 1)
            obj = doc.getObject(obj_name)
            if obj is None:
                raise ValueError(f"Object not found: {obj_name}")
            gui.Selection.addSelection(doc.Name, obj.Name, sub_name)
            selected.append(f"{obj.Name}.{sub_name}")
        else:
            obj = doc.getObject(spec)
            if obj is None:
                raise ValueError(f"Object not found: {spec}")
            gui.Selection.addSelection(obj)
            selected.append(obj.Name)

    return {"document": doc.Name, "selected": selected}


def get_console_output(lines: int = 100) -> dict[str, Any]:
    """Read recent lines from FreeCAD's Report view panel or Console log."""
    if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 1000:
        raise ValueError("lines must be an integer between 1 and 1000")

    output: str | None = None
    source = "unavailable"

    if getattr(FreeCAD, "GuiUp", False):
        gui = require_gui()
        qt_widgets = _qt_widgets_module()
        text_edit_cls = getattr(qt_widgets, "QTextEdit", None) if qt_widgets else None
        try:
            main_window = gui.getMainWindow()
            widget = (
                main_window.findChild(text_edit_cls, "Report view")
                if main_window is not None and text_edit_cls is not None
                else None
            )
        except Exception:
            widget = None
        if widget is not None:
            output = widget.toPlainText()
            source = "report_view"

    if output is None and hasattr(FreeCAD.Console, "GetLog"):
        output = FreeCAD.Console.GetLog()
        source = "console_log"

    if output is None:
        return {"available": False, "source": "unavailable", "lines": []}

    split_lines = output.splitlines()[-lines:]
    return {"available": True, "source": source, "lines": split_lines}
