"""
How a Glob call is named and shown.

Ported from `reference/claude-code/src/tools/GlobTool/UI.tsx` -> `userFacingName`,
`renderToolUseMessage`.
"""

from __future__ import annotations

from typing import Any


def user_facing_name(input: dict[str, Any] | None) -> str:
    return "Search"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    pattern = input.get("pattern")
    if not pattern:
        return None
    path = input.get("path")
    if not path:
        return f'pattern: "{pattern}"'
    return f'pattern: "{pattern}", path: "{path}"'
