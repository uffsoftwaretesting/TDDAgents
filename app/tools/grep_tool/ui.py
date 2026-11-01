"""
How a Grep call is named and shown.

Ported from `reference/claude-code/src/tools/GrepTool/UI.tsx` -> `renderToolUseMessage` and
`GrepTool.ts` -> `userFacingName`.
"""

from __future__ import annotations

from typing import Any


def user_facing_name(input: dict[str, Any] | None) -> str:
    return "Search"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    pattern = input.get("pattern")
    if not pattern:
        return None
    parts = [f'pattern: "{pattern}"']
    path = input.get("path")
    if path:
        parts.append(f'path: "{path}"')
    return ", ".join(parts)
