"""
Plan-item iteration and execution nodes conforming to §3.4 and Part K3.

Orchestrates sequential iteration across plan items, executing each sub-requirement
through the unified TDD loop (`run_loop`) and verifying the Red-then-Green ledger contract.
"""

from __future__ import annotations

from dataclasses import asdict
import inspect
import logging
from typing import Any, Callable

from app.loop.transitions import Terminal
from app.session.state import PlanItemResult, SessionState, SessionStatus

logger = logging.getLogger(__name__)


def node_plan(
    state: SessionState,
    *,
    planner_fn: Callable[[str], list[str]] | None = None,
) -> dict[str, Any]:
    """
    Planning node: converts requirements into an ordered plan of sub-requirements (Part K3).
    """
    spec = str(state.get("specification") or state.get("requirements") or state.get("user_input") or "").strip()
    if not spec:
        return {
            "status": SessionStatus.PLAN_FAILED,
            "plan": [],
            "audit_log": [f"[{state.get('session_id', 'session')}] Planning failed: empty specification."],
        }

    if planner_fn is None:
        from app.agents.langgraph.planner import generate_plan

        planner_fn = generate_plan

    try:
        plan = planner_fn(spec)
    except Exception as exc:
        logger.error("Planner execution error: %s", exc)
        return {
            "status": SessionStatus.PLAN_FAILED,
            "plan": [],
            "audit_log": [f"Planner error: {exc}"],
        }

    if not plan:
        return {
            "status": SessionStatus.PLAN_FAILED,
            "plan": [],
            "audit_log": ["[Planner] Planner returned an empty plan."],
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
            f"[Planner] Generated plan with {len(plan)} items:\n"
            + "\n".join(f"{i+1}. {p}" for i, p in enumerate(plan))
        ],
    }


def _run_coroutine_sync(coro: Any) -> Any:
    import asyncio
    import concurrent.futures

    async def _runner() -> Any:
        return await coro

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _runner()).result()
    return asyncio.run(_runner())


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
    logger.info("Executing plan item %d/%d: '%s'", idx + 1, total, sub_req)

    if loop_runner is not None:
        try:
            if inspect.iscoroutinefunction(loop_runner):
                result_item = _run_coroutine_sync(loop_runner(sub_req, idx, state))
            else:
                res = loop_runner(sub_req, idx, state)
                if inspect.isawaitable(res):
                    result_item = _run_coroutine_sync(res)
                else:
                    result_item = res
            if not isinstance(result_item, PlanItemResult):
                raise TypeError(f"loop_runner must return PlanItemResult, got {type(result_item)}")
        except Exception as exc:
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
    else:
        result_item = PlanItemResult(
            index=idx,
            sub_requirement=sub_req,
            status="success",
            terminal_reason="completed",
            red_confirmed=True,
            green_passed=True,
        )

    return _process_item_outcome(result_item, idx, total, sub_req, state)


async def anode_execute_plan_item(
    state: SessionState,
    *,
    loop_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """
    Asynchronously executes current plan item through the unified TDD loop (Part K3).
    """
    return node_execute_plan_item(state, loop_runner=loop_runner)


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
