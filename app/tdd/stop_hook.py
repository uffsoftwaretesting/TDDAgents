"""
TDD Stop Hook: tdd_phase_incomplete (Part D5).

Refuses turn exit when the Red -> Green -> Refactor cycle is incomplete while remaining terminable
via the `stop_hook_active` contract (§3.3 of `docs/transition_elaboration_plan.md`).

**This module holds the Stop hook and nothing else.** Phase 2 deleted the PreToolUse and
PostToolUse hooks that used to live here:

* the PreToolUse one duplicated `tdd/phase_permissions.py::check_tdd_phase_permission`, giving
  RED two enforcement points and so two places to get it wrong. The gate is the surviving
  one, because a PreToolUse hook's `allow` cannot talk its way past it
  (`services/tools/tool_hooks.py`);
* the PostToolUse one decided the phase by grepping tool output for the words "fail" and
  "error", which could contradict the ledger it was describing — output mentioning an error
  while the suite passes is ordinary. `RunTests` and its exit code are the only evidence the
  phase moves on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage

from app.query.stop_hooks import StopHookResult
from app.tdd.coverage_gate import COVERAGE_THRESHOLD
from app.tdd.ledger import PhaseLedger

if TYPE_CHECKING:
    from app.query.config import RunConfig
    from app.query.state import LoopState


def incomplete_cycle_message(ledger: PhaseLedger) -> str:
    """What the cycle still needs (Phase 12): RED's tests, GREEN's pass, or REFACTOR's coverage."""
    if not ledger.tests_written:
        return ("TDD cycle incomplete: RED is not done (tests_written=False). Write robust tests for this "
                "sub-requirement, covering every relevant scenario and edge case, and run RunTests.")
    if not ledger.green_passed:
        return ("TDD cycle incomplete: GREEN is not done (green_passed=False). Implement the code the tests "
                "describe and run RunTests until every test passes.")
    figure = "not measured yet" if ledger.coverage is None else f"{ledger.coverage:.1f}%"
    return ("TDD cycle incomplete: REFACTOR is not done (refactor_passed=False). It is mandatory: improve the "
            "code and the tests, then run RunTests until every test passes with line + branch coverage of at "
            f"least {COVERAGE_THRESHOLD:.0f}% (last measured: {figure}).")


async def tdd_phase_incomplete_hook(state: LoopState, config: RunConfig) -> StopHookResult:
    """
    Stop hook that blocks turn completion if the TDD cycle is incomplete (Part D5).

    It never gives up (Phase 12, user decision 2026-10-28, as upstream's Stop hooks, which may
    block every stop): while the cycle is incomplete, every stop is blocked with what is still
    missing, naming the current sub-requirement when there is a plan. `stop_hook_active` is
    no latch here; a turn ends early only on `/pause`, a configured hook's `continue: false`,
    an abort or an error. A complete cycle allows termination (COMPLETED).
    """
    app_state = state.tool_context.get_app_state()
    ledger = app_state.phase_ledger

    if ledger.is_cycle_complete:
        return StopHookResult()

    current = app_state.tdd_plan.current
    msg = incomplete_cycle_message(ledger)
    if current is not None:
        msg = f"Current sub-requirement {current.id}: {current.text}\n\n{msg}"

    return StopHookResult(blocking_errors=(HumanMessage(content=msg),))
