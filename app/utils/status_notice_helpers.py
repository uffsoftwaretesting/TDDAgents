"""
How many tokens the custom agents' descriptions take in the Agent tool's prompt.

Ported from `reference/claude-code/src/utils/statusNoticeHelpers.ts` -> `AGENT_DESCRIPTIONS_THRESHOLD`,
`getAgentDescriptionsTotalTokens`. An agent's `agentType` is its `name` here and its `whenToUse` its
`description` (`app/tools/agent_tool/load_agents_dir.py`). `/doctor` reads it
(`app/utils/doctor_context_warnings.py`); the startup status notices are not ported.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.utils.tokens import rough_token_count_estimation

#: `AGENT_DESCRIPTIONS_THRESHOLD`.
AGENT_DESCRIPTIONS_THRESHOLD = 15_000


def agent_description_tokens(agent: Any) -> int:
    """One agent's `${agentType}: ${whenToUse}`, roughly counted."""
    return rough_token_count_estimation(f"{agent.name}: {agent.description}")


def get_agent_descriptions_total_tokens(active_agents: Iterable[Any] | None) -> int:
    """`getAgentDescriptionsTotalTokens`: every active agent but the built-ins."""
    if active_agents is None:
        return 0
    return sum(agent_description_tokens(agent) for agent in active_agents if agent.source != "built-in")


__all__ = ["AGENT_DESCRIPTIONS_THRESHOLD", "agent_description_tokens", "get_agent_descriptions_total_tokens"]
