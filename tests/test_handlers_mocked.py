"""Unit tests for FreeCAD addon handlers using lightweight FreeCAD/Qt fakes."""

import importlib
import math
import types

import pytest

from tests.conftest import FakeObject, FakeVector


def _reload_handlers():
    from handlers import (
        common,
        document,
        execution,
        inspection,
        modeling,
        recompute,
        sketcher,
        ui_discovery,
        ui_highlight,
        viewport,
    )

    for mod in (
        common,
        document,
        inspection,
        sketcher,
        recompute,
        ui_discovery,
        ui_highlight,
        viewport,
        modeling,
        execution,
    ):
        importlib.reload(mod)
    return document, inspection, sketcher, recompute, ui_discovery, ui_highlight, viewport, modeling, execution


def test_document_graph_tnp_and_nan_sanitization(fake_freecad):
    fc, gui = fake_freecad
    document, _, _, _, _, _, _, _, _ = _reload_handlers()

    doc = fc.ActiveDocument
    pad = doc.addObject("PartDesign::Pad", "Pad")
    sketch = doc.addObject("Sketcher::SketchObject", "Sketch001")
    sketch.AttachmentSupport = [(pad, "Face3")]
    sketch.PropertiesList.append("BrokenFloat")
    sketch.BrokenFloat = float("nan")
    gui.Control._active = True

    compact = document.get_document_graph()
    assert compact["ui_state"]["active_dialog"] is True
    assert len(compact["tnp_warnings"]) == 1
    assert "Face3" in compact["tnp_warnings"][0]["warning"]
    # Compact mode omits arbitrary property payloads by default to save context tokens.
    assert "BrokenFloat" not in compact["objects"]["Sketch001"].get("properties", {})

    full = document.get_document_graph(include_properties=True)
    assert full["objects"]["Sketch001"]["properties"]["BrokenFloat"] is None
    assert math.isfinite(1.0)


def test_sketch_diagnostics_uses_dof_not_solve_status(fake_freecad):
    fc, _ = fake_freecad
    _, _, sketcher, _, _, _, _, _, _ = _reload_handlers()

    doc = fc.ActiveDocument
    sk = doc.addObject("Sketcher::SketchObject", "Sketch")
    sk.ConstraintCount = 2
    sk.GeometryCount = 1
    sk.ExternalGeometryCount = 0
    sk.DoF = 3
    sk.FullyConstrained = False
    # solve() returns 0 (success status code), NOT 0 degrees of freedom
    sk.solve = lambda: 0
    sk.getRedundantConstraints = lambda: []
    sk.getConflictingConstraints = lambda: [1]
    sk.Constraints = [
        types.SimpleNamespace(
            Type="Horizontal", Name="", First=0, FirstPos=0, Second=-1, SecondPos=0, Third=-1, ThirdPos=0,
            Driving=True, InVirtualSpace=False, IsActive=True,
        ),
        types.SimpleNamespace(
            Type="Distance", Name="Width", First=0, FirstPos=0, Second=-1, SecondPos=0, Third=-1, ThirdPos=0,
            Driving=True, InVirtualSpace=False, IsActive=True, Value=25.0,
        ),
    ]
    sk.Geometry = [
        types.SimpleNamespace(StartPoint=FakeVector(0, 0, 0), EndPoint=FakeVector(25, 0, 0), Construction=False)
    ]

    diag = sketcher.get_sketch_diagnostics("Sketch")
    assert diag["degrees_of_freedom"] == 3
    assert diag["fully_constrained"] is False
    assert diag["conflicting_indices"] == [1]
    assert diag["constraints"][1]["is_conflicting"] is True
    assert any("3 remaining Degree(s) of Freedom" in h for h in diag["tutor_hints"])


def test_ui_discovery_commands_and_console(fake_freecad):
    fc, gui = fake_freecad
    _, _, _, _, ui_discovery, _, _, _, _ = _reload_handlers()

    class FakeCmd:
        def __init__(self, info, active):
            self._info = info
            self._active = active

        def getInfo(self):
            return self._info

        def isActive(self):
            return self._active

    gui.Command.cmds["PartDesign_Pad"] = FakeCmd(
        {"menuText": "&Pad", "toolTipText": "Pad a selected sketch", "shortcut": "P"},
        active=False,
    )
    gui.Command.cmds["Part_Box"] = FakeCmd(
        {"menuText": "Cube", "toolTipText": "Create a box", "shortcut": ""},
        active=True,
    )

    pad_act = types.SimpleNamespace(
        text=lambda: "&Pad",
        objectName=lambda: "PartDesign_Pad",
        menu=lambda: None,
    )
    pd_menu = types.SimpleNamespace(
        text=lambda: "&Part Design",
        menu=lambda: types.SimpleNamespace(actions=lambda: [pad_act]),
    )
    gui._main_window = types.SimpleNamespace(
        menuBar=lambda: types.SimpleNamespace(actions=lambda: [pd_menu]),
        findChildren=lambda cls: [],
        findChild=lambda cls, name: None,
    )

    res = ui_discovery.find_ui_command("pad")
    assert res["total_matches"] == 1
    match = res["matches"][0]
    assert match["command"] == "PartDesign_Pad"
    assert match["is_active"] is False
    assert "Part Design -> Pad" in match["menus"]
    assert "PartDesign::Body" in match["disabled_reason"]

    console = ui_discovery.get_console_output(lines=5)
    assert console["available"] is True
    assert console["source"] == "console_log"
    assert len(console["lines"]) == 2


def test_viewport_blocks_techdraw_tab_and_caps_size(fake_freecad, tmp_path):
    _, gui = fake_freecad
    _, _, _, _, _, _, viewport, _, _ = _reload_handlers()

    # Active tab is a TechDraw page (no saveImage attribute)
    gui._active_view = types.SimpleNamespace(getObjectInfo=lambda: None)
    with pytest.raises(RuntimeError, match="not a 3D viewport"):
        viewport.get_view()

    # Active tab is a 4K 3D view -> auto-scaled down to 1024px longest edge
    def fake_save(path, w, h, bg):
        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfakepng")

    gui._active_view = types.SimpleNamespace(
        saveImage=fake_save,
        getSize=lambda: (3840, 2160),
        viewIsometric=lambda: None,
        fitAll=lambda: None,
    )
    shot = viewport.get_view(view_name="Isometric")
    assert shot["width"] == 1024
    assert shot["height"] == 576
    assert shot["format"] == "png"


def test_modeling_rolls_back_invalid_pad_and_blocks_during_dialog(fake_freecad):
    fc, gui = fake_freecad
    _, _, _, recompute, _, _, _, modeling, _ = _reload_handlers()

    doc = fc.ActiveDocument
    body = FakeObject("Body", "PartDesign::Body")
    body.Group = []
    sk = FakeObject("Sketch", "Sketcher::SketchObject")
    body.Group.append(sk)

    def new_obj(type_id: str, name: str):
        feat = FakeObject(name, type_id, valid=False)
        feat._error_desc = "Result has multiple solids"
        doc.Objects.append(feat)
        body.Group.append(feat)
        return feat

    body.newObject = new_obj
    doc.Objects.extend([body, sk])

    with pytest.raises(RuntimeError, match="rolled back"):
        modeling.pad_sketch("Sketch", length=10.0)
    assert doc.transactions[-1][0] == "abort"

    gui.Control._active = True
    with pytest.raises(RuntimeError, match="Task panel is open"):
        recompute.tracked_recompute()


def test_part_primitive_and_pocket_through_all(fake_freecad):
    fc, _ = fake_freecad
    _, _, _, _, ui_discovery, _, _, modeling, _ = _reload_handlers()

    box_res = modeling.create_part_primitive("box", name="TutorBox", dimensions={"length": 20, "width": 10, "height": 5})
    assert box_res["type_id"] == "Part::Box"
    box_obj = fc.ActiveDocument.getObject("TutorBox")
    assert box_obj.Length == 20.0
    assert box_obj.Width == 10.0
    assert box_obj.Height == 5.0

    # Pocket through_all maps to FreeCAD's ThroughAll enum value.
    body = FakeObject("Body", "PartDesign::Body")
    body.Group = []
    sk = FakeObject("PocketSketch", "Sketcher::SketchObject")
    body.Group.append(sk)

    def new_obj(type_id: str, name: str):
        feat = FakeObject(name, type_id, valid=True)
        fc.ActiveDocument.Objects.append(feat)
        body.Group.append(feat)
        return feat

    body.newObject = new_obj
    fc.ActiveDocument.Objects.extend([body, sk])

    pocket_res = modeling.pocket_sketch("PocketSketch", through_all=True)
    assert pocket_res["through_all"] is True
    pocket_obj = fc.ActiveDocument.getObject(pocket_res["name"])
    assert pocket_obj.Type == "ThroughAll"

    # Unloaded workbench hint appears when searching for commands not yet registered.
    td_hint = ui_discovery.find_ui_command("techdraw")
    assert len(td_hint["unloaded_workbench_hints"]) >= 1


def test_ui_highlight_toolbar_panel_menu_and_clear(fake_freecad, monkeypatch):
    _, gui = fake_freecad
    _, _, _, _, _, ui_highlight, _, _, _ = _reload_handlers()

    class _Rect:
        def __init__(self, x: int, y: int, w: int, h: int):
            self._x, self._y, self._w, self._h = x, y, w, h

        def x(self):
            return self._x

        def y(self):
            return self._y

        def width(self):
            return self._w

        def height(self):
            return self._h

        def center(self):
            return (self._x + self._w // 2, self._y + self._h // 2)

        def contains(self, pt):
            return self._x <= pt[0] <= self._x + self._w and self._y <= pt[1] <= self._y + self._h

    class _FakeWidget:
        def __init__(self, name: str, rect: _Rect, visible: bool = True, title: str = "", local_rect: _Rect | None = None):
            self._name = name
            self._rect = rect
            self._local_rect = local_rect or rect
            self._visible = visible
            self._title = title or name

        def objectName(self):
            return self._name

        def windowTitle(self):
            return self._title

        def isVisible(self):
            return self._visible

        def x(self):
            return self._rect.x()

        def y(self):
            return self._rect.y()

        def width(self):
            return self._rect.width()

        def height(self):
            return self._rect.height()

        def geometry(self):
            return self._local_rect

        def rect(self):
            return _Rect(0, 0, self._rect.width(), self._rect.height())

    class _FakeDock(_FakeWidget):
        pass

    class _FakeCombo(_FakeWidget):
        def parentWidget(self):
            return None

    class _FakeToolButton(_FakeWidget):
        pass

    class _FakeToolBar(_FakeWidget):
        def __init__(self, name: str, rect: _Rect, actions_map: dict, overflow_btn=None):
            super().__init__(name, rect, visible=True, title=name)
            self._actions_map = actions_map
            self._overflow_btn = overflow_btn

        def actions(self):
            return list(self._actions_map.keys())

        def widgetForAction(self, act):
            return self._actions_map.get(act)

        def findChildren(self, cls):
            if cls is _FakeToolButton and self._overflow_btn is not None:
                return [self._overflow_btn]
            return []

    class _FakeAction:
        def __init__(self, obj_name: str, text: str):
            self._obj_name = obj_name
            self._text = text

        def objectName(self):
            return self._obj_name

        def text(self):
            return self._text

    pad_act = _FakeAction("PartDesign_Pad", "&Pad")
    pad_btn = _FakeToolButton("PartDesign_Pad", _Rect(40, 80, 28, 28), local_rect=_Rect(40, 4, 28, 28))
    fillet_act = _FakeAction("PartDesign_Fillet", "Fillet")
    # Simulate Fillet button clipped past toolbar width (center x=354 > toolbar width 200) -> overflow chevron
    fillet_btn = _FakeToolButton("PartDesign_Fillet", _Rect(340, 80, 28, 28), local_rect=_Rect(340, 4, 28, 28))
    ext_btn = _FakeToolButton("qt_toolbutton_ext", _Rect(185, 80, 14, 28))
    tb = _FakeToolBar("Part Design Modeling Features", _Rect(0, 75, 200, 36), {pad_act: pad_btn, fillet_act: fillet_btn}, overflow_btn=ext_btn)
    model_dock = _FakeDock("Model", _Rect(0, 120, 280, 500), title="Model")
    wb_combo = _FakeCombo("WorkbenchComboBox", _Rect(300, 42, 160, 26))

    class _FakeMenuBar:
        def __init__(self, top_actions):
            self._top_actions = top_actions

        def actions(self):
            return self._top_actions

        def actionGeometry(self, _act):
            return _Rect(90, 22, 65, 22)

    menu_only_act = types.SimpleNamespace(objectName=lambda: "Std_Measure", text=lambda: "Measure", menu=lambda: None)
    view_menu_act = types.SimpleNamespace(
        text=lambda: "&View",
        menu=lambda: types.SimpleNamespace(actions=lambda: [menu_only_act]),
    )

    def _find_children(cls):
        if cls is _FakeToolBar:
            return [tb]
        if cls is _FakeDock:
            return [model_dock]
        if cls is _FakeCombo:
            return [wb_combo]
        return []

    gui._main_window = types.SimpleNamespace(
        menuBar=lambda: _FakeMenuBar([view_menu_act]),
        findChildren=_find_children,
    )

    fake_qt_widgets = types.SimpleNamespace(
        QToolBar=_FakeToolBar,
        QToolButton=_FakeToolButton,
        QDockWidget=_FakeDock,
        QWidget=_FakeDock,
        QComboBox=_FakeCombo,
    )
    monkeypatch.setattr(ui_highlight, "_qt_modules", lambda: (None, None, fake_qt_widgets))

    # 1. Exact visible toolbar button
    res_pad = ui_highlight.highlight_ui_element("PartDesign_Pad")
    assert res_pad["highlighted"] is True
    assert res_pad["target_kind"] == "toolbar_button"
    assert res_pad["screen_rect"] == {"x": 40, "y": 80, "width": 28, "height": 28}

    # 2. Clipped toolbar button points to '>>' overflow button
    res_fillet = ui_highlight.highlight_ui_element("Fillet")
    assert res_fillet["highlighted"] is True
    assert res_fillet["target_kind"] == "toolbar_overflow"
    assert res_fillet["screen_rect"] == {"x": 185, "y": 80, "width": 14, "height": 28}

    # 3. Named panel
    res_model = ui_highlight.highlight_ui_element("Model")
    assert res_model["highlighted"] is True
    assert res_model["target_kind"] == "panel"
    assert res_model["display_name"] == "Model"

    # 4. Menu-only item points to top-level menu tab
    res_menu = ui_highlight.highlight_ui_element("Std_Measure")
    assert res_menu["highlighted"] is True
    assert res_menu["target_kind"] == "menu_bar_tab"
    assert "View -> Measure" in res_menu["callout"]

    # 5. Unloaded workbench command highlights the Workbench switcher
    res_td = ui_highlight.highlight_ui_element("techdraw")
    assert res_td["highlighted"] is True
    assert res_td["target_kind"] == "workbench_switcher_hint"
    assert res_td["suggested_workbench"] == "TechDrawWorkbench"

    # 6. Clear flag cleans up
    cleared = ui_highlight.highlight_ui_element(clear=True)
    assert cleared["cleared"] is True


def test_list_documents_consistent_dict_shape(fake_freecad):
    fc, _ = fake_freecad
    document, _, _, _, _, _, _, _, _ = _reload_handlers()

    # 1 document -> still wrapped in {active_document, count, documents}
    one = document.list_documents()
    assert isinstance(one, dict)
    assert one["count"] == 1
    assert one["active_document"] == "CoursePart"
    assert len(one["documents"]) == 1

    # 2 documents
    fc.newDocument("TutorDemo")
    two = document.list_documents()
    assert two["count"] == 2
    assert two["active_document"] == "TutorDemo"
    assert len(two["documents"]) == 2

    # 0 documents
    fc.listDocuments = lambda: {}
    fc.ActiveDocument = None
    zero = document.list_documents()
    assert zero == {"active_document": None, "count": 0, "documents": []}


def test_edit_sketch_auto_constrain_polyline_and_constraint_updates(fake_freecad, monkeypatch: pytest.MonkeyPatch):
    import sys

    fc, _ = fake_freecad
    _, _, sketcher, _, _, _, _, modeling, _ = _reload_handlers()

    class FakeLineSegment:
        def __init__(self, p1, p2):
            self.StartPoint = p1
            self.EndPoint = p2

    class FakeCircle:
        def __init__(self, center, normal, radius):
            self.Center = center
            self.Radius = radius

    class FakeArcOfCircle:
        def __init__(self, circle, a1, a2):
            self.Center = circle.Center
            self.Radius = circle.Radius
            self.StartPoint = FakeVector(circle.Center.x + circle.Radius, circle.Center.y, 0.0)
            self.EndPoint = FakeVector(circle.Center.x, circle.Center.y + circle.Radius, 0.0)

    class FakeConstraint:
        def __init__(self, *args):
            self.Type = args[0]
            self.Name = ""
            self.First = args[1] if len(args) > 1 else -1
            self.FirstPos = args[2] if len(args) > 2 else 0
            self.Second = args[3] if len(args) > 3 else -1
            self.SecondPos = args[4] if len(args) > 4 else 0
            self.Third = -1
            self.ThirdPos = 0
            self.Driving = True
            self.InVirtualSpace = False
            self.IsActive = True
            if self.Type in ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle"):
                self.Value = args[-1]

    fake_part = types.ModuleType("Part")
    fake_part.LineSegment = FakeLineSegment
    fake_part.Circle = FakeCircle
    fake_part.ArcOfCircle = FakeArcOfCircle
    fake_sketcher = types.ModuleType("Sketcher")
    fake_sketcher.Constraint = FakeConstraint
    monkeypatch.setitem(sys.modules, "Part", fake_part)
    monkeypatch.setitem(sys.modules, "Sketcher", fake_sketcher)

    doc = fc.ActiveDocument
    sk = doc.addObject("Sketcher::SketchObject", "AutoSk")
    sk.Geometry = []
    sk.Constraints = []
    sk.DoF = 0
    sk.FullyConstrained = True

    def _add_geo(g, construction=False):
        g.Construction = construction
        sk.Geometry.append(g)
        sk.GeometryCount = len(sk.Geometry)
        return len(sk.Geometry) - 1

    def _add_con(c):
        sk.Constraints.append(c)
        sk.ConstraintCount = len(sk.Constraints)
        return len(sk.Constraints) - 1

    sk.GeometryCount = 0
    sk.ConstraintCount = 0
    sk.addGeometry = _add_geo
    sk.addConstraint = _add_con
    sk.renameConstraint = lambda idx, name: setattr(sk.Constraints[idx], "Name", name)
    sk.setDatum = lambda idx, val: setattr(sk.Constraints[idx], "Value", val)
    sk.setDriving = lambda idx, state: setattr(sk.Constraints[idx], "Driving", state)
    sk.setActive = lambda idx, state: setattr(sk.Constraints[idx], "IsActive", state)
    sk.delConstraints = lambda indices, update=True: [sk.Constraints.pop(i) for i in sorted(indices, reverse=True)]
    sk.delGeometries = lambda indices: [sk.Geometry.pop(i) for i in sorted(indices, reverse=True)]

    # Closed rectangle drawn from 4 separate lines with auto_constrain=True
    res = modeling.edit_sketch(
        "AutoSk",
        geometries=[
            {"type": "line", "start": [0, 0], "end": [20, 0]},
            {"type": "line", "start": [20, 0], "end": [20, 10]},
            {"type": "line", "start": [20, 10], "end": [0, 10]},
            {"type": "line", "start": [0, 10], "end": [0, 0]},
        ],
        constraints=[{"type": "DistanceX", "first": 0, "value": 20.0, "name": "Width"}],
        auto_constrain=True,
    )
    types_added = [c["type"] for c in res["auto_constraints_added"]]
    assert types_added.count("Horizontal") == 2
    assert types_added.count("Vertical") == 2
    assert types_added.count("Coincident") == 4
    assert res["fully_constrained"] is True

    # Modify DistanceX by name, toggle reference, and delete an extra constraint
    modeling.edit_sketch(
        "AutoSk",
        update_constraints=[{"name": "Width", "value": 35.0, "driving": False, "new_name": "RefWidth"}],
        delete_constraint_indices=[0],
    )
    width_cons = [c for c in sk.Constraints if c.Name == "RefWidth"]
    assert len(width_cons) == 1
    assert width_cons[0].Value == 35.0
    assert width_cons[0].Driving is False

    # Diagnostics OpenVertices + missing constraints
    sk.OpenVertices = [FakeVector(20.0, 0.0, 0.0)]
    sk.detectMissingPointOnPointConstraints = lambda: 1
    sk.detectMissingVerticalHorizontalConstraints = lambda: 2
    sk.detectMissingEqualityConstraints = lambda: 0
    diag = sketcher.get_sketch_diagnostics("AutoSk")
    assert diag["open_vertex_count"] == 1
    assert diag["missing_coincident_count"] == 1
    assert diag["missing_hv_count"] == 2
    assert any("open vertex" in h.lower() for h in diag["tutor_hints"])


def test_error_kb_translations():
    from handlers.error_kb import explain_freecad_error

    fillet_exp = explain_freecad_error("Fillet not possible on selected shapes")
    assert fillet_exp["matched"] is True
    assert fillet_exp["code"] == "FILLET_CHAMFER_FAILED"
    assert fillet_exp["how_to_fix"]

    multi_exp = explain_freecad_error("Result has multiple solids. This is not supported at this time.")
    assert multi_exp["code"] == "MULTIPLE_SOLIDS"

    unknown = explain_freecad_error("Something completely unexpected")
    assert unknown["matched"] is False
    assert unknown["code"] == "UNKNOWN_FREECAD_ERROR"
