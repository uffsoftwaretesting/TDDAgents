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


def search_result_summary(count: int, count_label: str, content: str | None, verbose: bool,
                          secondary_count: int | None = None, secondary_label: str | None = None) -> str:
    """`SearchResultSummary`: `Found N files` (`across M files`), then the content when verbose."""
    label = count_label if count == 0 or count > 1 else count_label[:-1]
    text = f"Found {count} {label}"
    if secondary_count is not None and secondary_label:
        secondary = secondary_label if secondary_count == 0 or secondary_count > 1 else secondary_label[:-1]
        text += f" across {secondary_count} {secondary}"
    if verbose and content:
        return f"{text}\n{content}"
    return text


def render_tool_result_message(output: Any, verbose: bool) -> str | None:
    """`renderToolResultMessage` (Glob reuses it): the count found, by output mode."""
    if not isinstance(output, dict):
        return None
    mode = output.get("mode") or "files_with_matches"
    if mode == "content":
        return search_result_summary(int(output.get("numLines") or 0), "lines", output.get("content"), verbose)
    if mode == "count":
        return search_result_summary(int(output.get("numMatches") or 0), "matches", output.get("content"), verbose,
                                     int(output.get("numFiles") or 0), "files")
    filenames = [str(f) for f in output.get("filenames") or []]
    return search_result_summary(int(output.get("numFiles") or 0), "files", "\n".join(filenames), verbose)
