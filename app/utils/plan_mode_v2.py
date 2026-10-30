"""
Plan-mode configuration.

Ported from `reference/claude-code/src/utils/planModeV2.ts` -> `getPlanModeV2AgentCount`,
`getPlanModeV2ExploreAgentCount`, `isPlanModeInterviewPhaseEnabled`, `getPewterLedgerVariant`.
The environment variables are renamed (`CLAUDE_CODE_*` -> `TDDAGENTS_*`).

*Divergences:* there are no subscription tiers, so the Plan-agent count is upstream's default
for them (1); there is no GrowthBook, so the interview phase and `tengu_pewter_ledger` take their
external defaults (off, and the control arm).
"""

from __future__ import annotations

import os

from app.utils.claudemd import is_env_truthy
from app.utils.ripgrep import is_env_defined_falsy

PLAN_V2_AGENT_COUNT_ENV = "TDDAGENTS_PLAN_V2_AGENT_COUNT"
PLAN_V2_EXPLORE_AGENT_COUNT_ENV = "TDDAGENTS_PLAN_V2_EXPLORE_AGENT_COUNT"
PLAN_MODE_INTERVIEW_PHASE_ENV = "TDDAGENTS_PLAN_MODE_INTERVIEW_PHASE"


def _count(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw:
        try:
            count = int(raw)
        except ValueError:
            return default
        if 0 < count <= 10:
            return count
    return default


def get_plan_mode_v2_agent_count() -> int:
    """`getPlanModeV2AgentCount`: the env override (1-10), else 1."""
    return _count(PLAN_V2_AGENT_COUNT_ENV, 1)


def get_plan_mode_v2_explore_agent_count() -> int:
    """`getPlanModeV2ExploreAgentCount`: the env override (1-10), else 3."""
    return _count(PLAN_V2_EXPLORE_AGENT_COUNT_ENV, 3)


def is_plan_mode_interview_phase_enabled() -> bool:
    """`isPlanModeInterviewPhaseEnabled`, external build: the env switch, else off."""
    value = os.environ.get(PLAN_MODE_INTERVIEW_PHASE_ENV)
    if is_env_truthy(value):
        return True
    if is_env_defined_falsy(value):
        return False
    return False


def get_pewter_ledger_variant() -> str | None:
    """`getPewterLedgerVariant`: the Phase 4 experiment arm; `None` is the control arm."""
    return None


__all__ = [
    "PLAN_MODE_INTERVIEW_PHASE_ENV",
    "PLAN_V2_AGENT_COUNT_ENV",
    "PLAN_V2_EXPLORE_AGENT_COUNT_ENV",
    "get_pewter_ledger_variant",
    "get_plan_mode_v2_agent_count",
    "get_plan_mode_v2_explore_agent_count",
    "is_plan_mode_interview_phase_enabled",
]
