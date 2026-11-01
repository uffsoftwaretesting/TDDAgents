"""
A command hook's question, as the REPL shows it.

Ported from `reference/claude-code/src/components/hooks/PromptDialog.tsx` (a `PermissionDialog`
titled with the hook's name, the tool's summary dim on the right, the request's message as its
subtitle, and its options to pick from) and `screens/REPL.tsx`'s `requestPrompt` gate:
`feature('HOOK_PROMPTS')` is TDDAgents' `TDDAGENTS_HOOK_PROMPTS` opt-in (user decision
2026-11-01). The terminal is a line printer, so the options are numbered and answered by number.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from app.types.hooks import PromptOption, PromptRequest

HOOK_PROMPTS_ENV = "TDDAGENTS_HOOK_PROMPTS"
#: The error a cancelled prompt rejects with (`REPL.tsx`).
PROMPT_CANCELLED = "Prompt cancelled by user"


class PromptCancelled(Exception):
    """`new Error('Prompt cancelled by user')`."""


def hook_prompts_enabled(env: Mapping[str, str] | None = None) -> bool:
    """`feature('HOOK_PROMPTS')`, here an environment opt-in."""
    value = (os.environ if env is None else env).get(HOOK_PROMPTS_ENV, "")
    return value.strip().lower() in ("1", "true", "yes", "on")


def render_prompt_dialog(title: str, tool_input_summary: str | None, request: PromptRequest) -> str:
    """The dialog: `⚠ <title>  <summary>`, the message, the numbered options."""
    head = f"⚠ {title}" + (f"  {tool_input_summary}" if tool_input_summary else "")
    lines = [head, request.message]
    for number, option in enumerate(request.options, start=1):
        lines.append(f"  {number}. {option.label}" + (f" — {option.description}" if option.description else ""))
    lines.append(f"Choose 1-{len(request.options)}.")
    return "\n".join(lines)


def parse_prompt_choice(options: tuple[PromptOption, ...], line: str) -> str | None:
    """The chosen option's key for `N`, else `None` (asked again)."""
    text = line.strip()
    if not text.isdigit():
        return None
    index = int(text) - 1
    return options[index].key if 0 <= index < len(options) else None


__all__ = [
    "HOOK_PROMPTS_ENV",
    "PROMPT_CANCELLED",
    "PromptCancelled",
    "hook_prompts_enabled",
    "parse_prompt_choice",
    "render_prompt_dialog",
]
