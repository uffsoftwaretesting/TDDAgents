"""
TDD rules for spawning a worker (TDDAgents' customization of `AgentTool.call`).

* **Phase-on-spawn.** Before a phase agent (one with a TDD `phase`) starts, the shared ledger
  moves to that phase through `PhaseLedger.transition_to`: GREEN needs written tests,
  REFACTOR a passing GREEN, and RED restarts the cycle (with a new test baseline, Phase 12).
  A refused move raises before any worker runs. The Agent tool and the Skill tool's forked
  path are the only callers, and both write the returned ledger as `spawn_ledger` (pinned by
  `test_run_tests_is_the_only_ledger_writer`). With no current sub-requirement (an empty or a
  finished plan) the TDD rules are off (user decision 2026-10-31), so a spawn moves nothing.
* **No background phase agent.** An async worker's app-state writes are discarded upstream
  (`shareSetAppState: !isAsync`), so a background tester could never end RED: a phase
  agent asked to go async is refused.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.tdd.ledger import PhaseLedger
from app.tools.agent_tool.load_agents_dir import AgentDefinition

if TYPE_CHECKING:
    from app.state.app_state_store import AppState


def refuse_background_phase_agent(agent: AgentDefinition, requested: bool) -> None:
    """A phase agent asked to run in the background is refused with the reason (TDD)."""
    if agent.phase is not None and requested:
        raise ValueError(
            f"Agent '{agent.name}' declares TDD phase {agent.phase} and cannot run in the background: a "
            "background worker's state changes are discarded, so its RunTests could not move the phase "
            "ledger. Run it in the foreground."
        )


def phase_on_spawn(state: AppState, agent: AgentDefinition) -> PhaseLedger:
    """The ledger a spawn of `agent` starts from; raises when the cycle forbids the spawn."""
    ledger = state.phase_ledger
    if agent.phase is None or agent.phase == ledger.phase or state.tdd_plan.current is None:
        return ledger
    try:
        return ledger.transition_to(agent.phase)
    except ValueError as exc:
        raise ValueError(f"Agent '{agent.name}' cannot start: {exc}") from None
