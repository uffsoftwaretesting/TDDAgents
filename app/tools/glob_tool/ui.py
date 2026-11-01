"""
How a Glob call is named and shown.

Ported from `reference/claude-code/src/tools/GlobTool/UI.tsx` -> `userFacingName`,
`renderToolUseMessage`, `getToolUseSummary`; `GlobTool.ts` -> `getActivityDescription`.
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
    path = input.get("path")
    if not path:
        return f'pattern: "{pattern}"'
    return f'pattern: "{pattern}", path: "{path}"'


def get_tool_use_summary(input: dict[str, Any] | None, context: Any = None) -> str | None:
    """`getToolUseSummary`: the pattern, truncated to `TOOL_SUMMARY_MAX_LENGTH` columns."""
    if not input or not input.get("pattern"):
        return None
    return truncate(str(input["pattern"]), TOOL_SUMMARY_MAX_LENGTH)


def get_activity_description(input: dict[str, Any] | None, context: Any = None) -> str | None:
    """`getActivityDescription`."""
    summary = get_tool_use_summary(input, context)
    return f"Finding {summary}" if summary else "Finding files"
