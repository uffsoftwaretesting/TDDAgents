"""
How a ReadFile call is named and shown.

Ported from `reference/claude-code/src/tools/FileReadTool/UI.tsx` -> `userFacingName`,
`renderToolUseMessage`. Not ported: the plan-file and agent-output names (`Reading Plan`,
`Read agent output`), which need the session's plan directory and task registry, and the
non-verbose `getDisplayPath` shortening, which needs the tracked cwd.
"""

from __future__ import annotations

from typing import Any


def user_facing_name(input: dict[str, Any] | None) -> str:
    return "Read"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    file_path = input.get("file_path")
    if not file_path:
        return None
    display = str(file_path)
    pages = input.get("pages")
    if pages:
        return f"{display} · pages {pages}"
    offset, limit = input.get("offset"), input.get("limit")
    if verbose and (offset or limit):
        start = offset or 1
        line_range = f"lines {start}-{start + limit - 1}" if limit else f"from line {start}"
        return f"{display} · {line_range}"
    return display
