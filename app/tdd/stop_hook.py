"""
TDD Stop Hook: tdd_phase_incomplete (Part D5).

Refuses turn exit when the Red->Green cycle is incomplete while remaining terminable
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

if TYPE_CHECKING:
    from app.query.config import RunConfig
    from app.query.state import LoopState


async def tdd_phase_incomplete_hook(state: LoopState, config: RunConfig) -> StopHookResult:
    """
    Stop hook that blocks turn completion if the TDD cycle is incomplete (Part D5).

    The stop_hook_active contract, per sub-requirement (Phase 11):
    - If the hook already blocked for the current item (`state.stop_hook_active` and
      `AppState.tdd_blocked_item` is that item), it declines to block again and sets
      prevent_continuation=True, terminating with STOP_HOOK_PREVENTED rather than COMPLETED.
      Without a plan the item is `None`, which is the one-block latch of Part D5.
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
    if state.stop_hook_active and app_state.tdd_blocked_item == item_id:
        # Honour stop_hook_active: prevent continuation to stop the turn without retrying forever
        return StopHookResult(prevent_continuation=True)
    state.tool_context.set_app_state(lambda s: replace(s, tdd_blocked_item=item_id))

    # First block: build explanation of why turn cannot exit
    if not ledger.red_confirmed:
        if ledger.green_passed:
            # F2 "green in red" case
            msg = (
                "TDD cycle incomplete: tests passed on first run without prior failure "
                "(red_confirmed=False). A failing test must be observed (RED phase) before implementation."
            )
        else:
            msg = (
                "TDD cycle incomplete: no failing test observed yet in RED phase "
                "(red_confirmed=False). You must write a failing test and run RunTests."
            )
    else:
        msg = (
            "TDD cycle incomplete: tests are not passing yet in GREEN phase "
            "(green_passed=False). You must implement code and run RunTests until tests pass."
        )
    if current is not None:
        msg = f"Current sub-requirement {current.id}: {current.text}\n\n{msg}"

    return StopHookResult(blocking_errors=(HumanMessage(content=msg),))
