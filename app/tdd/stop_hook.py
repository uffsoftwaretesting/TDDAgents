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

from dataclasses import replace
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

    The stop_hook_active contract, per sub-requirement (Phase 11) and phase (Phase 12):
    - If the hook already blocked for the current item in its current phase
      (`state.stop_hook_active` and `AppState.tdd_blocked_at` is that item and phase), it
      declines to block again and sets prevent_continuation=True, terminating with
      STOP_HOOK_PREVENTED rather than COMPLETED. The cycle has three phases, so each item gets
      at most three nudges; a stop without progress since the last one ends the turn. Without a
      plan the item is `None`, which is the one-block-per-phase latch of Part D5.
    - Otherwise:
      - If ledger.is_cycle_complete: cycle complete, allows termination (COMPLETED).
      - If cycle incomplete: returns blocking_errors explaining why the model cannot stop,
        naming the current sub-requirement when there is a plan.
    """
    app_state = state.tool_context.get_app_state()
    ledger = app_state.phase_ledger

    if ledger.is_cycle_complete:
        return StopHookResult()

    current = app_state.tdd_plan.current
    item_id = current.id if current is not None else None
    blocked_at = (item_id, ledger.phase)
    if state.stop_hook_active and app_state.tdd_blocked_at == blocked_at:
        # Honour stop_hook_active: prevent continuation to stop the turn without retrying forever
        return StopHookResult(prevent_continuation=True)
    state.tool_context.set_app_state(lambda s: replace(s, tdd_blocked_at=blocked_at))

    # First block: build explanation of why turn cannot exit
    msg = incomplete_cycle_message(ledger)
    if current is not None:
        msg = f"Current sub-requirement {current.id}: {current.text}\n\n{msg}"

    return StopHookResult(blocking_errors=(HumanMessage(content=msg),))
