"""
The planning node (Part K3): the engineer's specification becomes the ordered sub-requirements
the development conversation works through (Phase 11, `app/cli/print.py`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from app.session.llm import call_with_retry
from app.session.state import SessionState, SessionStatus

logger = logging.getLogger(__name__)


def node_plan(
    state: SessionState,
    *,
    planner_fn: Callable[[str, Sequence[str], Sequence[str], str], list[str]] | None = None,
) -> dict[str, Any]:
    """
    Planning node: the engineer's specification becomes an ordered plan of sub-requirements
    (Part K3). On a re-scope the sub-requirements already done are passed on, so they are not
    planned again. After the user's review asked for changes, the previous plan and that
    feedback are passed on (`app/session/review.py`). The call goes through the shell's
    `with_retry` (`app/session/llm.py`).
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
        feedback = str(state.get("planner_feedback") or "")
        previous = list(state.get("plan") or []) if feedback else []
        plan = call_with_retry(planner_fn, spec, list(state.get("completed_items") or []), previous, feedback)
    except Exception as exc:  # noqa: BLE001 - any model failure is PLAN_FAILED
        logger.error("Planner execution error: %s", exc)
        return {
            "status": SessionStatus.PLAN_FAILED,
            "plan": [],
            "audit_log": [*audit_log, f"Planner error: {exc}"],
        }

    if not plan and not state.get("completed_items"):
        return {
            "status": SessionStatus.PLAN_FAILED,
            "plan": [],
            "audit_log": [*audit_log, "[Planner] Planner returned an empty plan."],
        }

    logger.info("Generated plan with %d sub-requirements.", len(plan))
    return {
        "plan": list(plan),
        "planner_feedback": "",
        "status": SessionStatus.PLAN_PROPOSED,
        "audit_log": [
            *audit_log,
            f"[Planner] Generated plan with {len(plan)} items:\n"
            + "\n".join(f"{i+1}. {p}" for i, p in enumerate(plan)),
        ],
    }
