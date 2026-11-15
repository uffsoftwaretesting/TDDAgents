"""
How a WriteFile call is named and shown.

Ported from `reference/claude-code/src/tools/FileWriteTool/UI.tsx` -> `userFacingName`,
`renderToolUseMessage`, `getToolUseSummary`, `countLines`, `MAX_LINES_TO_RENDER`,
`FileWriteToolCreatedMessage`, `renderToolResultMessage`; `FileWriteTool.ts` ->
`getActivityDescription`. The renderers take the `context` that stands in for upstream's
process-wide cwd (the display path, the path a created file is shown relative to).

*Divergences:* the plan-file renderings (`Updated plan`, `/plan to preview`) and the `condensed`
style (upstream's subagent view) are not ported; the created file's code is plain text (no syntax
highlighting), and the `… +N lines` hint has no `(ctrl+o to expand)`: the REPL's `--verbose` is the
expanded view.
"""

from __future__ import annotations

from typing import Any

from app.components.file_edit_tool_updated_message import file_edit_tool_updated_message
from app.utils.file import get_absolute_and_relative_paths, get_display_path

#: `MAX_LINES_TO_RENDER`: the created file's lines shown unless verbose.
MAX_LINES_TO_RENDER = 10
#: `EOL`: model output uses `\n` on every platform.
EOL = "\n"


def user_facing_name(input: dict[str, Any] | None) -> str:
    return "Write"


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
    return f"Writing {summary}" if summary else "Writing file"


def count_lines(content: str) -> int:
    """`countLines`: a trailing newline ends the last line rather than starting a new one."""
    parts = content.split(EOL)
    return len(parts) - 1 if content.endswith(EOL) else len(parts)


def file_write_tool_created_message(file_path: str, content: str, verbose: bool, context: Any = None) -> str:
    """
    `FileWriteToolCreatedMessage` with `verbose`: `Wrote N lines to <path>` and the whole content. Without
    it, upstream's `condensed` line alone, the path relative to the project (the REPL's view, user decision
    2026-11-14; upstream's normal view adds the first `MAX_LINES_TO_RENDER` lines and how many are left).
    """
    content_with_fallback = content or "(No content)"
    num_lines = count_lines(content)
    if not verbose:
        # `style === 'condensed' && !verbose`: the REPL's view (user decision 2026-11-14).
        return f"Wrote {num_lines} lines to {get_absolute_and_relative_paths(file_path, context)[1] or ''}"
    plus_lines = num_lines - MAX_LINES_TO_RENDER
    shown_path = file_path if verbose else (get_absolute_and_relative_paths(file_path, context)[1] or "")
    code = content_with_fallback if verbose else "\n".join(content_with_fallback.split("\n")[:MAX_LINES_TO_RENDER])
    parts = [f"Wrote {num_lines} lines to {shown_path}", code]
    if not verbose and plus_lines > 0:
        parts.append(f"… +{plus_lines} {'line' if plus_lines == 1 else 'lines'}")
    return "\n".join(parts)


def render_tool_result_message(output: Any, verbose: bool, context: Any = None) -> str | None:
    """`renderToolResultMessage`: a created file's lines, or an updated file's count and hunks."""
    if not isinstance(output, dict):
        return None
    if output.get("type") == "create":
        return file_write_tool_created_message(str(output.get("filePath") or ""), str(output.get("content") or ""),
                                               verbose, context)
    if output.get("type") == "update":
        return file_edit_tool_updated_message(output.get("structuredPatch") or [], verbose)
    return None
