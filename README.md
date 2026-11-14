# FreeCAD Tutor MCP

This is a completely vibe-coded MCP server to help my brother learn FreeCAD with Claude as an interactive tutor. Pretty much everything else below this sentence is AI-generated.

---

**FreeCAD Tutor MCP** connects **FreeCAD 1.0 / 1.1+** to **Claude Desktop** on Windows so you can ask Claude for help while you model.

With it connected, Claude can:
* **Show you where buttons are** — highlight toolbar buttons, menus, or panels right on your FreeCAD screen instead of making you hunt for them.
* **Explain why something broke** — look at your sketch, shape, open dialog, and error messages, then translate them into plain English.
* **Demonstrate safely** — if you ask Claude to show you how a feature works, it asks before changing anything, puts demos in a separate practice file (`Tutor_Demo`), and every step can be undone with `Ctrl+Z`.

---

## Before You Start

Make sure you already have these installed on your Windows 10 or Windows 11 PC:
1. **FreeCAD 1.0 or 1.1+** ([freecad.org](https://www.freecad.org/))
2. **Claude Desktop** ([claude.ai/download](https://claude.ai/download))

You do **not** need Git, Python, or a GitHub account.

---

## Setup (4 Steps)

### Step 1: Download and unzip this folder
1. Download the project ZIP file here:
   👉 **[Download FreeCAD Tutor MCP (.zip)](https://github.com/alex-r-t/freecad-tutor-mcp/archive/refs/heads/main.zip)**
   *(Or click the green **Code** button above, then **Download ZIP**.)*
2. Find the downloaded `.zip` file in your **Downloads** folder.
3. Right-click it → click **Extract All...** → extract it somewhere easy to find (like **Documents**).

### Step 2: Run the installer
1. Open the extracted folder (`freecad-tutor-mcp-main`).
2. Double-click **`install.bat`**.
3. Wait for the black window to finish. It handles everything automatically:
   * Installs the **FreeCADTutorMCP** add-on into your FreeCAD `Mod` folder.
   * Sets up a local Python environment inside this folder (and downloads `uv` / Python automatically if your PC doesn't have them).
   * Backs up and updates your Claude Desktop config file (`%APPDATA%\Claude\claude_desktop_config.json`).

### Step 3: Restart FreeCAD and Claude Desktop
1. **Open (or restart) FreeCAD.** The bridge starts quietly in the background on your PC (`127.0.0.1:9876`).
2. **Restart Claude Desktop** (if it was already open, close it from the system tray and reopen it).

### Step 4: Set up the Tutor instructions *(Recommended)*
1. In Claude Desktop, create a new **Project** (for example, `FreeCAD Course`).
2. Open [`CLAUDE_PROJECT_INSTRUCTIONS.md`](CLAUDE_PROJECT_INSTRUCTIONS.md), copy all of its text, and paste it into the project's **Instructions**.

This tells Claude to act like a patient tutor—explaining *why* things work and letting you click the buttons yourself—instead of taking over your model.

---

## Things You Can Ask Claude

Once both apps are open, try asking questions like:
* *"Where is the Pad button, and why is it greyed out right now?"*
* *"Why won't my sketch turn green?"*
* *"Look at what I selected and tell me why my fillet failed."*
* *"Open a practice file and show me how Revolve works step by step."*

---

## Safety & Privacy

* **Nothing leaves your PC** except the text and screenshots sent to Claude in your chat.
* **Read first, ask before editing:** Claude can inspect your model and take screenshots freely, while any action that changes your model prompts you for permission first.
* **Full Undo:** Every model change is wrapped in a FreeCAD undo step (`Ctrl+Z`).
* **Local lock:** FreeCAD generates a private token on your own PC (`%APPDATA%\FreeCAD\mcp_auth_token`) so other programs cannot connect without permission.

---

## Full Tool List & Developer Details

<details>
<summary><strong>Click to expand: All 58 MCP tools (v0.2)</strong></summary>

### Read, Diagnose & Understand
* **Connection & History:** `get_rpc_status`, `get_console_output`, `get_recent_activity`, `explain_freecad_error`, `get_undo_redo_status`
* **Documents & Objects:** `list_documents`, `get_document_graph` (with active `Body.Tip`, open Task panel controls, and plain-English error explanations), `inspect_object`, `tracked_recompute`
* **Shapes & Sketches:** `analyze_shape` (including optional deep `BOPCheck`), `find_edges`, `find_faces`, `measure_distance`, `get_sketch_diagnostics` (`DoF`, `FullyConstrained`, open vertices, missing/conflicting/redundant constraints)

### UI Guidance & Camera
* `find_ui_command` — Menu path, toolbar, shortcut, and why a button is disabled
* `highlight_ui_element` — On-screen spotlight box + callout label in FreeCAD
* `list_workbenches` / `get_selection`
* `set_view` — Standard views (`Isometric`, `Top`, `Front`, `Right`, etc.) and zoom to selection
* `get_view` — Capture the 3D viewport or the full FreeCAD window (including Task panels and dialogs)

### Edit, Practice & Export (asks permission first, `Ctrl+Z` undoable)
* **Files:** `create_document` (defaults to `Tutor_Demo`), `save_document`, `open_document`, `close_document` (refuses to discard unsaved work unless told), `export_document` (STEP, STL, OBJ, 3MF)
* **UI & Undo:** `activate_workbench`, `set_selection`, `undo`, `redo`
* **Sketcher:** `create_sketch`, `edit_sketch` (`auto_constrain`, lines, polylines, rectangles, circles, arcs, slots, polygons, fillets, construction geometry, external geometry, constraint editing/deletion)
* **PartDesign:** `create_partdesign_body`, `pad_sketch`, `pocket_sketch`, `partdesign_thickness`, `partdesign_hole`, `partdesign_revolution`, `partdesign_groove`, `partdesign_loft`, `partdesign_sweep`, `partdesign_mirrored`, `partdesign_linear_pattern`, `partdesign_polar_pattern`, `partdesign_draft`, `create_datum_plane`
* **Parameters & Appearance:** `create_varset`, `set_property` (dimensions and formula expressions), `set_appearance` (visibility, transparency, color)
* **Part (CSG):** `create_part_primitive`, `boolean_operation` (`Fuse`, `Cut`, `Common`), `fillet_edges`, `chamfer_edges`
* **TechDraw (2D Drawings):** `create_techdraw_page`, `add_techdraw_view`, `add_techdraw_dimension`, `add_techdraw_section`, `export_techdraw_page` (PDF / SVG)
* **Escape Hatch:** `execute_script` (undo-transaction-wrapped Python)

</details>

<details>
<summary><strong>Click to expand: PowerShell install & running tests</strong></summary>

If you prefer PowerShell over `install.bat`:
```powershell
git clone https://github.com/alex-r-t/freecad-tutor-mcp.git
cd freecad-tutor-mcp
.\install.ps1
```

Run the unit & mocked test suite:
```powershell
uv run pytest -v
```

Run live FreeCAD verification (requires FreeCAD 1.0/1.1+ installed):
```powershell
freecadcmd.exe -c "import sys; sys.argv=['live_smoke.py','--headless']; exec(open('tests/live_smoke.py').read(), {'__name__':'__main__'})"
uv run python tests/live_smoke.py --gui
```

</details>

---

## Credits & Inspirations

`FreeCAD Tutor MCP` was built from scratch for student tutoring by combining architectural ideas, safety patterns, and FreeCAD API lessons from several open-source FreeCAD MCP projects and FreeCAD itself:

* **[theosib/FreeCAD-MCP-Server](https://github.com/theosib/FreeCAD-MCP-Server)** *(Timothy Miller, `LGPL-2.1-or-later`)* — Inspired the dual-process TCP JSON-RPC 2.0 addon/bridge architecture (`127.0.0.1:9876`, newline-delimited framing), workbench loader pattern, and deep runtime diagnostic tools (`list_documents`, `get_document_graph`, `inspect_object`, `analyze_shape`, `get_sketch_diagnostics`, `tracked_recompute`, `execute_script`).
* **[neka-nat/freecad-mcp](https://github.com/neka-nat/freecad-mcp)** *(Shirokuma / k tanaka, `MIT`)* — Inspired the hardened Qt main-thread GUI dispatcher (`rpc_server.py`: per-call response queues, `QueuedConnection` wakeups, bounded drag deferral) and the crash-safe `Gui.activeView()` / `saveImage` viewport guard (`get_view` TechDraw-tab safety).
* **[spkane/freecad-addon-robust-mcp-server](https://github.com/spkane/freecad-addon-robust-mcp-server)** *(Sean P. Kane and contributors, `Apache-2.0 AND MIT`)* — Inspired GUI context and safety tooling (`get_selection` / `set_selection`, `list_workbenches` / `activate_workbench`, Report view `QTextEdit` capture in `get_console_output`, `undo` / `redo` transaction wrappers), FreeCAD 1.x `AttachmentSupport` compatibility handling, and local token authentication for the bridge.
* **[sergiudanstan/freecad-mcp](https://github.com/sergiudanstan/freecad-mcp)** *(Dan Stan, `MIT`)* — Inspired `TechDraw` 2D drawing and template patterns (`create_techdraw_page`, `add_techdraw_view`, `add_techdraw_dimension`, `add_techdraw_section`, `export_techdraw_page`) and broad `Part` / `PartDesign` / `Sketcher` tool coverage.
* **[FreeCAD/FreeCAD](https://github.com/FreeCAD/FreeCAD)** *(`LGPL-2.1-or-later`)* — Upstream FreeCAD 1.1 source tree referenced for Python/C++ APIs (`SketchObjectPyImp.cpp`, `DocumentObjectPyImp.cpp`, `FeaturePad.cpp`, `FeaturePocket.cpp`, `PartDesign::Plane`, `App::VarSet`, and `TechDrawGui`).

Other FreeCAD MCP ecosystem repositories surveyed during initial architecture research:
* **[contextform/freecad-mcp](https://github.com/contextform/freecad-mcp)**
* **[bonninr/freecad_mcp](https://github.com/bonninr/freecad_mcp)** *(Rodolfo Bonnin, `MIT`)*
* **[YunchaoYang/robust-freecad-mcp](https://github.com/YunchaoYang/robust-freecad-mcp)**

See [`NOTICE`](NOTICE) for detailed component-level attributions.

---

## License

`FreeCAD Tutor MCP` is distributed under **`LGPL-2.1-or-later`**. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
