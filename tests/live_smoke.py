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
    import tempfile

    sys.path.insert(0, str(ROOT / "freecad_addon"))
    from handlers import (
        document,
        error_kb,
        inspection,
        modeling,
        partdesign,
        recompute,
        sketcher,
        techdraw,
    )

    docs_before = document.list_documents()
    assert isinstance(docs_before, dict) and "documents" in docs_before

    doc_info = document.create_document("SmokeTestDoc")
    body = modeling.create_partdesign_body("Body", doc_name=doc_info["name"])
    vs = partdesign.create_varset(
        "Params",
        variables=[{"name": "PadHeight", "type": "Length", "value": 15.0}],
        doc_name=doc_info["name"],
    )
    assert vs["name"] == "Params"

    # Sketch drawn with 4 individual lines -> auto_constrain must join all 4 corners + H/V!
    sk = modeling.create_sketch(body_name=body["name"], plane="XY_Plane", doc_name=doc_info["name"])
    edit_res = modeling.edit_sketch(
        sk["name"],
        geometries=[
            {"type": "line", "start": [0, 0], "end": [40, 0]},
            {"type": "line", "start": [40, 0], "end": [40, 20]},
            {"type": "line", "start": [40, 20], "end": [0, 20]},
            {"type": "line", "start": [0, 20], "end": [0, 0]},
        ],
        constraints=[
            {"type": "Coincident", "first": 0, "first_pos": 1, "second": -1, "second_pos": 1},
            {"type": "DistanceX", "first": 0, "value": 40.0, "name": "Width"},
            {"type": "DistanceY", "first": 1, "value": 20.0, "name": "Depth"},
        ],
        auto_constrain=True,
        doc_name=doc_info["name"],
    )
    assert len([c for c in edit_res["auto_constraints_added"] if c["type"] == "Coincident"]) == 4
    assert edit_res["degrees_of_freedom"] == 0
    assert edit_res["fully_constrained"] is True

    # Modify datum by constraint name cleanly
    modeling.edit_sketch(
        sk["name"],
        update_constraints=[{"name": "Depth", "value": 20.0}],
        doc_name=doc_info["name"],
    )
    diag = sketcher.get_sketch_diagnostics(sk["name"], doc_name=doc_info["name"])
    assert diag["geometry_count"] == 4
    assert diag["open_vertex_count"] == 0

    pad = modeling.pad_sketch(sk["name"], length=10.0, doc_name=doc_info["name"])
    # Parametric expression binding + dimension edit via set_property
    partdesign.set_property(
        pad["name"],
        "Length",
        expression="Params.PadHeight",
        doc_name=doc_info["name"],
    )
    shape = inspection.analyze_shape(pad["name"], deep_check=True, doc_name=doc_info["name"])
    assert shape["is_valid"] is True
    assert shape["bop_check_passed"] is True
    assert abs(shape["volume"] - 12000.0) < 1e-3

    # Topology query + measure_distance + Thickness hollowing
    z_edges = inspection.find_edges(pad["name"], direction="Z", length=15.0, doc_name=doc_info["name"])
    assert z_edges["count"] == 4
    top_faces = inspection.find_faces(pad["name"], normal="+Z", doc_name=doc_info["name"])
    assert top_faces["count"] == 1
    bot_faces = inspection.find_faces(pad["name"], normal="-Z", doc_name=doc_info["name"])
    assert bot_faces["count"] == 1
    dist = inspection.measure_distance(
        pad["name"],
        top_faces["face_names"][0],
        pad["name"],
        bot_faces["face_names"][0],
        doc_name=doc_info["name"],
    )
    assert abs(dist["min_distance"] - 15.0) < 1e-3

    thick = partdesign.partdesign_thickness(
        pad["name"],
        faces=top_faces["face_names"],
        thickness=2.0,
        inside=True,
        doc_name=doc_info["name"],
    )
    thick_shape = inspection.analyze_shape(thick["name"], doc_name=doc_info["name"])
    assert thick_shape["is_valid"] is True and thick_shape["volume"] < 12000.0

    # Datum plane + multi-body Revolution + Hole + LinearPattern
    datum = partdesign.create_datum_plane(body["name"], attachment_support="XY_Plane", offset=[0, 0, 15], doc_name=doc_info["name"])
    assert datum["name"].startswith("DatumPlane")

    body2 = modeling.create_partdesign_body("Body2", doc_name=doc_info["name"])
    sk_rev = modeling.create_sketch(body_name=body2["name"], plane="XY_Plane", name="RevSk", doc_name=doc_info["name"])
    modeling.edit_sketch(
        sk_rev["name"],
        geometries=[{"type": "rectangle", "x": 10, "y": 0, "width": 5, "height": 10}],
        doc_name=doc_info["name"],
    )
    rev = partdesign.partdesign_revolution(sk_rev["name"], angle=360.0, axis="V_Axis", doc_name=doc_info["name"])
    assert inspection.analyze_shape(rev["name"], doc_name=doc_info["name"])["is_valid"] is True

    # TechDraw View + Dimension + Section
    page = modeling.create_techdraw_page(doc_name=doc_info["name"])
    td_view = modeling.add_techdraw_view(page["page"], body["name"], doc_name=doc_info["name"])
    techdraw.add_techdraw_dimension(page["page"], td_view["view"], ["Edge0"], dim_type="Distance", doc_name=doc_info["name"])
    techdraw.add_techdraw_section(
        page["page"],
        td_view["view"],
        section_normal=[0, 0, 1],
        section_origin=[20, 10, 7.5],
        doc_name=doc_info["name"],
    )

    rec = recompute.tracked_recompute(doc_name=doc_info["name"])
    assert rec["new_errors"] == []

    # Save / export STEP + STL / close / open roundtrip
    with tempfile.TemporaryDirectory() as tmpdir:
        fcstd_path = str(Path(tmpdir) / "smoke.FCStd")
        step_path = str(Path(tmpdir) / "smoke.step")
        stl_path = str(Path(tmpdir) / "smoke.stl")
        document.save_document(fcstd_path, doc_name=doc_info["name"])
        step_res = document.export_document(step_path, object_names=[body["name"]], doc_name=doc_info["name"])
        stl_res = document.export_document(stl_path, object_names=[body["name"]], doc_name=doc_info["name"])
        assert step_res["file_size_bytes"] > 0 and stl_res["file_size_bytes"] > 0

        closed = document.close_document(doc_name=doc_info["name"])
        assert closed["closed"] is True
        reopened = document.open_document(fcstd_path)
        assert reopened["opened"] is True

    exp = error_kb.explain_freecad_error("Result has multiple solids")
    assert exp["code"] == "MULTIPLE_SOLIDS"
    print("HEADLESS SMOKE TEST PASSED")


def run_gui(host: str, port: int) -> None:
    import tempfile

    sys.path.insert(0, str(ROOT / "src"))
    from freecad_tutor_mcp.bridge import FreeCADBridge

    bridge = FreeCADBridge(host=host, port=port)
    try:
        ping = bridge.call("ping")
        assert ping["pong"] is True
        wbs = bridge.call("list_workbenches")
        assert "PartDesignWorkbench" in [w["name"] for w in wbs["workbenches"]]
        bridge.call("activate_workbench", {"name": "PartDesignWorkbench"})

        docs = bridge.call("list_documents")
        assert isinstance(docs, dict) and "documents" in docs

        doc = bridge.call("create_document", {"name": "LiveGUISmoke", "activate": True})
        pad_before = bridge.call("find_ui_command", {"query": "Pad"})
        assert pad_before["matches"], "Expected PartDesign_Pad in live command list"

        hl_btn = bridge.call("highlight_ui_element", {"target": "PartDesign_Pad", "duration_s": 2.0})
        assert hl_btn["highlighted"] is True and hl_btn["overlay_rendered"] is True
        hl_clr = bridge.call("highlight_ui_element", {"clear": True})
        assert hl_clr["cleared"] is True

        body = bridge.call("create_partdesign_body", {"name": "Body", "doc_name": doc["name"]})
        sk = bridge.call("create_sketch", {"body_name": body["name"], "doc_name": doc["name"]})
        bridge.call(
            "edit_sketch",
            {
                "sketch_name": sk["name"],
                "geometries": [{"type": "polyline", "points": [[0, 0], [30, 0], [30, 20], [0, 20]], "closed": True}],
                "doc_name": doc["name"],
            },
        )
        pad = bridge.call("pad_sketch", {"sketch_name": sk["name"], "length": 10.0, "doc_name": doc["name"]})
        z_edges = bridge.call("find_edges", {"object_name": pad["name"], "direction": "Z", "select_matches": True, "doc_name": doc["name"]})
        assert z_edges["count"] == 4 and z_edges["selected_in_gui"] is True

        view_set = bridge.call("set_view", {"view_name": "Isometric", "focus_object": pad["name"]})
        assert view_set["view_name"] == "Isometric"

        vp_shot = bridge.call("get_view", {"view_name": "Current", "capture_scope": "viewport"})
        assert vp_shot["capture_scope"] == "viewport" and len(vp_shot["base64_png"]) > 100

        win_shot = bridge.call("get_view", {"view_name": "Current", "capture_scope": "window"})
        assert win_shot["capture_scope"] == "window" and len(win_shot["base64_png"]) > 100

        bridge.call("set_appearance", {"object_name": pad["name"], "transparency": 30, "doc_name": doc["name"]})

        td_page = bridge.call("create_techdraw_page", {"doc_name": doc["name"]})
        bridge.call("add_techdraw_view", {"page_name": td_page["page"], "object_name": body["name"], "doc_name": doc["name"]})
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_out = str(Path(tmpdir) / "live_sheet.pdf")
            pdf_res = bridge.call("export_techdraw_page", {"page_name": td_page["page"], "path": pdf_out, "doc_name": doc["name"]})
            assert pdf_res["exported"] is True and pdf_res["file_size_bytes"] > 0

        activity = bridge.call("get_recent_activity", {"limit": 20, "doc_name": doc["name"]})
        assert activity["observer_active"] is True and activity["count"] > 0

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
