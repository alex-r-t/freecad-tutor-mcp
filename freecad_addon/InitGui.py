"""FreeCAD Tutor MCP — workbench registration and auto-start.

Loaded by FreeCAD on startup. Registers the "FreeCAD Tutor MCP" workbench
with Start/Stop/Auto-Start controls and auto-starts the local TCP RPC server
by default on 127.0.0.1:9876.
"""

import os
import sys

_addon_dir = os.path.dirname(os.path.realpath(__file__))
if _addon_dir not in sys.path:
    sys.path.insert(0, _addon_dir)

import FreeCAD
import FreeCADGui as Gui

_PREF_GROUP = "User parameter:BaseApp/Preferences/Mod/FreeCADTutorMCP"


def _get_autostart_pref() -> bool:
    """Read AutoStart preference. Defaults to True for zero-friction student use."""
    return FreeCAD.ParamGet(_PREF_GROUP).GetBool("AutoStart", True)


def _set_autostart_pref(value: bool) -> None:
    FreeCAD.ParamGet(_PREF_GROUP).SetBool("AutoStart", value)


class FreeCADTutorMCPWorkbench(Gui.Workbench):
    """Workbench providing Start/Stop/Auto-Start controls for FreeCAD Tutor MCP."""

    def __init__(self):
        self.__class__.MenuText = "FreeCAD Tutor MCP"
        self.__class__.ToolTip = "AI Tutor & Diagnostics MCP Bridge for FreeCAD"

    def Initialize(self):
        import mcp_commands  # noqa: F401 — registers FreeCADGui commands

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


class ToggleTutorAutoStart:
    """Toggle whether the Tutor MCP RPC server auto-starts on FreeCAD launch."""

    def GetResources(self):
        on = _get_autostart_pref()
        return {
            "MenuText": "Auto-Start Tutor Server [ON]" if on else "Auto-Start Tutor Server [OFF]",
            "ToolTip": "Toggle automatic startup of the FreeCAD Tutor MCP RPC server",
        }

    def Activated(self):
        new_val = not _get_autostart_pref()
        _set_autostart_pref(new_val)
        state = "ON" if new_val else "OFF"
        FreeCAD.Console.PrintMessage(
            f"FreeCAD Tutor MCP: Auto-start is now {state} (takes effect on next FreeCAD launch)\n"
        )

    def IsActive(self):
        return True


Gui.addCommand("ToggleTutorAutoStart", ToggleTutorAutoStart())
Gui.addWorkbench(FreeCADTutorMCPWorkbench())


def _should_autostart() -> bool:
    if os.environ.get("FREECAD_MCP_NO_AUTOSTART", "").strip() == "1":
        return False
    if os.environ.get("FREECAD_MCP_AUTOSTART", "").strip() == "1":
        return True
    return _get_autostart_pref()


def _auto_start_rpc() -> None:
    import mcp_commands

    mcp_commands.ensure_started(silent_if_running=True)


if _should_autostart():
    try:
        from PySide import QtCore
    except ImportError:
        try:
            from PySide6 import QtCore
        except ImportError:
            from PySide2 import QtCore

    QtCore.QTimer.singleShot(1000, _auto_start_rpc)
