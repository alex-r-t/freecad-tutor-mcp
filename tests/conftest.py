"""Shared pytest fixtures and lightweight FreeCAD/FreeCADGui fakes."""

import sys
import types
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
ADDON_DIR = ROOT / "freecad_addon"
SRC_DIR = ROOT / "src"

for path in (str(ADDON_DIR), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)


class FakeVector:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0):
        self.x, self.y, self.z = x, y, z


class FakeRotation:
    def __init__(self, *args: Any):
        self.Q = (0.0, 0.0, 0.0, 1.0)


class FakePlacement:
    def __init__(self, base: FakeVector | None = None, rotation: FakeRotation | None = None):
        self.Base = base or FakeVector()
        self.Rotation = rotation or FakeRotation()


class FakeObject:
    def __init__(self, name: str, type_id: str, label: str | None = None, valid: bool = True):
        self.Name = name
        self.TypeId = type_id
        self.Label = label or name
        self.InList: list[Any] = []
        self.OutList: list[Any] = []
        self.State: list[str] = [] if valid else ["Invalid"]
        self._valid = valid
        self._touched = False
        self._error_desc = "" if valid else "Mock geometry failure"
        self.PropertiesList: list[str] = ["Label"]

    def isValid(self) -> bool:
        return self._valid

    def isTouched(self) -> bool:
        return self._touched

    def getStatusString(self) -> str:
        return "Valid" if self._valid else (self._error_desc or "Error")

    def getErrorDescription(self) -> str:
        return self._error_desc

    def getTypeIdOfProperty(self, prop: str) -> str:
        return "App::PropertyString"

    def getGroupOfProperty(self, prop: str) -> str:
        return "Base"

    def getDocumentationOfProperty(self, prop: str) -> str:
        return f"Doc for {prop}"


class FakeDocument:
    def __init__(self, name: str = "TestDoc"):
        self.Name = name
        self.Label = name
        self.FileName = ""
        self.Objects: list[Any] = []
        self.UndoCount = 0
        self.RedoCount = 0
        self.UndoNames: list[str] = []
        self.RedoNames: list[str] = []
        self.transactions: list[tuple[str, str]] = []
        self._saved = True

    def isSaved(self) -> bool:
        return self._saved

    def getObject(self, name: str) -> Any:
        for obj in self.Objects:
            if obj.Name == name:
                return obj
        return None

    def addObject(self, type_id: str, name: str) -> FakeObject:
        obj = FakeObject(name, type_id)
        self.Objects.append(obj)
        return obj

    def recompute(self) -> None:
        return

    def openTransaction(self, label: str) -> None:
        self.transactions.append(("open", label))

    def commitTransaction(self) -> None:
        self.transactions.append(("commit", ""))
        self.UndoCount += 1
        self.UndoNames.append("Action")

    def abortTransaction(self) -> None:
        self.transactions.append(("abort", ""))

    def undo(self) -> None:
        if self.UndoCount > 0:
            self.UndoCount -= 1
            self.RedoCount += 1

    def redo(self) -> None:
        if self.RedoCount > 0:
            self.RedoCount -= 1
            self.UndoCount += 1


class FakeConsole:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self._log = "Init FreeCAD\nLoaded workbenches"

    def PrintMessage(self, msg: str) -> None:
        self.messages.append(msg)

    def PrintWarning(self, msg: str) -> None:
        self.messages.append(msg)

    def PrintError(self, msg: str) -> None:
        self.messages.append(msg)

    def GetLog(self) -> str:
        return self._log


@pytest.fixture
def fake_freecad(monkeypatch: pytest.MonkeyPatch):
    """Install fake FreeCAD and FreeCADGui modules for isolated unit tests."""
    doc = FakeDocument("CoursePart")
    docs = {doc.Name: doc}

    fc_mod = types.ModuleType("FreeCAD")
    fc_mod.GuiUp = True
    fc_mod.ActiveDocument = doc
    fc_mod.Console = FakeConsole()
    fc_mod.Vector = FakeVector
    fc_mod.Rotation = FakeRotation
    fc_mod.Placement = FakePlacement
    fc_mod.listDocuments = lambda: docs
    fc_mod.getDocument = lambda name: docs.get(name)

    def _new_doc(name: str = "Unnamed"):
        nd = FakeDocument(name)
        docs[nd.Name] = nd
        fc_mod.ActiveDocument = nd
        return nd

    fc_mod.newDocument = _new_doc
    fc_mod.setActiveDocument = lambda name: setattr(fc_mod, "ActiveDocument", docs[name])
    fc_mod.getResourceDir = lambda: str(ROOT)

    gui_mod = types.ModuleType("FreeCADGui")

    class _Control:
        def __init__(self):
            self._active = False

        def activeDialog(self) -> bool:
            return self._active

    class _Selection:
        def __init__(self):
            self._items: list[Any] = []

        def clearSelection(self) -> None:
            self._items.clear()

        def addSelection(self, *args: Any) -> None:
            self._items.append(args)

        def getSelectionEx(self) -> list[Any]:
            return list(self._items)

    class _CommandNamespace:
        def __init__(self):
            self.cmds: dict[str, Any] = {}

        def listAll(self) -> list[str]:
            return list(self.cmds.keys())

        def get(self, name: str) -> Any:
            return self.cmds.get(name)

    gui_mod.Control = _Control()
    gui_mod.Selection = _Selection()
    gui_mod.Command = _CommandNamespace()
    gui_mod.ActiveDocument = types.SimpleNamespace(getInEdit=lambda: None)
    gui_mod._active_view = None
    gui_mod.activeView = lambda: gui_mod._active_view
    gui_mod._main_window = None
    gui_mod.getMainWindow = lambda: gui_mod._main_window
    gui_mod._workbenches = {
        "PartDesignWorkbench": types.SimpleNamespace(MenuText="PartDesign", ToolTip="Parametric parts"),
        "PartWorkbench": types.SimpleNamespace(MenuText="Part", ToolTip="CSG primitives"),
        "TechDrawWorkbench": types.SimpleNamespace(MenuText="TechDraw", ToolTip="2D drawings"),
    }
    gui_mod._active_wb = "PartDesignWorkbench"
    gui_mod.listWorkbenches = lambda: gui_mod._workbenches
    gui_mod.activeWorkbench = lambda: types.SimpleNamespace(name=lambda: gui_mod._active_wb)
    gui_mod.activateWorkbench = lambda name: setattr(gui_mod, "_active_wb", name)
    gui_mod.runCommand = lambda name: None

    monkeypatch.setitem(sys.modules, "FreeCAD", fc_mod)
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui_mod)
    return fc_mod, gui_mod
