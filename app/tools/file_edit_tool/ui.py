"""
How an Edit call is named and shown.

Ported from `reference/claude-code/src/tools/FileEditTool/UI.tsx` -> `userFacingName`,
`renderToolUseMessage`, `getToolUseSummary`, `renderToolResultMessage`; `FileEditTool.ts` ->
`getActivityDescription`. `render_tool_use_message` takes the `context` that stands in for
upstream's process-wide cwd (the display path). Not ported: the plan-file renderings (`Updated
plan`, `/plan to preview`) and the hashline `edits` form, which TDDAgents' Edit does not take.
"""

from __future__ import annotations

from typing import Any

from app.components.file_edit_tool_updated_message import file_edit_tool_updated_message
from app.utils.file import get_display_path


def user_facing_name(input: dict[str, Any] | None) -> str:
    """`Create` for an empty `old_string` (a new file), else `Update`."""
    if input is not None and input.get("old_string") == "":
        return "Create"
    return "Update"


def render_tool_use_message(input: dict[str, Any], verbose: bool, context: Any = None) -> str | None:
    """`renderToolUseMessage`: the path as given when `verbose`, else its display path."""
    file_path = input.get("file_path")
    if not file_path:
        return None
    return str(file_path) if verbose else get_display_path(str(file_path), context)


def get_tool_use_summary(input: dict[str, Any] | None, context: Any = None) -> str | None:
    """`getToolUseSummary`: the file's display path."""
    if not input or not input.get("file_path"):
        return None
    return get_display_path(str(input["file_path"]), context)


def get_activity_description(input: dict[str, Any] | None, context: Any = None) -> str | None:
    """`getActivityDescription`."""
    summary = get_tool_use_summary(input, context)
    return f"Editing {summary}" if summary else "Editing file"


def render_tool_result_message(output: Any, verbose: bool) -> str | None:
    """`renderToolResultMessage`: `FileEditToolUpdatedMessage` of the edit's hunks."""
    if not isinstance(output, dict):
        return None
    return file_edit_tool_updated_message(output.get("structuredPatch") or [], verbose)
