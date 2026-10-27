"""
The Skill tool's prompt and the skill listing's budget (Phase 8).

Ported from `reference/claude-code/src/tools/SkillTool/prompt.ts` -> `getCharBudget`,
`getCommandDescription`, `formatCommandDescription`, `formatCommandsWithinBudget`, `getPrompt`;
`utils/truncate.ts` -> `truncate`, `truncateToWidth`.

The listing goes to the model as the `skill_listing` attachment, not in the tool prompt, and is
capped at 1% of the context window (in characters). Bundled skills — TDDAgents' TDD roster —
always keep their full description; the rest are trimmed to share what is left, or reduced to
their names. Widths are `len()`: upstream's `stringWidth` counts terminal columns, which differ
only for wide (CJK, emoji) characters.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from app.skills.load_skills_dir import SkillCommand
from app.utils.frontmatter_parser import parse_markdown_frontmatter

SKILL_TOOL_NAME = "Skill"
PROMPT_PATH = Path(__file__).with_name("prompt.md")

SKILL_BUDGET_CONTEXT_PERCENT = 0.01
CHARS_PER_TOKEN = 4
#: Fallback: 1% of 200k x 4.
DEFAULT_CHAR_BUDGET = 8_000
MAX_LISTING_DESC_CHARS = 250
MIN_DESC_LENGTH = 20
#: Upstream's env name, unrenamed: it is the documented knob for this budget.
CHAR_BUDGET_ENV = "SLASH_COMMAND_TOOL_CHAR_BUDGET"
ELLIPSIS = "…"


def _env_number(name: str) -> float:
    """`Number(process.env[name])`: 0 (falsy) when unset or not a number."""
    try:
        return float(os.environ.get(name, "").strip() or 0)
    except ValueError:
        return 0


def get_char_budget(context_window_tokens: int | None = None) -> int:
    """`getCharBudget`."""
    override = _env_number(CHAR_BUDGET_ENV)
    if override:
        return int(override)
    if context_window_tokens:
        return int(context_window_tokens * CHARS_PER_TOKEN * SKILL_BUDGET_CONTEXT_PERCENT)
    return DEFAULT_CHAR_BUDGET


def truncate(text: str, max_width: int) -> str:
    """`truncate` / `truncateToWidth`: at most `max_width` columns, the last one an ellipsis."""
    if len(text) <= max_width:
        return text
    if max_width <= 1:
        return ELLIPSIS
    return text[: max_width - 1] + ELLIPSIS


def get_command_description(cmd: SkillCommand) -> str:
    """`getCommandDescription`: description plus `when_to_use`, capped per entry."""
    desc = f"{cmd.description} - {cmd.when_to_use}" if cmd.when_to_use else cmd.description
    return desc[: MAX_LISTING_DESC_CHARS - 1] + ELLIPSIS if len(desc) > MAX_LISTING_DESC_CHARS else desc


def format_command_description(cmd: SkillCommand) -> str:
    """`formatCommandDescription`."""
    return f"- {cmd.name}: {get_command_description(cmd)}"


def format_commands_within_budget(commands: Sequence[SkillCommand], context_window_tokens: int | None = None) -> str:
    """`formatCommandsWithinBudget`."""
    if not commands:
        return ""
    budget = get_char_budget(context_window_tokens)
    full = [format_command_description(cmd) for cmd in commands]
    if sum(len(entry) for entry in full) + len(full) - 1 <= budget:
        return "\n".join(full)

    bundled = {i for i, cmd in enumerate(commands) if cmd.source == "bundled"}
    rest = [cmd for i, cmd in enumerate(commands) if i not in bundled]
    if not rest:
        return "\n".join(full)
    remaining = budget - sum(len(full[i]) + 1 for i in bundled)
    name_overhead = sum(len(cmd.name) + 4 for cmd in rest) + len(rest) - 1
    max_desc_len = (remaining - name_overhead) // len(rest)
    if max_desc_len < MIN_DESC_LENGTH:
        return "\n".join(full[i] if i in bundled else f"- {cmd.name}" for i, cmd in enumerate(commands))
    return "\n".join(
        full[i] if i in bundled else f"- {cmd.name}: {truncate(get_command_description(cmd), max_desc_len)}"
        for i, cmd in enumerate(commands)
    )


def get_prompt() -> str:
    """`getPrompt`: the Skill tool's description (the listing travels as an attachment)."""
    return parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1]


__all__ = [
    "CHAR_BUDGET_ENV",
    "DEFAULT_CHAR_BUDGET",
    "MAX_LISTING_DESC_CHARS",
    "MIN_DESC_LENGTH",
    "SKILL_TOOL_NAME",
    "format_command_description",
    "format_commands_within_budget",
    "get_char_budget",
    "get_command_description",
    "get_prompt",
    "truncate",
]
