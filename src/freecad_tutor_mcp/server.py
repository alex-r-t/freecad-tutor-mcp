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
    def analyze_shape(name: str, doc_name: str = "") -> dict[str, Any]:
        """Inspect face/edge topology, bounding box, volume, and validity of an object's Shape."""
        return _call("analyze_shape", {"name": name, "doc_name": doc_name})

    @mcp.tool(annotations=READ_ONLY)
    def get_sketch_diagnostics(name: str, doc_name: str = "") -> dict[str, Any]:
        """Inspect sketch Degrees of Freedom (DoF), conflicts, redundancies, geometry, and tutor hints."""
        return _call("get_sketch_diagnostics", {"name": name, "doc_name": doc_name})

    @mcp.tool(annotations=READ_ONLY)
    def tracked_recompute(doc_name: str = "") -> dict[str, Any]:
        """Recompute the document and report new, resolved, and persistent object errors."""
        return _call("tracked_recompute", {"doc_name": doc_name})

    @mcp.tool(annotations=READ_ONLY)
    def get_console_output(lines: int = 100) -> dict[str, Any]:
        """Read recent lines from FreeCAD's Report view / console log (1-1000 lines)."""
        return _call("get_console_output", {"lines": lines})

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
    def get_view(
        view_name: str = "Current",
        width: int | None = None,
        height: int | None = None,
        focus_object: str = "",
    ) -> list[Any]:
        """Capture the 3D viewport as an inline PNG image.

        Args:
            view_name: 'Current', 'Isometric', 'Front', 'Top', 'Right', 'Back', 'Left', 'Bottom', 'Dimetric', or 'Trimetric'.
            width: Optional explicit image width (defaults to auto-scaled <=1024px longest edge).
            height: Optional explicit image height.
            focus_object: Optional object name to zoom/fit before capturing.
        """
        data = _call(
            "get_view",
            {
                "view_name": view_name,
                "width": width,
                "height": height,
                "focus_object": focus_object,
            },
        )
        meta = f"Captured {data['view_name']} view ({data['width']}x{data['height']} px)"
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
        doc_name: str = "",
    ) -> dict[str, Any]:
        """Add geometry (line, rectangle, circle, arc) and constraints to a Sketch (undoable)."""
        return _call(
            "edit_sketch",
            {
                "sketch_name": sketch_name,
                "geometries": geometries,
                "constraints": constraints,
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
