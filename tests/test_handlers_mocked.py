"""Unit tests for FreeCAD addon handlers using lightweight FreeCAD/Qt fakes."""

import importlib
import math
import types

import pytest

from tests.conftest import FakeObject, FakeVector


def _reload_handlers():
    from handlers import common, document, execution, inspection, modeling, recompute, sketcher, ui_discovery, viewport

    for mod in (common, document, inspection, sketcher, recompute, ui_discovery, viewport, modeling, execution):
        importlib.reload(mod)
    return document, inspection, sketcher, recompute, ui_discovery, viewport, modeling, execution


def test_document_graph_tnp_and_nan_sanitization(fake_freecad):
    fc, gui = fake_freecad
    document, _, _, _, _, _, _, _ = _reload_handlers()

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
    _, _, sketcher, _, _, _, _, _ = _reload_handlers()

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
    _, _, _, _, ui_discovery, _, _, _ = _reload_handlers()

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
    _, _, _, _, _, viewport, _, _ = _reload_handlers()

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
    _, _, _, recompute, _, _, modeling, _ = _reload_handlers()

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
    _, _, _, _, ui_discovery, _, modeling, _ = _reload_handlers()

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
