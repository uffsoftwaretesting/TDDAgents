"""
How a TaskOutput call is named and shown.

Ported from `reference/claude-code/src/tools/TaskOutputTool/TaskOutputTool.tsx` ->
`userFacingName` and `renderToolUseMessage`: `non-blocking` for a call with `block: false`, else
the name alone. Not ported, as React-only: `renderToolUseTag` (the task id beside the name).
"""

from __future__ import annotations

from typing import Any


def user_facing_name(input: dict[str, Any] | None) -> str:
    """`userFacingName`."""
    return "Task Output"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    """`renderToolUseMessage`: `block` defaults to true."""
    if not input.get("block", True):
        return "non-blocking"
    return ""


__all__ = ["render_tool_use_message", "user_facing_name"]
