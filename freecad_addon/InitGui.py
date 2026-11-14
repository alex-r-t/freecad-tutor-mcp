"""FreeCAD Tutor MCP — workbench registration and auto-start.

Loaded by FreeCAD on startup via RunInitGuiPy() (note: FreeCAD executes
InitGui.py without __file__ and with split globals/locals, so all command
classes and startup helpers live inside the imported `mcp_commands` module).
"""

import os
import sys

import FreeCAD
import FreeCADGui as Gui


def _resolve_addon_dir():
    if "__file__" in globals():
        return os.path.dirname(os.path.realpath(__file__))
    for entry in sys.path:
        if entry and os.path.isfile(os.path.join(entry, "mcp_commands.py")):
            return os.path.realpath(entry)
    candidate = os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "FreeCADTutorMCP")
    return candidate if os.path.isdir(candidate) else None


_addon_dir = _resolve_addon_dir()
if _addon_dir and _addon_dir not in sys.path:
    sys.path.insert(0, _addon_dir)

import mcp_commands  # noqa: E402, F401 — registers FreeCADGui commands


class FreeCADTutorMCPWorkbench(Gui.Workbench):
    """Workbench providing Start/Stop/Auto-Start controls for FreeCAD Tutor MCP."""

    def __init__(self):
        self.__class__.MenuText = "FreeCAD Tutor MCP"
        self.__class__.ToolTip = "AI Tutor & Diagnostics MCP Bridge for FreeCAD"

    def Initialize(self):
        self.appendToolbar(
            "FreeCAD Tutor MCP",
            ["StartTutorServer", "StopTutorServer", "ToggleTutorAutoStart"],
        )
        self.appendMenu(
            "FreeCAD Tutor MCP",
            [
                "StartTutorServer",
                "StopTutorServer",
                "Separator",
                "ToggleTutorAutoStart",
            ],
        )

    def Activated(self):
        return

    def Deactivated(self):
        return

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(FreeCADTutorMCPWorkbench())
mcp_commands.schedule_autostart()
