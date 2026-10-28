"""
Moving the session from one sub-requirement to the next (Phase 11, TDDAgents).

Runs inside the stop-hook runner (`app/query/stop_hooks.py`), before the TDD hook, whenever the
model stops without asking for tools. The advance is ledger-driven (user decision 2026-10-27):

* the current item's ledger shows a complete Red -> Green -> Refactor cycle → the item is recorded done
  (`TddPlan.complete_current`, which refuses anything less), the ledger restarts in RED for
  the next item, and the next item is announced as a stop-hook block, so the loop keeps
  working in the same conversation (upstream's `stop_hook_blocking` continue);
* every item done → the turn may end `completed`;
* the user paused (`/pause`) → the turn ends `stop_hook_prevented`, never `completed`;
* otherwise the TDD hook decides, against the current item.

The ledger restart is `update_tdd_plan`'s (`app/tdd/plan.py`): like phase-on-spawn it can only
erase evidence, and the next item's observations are made by `RunTests` alone
(`test_run_tests_is_the_only_ledger_writer` pins the shape).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage

from app.query.stop_hooks import StopHookResult
from app.tdd.plan import PlanItem, TddPlan, render_plan, update_tdd_plan

if TYPE_CHECKING:
    from app.query.state import LoopState


def next_item_message(done: PlanItem, plan: TddPlan) -> str:
    current = plan.current
    assert current is not None
    return (
        f"Sub-requirement {done.id} is done: RunTests observed its tests written, passing, and passing again "
        "after REFACTOR with enough coverage.\n\n"
        f"Next sub-requirement ({plan.done_count + 1} of {len(plan.items)}): {current.id}. {current.text}\n\n"
        "The TDD ledger is back in RED for it: write robust tests for this sub-requirement, covering every "
        "relevant scenario and edge case, and run RunTests before implementing it.\n\n"
        f"Plan:\n{render_plan(plan)}"
    )


def advance_plan(state: LoopState) -> StopHookResult | None:
    """The plan's verdict at a stop, or `None` to let the TDD hook judge the current item."""
    context: Any = state.tool_context
    app_state = context.get_app_state()
    plan = app_state.tdd_plan
    if not plan.items:
        return None
    ledger = app_state.phase_ledger
    done = plan.current
    if done is not None and ledger.is_cycle_complete:
        # Completing the current item moves the work on, so the ledger restarts in RED for the
        # next one (`update_tdd_plan`).
        plan = update_tdd_plan(context, lambda p: p.complete_current(ledger))
        if plan.is_complete:
            return StopHookResult()
        if app_state.tdd_paused:
            return StopHookResult(prevent_continuation=True)
        return StopHookResult(blocking_errors=(HumanMessage(content=next_item_message(done, plan)),))
    if plan.is_complete:
        return StopHookResult()
    if app_state.tdd_paused:
        return StopHookResult(prevent_continuation=True)
    return None
