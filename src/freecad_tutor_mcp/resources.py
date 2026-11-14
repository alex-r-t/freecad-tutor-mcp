"""Curated FreeCAD 1.0/1.1+ reference guides exposed as MCP Resources."""

from typing import Any


def register_resources(mcp: Any) -> None:
    """Register FreeCAD course reference guides on the FastMCP server."""

    @mcp.resource("freecad://guides/partdesign-vs-part")
    def guide_partdesign_vs_part() -> str:
        """Compare PartDesign (feature-based single solid) vs. Part (CSG booleans)."""
        return """# FreeCAD Mental Models: PartDesign vs. Part Workbench

## 1. PartDesign Workbench (Parametric Feature-Based Modeling)
* **Mental model:** Sculpting *one* contiguous solid step-by-step inside a **Body** (`PartDesign::Body`).
* **Workflow:** Create Body -> Create Sketch on Origin Plane -> **Pad** (add material) -> Create next Sketch -> **Pocket** (remove material) -> **Fillet/Chamfer** (dress-up).
* **Common beginner pitfalls:**
  * Trying to Pad/Pocket without an active `Body`.
  * Creating two disconnected islands inside one `Body` (PartDesign requires a single contiguous solid per Body).
  * Leaving a Sketch with open endpoints (a profile must be a closed loop to Pad/Pocket).

## 2. Part Workbench (Constructive Solid Geometry / CSG)
* **Mental model:** Combining independent primitive solids (`Box`, `Cylinder`, `Sphere`, `Cone`) using Boolean operations (`Fuse`, `Cut`, `Common`).
* **Workflow:** Add primitives -> Position via `Placement` -> Select Base then Tool -> **Cut** / **Fuse**.
* **When to prefer which:**
  * Use **PartDesign** for most mechanical/engineering parts driven by 2D dimensioned sketches.
  * Use **Part** when combining multiple simple primitives or intersecting unrelated shapes.
  * **Rule of thumb:** Avoid mixing Part Booleans directly on features *inside* a PartDesign Body; apply Booleans to the whole `Body` instead.
"""

    @mcp.resource("freecad://guides/sketcher-constraints")
    def guide_sketcher_constraints() -> str:
        """Guide to Sketcher constraints, Degrees of Freedom (DOF), and conflicts."""
        return """# Sketcher Constraint Strategy (FreeCAD 1.0 / 1.1+)

## Goal: Fully Constrained Sketch (`DoF == 0`, green lines)
1. **Draw rough shape first** (lines, arcs, circles) without worrying about exact sizes.
2. **Apply Geometric Constraints first:**
   * `Coincident` (connect endpoints into a closed loop), `Horizontal`, `Vertical`, `Parallel`, `Perpendicular`, `Tangent`, `Equal`, `Symmetric`.
   * Anchor one corner/center to the **Origin** (`0, 0`) early so the whole sketch doesn't float.
3. **Apply Dimensional Constraints last:**
   * Lengths, radii/diameters, angles.
4. **Troubleshooting:**
   * **Under-constrained (`DoF > 0`):** Drag a blue/white vertex or edge in the 3D view—whatever moves shows what's missing!
   * **Redundant / Conflicting constraints:** Delete the highlighted constraint(s) reported by Solver Messages (or `get_sketch_diagnostics`), or toggle one to a **Reference (blue) constraint** if you just want to read the measurement.
   * **Construction geometry (blue dashed):** Helper lines/circles ignored when Padding/Pocketing.
"""

    @mcp.resource("freecad://guides/tnp-best-practices")
    def guide_tnp_best_practices() -> str:
        """Guide to avoiding the Topological Naming Problem (TNP) in FreeCAD."""
        return """# Avoiding Broken Models (Topological Naming Problem / TNP)

FreeCAD 1.0/1.1+ includes topological naming mitigations, **but** attaching sketches directly to generated faces (`Face1`, `Face4`) can still break downstream features if an earlier edit changes the face count.

## Best Practices
1. **Attach sketches to Origin planes** (`XY_Plane`, `XZ_Plane`, `YZ_Plane`) whenever possible and offset them via **Attachment Offset (`Position.z`)**.
2. **Use Datum Planes** (attached to Origin planes + offset/expressions) instead of generated solid faces.
3. **Reference master dimensions via Expressions or a Master Sketch** rather than external geometry edges on late features.
4. **Apply Fillets and Chamfers last** at the bottom of the feature tree—never attach a sketch to a filleted face.
"""

    @mcp.resource("freecad://guides/techdraw-basics")
    def guide_techdraw_basics() -> str:
        """Guide to creating 2D engineering drawings in the TechDraw workbench."""
        return """# TechDraw 2D Engineering Drawings (FreeCAD 1.0 / 1.1+)

## Basic Workflow
1. Finish (or stabilize) your 3D `PartDesign::Body` or `Part` solid.
2. Switch to the **TechDraw** workbench.
3. Click **Insert Page using Template** (e.g., `A4_Landscape` or `USLetter_Landscape`).
4. Select your `Body` (not an intermediate feature inside it!) in the Model tree and click:
   * **Insert View** (single orthographic/isometric view), or
   * **Insert Projection Group** (Front + Top + Right + Isometric linked views).
5. Select vertices or edges on the drawing view to add **Length / Horizontal / Vertical / Radius / Diameter Dimensions**.
6. *Tip:* If a view stops updating, check **Keep Updated** on the Page or click **Redraw Page**. Note that 3D viewport screenshots only work when a 3D view tab (not a TechDraw page tab) is in front.
"""
