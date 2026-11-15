"""
How a Skill call is shown.

Ported from `reference/claude-code/src/tools/SkillTool/UI.tsx` -> `renderToolUseMessage`: the
skill's name (`/<name>` for a command from the legacy `commands/` folder). The name is
`buildTool`'s default, the tool's own (`Skill`).

*Divergence:* no skill here is loaded from the legacy folder (`commands_DEPRECATED`), so the name
is always shown as given.
"""

from __future__ import annotations

from typing import Any


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    """`renderToolUseMessage`: the skill's name, or no row until it is there."""
    skill = input.get("skill")
    return str(skill) if skill else None


__all__ = ["render_tool_use_message"]
