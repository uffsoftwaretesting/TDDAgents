"""
The system prompt a main-thread request sends, from its candidates.

Ported from `reference/claude-code/src/utils/systemPrompt.ts` -> `buildEffectiveSystemPrompt`:
an agent's own prompt wins, then a custom prompt (`--system-prompt`), else the default
sections; an appended prompt (`--append-system-prompt`) always follows.

**TDDAgents**: a custom prompt replaces upstream's default sections but not TDDAgents' TDD
contract (`tdd-contract.md`), which follows it, so the model keeps its TDD instructions (user
decision 2026-11-01). The barriers themselves are enforced in code either way.

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
    tdd_contract: str | None = None,
) -> list[str]:
    """`buildEffectiveSystemPrompt`; a custom prompt keeps `tdd_contract` after it."""
    if agent_system_prompt is not None:
        base = list(agent_system_prompt)
    elif custom_system_prompt:
        base = [custom_system_prompt, *([tdd_contract] if tdd_contract else [])]
    else:
        base = list(default_system_prompt)
    return [*base, *([append_system_prompt] if append_system_prompt else [])]


__all__ = ["build_effective_system_prompt"]
