"""Tests for the MCPServer tool surface, ToolAnnotations, prompts, and resources."""

import json
import os
import sys
import threading
import time
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from freecad_tutor_mcp.server import create_server
from rpc_server import RPCServer


class FakeBridge:
    def __init__(self, version_warning: str | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.version_warning = version_warning

    def call(self, method: str, params: dict | None = None):
        params = params or {}
        self.calls.append((method, params))
        if method == "get_view":
            return {
                "base64_png": "ZmFrZQ==",
                "width": 800,
                "height": 600,
                "view_name": params.get("view_name", "Current"),
                "format": "png",
            }
        if method == "get_rpc_status":
            return {"running": True, "protocol_version": "0.1.0", "dispatch": {"stuck": False}}
        if method == "find_ui_command":
            return {"query": params["query"], "matches": []}
        return {"ok": True, "method": method}


def test_tool_annotations_and_curated_surface():
    bridge = FakeBridge()
    mcp = create_server(bridge=bridge)

    async def _inspect():
        tools = await mcp.list_tools()
        by_name = {t.name: t for t in tools}
        assert len(tools) == 58

        # Diagnostic & UI read tools must be read_only_hint=True
        for ro in (
            "get_rpc_status",
            "list_documents",
            "get_document_graph",
            "inspect_object",
            "analyze_shape",
            "find_edges",
            "find_faces",
            "measure_distance",
            "get_sketch_diagnostics",
            "tracked_recompute",
            "get_console_output",
            "get_recent_activity",
            "explain_freecad_error",
            "find_ui_command",
            "highlight_ui_element",
            "list_workbenches",
            "get_selection",
            "set_view",
            "get_view",
            "get_undo_redo_status",
        ):
            assert by_name[ro].annotations.read_only_hint is True, f"{ro} should be readOnly"

        # Mutation/demo tools must require confirmation (read_only_hint=False)
        for rw in (
            "create_document",
            "save_document",
            "open_document",
            "close_document",
            "export_document",
            "create_part_primitive",
            "pad_sketch",
            "pocket_sketch",
            "partdesign_thickness",
            "partdesign_hole",
            "partdesign_revolution",
            "partdesign_groove",
            "partdesign_loft",
            "partdesign_sweep",
            "partdesign_mirrored",
            "partdesign_linear_pattern",
            "partdesign_polar_pattern",
            "partdesign_draft",
            "create_datum_plane",
            "create_varset",
            "set_property",
            "set_appearance",
            "boolean_operation",
            "create_techdraw_page",
            "add_techdraw_dimension",
            "add_techdraw_section",
            "export_techdraw_page",
            "execute_script",
        ):
            assert by_name[rw].annotations.read_only_hint is False, f"{rw} should not be readOnly"
            assert by_name[rw].annotations.destructive_hint is False

        resources = await mcp.list_resources()
        uris = {str(r.uri) for r in resources}
        assert "freecad://guides/partdesign-vs-part" in uris
        assert "freecad://guides/sketcher-constraints" in uris
        assert "freecad://guides/tnp-best-practices" in uris
        assert "freecad://guides/techdraw-basics" in uris

        guide_contents = await mcp.read_resource("freecad://guides/partdesign-vs-part")
        assert len(guide_contents) == 1
        assert "PartDesign" in guide_contents[0].content

        prompts = await mcp.list_prompts()
        p_names = {p.name for p in prompts}
        assert {"tutor_mode", "diagnose_broken_model", "compare_workflows"} <= p_names

    anyio.run(_inspect)


def test_get_view_returns_image_and_version_warning():
    bridge = FakeBridge(version_warning="Version mismatch: update addon")
    mcp = create_server(bridge=bridge)

    async def _call():
        result = await mcp.call_tool("get_view", {"view_name": "Isometric"})
        content = result.content
        assert content[0].type == "image"
        assert content[0].mime_type == "image/png"
        assert "800x600" in content[1].text
        assert "Version mismatch" in content[1].text

        prompt_res = await mcp.get_prompt("tutor_mode")
        assert "find_ui_command" in prompt_res.messages[0].content.text

    anyio.run(_call)


def test_stdio_entrypoint_end_to_end():
    """Spawn the installed freecad-tutor-mcp.exe over stdio connected to a live RPCServer."""
    srv = RPCServer(host="127.0.0.1", port=0, use_qt=False, main_thread_timeout=2.0)
    srv.register(
        "list_documents",
        lambda: {
            "active_document": "TutorDemo",
            "count": 1,
            "documents": [{"name": "TutorDemo", "is_active": True, "object_count": 1}],
        },
    )
    srv.register(
        "get_view",
        lambda view_name="Current", **_: {
            "base64_png": "ZmFrZQ==",
            "width": 640,
            "height": 480,
            "view_name": view_name,
            "format": "png",
        },
    )
    stop_pump = threading.Event()

    def _pump():
        while not stop_pump.is_set():
            srv.drain_work_queue()
            time.sleep(0.01)

    srv.start()
    pump_thread = threading.Thread(target=_pump, daemon=True)
    pump_thread.start()

    exe_path = Path(sys.executable).parent / "freecad-tutor-mcp.exe"
    if exe_path.exists():
        cmd, args = str(exe_path), []
    else:
        cmd, args = sys.executable, ["-m", "freecad_tutor_mcp.server"]

    env = dict(os.environ)
    env["FREECAD_MCP_HOST"] = "127.0.0.1"
    env["FREECAD_MCP_PORT"] = str(srv.port)

    async def _run_client():
        params = StdioServerParameters(command=cmd, args=args, env=env)
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                init_res = await session.initialize()
                assert init_res.server_info.name == "freecad-tutor"

                listed = await session.list_tools()
                assert len(listed.tools) == 58

                status_res = await session.call_tool("get_rpc_status", {})
                status_payload = json.loads(status_res.content[0].text)
                assert status_payload["running"] is True

                docs_res = await session.call_tool("list_documents", {})
                docs_payload = json.loads(docs_res.content[0].text)
                assert docs_payload["count"] == 1
                assert docs_payload["documents"][0]["name"] == "TutorDemo"

                view_res = await session.call_tool("get_view", {"view_name": "Isometric"})
                assert view_res.content[0].type == "image"
                assert "640x480" in view_res.content[1].text

    try:
        anyio.run(_run_client)
    finally:
        stop_pump.set()
        pump_thread.join(timeout=2)
        srv.stop()


def test_every_mcp_tool_has_addon_handler(fake_freecad):
    """Ensure every MCP tool exposed by server.py maps to a real RPC handler in mcp_commands."""
    import mcp_commands

    handler_map = mcp_commands._build_handler_map()
    mcp = create_server(bridge=FakeBridge())

    async def _check():
        tools = await mcp.list_tools()
        tool_names = {t.name for t in tools}
        # get_rpc_status is registered directly on RPCServer, not via _build_handler_map.
        expected_in_map = tool_names - {"get_rpc_status"}
        missing = sorted(expected_in_map - set(handler_map.keys()))
        assert missing == [], f"MCP tools missing from _build_handler_map(): {missing}"

    anyio.run(_check)
