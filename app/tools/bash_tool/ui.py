"""
How a Bash call is named and shown in a permission dialog.

Ported from `reference/claude-code/src/tools/BashTool/UI.tsx` -> `renderToolUseMessage` and
`BashTool.tsx` -> `userFacingName`, `getToolUseSummary`, `getActivityDescription`. `verbose`
returns the command unchanged, as the dialog's "always show the full command" asks; the non-
verbose rendering truncates to `MAX_COMMAND_DISPLAY_LINES` lines and `MAX_COMMAND_DISPLAY_CHARS`
characters. The sed-edit rendering (`parseSedEditCommand`) and the fullscreen comment label are
not ported.
"""

from __future__ import annotations

from typing import Any

from app.constants.tool_limits import TOOL_SUMMARY_MAX_LENGTH
from app.utils.truncate import truncate

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


def get_tool_use_summary(input: dict[str, Any] | None, context: Any = None) -> str | None:
    """`getToolUseSummary`: the description as given, else the command truncated."""
    if not input or not input.get("command"):
        return None
    description = input.get("description")
    if description:
        return str(description)
    return truncate(str(input["command"]), TOOL_SUMMARY_MAX_LENGTH)


def get_activity_description(input: dict[str, Any] | None, context: Any = None) -> str | None:
    """`getActivityDescription`: `Running <description ?? truncated command>`."""
    if not input or not input.get("command"):
        return "Running command"
    description = input.get("description")
    shown = description if description is not None else truncate(str(input["command"]), TOOL_SUMMARY_MAX_LENGTH)
    return f"Running {shown}"
