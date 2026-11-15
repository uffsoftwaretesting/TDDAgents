"""
How a TaskStop call is named and shown.

Ported from `reference/claude-code/src/tools/TaskStopTool/TaskStopTool.ts` -> `userFacingName`
(the external build's `Stop Task`) and `UI.tsx` -> `renderToolUseMessage` (`''`: the name alone).
"""

from __future__ import annotations

from typing import Any


def user_facing_name(input: dict[str, Any] | None) -> str:
    """`userFacingName`."""
    return "Stop Task"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    """`renderToolUseMessage`: the name alone."""
    return ""


__all__ = ["render_tool_use_message", "user_facing_name"]
