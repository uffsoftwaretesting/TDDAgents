"""
How an AskUserQuestion call is named and shown.

Ported from `reference/claude-code/src/tools/AskUserQuestionTool/AskUserQuestionTool.tsx` ->
`userFacingName` (`''`: no name) and `renderToolUseMessage` (`null`: no row); the questions are
the permission dialog's.
"""

from __future__ import annotations

from typing import Any


def user_facing_name(input: dict[str, Any] | None) -> str:
    """`userFacingName`: none."""
    return ""


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    """`renderToolUseMessage`: no row."""
    return None


__all__ = ["render_tool_use_message", "user_facing_name"]
