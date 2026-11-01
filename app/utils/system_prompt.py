"""
The system prompt a main-thread request sends, from its candidates.

Ported from `reference/claude-code/src/utils/systemPrompt.ts` -> `buildEffectiveSystemPrompt`:
an agent's own prompt wins, then a custom prompt (`--system-prompt`), else the default
sections; an appended prompt (`--append-system-prompt`) always follows.

The coordinator branch is the agent branch here: TDDAgents puts the coordinator's prompt on the
main thread's `ToolContext.agent_system_prompt` (`app/cli/print.py::coordinator_context`), and
upstream's coordinator branch, `[coordinatorPrompt, appendSystemPrompt?]`, has the same shape.

Not ported: `overrideSystemPrompt` (loop mode), a main-thread agent definition (`--agent`), and
the proactive branch (`PROACTIVE` / `KAIROS`, off in the external build). None exists here.
"""

from __future__ import annotations

from collections.abc import Sequence


def build_effective_system_prompt(
    *,
    agent_system_prompt: Sequence[str] | None,
    custom_system_prompt: str | None,
    default_system_prompt: Sequence[str],
    append_system_prompt: str | None,
) -> list[str]:
    """`buildEffectiveSystemPrompt`."""
    if agent_system_prompt is not None:
        base = list(agent_system_prompt)
    elif custom_system_prompt:
        base = [custom_system_prompt]
    else:
        base = list(default_system_prompt)
    return [*base, *([append_system_prompt] if append_system_prompt else [])]


__all__ = ["build_effective_system_prompt"]
