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


def test_camera_set_view_and_window_capture(fake_freecad, monkeypatch: pytest.MonkeyPatch):
    fc, gui = fake_freecad
    _, _, _, _, _, _, viewport, _, _ = _reload_handlers()

    called: list[str] = []
    fake_view = types.SimpleNamespace(
        viewIsometric=lambda: called.append("iso"),
        viewTop=lambda: called.append("top"),
        fitAll=lambda: called.append("fit"),
        getSize=lambda: (1600, 1200),
        saveImage=lambda path, w, h, bg: None,
    )
    gui._active_view = fake_view
    gui.runCommand = lambda name: called.append(name)

    # Empty selection + zoom_to_selection raises clearly
    with pytest.raises(ValueError, match="zoom_to_selection"):
        viewport.set_view(view_name="Top", zoom_to_selection=True)

    gui.Selection.addSelection("CoursePart", "Pad", "")
    res = viewport.set_view(view_name="iso", zoom_to_selection=True)
    assert res["view_name"] == "Isometric"
    assert "iso" in called
    assert "Std_ViewFitSelection" in called

    # Window screenshot path with fake Qt
    class _FakeByteArray:
        def __init__(self):
            self.data = b""

        def __bytes__(self):
            return b"\x89PNG\r\n\x1a\nfakewin"

    class _FakeQBuffer:
        def __init__(self, ba):
            self.ba = ba

        def open(self, mode):
            return True

        def close(self):
            return None

    class _FakePixmap:
        def __init__(self, w=1600, h=1000):
            self._w, self._h = w, h

        def isNull(self):
            return False

        def width(self):
            return self._w

        def height(self):
            return self._h

        def scaled(self, w, h, aspect, mode):
            return _FakePixmap(w, h)

        def save(self, qbuf, fmt):
            return True

    fake_qt_core = types.SimpleNamespace(
        QByteArray=_FakeByteArray,
        QBuffer=_FakeQBuffer,
        QIODevice=types.SimpleNamespace(WriteOnly=1),
        Qt=types.SimpleNamespace(KeepAspectRatio=1, SmoothTransformation=1),
    )
    fake_qt_gui = types.SimpleNamespace(
        QGuiApplication=types.SimpleNamespace(
            primaryScreen=lambda: types.SimpleNamespace(grabWindow=lambda wid: _FakePixmap())
        )
    )
    gui._main_window = types.SimpleNamespace(winId=lambda: 123, grab=lambda: _FakePixmap())
    monkeypatch.setattr(viewport, "_import_qt_gui", lambda: (fake_qt_core, fake_qt_gui, None))

    win_shot = viewport.get_view(view_name="Current", capture_scope="window")
    assert win_shot["capture_scope"] == "window"
    assert win_shot["width"] == 1280
    assert win_shot["format"] == "png"


def test_document_lifecycle_save_open_close_export_and_body_tips(
    fake_freecad, monkeypatch: pytest.MonkeyPatch, tmp_path
):
    import sys

    fc, _ = fake_freecad
    document, _, _, _, _, _, _, _, _ = _reload_handlers()

    doc = fc.ActiveDocument
    doc._saved = False
    body = doc.addObject("PartDesign::Body", "Body")
    pad = doc.addObject("PartDesign::Pad", "Pad")
    pad.Shape = types.SimpleNamespace(isNull=lambda: False)
    body.Tip = pad
    body.Shape = types.SimpleNamespace(isNull=lambda: False)

    # Error object includes error_explanation, and body_tips records Pad
    broken = doc.addObject("PartDesign::Fillet", "Fillet")
    broken._valid = False
    broken.State = ["Invalid"]
    broken._error_desc = "Fillet not possible on selected shapes"
    graph = document.get_document_graph()
    assert graph["body_tips"] == {"Body": "Pad"}
    assert graph["objects"]["Fillet"]["error_explanation"]["code"] == "FILLET_CHAMFER_FAILED"

    # Closing unsaved doc without confirmation fails
    with pytest.raises(RuntimeError, match="unsaved changes"):
        document.close_document()

    # SaveAs adds .FCStd and clears modified flag
    fc_path = tmp_path / "demo_part"
    doc.saveAs = lambda p: (setattr(doc, "FileName", p), setattr(doc, "_saved", True))
    doc.save = lambda: setattr(doc, "_saved", True)
    saved = document.save_document(path=str(fc_path))
    assert saved["file_name"].endswith(".FCStd")

    # Export STEP & STL via fake Part / Mesh
    exported_calls: list[tuple[str, list[str], str]] = []
    fake_part = types.ModuleType("Part")
    fake_part.export = lambda objs, p: (
        exported_calls.append(("step", [o.Name for o in objs], p)),
        open(p, "wb").write(b"ISO-10303-21;"),
    )
    fake_mesh = types.ModuleType("Mesh")
    fake_mesh.export = lambda objs, p: (
        exported_calls.append(("stl", [o.Name for o in objs], p)),
        open(p, "wb").write(b"solid"),
    )
    monkeypatch.setitem(sys.modules, "Part", fake_part)
    monkeypatch.setitem(sys.modules, "Mesh", fake_mesh)

    step_res = document.export_document(str(tmp_path / "out.step"))
    stl_res = document.export_document(str(tmp_path / "out.stl"))
    assert step_res["object_names"] == ["Body"]
    assert stl_res["format"] == "stl"
    assert step_res["file_size_bytes"] > 0

    # Safe close after save + re-open
    closed_docs: list[str] = []
    fc.closeDocument = lambda name: (closed_docs.append(name), fc.listDocuments().pop(name, None))
    close_res = document.close_document()
    assert close_res["closed"] is True
    assert closed_docs == ["CoursePart"]

    real_fcstd = tmp_path / "demo_part.FCStd"
    real_fcstd.write_bytes(b"zip")
    fc.openDocument = lambda p: fc.newDocument("CoursePart")
    opened = document.open_document(str(real_fcstd))
    assert opened["opened"] is True
    assert opened["name"] == "CoursePart"


def test_find_edges_faces_measure_and_deep_check(fake_freecad):
    fc, gui = fake_freecad
    _, inspection, _, _, _, _, _, _, _ = _reload_handlers()

    doc = fc.ActiveDocument
    pad = doc.addObject("PartDesign::Pad", "Pad")

    class _FakeSurface:
        def __init__(self, axis):
            self.Axis = FakeVector(*axis)
            self.Position = FakeVector(0, 0, 0)

    class _Plane(_FakeSurface):
        pass

    _Plane.__name__ = "Plane"

    class _LineCurve:
        pass

    _LineCurve.__name__ = "Line"

    class _Edge:
        def __init__(self, p1, p2):
            self.Vertexes = [types.SimpleNamespace(Point=FakeVector(*p1)), types.SimpleNamespace(Point=FakeVector(*p2))]
            dx, dy, dz = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
            self.Length = math.sqrt(dx * dx + dy * dy + dz * dz)
            self.CenterOfMass = FakeVector((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2, (p1[2] + p2[2]) / 2)
            self.Curve = _LineCurve()

    class _Face:
        def __init__(self, normal, area, com):
            self.Surface = _Plane(normal)
            self.Area = area
            self.CenterOfMass = FakeVector(*com)

    v_edge = _Edge((0, 0, 0), (0, 0, 15))
    h_edge = _Edge((0, 0, 0), (40, 0, 0))
    top_face = _Face((0, 0, 1), 800.0, (20, 10, 15))
    side_face = _Face((1, 0, 0), 150.0, (40, 10, 7.5))

    pad.Shape = types.SimpleNamespace(
        ShapeType="Solid",
        isValid=lambda: True,
        isNull=lambda: False,
        Solids=[object()],
        Shells=[object()],
        Faces=[top_face, side_face],
        Edges=[v_edge, h_edge],
        Vertexes=[object()] * 8,
        Volume=12000.0,
        Area=1900.0,
        BoundBox=types.SimpleNamespace(XMin=0, XMax=40, YMin=0, YMax=20, ZMin=0, ZMax=15, XLength=40, YLength=20, ZLength=15),
        check=lambda bop: (_ for _ in ()).throw(ValueError("BRepCheck_SelfIntersectingWire")) if bop else None,
        getElement=lambda sub: types.SimpleNamespace(
            distToShape=lambda other: (15.0, [(FakeVector(0, 0, 0), FakeVector(0, 0, 15))], [])
        ),
        distToShape=lambda other: (15.0, [(FakeVector(0, 0, 0), FakeVector(0, 0, 15))], []),
    )

    z_edges = inspection.find_edges("Pad", direction="Z", length=15.0, select_matches=True)
    assert z_edges["edge_names"] == ["Edge1"]
    assert z_edges["selected_in_gui"] is True

    top_faces = inspection.find_faces("Pad", normal="+Z", min_area=500.0)
    assert top_faces["face_names"] == ["Face1"]

    dist = inspection.measure_distance("Pad", "Face1", "Pad", "Face2")
    assert dist["min_distance"] == 15.0
    assert dist["delta"] == [0.0, 0.0, 15.0]

    deep = inspection.analyze_shape("Pad", deep_check=True)
    assert deep["bop_check_passed"] is False
    assert deep["error_explanation"]["code"] == "UNKNOWN_FREECAD_ERROR"


def test_partdesign_varset_datum_property_and_techdraw(fake_freecad, monkeypatch: pytest.MonkeyPatch, tmp_path):
    import sys

    fc, gui = fake_freecad
    _reload_handlers()
    from handlers import partdesign, techdraw

    importlib.reload(partdesign)
    importlib.reload(techdraw)

    monkeypatch.setitem(sys.modules, "PartDesign", types.ModuleType("PartDesign"))
    fake_td_gui = types.ModuleType("TechDrawGui")
    exported_pdf: list[str] = []
    fake_td_gui.exportPageAsPdf = lambda page, p: (exported_pdf.append(p), open(p, "wb").write(b"%PDF-1.4"))
    fake_td_gui.exportPageAsSvg = lambda page, p: open(p, "wb").write(b"<svg/>")
    monkeypatch.setitem(sys.modules, "TechDrawGui", fake_td_gui)

    doc = fc.ActiveDocument
    body = doc.addObject("PartDesign::Body", "Body")
    x_ax = types.SimpleNamespace(Name="X_Axis001", Role="X_Axis")
    yz_pl = types.SimpleNamespace(Name="YZ_Plane001", Role="YZ_Plane")
    xy_pl = types.SimpleNamespace(Name="XY_Plane001", Role="XY_Plane")
    z_ax = types.SimpleNamespace(Name="Z_Axis001", Role="Z_Axis")
    body.Origin = types.SimpleNamespace(OriginFeatures=[x_ax, yz_pl, xy_pl, z_ax], getObject=lambda n: None)

    def _new_obj(type_id, name):
        o = FakeObject(name, type_id)
        if type_id == "PartDesign::Hole":
            o.DepthType = "Dimension"
            o.HoleCutType = "None"
            o.ModelThread = False
            o.Reversed = False
        doc.Objects.append(o)
        body.Group = list(getattr(body, "Group", [])) + [o]
        return o

    body.Group = []
    body.newObject = _new_obj

    sketch = body.newObject("Sketcher::SketchObject", "Sk")

    # Multi-body Origin Role resolution works for DatumPlane, Thickness, Hole, Revolution, Mirror, Linear/PolarPattern
    datum = partdesign.create_datum_plane("Body", attachment_support="XY_Plane", offset=[0, 0, 10])
    assert datum["attachment_support"] == "XY_Plane001"

    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Length = 10.0
    pad.PropertiesList.append("Length")
    pad.setExpression = lambda prop, expr: setattr(pad, "_expr", (prop, expr))
    pad.ViewObject = types.SimpleNamespace(Visibility=True, Transparency=0, ShapeColor=(0.8, 0.8, 0.8))

    vs_res = partdesign.create_varset("Params", variables=[{"name": "Wall", "type": "Length", "value": 4.0}])
    vs = doc.getObject(vs_res["name"])
    vs.addProperty = lambda full_t, p_name, grp: (vs.PropertiesList.append(p_name), setattr(vs, p_name, 0.0))
    vs.Wall = 4.0
    vs.PropertiesList.append("Wall")

    # VarSet variable creation via set_property + expression binding on Pad.Length
    partdesign.set_property("Params", "Height", value=25.0, property_type="Length")
    assert vs.Height == 25.0
    set_res = partdesign.set_property("Pad", "Length", value=25.0, expression="Params.Height")
    assert set_res["value"] == 25.0
    assert pad._expr == ("Length", "Params.Height")

    app_res = partdesign.set_appearance("Pad", visible=True, transparency=40, color=[255, 0, 0])
    assert app_res["transparency"] == 40

    thick = partdesign.partdesign_thickness("Pad", faces=["Face6"], thickness=2.0)
    assert doc.getObject(thick["name"]).Reversed is True

    hole = partdesign.partdesign_hole("Sk", diameter=6.0, through_all=True)
    assert doc.getObject(hole["name"]).DepthType == "ThroughAll"

    rev = partdesign.partdesign_revolution("Sk", angle=180.0, axis="V_Axis")
    assert doc.getObject(rev["name"]).ReferenceAxis == (sketch, ["V_Axis"])

    mir = partdesign.partdesign_mirrored(["Pad"], mirror_plane="YZ_Plane")
    assert doc.getObject(mir["name"]).MirrorPlane == (yz_pl, [""])

    lp = partdesign.partdesign_linear_pattern(["Pad"], direction="X_Axis", occurrences=3, length=60.0)
    assert doc.getObject(lp["name"]).Direction == (x_ax, [""])

    pp = partdesign.partdesign_polar_pattern(["Pad"], axis="Z_Axis", occurrences=4)
    assert doc.getObject(pp["name"]).Axis == (z_ax, [""])

    # TechDraw dimension, section, PDF export
    page = doc.addObject("TechDraw::DrawPage", "Page")
    page.addView = lambda v: None
    view = doc.addObject("TechDraw::DrawViewPart", "View")
    view.Source = [body]
    view.Scale = 1.0

    dim_res = techdraw.add_techdraw_dimension("Page", "View", ["Edge0"], dim_type="Distance")
    assert doc.getObject(dim_res["name"]).Type == "Distance"

    sec_res = techdraw.add_techdraw_section("Page", "View", section_normal=[0, 1, 0])
    assert doc.getObject(sec_res["name"]).BaseView is view

    pdf_out = tmp_path / "sheet.pdf"
    pdf_res = techdraw.export_techdraw_page("Page", str(pdf_out))
    assert pdf_res["exported"] is True
    assert pdf_res["file_size_bytes"] > 0
