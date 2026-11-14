# FreeCAD Tutor MCP

A tutor-focused **Model Context Protocol (MCP)** server and FreeCAD workbench built for learning **FreeCAD 1.0 / 1.1+** with **Claude Desktop on Windows**.

It combines:
* **Deep diagnostics** (`get_document_graph`, `get_sketch_diagnostics`, `analyze_shape`, `tracked_recompute`, `inspect_object`)
* **Live Qt UI discovery** (`find_ui_command` for exact menu paths, shortcuts, and why buttons are greyed out, plus `list_workbenches`, `activate_workbench`, `get_selection`, `set_selection`, `get_console_output`)
* **Crash-safe multi-angle screenshots** (`get_view`)
* **Confirmed, `Ctrl+Z`-undoable modeling & demo tools** (`PartDesign`, `Sketcher`, `Part` CSG, `TechDraw`)

---

## Quick Start (Windows)

```powershell
cd freecad-tutor-mcp
.\install.ps1
```

Then:
1. Launch (or restart) **FreeCAD 1.0 / 1.1+**. The RPC server auto-starts on `127.0.0.1:9876`.
2. Restart **Claude Desktop**.
3. *(Recommended)* Create a Claude Desktop Project and paste [`CLAUDE_PROJECT_INSTRUCTIONS.md`](CLAUDE_PROJECT_INSTRUCTIONS.md).

---

## Curated Toolset

### Read & Troubleshoot (`readOnlyHint=True`)
* `get_rpc_status` — Connection, addon version check, PID, and active Task dialog status
* `list_documents` / `get_document_graph` / `inspect_object` / `analyze_shape`
* `get_sketch_diagnostics` — Reads `sketch.DoF`, `FullyConstrained`, conflicts/redundancies, geometries, and TNP attachment warnings
* `tracked_recompute` — Recomputes with before/after error diff
* `get_console_output` — Reads FreeCAD's Report view / console log

### UI & Visual Context
* `find_ui_command` — Finds menu paths, toolbars, shortcuts, and `enabled`/`disabled_reason` from live FreeCAD
* `list_workbenches` / `activate_workbench`
* `get_selection` / `set_selection`
* `get_view` — Multi-angle viewport PNGs (`Isometric`, `Front`, `Top`, `Right`, etc.) with `focus_object` and TechDraw-tab safety

### Confirmed, Undoable Edit & Demo Tools (`readOnlyHint=False`)
* `create_document` (defaults to safe `Tutor_Demo` scratch document)
* `undo` / `redo` / `get_undo_redo_status`
* `create_partdesign_body`, `create_sketch`, `edit_sketch`, `pad_sketch`, `pocket_sketch`
* `boolean_operation` (`Fuse`, `Cut`, `Common`), `fillet_edges`, `chamfer_edges`
* `create_techdraw_page`, `add_techdraw_view`
* `execute_script` (undo-transaction-wrapped escape hatch)

---

## Running Tests

```powershell
uv run pytest -v
```

For live FreeCAD verification (once FreeCAD 1.0/1.1+ is installed):
```powershell
FreeCADCmd.exe tests/live_smoke.py --headless
uv run python tests/live_smoke.py --gui
```
