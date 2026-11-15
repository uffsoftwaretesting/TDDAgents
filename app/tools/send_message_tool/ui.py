"""
How a SendMessage call is named and shown.

Ported from `reference/claude-code/src/tools/SendMessageTool/SendMessageTool.ts` ->
`userFacingName` and `UI.tsx` -> `renderToolUseMessage`, which shows a row only for a structured
`plan_approval_response` message ("approve plan from: …"); a plain-text message has none.

*Divergence:* the structured messages are not ported (`message` is plain text only), so there is
never a row.
"""

from __future__ import annotations

from typing import Any


def user_facing_name(input: dict[str, Any] | None) -> str:
    """`userFacingName`."""
    return "SendMessage"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    """`renderToolUseMessage`: no row for a plain-text message."""
    return None


__all__ = ["render_tool_use_message", "user_facing_name"]
