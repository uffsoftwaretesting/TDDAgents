"""
How an Edit call is named and shown.

Ported from `reference/claude-code/src/tools/FileEditTool/UI.tsx` -> `userFacingName`,
`renderToolUseMessage`. Not ported: the plan-file name (`Updated plan`) and the hashline
`edits` form, which TDDAgents' Edit does not take.
"""

from __future__ import annotations

from typing import Any


def user_facing_name(input: dict[str, Any] | None) -> str:
    """`Create` for an empty `old_string` (a new file), else `Update`."""
    if input is not None and input.get("old_string") == "":
        return "Create"
    return "Update"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    file_path = input.get("file_path")
    return str(file_path) if file_path else None
