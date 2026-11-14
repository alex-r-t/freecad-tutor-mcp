"""Live FreeCAD verification smoke script (headless CLI & live GUI TCP modes).

Usage once FreeCAD 1.0/1.1+ is installed:
  FreeCADCmd.exe tests/live_smoke.py --headless
  uv run python tests/live_smoke.py --gui
"""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent


def run_headless() -> None:
    sys.path.insert(0, str(ROOT / "freecad_addon"))
    from handlers import document, inspection, modeling, recompute, sketcher

    doc_info = document.create_document("SmokeTestDoc")
    body = modeling.create_partdesign_body("Body", doc_name=doc_info["name"])
    sk = modeling.create_sketch(body_name=body["name"], plane="XY_Plane", doc_name=doc_info["name"])
    edit_res = modeling.edit_sketch(
        sk["name"],
        geometries=[{"type": "rectangle", "x": 0, "y": 0, "width": 40, "height": 20}],
        doc_name=doc_info["name"],
    )
    assert edit_res["degrees_of_freedom"] is not None
    diag = sketcher.get_sketch_diagnostics(sk["name"], doc_name=doc_info["name"])
    assert diag["geometry_count"] == 4

    pad = modeling.pad_sketch(sk["name"], length=15.0, doc_name=doc_info["name"])
    shape = inspection.analyze_shape(pad["name"], doc_name=doc_info["name"])
    assert shape["is_valid"] is True
    assert abs(shape["volume"] - 12000.0) < 1e-3

    rec = recompute.tracked_recompute(doc_name=doc_info["name"])
    assert rec["new_errors"] == []

    page = modeling.create_techdraw_page(doc_name=doc_info["name"])
    modeling.add_techdraw_view(page["page"], body["name"], doc_name=doc_info["name"])

    # Exercise Part CSG primitive + Boolean Cut workflow as well
    b1 = modeling.create_part_primitive("box", name="CSGBox", dimensions={"length": 20, "width": 20, "height": 20}, doc_name=doc_info["name"])
    c1 = modeling.create_part_primitive("cylinder", name="CSGHole", dimensions={"radius": 4, "height": 25}, position=[10, 10, -2], doc_name=doc_info["name"])
    cut = modeling.boolean_operation("cut", b1["name"], c1["name"], name="CutBlock", doc_name=doc_info["name"])
    cut_shape = inspection.analyze_shape(cut["name"], doc_name=doc_info["name"])
    assert cut_shape["is_valid"] is True

    graph = document.get_document_graph(doc_name=doc_info["name"], include_properties=False)
    assert cut["name"] in graph["objects"]
    print("HEADLESS SMOKE TEST PASSED")


def run_gui(host: str, port: int) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from freecad_tutor_mcp.bridge import FreeCADBridge

    bridge = FreeCADBridge(host=host, port=port)
    try:
        ping = bridge.call("ping")
        assert ping["pong"] is True
        wbs = bridge.call("list_workbenches")
        assert "PartDesignWorkbench" in [w["name"] for w in wbs["workbenches"]]
        bridge.call("activate_workbench", {"name": "PartDesignWorkbench"})

        doc = bridge.call("create_document", {"name": "LiveGUISmoke", "activate": True})
        pad_before = bridge.call("find_ui_command", {"query": "Pad"})
        assert pad_before["matches"], "Expected PartDesign_Pad in live command list"

        body = bridge.call("create_partdesign_body", {"name": "Body", "doc_name": doc["name"]})
        sk = bridge.call("create_sketch", {"body_name": body["name"], "doc_name": doc["name"]})
        bridge.call(
            "edit_sketch",
            {
                "sketch_name": sk["name"],
                "geometries": [{"type": "rectangle", "x": 0, "y": 0, "width": 30, "height": 20}],
                "doc_name": doc["name"],
            },
        )
        pad = bridge.call("pad_sketch", {"sketch_name": sk["name"], "length": 10.0, "doc_name": doc["name"]})
        bridge.call("set_selection", {"names": [pad["name"]], "doc_name": doc["name"]})
        sel = bridge.call("get_selection")
        assert sel["count"] == 1

        shot = bridge.call("get_view", {"view_name": "Isometric", "focus_object": pad["name"]})
        assert len(shot["base64_png"]) > 100

        console = bridge.call("get_console_output", {"lines": 20})
        assert console["available"] is True
        print("LIVE GUI SMOKE TEST PASSED")
    finally:
        bridge.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--headless", action="store_true")
    group.add_argument("--gui", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9876)
    args = parser.parse_args()

    if args.headless:
        run_headless()
    else:
        run_gui(args.host, args.port)


if __name__ == "__main__":
    main()
