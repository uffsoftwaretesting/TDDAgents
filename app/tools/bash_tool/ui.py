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

import json
import re
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


#: `BashToolResultMessage`'s `SHELL_CWD_RESET_PATTERN`.
SHELL_CWD_RESET_PATTERN = re.compile(r"(?:^|\n)(Shell cwd was reset to .+)$")


def try_format_json(line: str) -> str:
    """`tryFormatJson`: a JSON line pretty-printed, unless the round trip would change it."""
    try:
        parsed = json.loads(line)
    except (ValueError, TypeError):
        return line
    stringified = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    original = re.sub(r"\s+", "", line.replace("\\/", "/"))
    if original != re.sub(r"\s+", "", stringified):
        return line
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def try_json_format_content(content: str) -> str:
    """`tryJsonFormatContent`: each line through `try_format_json`, up to 10 000 characters."""
    if len(content) > 10_000:
        return content
    return "\n".join(try_format_json(line) for line in content.split("\n"))


def render_tool_result_message(output: Any, verbose: bool) -> str | None:
    """`BashToolResultMessage`: stdout, stderr, the cwd-reset warning, or what ran silently."""
    if not isinstance(output, dict):
        return None
    stdout = str(output.get("stdout") or "")
    stderr = str(output.get("stderr") or "")
    match = SHELL_CWD_RESET_PATTERN.search(stderr)
    warning = match.group(1) if match else None
    if match:
        stderr = SHELL_CWD_RESET_PATTERN.sub("", stderr).strip()
    blocks = []
    if stdout:
        blocks.append(try_json_format_content(stdout))
    if stderr.strip():
        blocks.append(try_json_format_content(stderr))
    if warning:
        blocks.append(warning)
    if not blocks:
        if output.get("backgroundTaskId"):
            blocks.append("Running in the background (↓ to manage)")
        else:
            blocks.append(str(output.get("returnCodeInterpretation") or "")
                          or ("Done" if output.get("noOutputExpected") else "(No output)"))
    return "\n".join(blocks)
