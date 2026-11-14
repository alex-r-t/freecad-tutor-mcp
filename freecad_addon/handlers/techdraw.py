"""TechDraw 2D engineering drawing handlers (dimensions, section views, PDF/SVG export)."""

import os
from typing import Any

import FreeCAD

from handlers.common import (
    require_gui,
    resolve_doc,
    undo_transaction,
    verify_object_valid,
)


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
    """Add a 2D measurement annotation (Distance, Radius, Diameter, Angle) to a TechDraw view.

    Args:
        page_name: Target TechDraw::DrawPage name.
        view_name: View on that page (e.g. 'View' or 'ProjGroupFront').
        references: Sub-element names in the 2D view (e.g. ``['Edge0']`` or ``['Vertex0', 'Vertex1']``).
        dim_type: FreeCAD DrawViewDimension Type (``Distance``, ``DistanceX``, ``DistanceY``,
            ``Radius``, ``Diameter``, ``Angle``, ``Angle3Pt``, ``Area``).
        format_spec: Optional display format string such as ``"%.1f mm"``.
        x: Optional X coordinate of the dimension label on the sheet (mm).
        y: Optional Y coordinate of the dimension label on the sheet (mm).
        doc_name: Document name (empty string uses ActiveDocument).
    """
    if not references:
        raise ValueError("Provide at least one 2D reference edge or vertex (e.g. ['Edge0']).")

    doc = resolve_doc(doc_name)
    page = doc.getObject(page_name)
    if page is None:
        raise ValueError(f"TechDraw page not found: {page_name}")
    view = doc.getObject(view_name)
    if view is None:
        raise ValueError(f"TechDraw view not found: {view_name}")

    valid_types = (
        "Distance",
        "DistanceX",
        "DistanceY",
        "DistanceZ",
        "Radius",
        "Diameter",
        "Angle",
        "Angle3Pt",
        "Area",
    )
    if dim_type not in valid_types:
        raise ValueError(f"Invalid dim_type '{dim_type}'. Valid: {', '.join(valid_types)}")

    with undo_transaction(doc, "Add TechDraw Dimension"):
        dim = doc.addObject("TechDraw::DrawViewDimension", name or "Dimension")
        dim.Type = dim_type
        dim.References2D = [(view, ref) for ref in references]
        if format_spec and hasattr(dim, "FormatSpec"):
            dim.FormatSpec = format_spec
        if x is not None and hasattr(dim, "X"):
            dim.X = float(x)
        if y is not None and hasattr(dim, "Y"):
            dim.Y = float(y)
        page.addView(dim)
        doc.recompute()
        verify_object_valid(dim, "TechDraw Dimension")

    return {
        "document": doc.Name,
        "page": page.Name,
        "view": view.Name,
        "name": dim.Name,
        "dim_type": dim_type,
        "references": list(references),
    }


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
    """Add a cross-section view (TechDraw::DrawViewSection) cutting through a BaseView."""
    doc = resolve_doc(doc_name)
    page = doc.getObject(page_name)
    if page is None:
        raise ValueError(f"TechDraw page not found: {page_name}")
    base_view = doc.getObject(base_view_name)
    if base_view is None:
        raise ValueError(f"Base TechDraw view not found: {base_view_name}")

    normal = section_normal or [0.0, 0.0, 1.0]
    origin = section_origin or [0.0, 0.0, 0.0]

    with undo_transaction(doc, "Add TechDraw Section"):
        sec = doc.addObject("TechDraw::DrawViewSection", name or "SectionView")
        sec.BaseView = base_view
        if hasattr(base_view, "Source"):
            sec.Source = list(base_view.Source)
        sec.SectionNormal = FreeCAD.Vector(float(normal[0]), float(normal[1]), float(normal[2]))
        sec.SectionOrigin = FreeCAD.Vector(float(origin[0]), float(origin[1]), float(origin[2]))
        if hasattr(sec, "SectionDirection") and section_direction:
            sec.SectionDirection = section_direction
        if scale is not None and hasattr(sec, "Scale"):
            sec.Scale = float(scale)
        elif hasattr(base_view, "Scale") and hasattr(sec, "Scale"):
            sec.Scale = base_view.Scale
        if x is not None and hasattr(sec, "X"):
            sec.X = float(x)
        if y is not None and hasattr(sec, "Y"):
            sec.Y = float(y)
        page.addView(sec)
        doc.recompute()
        verify_object_valid(sec, "TechDraw Section")

    return {
        "document": doc.Name,
        "page": page.Name,
        "base_view": base_view.Name,
        "name": sec.Name,
        "section_normal": normal,
        "section_origin": origin,
    }


def export_techdraw_page(
    page_name: str,
    path: str,
    export_format: str = "",
    doc_name: str = "",
) -> dict[str, Any]:
    """Export a TechDraw::DrawPage sheet to PDF or SVG via TechDrawGui."""
    require_gui()
    import TechDrawGui

    doc = resolve_doc(doc_name)
    page = doc.getObject(page_name)
    if page is None:
        raise ValueError(f"TechDraw page not found: {page_name}")

    abs_path = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    fmt = (export_format or os.path.splitext(abs_path)[1].lstrip(".")).strip().lower()
    if fmt == "pdf":
        TechDrawGui.exportPageAsPdf(page, abs_path)
    elif fmt == "svg":
        TechDrawGui.exportPageAsSvg(page, abs_path)
    else:
        raise ValueError(f"Unsupported TechDraw page export format '{fmt}'. Use 'pdf' or 'svg'.")

    return {
        "exported": True,
        "document": doc.Name,
        "page": page.Name,
        "format": fmt,
        "path": abs_path,
        "file_size_bytes": os.path.getsize(abs_path) if os.path.exists(abs_path) else None,
    }
