"""
TDD Stop Hook: tdd_phase_incomplete (Part D5).

Refuses turn exit when the Red->Green cycle is incomplete while remaining terminable
via the `stop_hook_active` contract (§3.3 of `docs/transition_elaboration_plan.md`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage

from app.loop.deps import StopHookResult

if TYPE_CHECKING:
    from app.loop.config import RunConfig
    from app.loop.state import LoopState


async def tdd_phase_incomplete_hook(state: LoopState, config: RunConfig) -> StopHookResult:
    """
    Stop hook that blocks turn completion if the TDD cycle is incomplete (Part D5).

    The stop_hook_active contract:
    - If state.stop_hook_active is True: the hook already blocked once on this context.
      To prevent infinite loops, it declines to return blocking errors again, setting
      prevent_continuation=True to terminate with STOP_HOOK_PREVENTED rather than COMPLETED.
    - If state.stop_hook_active is not True:
      - If ledger.is_cycle_complete: cycle complete, allows termination (COMPLETED).
      - If cycle incomplete: returns blocking_errors explaining why the model cannot stop.
    """
    ledger = state.tool_context.get_app_state().phase_ledger

    if ledger.is_cycle_complete:
        return StopHookResult()

    # Cycle is incomplete
    if state.stop_hook_active:
        # Honour stop_hook_active: prevent continuation to stop the turn without retrying forever
        return StopHookResult(prevent_continuation=True)

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

    return StopHookResult(blocking_errors=(HumanMessage(content=msg),))
