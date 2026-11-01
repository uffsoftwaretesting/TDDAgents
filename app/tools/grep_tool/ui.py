"""
How a Grep call is named and shown.

Ported from `reference/claude-code/src/tools/GrepTool/UI.tsx` -> `renderToolUseMessage` and
`getToolUseSummary` and `GrepTool.ts` -> `userFacingName`, `getActivityDescription`.
"""

from __future__ import annotations

from typing import Any

from app.constants.tool_limits import TOOL_SUMMARY_MAX_LENGTH
from app.utils.truncate import truncate


def user_facing_name(input: dict[str, Any] | None) -> str:
    return "Search"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    pattern = input.get("pattern")
    if not pattern:
        return None
    parts = [f'pattern: "{pattern}"']
    path = input.get("path")
    if path:
        parts.append(f'path: "{path}"')
    return ", ".join(parts)


def get_tool_use_summary(input: dict[str, Any] | None, context: Any = None) -> str | None:
    """`getToolUseSummary`: the pattern, truncated to `TOOL_SUMMARY_MAX_LENGTH` columns."""
    if not input or not input.get("pattern"):
        return None
    return truncate(str(input["pattern"]), TOOL_SUMMARY_MAX_LENGTH)


def get_activity_description(input: dict[str, Any] | None, context: Any = None) -> str | None:
    """`getActivityDescription`."""
    summary = get_tool_use_summary(input, context)
    return f"Searching for {summary}" if summary else "Searching"
