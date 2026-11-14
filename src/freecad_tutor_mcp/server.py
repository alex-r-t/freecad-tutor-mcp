"""MCP stdio server entry point for FreeCAD Tutor MCP (MCP Python SDK 2.x).

This process is launched by Claude Desktop over stdio and talks to the
FreeCADTutorMCP workbench addon over a local TCP socket (127.0.0.1:9876).
"""

import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, TextContent, ToolAnnotations

from freecad_tutor_mcp.bridge import FreeCADBridge
from freecad_tutor_mcp.prompts import register_prompts
from freecad_tutor_mcp.resources import register_resources

# IMPORTANT: Never log to stdout; stdout is reserved strictly for MCP JSON-RPC frames.
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("freecad_tutor_mcp.server")

SERVER_INSTRUCTIONS = """You are connected to a live FreeCAD 1.0/1.1+ instance via FreeCAD Tutor MCP.

Workflow rules:
1. Inspect before acting: use `get_document_graph`, `get_sketch_diagnostics`, `get_selection`, `get_recent_activity`, and `get_console_output` to observe the student's real model state.
2. Search for specialized tools before scripting: Claude Desktop may defer some tools behind tool search. Before falling back to `execute_script`, search for and load matching 1-to-1 tools (`partdesign_*`, `find_edges`, `find_faces`, `measure_distance`, `set_view`, `save_document`/`open_document`/`close_document`/`export_document`, `set_property`, `set_appearance`, `create_datum_plane`, `create_varset`, `add_techdraw_*`, `export_techdraw_page`, `explain_freecad_error`).
3. Never guess FreeCAD menu or toolbar locations: call `find_ui_command` to get exact live menu paths, shortcuts, and why a button might be greyed out, and use `highlight_ui_element` to put a temporary spotlight on the button, menu, or panel in the live FreeCAD window.
4. Do not mutate the student's active coursework file unless explicitly asked; prefer `create_document(name="Tutor_Demo")` for demonstrations, and never discard unsaved changes without consent.
5. All editing tools are wrapped in FreeCAD undo transactions (`Ctrl+Z` / `undo`)."""

# Read-only tools run smoothly without confirmation prompts in Claude Desktop.
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

# Edit/UI-mutation tools prompt for confirmation and are undoable in FreeCAD.
SAFE_EDIT = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


def create_server(bridge: FreeCADBridge | None = None) -> MCPServer:
    """Create and configure the MCPServer (accepts an injectable bridge for tests)."""
    active_bridge = bridge or FreeCADBridge()
    mcp = MCPServer("freecad-tutor", instructions=SERVER_INSTRUCTIONS)

    def _call(method: str, params: dict[str, Any] | None = None) -> Any:
        result = active_bridge.call(method, params)
        if active_bridge.version_warning and isinstance(result, dict):
            result.setdefault("_version_warning", active_bridge.version_warning)
        return result

    # ── Status, Read & Troubleshooting (readOnlyHint=True) ───────────────

    @mcp.tool(annotations=READ_ONLY)
    def get_rpc_status() -> dict[str, Any]:
        """Check connection health, addon protocol version, FreeCAD PID, and dispatch status."""
        status = active_bridge.call("get_rpc_status")
        if active_bridge.version_warning and isinstance(status, dict):
            status["version_warning"] = active_bridge.version_warning
        return status

    @mcp.tool(annotations=READ_ONLY)
    def list_documents() -> dict[str, Any]:
        """List all open FreeCAD documents (`active_document`, `count`, and `documents` list)."""
        return _call("list_documents")

    @mcp.tool(annotations=READ_ONLY)
    def get_document_graph(
        doc_name: str = "",
        include_properties: bool = False,
    ) -> dict[str, Any]:
        """Inspect the feature tree, dependencies, error messages, UI edit state, and TNP warnings.

        Args:
            doc_name: Document name; empty string uses the active document.
            include_properties: Keep False (default) for a compact graph; set True for all properties.
        """
        return _call(
            "get_document_graph",
            {"doc_name": doc_name, "include_properties": include_properties},
        )

    @mcp.tool(annotations=READ_ONLY)
    def inspect_object(name: str, doc_name: str = "") -> dict[str, Any]:
        """Dump all properties, validity state, error description, and shape metadata for one object."""
        return _call("inspect_object", {"name": name, "doc_name": doc_name})

    @mcp.tool(annotations=READ_ONLY)
    def analyze_shape(name: str, deep_check: bool = False, doc_name: str = "") -> dict[str, Any]:
        """Inspect face/edge topology, bounding box, volume, and validity (optional OCC BOPCheck).

        Args:
            name: Object name in the document.
            deep_check: Set True to run OCC's slower deep `Shape.check(True)` BOP validity test.
            doc_name: Target document name (empty string uses ActiveDocument).
        """
        return _call("analyze_shape", {"name": name, "deep_check": deep_check, "doc_name": doc_name})

    @mcp.tool(annotations=READ_ONLY)
    def find_edges(
        object_name: str,
        direction: str | list[float] = "",
        length: float | None = None,
        min_length: float | None = None,
        max_length: float | None = None,
        curve_type: str = "",
        near_point: list[float] | None = None,
        max_distance: float | None = None,
        select_matches: bool = False,
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Find EdgeN names on a shape matching direction ('X'/'Y'/'Z'/'+Z'/'-Z'), length, or location."""
        return _call(
            "find_edges",
            {
                "object_name": object_name,
                "direction": direction,
                "length": length,
                "min_length": min_length,
                "max_length": max_length,
                "curve_type": curve_type,
                "near_point": near_point,
                "max_distance": max_distance,
                "select_matches": select_matches,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=READ_ONLY)
    def find_faces(
        object_name: str,
        normal: str | list[float] = "",
        area: float | None = None,
        min_area: float | None = None,
        max_area: float | None = None,
        surface_type: str = "",
        near_point: list[float] | None = None,
        max_distance: float | None = None,
        select_matches: bool = False,
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Find FaceN names on a shape matching normal ('+Z', '-Z', 'X'), area, surface type, or point."""
        return _call(
            "find_faces",
            {
                "object_name": object_name,
                "normal": normal,
                "area": area,
                "min_area": min_area,
                "max_area": max_area,
                "surface_type": surface_type,
                "near_point": near_point,
                "max_distance": max_distance,
                "select_matches": select_matches,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=READ_ONLY)
    def measure_distance(
        object1: str,
        subelement1: str = "",
        object2: str = "",
        subelement2: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Measure minimum 3D distance and delta vector between two objects or Face/Edge/Vertex refs."""
        return _call(
            "measure_distance",
            {
                "object1": object1,
                "subelement1": subelement1,
                "object2": object2,
                "subelement2": subelement2,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=READ_ONLY)
    def get_sketch_diagnostics(name: str, doc_name: str = "") -> dict[str, Any]:
        """Inspect Sketch DoF, OpenVertices, missing/conflicting/redundant constraints, and tutor hints."""
        return _call("get_sketch_diagnostics", {"name": name, "doc_name": doc_name})

    @mcp.tool(annotations=READ_ONLY)
    def tracked_recompute(doc_name: str = "") -> dict[str, Any]:
        """Recompute the document and report new, resolved, and persistent object errors."""
        return _call("tracked_recompute", {"doc_name": doc_name})

    @mcp.tool(annotations=READ_ONLY)
    def get_console_output(lines: int = 100) -> dict[str, Any]:
        """Read recent lines from FreeCAD's Report view / console log (1-1000 lines)."""
        return _call("get_console_output", {"lines": lines})

    @mcp.tool(annotations=READ_ONLY)
    def get_recent_activity(limit: int = 25, doc_name: str = "") -> dict[str, Any]:
        """Read recent student/model events recorded by the FreeCAD observer (edits, undos, dialogs)."""
        return _call("get_recent_activity", {"limit": limit, "doc_name": doc_name})

    @mcp.tool(annotations=READ_ONLY)
    def explain_freecad_error(error_message: str) -> dict[str, Any]:
        """Translate a cryptic FreeCAD/OpenCASCADE error message into beginner causes and fixes."""
        return _call("explain_freecad_error", {"error_message": error_message})

    # ── UI Discovery & Visual Context ────────────────────────────────────

    @mcp.tool(annotations=READ_ONLY)
    def find_ui_command(
        query: str,
        workbench_filter: str = "",
        limit: int = 15,
    ) -> dict[str, Any]:
        """Search live FreeCAD commands for menu paths, toolbars, shortcuts, and why a button is greyed out.

        Args:
            query: Keyword such as 'Pad', 'Fillet', 'Sketch', or 'TechDraw'.
            workbench_filter: Optional command prefix filter (e.g. 'PartDesign', 'Sketcher').
            limit: Max results (1-50).
        """
        return _call(
            "find_ui_command",
            {"query": query, "workbench_filter": workbench_filter, "limit": limit},
        )

    @mcp.tool(annotations=READ_ONLY)
    def highlight_ui_element(
        target: str = "",
        label: str = "",
        duration_s: float = 6.0,
        clear: bool = False,
    ) -> dict[str, Any]:
        """Put a temporary, click-through visual spotlight on a FreeCAD button, menu, panel, or workbench selector.

        Args:
            target: Command name (e.g. 'PartDesign_Pad'), button label ('Pad'),
                or panel name ('Model', 'Tasks', 'Report view', 'Workbench').
            label: Optional short callout text shown next to the highlight frame.
            duration_s: Auto-dismiss time in seconds (1.0-20.0).
            clear: Set True to immediately remove any active highlight.
        """
        return _call(
            "highlight_ui_element",
            {
                "target": target,
                "label": label,
                "duration_s": duration_s,
                "clear": clear,
            },
        )

    @mcp.tool(annotations=READ_ONLY)
    def list_workbenches() -> dict[str, Any]:
        """List all FreeCAD workbenches and show which one is currently active."""
        return _call("list_workbenches")

    @mcp.tool(annotations=SAFE_EDIT)
    def activate_workbench(name: str) -> dict[str, Any]:
        """Switch FreeCAD's active workbench (e.g., 'PartDesignWorkbench', 'PartWorkbench', 'TechDrawWorkbench')."""
        return _call("activate_workbench", {"name": name})

    @mcp.tool(annotations=READ_ONLY)
    def get_selection() -> dict[str, Any]:
        """Get objects and sub-elements (Face1, Edge2, etc.) currently selected by the user in FreeCAD."""
        return _call("get_selection")

    @mcp.tool(annotations=SAFE_EDIT)
    def set_selection(
        names: list[str],
        doc_name: str = "",
        clear_first: bool = True,
    ) -> dict[str, Any]:
        """Highlight/select objects or sub-elements (e.g. ['Pad', 'Pad.Face1']) in FreeCAD's viewport."""
        return _call(
            "set_selection",
            {"names": names, "doc_name": doc_name, "clear_first": clear_first},
        )

    @mcp.tool(annotations=READ_ONLY)
    def set_view(
        view_name: str = "Isometric",
        fit_all: bool = True,
        zoom_to_selection: bool = False,
        focus_object: str = "",
    ) -> dict[str, Any]:
        """Orient and zoom the 3D camera (Isometric, Front, Top, Right, Back/Rear, Left, Bottom) without screenshot."""
        return _call(
            "set_view",
            {
                "view_name": view_name,
                "fit_all": fit_all,
                "zoom_to_selection": zoom_to_selection,
                "focus_object": focus_object,
            },
        )

    @mcp.tool(annotations=READ_ONLY)
    def get_view(
        view_name: str = "Current",
        width: int | None = None,
        height: int | None = None,
        focus_object: str = "",
        capture_scope: str = "viewport",
    ) -> list[Any]:
        """Capture the 3D viewport (`capture_scope='viewport'`) or full FreeCAD window (`'window'`) as PNG.

        Args:
            view_name: 'Current', 'Isometric', 'Front', 'Top', 'Right', 'Back', 'Left', 'Bottom', 'Dimetric', or 'Trimetric'.
            width: Optional explicit image width (auto-scaled <=1024px viewport / <=1280px window).
            height: Optional explicit image height.
            focus_object: Optional object name to zoom/fit before capturing.
            capture_scope: 'viewport' for clean 3D render, or 'window' to include Task panels, Combo View, and dialogs.
        """
        data = _call(
            "get_view",
            {
                "view_name": view_name,
                "width": width,
                "height": height,
                "focus_object": focus_object,
                "capture_scope": capture_scope,
            },
        )
        scope_label = data.get("capture_scope", capture_scope)
        meta = f"Captured {data['view_name']} {scope_label} ({data['width']}x{data['height']} px)"
        if "_version_warning" in data:
            meta += f"\nWarning: {data['_version_warning']}"
        return [
            ImageContent(type="image", data=data["base64_png"], mimeType="image/png"),
            TextContent(type="text", text=meta),
        ]

    # ── Confirmed, Undoable Edit & Demonstration Tools ───────────────────

    @mcp.tool(annotations=SAFE_EDIT)
    def create_document(name: str = "Tutor_Demo", activate: bool = True) -> dict[str, Any]:
        """Create a new FreeCAD document (defaults to a safe 'Tutor_Demo' scratch file for demonstrations)."""
        return _call("create_document", {"name": name, "activate": activate})

    @mcp.tool(annotations=SAFE_EDIT)
    def save_document(path: str = "", doc_name: str = "") -> dict[str, Any]:
        """Save an open document to its existing `.FCStd` path or a new destination `path`."""
        return _call("save_document", {"path": path, "doc_name": doc_name})

    @mcp.tool(annotations=SAFE_EDIT)
    def open_document(path: str, activate: bool = True) -> dict[str, Any]:
        """Open a `.FCStd` file (or import a `.step`/`.stp`/`.iges`/`.igs` file into a new document)."""
        return _call("open_document", {"path": path, "activate": activate})

    @mcp.tool(annotations=SAFE_EDIT)
    def close_document(
        doc_name: str = "",
        save_before_close: bool = False,
        discard_unsaved: bool = False,
    ) -> dict[str, Any]:
        """Close a document safely (refuses if modified unless `save_before_close` or `discard_unsaved` is True)."""
        return _call(
            "close_document",
            {
                "doc_name": doc_name,
                "save_before_close": save_before_close,
                "discard_unsaved": discard_unsaved,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def export_document(
        path: str,
        object_names: list[str] | None = None,
        export_format: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Export Bodies or solids to `.step`/`.stp`, `.stl`, `.obj`, `.3mf`, `.iges`, or `.brep`."""
        return _call(
            "export_document",
            {
                "path": path,
                "object_names": object_names,
                "export_format": export_format,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def undo(doc_name: str = "") -> dict[str, Any]:
        """Undo the most recent FreeCAD transaction."""
        return _call("undo", {"doc_name": doc_name})

    @mcp.tool(annotations=SAFE_EDIT)
    def redo(doc_name: str = "") -> dict[str, Any]:
        """Redo the most recently undone FreeCAD transaction."""
        return _call("redo", {"doc_name": doc_name})

    @mcp.tool(annotations=READ_ONLY)
    def get_undo_redo_status(doc_name: str = "") -> dict[str, Any]:
        """Check available undo/redo steps and transaction names."""
        return _call("get_undo_redo_status", {"doc_name": doc_name})

    @mcp.tool(annotations=SAFE_EDIT)
    def create_partdesign_body(name: str = "", doc_name: str = "") -> dict[str, Any]:
        """Create a PartDesign::Body container (undoable)."""
        return _call("create_partdesign_body", {"name": name, "doc_name": doc_name})

    @mcp.tool(annotations=SAFE_EDIT)
    def create_sketch(
        body_name: str = "",
        plane: str = "XY_Plane",
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Create a Sketcher::SketchObject on an Origin plane or face (undoable)."""
        return _call(
            "create_sketch",
            {"body_name": body_name, "plane": plane, "name": name, "doc_name": doc_name},
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def edit_sketch(
        sketch_name: str,
        geometries: list[dict[str, Any]] | None = None,
        constraints: list[dict[str, Any]] | None = None,
        auto_constrain: bool = True,
        update_constraints: list[dict[str, Any]] | None = None,
        delete_constraint_indices: list[int] | None = None,
        delete_geometry_indices: list[int] | None = None,
        external_geometries: list[dict[str, Any]] | None = None,
        toggle_construction: list[int] | None = None,
        fillet_corners: list[dict[str, Any]] | None = None,
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Add/edit/delete Sketch geometry & constraints with automatic Coincident/H/V constraining (undoable)."""
        return _call(
            "edit_sketch",
            {
                "sketch_name": sketch_name,
                "geometries": geometries,
                "constraints": constraints,
                "auto_constrain": auto_constrain,
                "update_constraints": update_constraints,
                "delete_constraint_indices": delete_constraint_indices,
                "delete_geometry_indices": delete_geometry_indices,
                "external_geometries": external_geometries,
                "toggle_construction": toggle_construction,
                "fillet_corners": fillet_corners,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def pad_sketch(
        sketch_name: str,
        length: float = 10.0,
        symmetric: bool = False,
        reversed: bool = False,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Extrude a Sketch inside a PartDesign Body using Pad (undoable; rolls back if invalid)."""
        return _call(
            "pad_sketch",
            {
                "sketch_name": sketch_name,
                "length": length,
                "symmetric": symmetric,
                "reversed": reversed,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def pocket_sketch(
        sketch_name: str,
        length: float = 10.0,
        through_all: bool = False,
        reversed: bool = False,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Cut material from a PartDesign Body using Pocket (undoable; rolls back if invalid)."""
        return _call(
            "pocket_sketch",
            {
                "sketch_name": sketch_name,
                "length": length,
                "through_all": through_all,
                "reversed": reversed,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def partdesign_thickness(
        object_name: str,
        faces: list[str],
        thickness: float = 2.0,
        mode: str = "Skin",
        join: str = "Intersection",
        inside: bool = True,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Hollow out a PartDesign solid by opening selected faces and setting wall thickness (undoable)."""
        return _call(
            "partdesign_thickness",
            {
                "object_name": object_name,
                "faces": faces,
                "thickness": thickness,
                "mode": mode,
                "join": join,
                "inside": inside,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def partdesign_hole(
        sketch_name: str,
        diameter: float = 6.0,
        depth: float = 10.0,
        through_all: bool = False,
        reversed: bool = False,
        hole_cut_type: str = "None",
        hole_cut_diameter: float | None = None,
        hole_cut_depth: float | None = None,
        threaded: bool = False,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Drill parametric simple, counterbore, countersink, or threaded holes from a Sketch (undoable)."""
        return _call(
            "partdesign_hole",
            {
                "sketch_name": sketch_name,
                "diameter": diameter,
                "depth": depth,
                "through_all": through_all,
                "reversed": reversed,
                "hole_cut_type": hole_cut_type,
                "hole_cut_diameter": hole_cut_diameter,
                "hole_cut_depth": hole_cut_depth,
                "threaded": threaded,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def partdesign_revolution(
        sketch_name: str,
        angle: float = 360.0,
        axis: str = "V_Axis",
        reversed: bool = False,
        midplane: bool = False,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Revolve a Sketch around an axis to add solid material in PartDesign (undoable)."""
        return _call(
            "partdesign_revolution",
            {
                "sketch_name": sketch_name,
                "angle": angle,
                "axis": axis,
                "reversed": reversed,
                "midplane": midplane,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def partdesign_groove(
        sketch_name: str,
        angle: float = 360.0,
        axis: str = "V_Axis",
        reversed: bool = False,
        midplane: bool = False,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Revolve a Sketch around an axis to subtract/cut material in PartDesign (undoable)."""
        return _call(
            "partdesign_groove",
            {
                "sketch_name": sketch_name,
                "angle": angle,
                "axis": axis,
                "reversed": reversed,
                "midplane": midplane,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def partdesign_loft(
        profile_names: list[str],
        ruled: bool = False,
        closed: bool = False,
        subtractive: bool = False,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Loft between two or more Sketches (AdditiveLoft or SubtractiveLoft) (undoable)."""
        return _call(
            "partdesign_loft",
            {
                "profile_names": profile_names,
                "ruled": ruled,
                "closed": closed,
                "subtractive": subtractive,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def partdesign_sweep(
        profile_name: str,
        spine_name: str,
        spine_subelements: list[str] | None = None,
        subtractive: bool = False,
        orientation: str = "Standard",
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Sweep a profile Sketch along a spine path (AdditivePipe or SubtractivePipe) (undoable)."""
        return _call(
            "partdesign_sweep",
            {
                "profile_name": profile_name,
                "spine_name": spine_name,
                "spine_subelements": spine_subelements,
                "subtractive": subtractive,
                "orientation": orientation,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def partdesign_mirrored(
        feature_names: list[str],
        mirror_plane: str = "YZ_Plane",
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Mirror PartDesign feature(s) across a plane (PartDesign::Mirrored) (undoable)."""
        return _call(
            "partdesign_mirrored",
            {
                "feature_names": feature_names,
                "mirror_plane": mirror_plane,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def partdesign_linear_pattern(
        feature_names: list[str],
        direction: str = "X_Axis",
        length: float = 50.0,
        occurrences: int = 3,
        reversed: bool = False,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Repeat PartDesign feature(s) along a line (PartDesign::LinearPattern) (undoable)."""
        return _call(
            "partdesign_linear_pattern",
            {
                "feature_names": feature_names,
                "direction": direction,
                "length": length,
                "occurrences": occurrences,
                "reversed": reversed,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def partdesign_polar_pattern(
        feature_names: list[str],
        axis: str = "Z_Axis",
        angle: float = 360.0,
        occurrences: int = 4,
        reversed: bool = False,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Repeat PartDesign feature(s) around an axis (PartDesign::PolarPattern) (undoable)."""
        return _call(
            "partdesign_polar_pattern",
            {
                "feature_names": feature_names,
                "axis": axis,
                "angle": angle,
                "occurrences": occurrences,
                "reversed": reversed,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def partdesign_draft(
        object_name: str,
        faces: list[str],
        angle: float = 5.0,
        neutral_plane: str = "",
        pull_direction: str = "",
        reversed: bool = False,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Taper selected solid faces by a draft angle (PartDesign::Draft) (undoable)."""
        return _call(
            "partdesign_draft",
            {
                "object_name": object_name,
                "faces": faces,
                "angle": angle,
                "neutral_plane": neutral_plane,
                "pull_direction": pull_direction,
                "reversed": reversed,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def create_datum_plane(
        body_name: str,
        attachment_support: str = "XY_Plane",
        subelements: list[str] | None = None,
        map_mode: str = "FlatFace",
        offset: list[float] | None = None,
        rotation_deg: list[float] | None = None,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Create a PartDesign::Plane Datum Plane inside a Body (TNP-safe sketch plane) (undoable)."""
        return _call(
            "create_datum_plane",
            {
                "body_name": body_name,
                "attachment_support": attachment_support,
                "subelements": subelements,
                "map_mode": map_mode,
                "offset": offset,
                "rotation_deg": rotation_deg,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def create_varset(
        name: str = "VarSet",
        variables: list[dict[str, Any]] | None = None,
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Create a FreeCAD 1.0/1.1 App::VarSet parametric variable table (undoable)."""
        return _call("create_varset", {"name": name, "variables": variables, "doc_name": doc_name})

    @mcp.tool(annotations=SAFE_EDIT)
    def set_property(
        object_name: str,
        property_name: str,
        value: Any = None,
        expression: str = "",
        clear_expression: bool = False,
        property_type: str = "",
        group: str = "Dimensions",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Update a Data-tab property (Pad.Length, Fillet.Radius, VarSet fields) or bind an expression (undoable)."""
        return _call(
            "set_property",
            {
                "object_name": object_name,
                "property_name": property_name,
                "value": value,
                "expression": expression,
                "clear_expression": clear_expression,
                "property_type": property_type,
                "group": group,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def set_appearance(
        object_name: str,
        visible: bool | None = None,
        transparency: int | None = None,
        color: list[float] | None = None,
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Set object Visibility, Transparency (0-100), or ShapeColor [r, g, b] in the View tab (undoable)."""
        return _call(
            "set_appearance",
            {
                "object_name": object_name,
                "visible": visible,
                "transparency": transparency,
                "color": color,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def create_part_primitive(
        shape_type: str,
        dimensions: dict[str, float] | None = None,
        position: list[float] | None = None,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Create a Part CSG primitive ('Box', 'Cylinder', 'Sphere', or 'Cone') (undoable)."""
        return _call(
            "create_part_primitive",
            {
                "shape_type": shape_type,
                "dimensions": dimensions,
                "position": position,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def boolean_operation(
        operation: str,
        base_name: str,
        tool_name: str,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Run a Part CSG Boolean ('Fuse', 'Cut', or 'Common') between two objects (undoable)."""
        return _call(
            "boolean_operation",
            {
                "operation": operation,
                "base_name": base_name,
                "tool_name": tool_name,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def fillet_edges(
        object_name: str,
        edges: list[str],
        radius: float = 1.0,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Round selected edges (e.g. ['Edge1', 'Edge2']) on a feature or shape (undoable)."""
        return _call(
            "fillet_edges",
            {
                "object_name": object_name,
                "edges": edges,
                "radius": radius,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def chamfer_edges(
        object_name: str,
        edges: list[str],
        size: float = 1.0,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Bevel selected edges (e.g. ['Edge1']) on a feature or shape (undoable)."""
        return _call(
            "chamfer_edges",
            {
                "object_name": object_name,
                "edges": edges,
                "size": size,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def create_techdraw_page(
        template: str = "A4_Landscape",
        name: str = "Page",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Create a TechDraw 2D drawing sheet with an SVG template (undoable)."""
        return _call("create_techdraw_page", {"template": template, "name": name, "doc_name": doc_name})

    @mcp.tool(annotations=SAFE_EDIT)
    def add_techdraw_view(
        page_name: str,
        object_name: str,
        direction: str = "Front",
        scale: float = 1.0,
        projection_group: bool = False,
        views: list[str] | None = None,
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Add an orthographic view or multi-view Projection Group to a TechDraw page (undoable)."""
        return _call(
            "add_techdraw_view",
            {
                "page_name": page_name,
                "object_name": object_name,
                "direction": direction,
                "scale": scale,
                "projection_group": projection_group,
                "views": views,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def add_techdraw_dimension(
        page_name: str,
        view_name: str,
        references: list[str],
        dim_type: str = "Distance",
        format_spec: str = "",
        x: float | None = None,
        y: float | None = None,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Add a 2D TechDraw dimension (Distance, Radius, Diameter, Angle) referencing view edges/vertices (undoable)."""
        return _call(
            "add_techdraw_dimension",
            {
                "page_name": page_name,
                "view_name": view_name,
                "references": references,
                "dim_type": dim_type,
                "format_spec": format_spec,
                "x": x,
                "y": y,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def add_techdraw_section(
        page_name: str,
        base_view_name: str,
        section_normal: list[float] | None = None,
        section_origin: list[float] | None = None,
        section_direction: str = "Right",
        scale: float | None = None,
        x: float | None = None,
        y: float | None = None,
        name: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Add a TechDraw cross-section view (DrawViewSection) cutting through a BaseView (undoable)."""
        return _call(
            "add_techdraw_section",
            {
                "page_name": page_name,
                "base_view_name": base_view_name,
                "section_normal": section_normal,
                "section_origin": section_origin,
                "section_direction": section_direction,
                "scale": scale,
                "x": x,
                "y": y,
                "name": name,
                "doc_name": doc_name,
            },
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def export_techdraw_page(
        page_name: str,
        path: str,
        export_format: str = "",
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Export a TechDraw drawing page to PDF or SVG."""
        return _call(
            "export_techdraw_page",
            {"page_name": page_name, "path": path, "export_format": export_format, "doc_name": doc_name},
        )

    @mcp.tool(annotations=SAFE_EDIT)
    def execute_script(
        script: str,
        doc_name: str = "",
        wrap_transaction: bool = True,
    ) -> dict[str, Any]:
        """Execute Python inside FreeCAD's main thread (escape hatch; undoable when wrap_transaction=True)."""
        return _call(
            "execute_script",
            {"script": script, "doc_name": doc_name, "wrap_transaction": wrap_transaction},
        )

    register_resources(mcp)
    register_prompts(mcp)
    return mcp


def main() -> None:
    """Run the FreeCAD Tutor MCP server over stdio."""
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
