"""
One sub-requirement through one development run, for tests of the run's mechanics.

Production drives a whole plan in one conversation (`app/cli/print.py::run_headless` and the
REPL, Phase 11). Many runner tests only need one sub-requirement as the first prompt and the
outcome it produced; `run_item` does exactly that over the production pieces —
`open_session_run` with a one-item plan, then `begin_session` with the sub-requirement as the
prompt — and reports what the ledger and the plan observed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.cli.print import (
    RUN_ID_NAMESPACE,
    begin_session,
    open_session_run,
    session_workspace,
)
from app.query.transitions import Terminal
from app.tdd.plan import TddPlan


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    """What one sub-requirement's run observed."""

    index: int
    sub_requirement: str
    status: str  # "success" | "failed"
    terminal_reason: str
    red_confirmed: bool
    green_passed: bool
    error_message: str | None = None


def item_run_id(session_id: Any, idx: int) -> str:
    """A stable run id for item `idx` of session `session_id`; fresh without a session id."""
    if not session_id:
        return f"loop-{uuid.uuid4().hex}"
    return f"loop-{uuid.uuid5(RUN_ID_NAMESPACE, f'{session_id}:{idx}').hex}"


async def run_item(sub_req: str, idx: int, state: Any, *, workspace: Any = None, **run_options: Any) -> ItemOutcome:
    """Run `sub_req` as a one-item plan; `state` carries the `session_id`."""
    session_id = state.get("session_id")
    ws = workspace if workspace is not None else session_workspace(str(session_id))
    async with open_session_run(run_id=item_run_id(session_id, idx), plan=TddPlan.from_texts([sub_req]),
                                workspace=ws, plan_index=idx, **run_options) as run:
        reason = await begin_session(run, sub_req)
        done = run.store.get().tdd_plan.is_complete
        final = run.store.get().phase_ledger
        stopped = run.stopped_reason
    succeeded = reason == Terminal.COMPLETED and done
    return ItemOutcome(
        index=idx,
        sub_requirement=sub_req,
        status="success" if succeeded else "failed",
        terminal_reason=str(reason),
        # A done item's ledger has already restarted in RED; its cycle was observed.
        red_confirmed=done or final.red_confirmed,
        green_passed=done or final.green_passed,
        error_message=None if succeeded else stopped or f"Terminated early: {reason}",
    )
