"""
How a Bash call is named and shown in a permission dialog.

Ported from `reference/claude-code/src/tools/BashTool/UI.tsx` -> `renderToolUseMessage` and
`BashTool.tsx` -> `userFacingName`. `verbose` returns the command unchanged, as the dialog's
"always show the full command" asks; the non-verbose rendering truncates to
`MAX_COMMAND_DISPLAY_LINES` lines and `MAX_COMMAND_DISPLAY_CHARS` characters. The sed-edit
rendering (`parseSedEditCommand`) and the fullscreen comment label are not ported.
"""

from __future__ import annotations

from typing import Any

#: `UI.tsx` -> `MAX_COMMAND_DISPLAY_LINES` / `MAX_COMMAND_DISPLAY_CHARS`.
MAX_COMMAND_DISPLAY_LINES = 2
MAX_COMMAND_DISPLAY_CHARS = 160


def user_facing_name(input: dict[str, Any] | None) -> str:
    """`BashTool.userFacingName`."""
    return "Bash"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    """`renderToolUseMessage`: the command, whole when `verbose`."""
    command = input.get("command")
    if not command:
        return None
    text = str(command)
    if verbose:
        return text
    lines = text.split("\n")
    truncated = len(lines) > MAX_COMMAND_DISPLAY_LINES or len(text) > MAX_COMMAND_DISPLAY_CHARS
    if not truncated:
        return text
    shown = "\n".join(lines[:MAX_COMMAND_DISPLAY_LINES])
    if len(shown) > MAX_COMMAND_DISPLAY_CHARS:
        shown = shown[:MAX_COMMAND_DISPLAY_CHARS]
    return f"{shown.strip()}…"
