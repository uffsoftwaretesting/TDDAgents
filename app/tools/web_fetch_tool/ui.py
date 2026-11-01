"""
How a WebFetch call is named and shown.

Ported from `reference/claude-code/src/tools/WebFetchTool/UI.tsx` -> `renderToolUseMessage` and
`getToolUseSummary` and `WebFetchTool.ts` -> `userFacingName`, `getActivityDescription`.
"""

from __future__ import annotations

from typing import Any

from app.constants.tool_limits import TOOL_SUMMARY_MAX_LENGTH
from app.utils.truncate import truncate


def user_facing_name(input: dict[str, Any] | None) -> str:
    return "Fetch"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    url = input.get("url")
    if not url:
        return None
    if verbose:
        prompt = input.get("prompt")
        return f'url: "{url}"' + (f', prompt: "{prompt}"' if prompt else "")
    return str(url)


def get_tool_use_summary(input: dict[str, Any] | None, context: Any = None) -> str | None:
    """`getToolUseSummary`: the url, truncated to `TOOL_SUMMARY_MAX_LENGTH` columns."""
    if not input or not input.get("url"):
        return None
    return truncate(str(input["url"]), TOOL_SUMMARY_MAX_LENGTH)


def get_activity_description(input: dict[str, Any] | None, context: Any = None) -> str | None:
    """`getActivityDescription`."""
    summary = get_tool_use_summary(input, context)
    return f"Fetching {summary}" if summary else "Fetching web page"
