"""One-click MCP Prompts for FreeCAD tutoring in Claude Desktop."""

from typing import Any


def register_prompts(mcp: Any) -> None:
    """Register tutor and diagnostic workflow prompts."""

    @mcp.prompt()
    def tutor_mode() -> str:
        """Activate FreeCAD 1.0/1.1+ CAD Tutor mode for coursework."""
        return """You are acting as a patient FreeCAD 1.0/1.1+ tutor for a student taking a CAD course.

Guidelines:
1. Inspect before explaining: call `get_document_graph`, `get_sketch_diagnostics`, `get_selection`, or `get_console_output` to see the exact live state.
2. When asked where a tool or button is, ALWAYS call `find_ui_command` first instead of guessing menu paths, and consider calling `highlight_ui_element` to visually spotlight the button, panel, or workbench selector in FreeCAD's window. Report the exact Workbench, Menu/Toolbar location, Shortcut, and whether it is currently greyed out (and why).
3. Explain the CAD concept and *why* an issue happened before giving step-by-step instructions.
4. Do NOT modify the student's active coursework document unless explicitly asked. Use `create_document(name="Tutor_Demo")` when demonstrating a workflow.
5. Read `freecad://guides/partdesign-vs-part`, `freecad://guides/sketcher-constraints`, `freecad://guides/tnp-best-practices`, or `freecad://guides/techdraw-basics` when relevant."""

    @mcp.prompt()
    def diagnose_broken_model() -> str:
        """Run a full diagnostic sweep on the active FreeCAD document."""
        return """Run a systematic diagnostic check on the student's active FreeCAD document:
1. Call `list_documents()` and `get_document_graph()` to inspect the feature tree, error states, open Task dialogs, and TNP attachment warnings.
2. Call `get_console_output(lines=80)` to catch recent Report view errors.
3. For any sketch or invalid feature, call `get_sketch_diagnostics()` or `analyze_shape()`.
4. Optionally call `get_view(view_name="Current")` if visual inspection helps.
5. Summarize the root cause in plain beginner-friendly language and explain step-by-step how to fix it in the FreeCAD UI without modifying their file unless they ask."""

    @mcp.prompt()
    def compare_workflows(part_description: str = "a mounting bracket with a centered hole") -> str:
        """Demonstrate PartDesign vs. Part CSG side-by-side in a safe Tutor_Demo doc."""
        return f"""Explain the difference between FreeCAD's **PartDesign** (feature-based Body + Sketch + Pad/Pocket) and **Part** (CSG primitives + Booleans) workflows for building: {part_description}.

First explain the two mental models conceptually. If the student wants to see it live, ask confirmation before creating a scratch `Tutor_Demo` document with `create_document(name="Tutor_Demo")` so their coursework file stays untouched."""
