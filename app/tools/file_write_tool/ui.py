"""
How a WriteFile call is named and shown.

Ported from `reference/claude-code/src/tools/FileWriteTool/UI.tsx` -> `userFacingName`,
`renderToolUseMessage`, `getToolUseSummary`; `FileWriteTool.ts` -> `getActivityDescription`. Not
ported: the plan-file name (`Updated plan`).
"""

from __future__ import annotations

from typing import Any

from app.utils.file import get_display_path


def user_facing_name(input: dict[str, Any] | None) -> str:
    return "Write"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    file_path = input.get("file_path")
    return str(file_path) if file_path else None


def get_tool_use_summary(input: dict[str, Any] | None, context: Any = None) -> str | None:
    """`getToolUseSummary`: the file's display path."""
    if not input or not input.get("file_path"):
        return None
    return get_display_path(str(input["file_path"]), context)


def get_activity_description(input: dict[str, Any] | None, context: Any = None) -> str | None:
    """`getActivityDescription`."""
    summary = get_tool_use_summary(input, context)
    return f"Writing {summary}" if summary else "Writing file"
