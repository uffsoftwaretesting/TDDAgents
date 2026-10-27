"""
Plan-item iteration and execution nodes conforming to §3.4 and Part K3.

Orchestrates sequential iteration across plan items, executing each sub-requirement
through the unified TDD loop (`run_loop`) and verifying the Red-then-Green ledger contract.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from app.query.transitions import Terminal
from app.session.llm import call_with_retry, run_sync
from app.session.state import PlanItemResult, SessionState, SessionStatus

logger = logging.getLogger(__name__)


class LoopRunnerNotConfigured(RuntimeError):
    """The session shell was asked to execute a plan item with no loop runner wired in."""


def node_plan(
    state: SessionState,
    *,
    planner_fn: Callable[[str], list[str]] | None = None,
) -> dict[str, Any]:
    """
    Planning node: the engineer's specification becomes an ordered plan of sub-requirements
    (Part K3). The call goes through the shell's `with_retry` (`app/session/llm.py`).
    """
    audit_log = list(state.get("audit_log") or [])
    spec = str(state.get("specification") or "").strip()
    if not spec:
        return {
            "status": SessionStatus.PLAN_FAILED,
            "plan": [],
            "audit_log": [*audit_log, f"[{state.get('session_id', 'session')}] Planning failed: empty specification."],
        }

    if planner_fn is None:
        from app.session.agents.planner import generate_plan

        planner_fn = generate_plan

    try:
        plan = call_with_retry(planner_fn, spec)
    except Exception as exc:  # noqa: BLE001 - any model failure is PLAN_FAILED
        logger.error("Planner execution error: %s", exc)
        return {
            "status": SessionStatus.PLAN_FAILED,
            "plan": [],
            "audit_log": [*audit_log, f"Planner error: {exc}"],
        }

    if not plan:
        return {
            "status": SessionStatus.PLAN_FAILED,
            "plan": [],
            "audit_log": [*audit_log, "[Planner] Planner returned an empty plan."],
        }

    logger.info("Generated plan with %d sub-requirements.", len(plan))
    return {
        "plan": list(plan),
        "plan_index": 0,
        "current_sub_req": plan[0],
        "subreq_results": [],
        "status": SessionStatus.EXECUTING_PLAN_ITEM,
        "success_count": 0,
        "failure_count": 0,
        "audit_log": [
            *audit_log,
            f"[Planner] Generated plan with {len(plan)} items:\n"
            + "\n".join(f"{i+1}. {p}" for i, p in enumerate(plan)),
        ],
    }


def _process_item_outcome(
    result_item: PlanItemResult,
    idx: int,
    total: int,
    sub_req: str,
    state: SessionState,
) -> dict[str, Any]:
    audit_log = list(state.get("audit_log") or [])
    subreq_results = list(state.get("subreq_results") or [])
    success_count = int(state.get("success_count") or 0)
    failure_count = int(state.get("failure_count") or 0)

    is_success = (
        result_item.status == "success"
        and result_item.terminal_reason == str(Terminal.COMPLETED)
        and result_item.red_confirmed
        and result_item.green_passed
    )

    if is_success:
        success_count += 1
        audit_log.append(f"[Evaluator] Completed item {idx+1}/{total}: '{sub_req}'.")
        status = SessionStatus.ITEM_COMPLETE
    else:
        failure_count += 1
        err = result_item.error_message or (
            f"Invariant failure (red={result_item.red_confirmed}, green={result_item.green_passed})"
        )
        audit_log.append(f"[Evaluator] Failed item {idx+1}/{total}: '{sub_req}': {err}")
        status = SessionStatus.ITEM_FAILED

    subreq_results.append(asdict(result_item))

    return {
        "subreq_results": subreq_results,
        "success_count": success_count,
        "failure_count": failure_count,
        "status": status,
        "audit_log": audit_log,
    }


def node_execute_plan_item(
    state: SessionState,
    *,
    loop_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """
    Synchronously executes current plan item through the unified TDD loop (Part K3).
    """
    plan = list(state.get("plan") or [])
    idx = int(state.get("plan_index") or 0)
    total = len(plan)

    if idx >= total or not plan:
        return {"status": SessionStatus.COMPLETED}

    sub_req = plan[idx]
    if loop_runner is None:
        # A shell without a runner is a wiring bug, not a TDD outcome: never report a
        # Red-Green cycle that nothing observed.
        raise LoopRunnerNotConfigured("the session shell has no loop runner; refusing to fabricate a result")
    logger.info("Executing plan item %d/%d: '%s'", idx + 1, total, sub_req)

    try:
        result_item = loop_runner(sub_req, idx, state)
        if inspect.isawaitable(result_item):
            result_item = run_sync(result_item)
        if not isinstance(result_item, PlanItemResult):
            raise TypeError(f"loop_runner must return PlanItemResult, got {type(result_item)}")
    except Exception as exc:  # noqa: BLE001 - a crashed item is a failed item, never a fabricated pass
        logger.error("Error executing plan item '%s': %s", sub_req, exc)
        result_item = PlanItemResult(
            index=idx,
            sub_requirement=sub_req,
            status="failed",
            terminal_reason="error",
            red_confirmed=False,
            green_passed=False,
            error_message=str(exc),
        )

    return _process_item_outcome(result_item, idx, total, sub_req, state)


def node_evaluator(state: SessionState) -> dict[str, Any]:
    """
    Evaluator node: decides whether to advance to the next plan item or terminate (Part K3).
    """
    plan = list(state.get("plan") or [])
    current_idx = int(state.get("plan_index") or 0)
    total = len(plan)
    next_idx = current_idx + 1

    audit_log = list(state.get("audit_log") or [])

    if next_idx < total:
        next_req = plan[next_idx]
        audit_log.append(f"[Evaluator] Advancing to item {next_idx + 1}/{total}: '{next_req}'.")
        return {
            "plan_index": next_idx,
            "current_sub_req": next_req,
            "status": SessionStatus.EXECUTING_PLAN_ITEM,
            "audit_log": audit_log,
        }

    # All plan items completed
    failure_count = int(state.get("failure_count") or 0)
    success_count = int(state.get("success_count") or 0)

    final_status = SessionStatus.COMPLETED if failure_count == 0 else SessionStatus.FAILED
    audit_log.append(
        f"[Evaluator] Plan finished. Status: {final_status} (Success={success_count}, Failed={failure_count})."
    )

    return {
        "status": final_status,
        "audit_log": audit_log,
    }
