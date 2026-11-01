"""
How a WriteFile call is named and shown.

Ported from `reference/claude-code/src/tools/FileWriteTool/UI.tsx` -> `userFacingName`,
`renderToolUseMessage`. Not ported: the plan-file name (`Updated plan`).
"""

from __future__ import annotations

from typing import Any


def user_facing_name(input: dict[str, Any] | None) -> str:
    return "Write"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    file_path = input.get("file_path")
    return str(file_path) if file_path else None
