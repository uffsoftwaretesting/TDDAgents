"""
The user's review of the engineer's specification and of the planner's plan (2026-10-29).

Upstream holds a plan's scope by the user's approval, not by rules in the planning prompt:
`ExitPlanModeV2Tool` asks "Would you like to proceed?", and the user approves, answers
"No, keep planning" with what to change, or edits the plan file in their editor
(`components/permissions/ExitPlanModePermissionRequest`, `utils/promptEditor.ts`). Each review
here is that question at the end of its node:

- `/yes` approves (the analyst's approval word, Phase 9);
- `{"edited": text}` replaces the output with the user's edit (`planEditedLocally`);
- for the plan, `/plan add|edit|remove|move …` changes the proposed plan (the REPL's
  `apply_plan_command`), and the plan is shown again;
- anything else is feedback, and the engineer or planner writes its output again from it.

Each review is its own node, as the analyst's is: LangGraph re-runs an interrupted node from its
first line, so the model call and the question cannot share one. A headless session approves, as
it approves the analyst's checklist.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

from app.commands import CommandError, apply_plan_command
from app.session.analyst import APPROVAL
from app.session.state import SessionState, SessionStatus
from app.tdd.plan import TddPlan, render_plan

logger = logging.getLogger(__name__)

#: The key of a resume value carrying the user's edit of the reviewed text.
EDITED = "edited"

#: What `user_prompts` records for an edit made in the editor.
EDIT_COMMAND = "/edit"

#: The REPL's plan command, accepted while the proposed plan is reviewed.
PLAN_COMMAND = "/plan"


def _audit(state: SessionState, line: str) -> list[str]:
    return [*(state.get("audit_log") or []), line]


def specification_payload(state: SessionState) -> dict[str, Any]:
    """What the user reviews after the engineer: the specification."""
    return {"type": "specification_approval", "prompt": str(state.get("specification") or "")}


def plan_payload(state: SessionState) -> dict[str, Any]:
    """What the user reviews after the planner: the numbered plan, and why a `/plan` command failed."""
    plan = list(state.get("plan") or [])
    return {
        "type": "plan_approval",
        "prompt": render_plan(TddPlan.from_texts(plan)),
        "plan": plan,
        "error": str(state.get("review_error") or ""),
    }


def _ask(payload: dict[str, Any], headless: bool) -> Any:
    if headless:
        return APPROVAL
    # LangGraph checkpoints here and hands `payload` to the caller; `Command(resume=…)` re-enters
    # this node and `interrupt` returns the caller's answer.
    answer = interrupt(payload)
    return answer if isinstance(answer, dict) else str(answer).strip()


def _edited(answer: Any) -> str | None:
    if isinstance(answer, dict) and isinstance(answer.get(EDITED), str):
        return str(answer[EDITED])
    return None


def node_engineer_review(state: SessionState, *, headless: bool = False) -> dict[str, Any]:
    """The user's answer to the specification: approval, an edit, or feedback for the engineer."""
    answer = _ask(specification_payload(state), headless)
    edited = _edited(answer)
    if edited is not None:
        return {
            "specification": edited.strip(),
            "user_prompts": [*(state.get("user_prompts") or []), EDIT_COMMAND],
            "status": SessionStatus.SPECIFIED,
            "audit_log": _audit(state, "[Engineer] Specification edited by the user."),
        }
    text = str(answer)
    user_prompts = [*(state.get("user_prompts") or []), text]
    if text.lower() == APPROVAL:
        return {"user_prompts": user_prompts, "status": SessionStatus.PLANNING,
                "audit_log": _audit(state, "[Engineer] Specification approved.")}
    return {"user_prompts": user_prompts, "engineer_feedback": text, "status": SessionStatus.SPECIFYING,
            "audit_log": _audit(state, "[Engineer] The user asked for changes.")}


def node_planner_review(state: SessionState, *, headless: bool = False) -> dict[str, Any]:
    """The user's answer to the plan: approval, a `/plan` change, an edit, or feedback for the planner."""
    answer = _ask(plan_payload(state), headless)
    edited = _edited(answer)
    if edited is not None:
        items = [line.strip() for line in edited.splitlines() if line.strip()]
        return {
            "plan": items,
            "review_error": "",
            "user_prompts": [*(state.get("user_prompts") or []), EDIT_COMMAND],
            "status": SessionStatus.PLAN_PROPOSED,
            "audit_log": _audit(state, "[Planner] Plan edited by the user."),
        }
    text = str(answer)
    user_prompts = [*(state.get("user_prompts") or []), text]
    if text.lower() == APPROVAL:
        return {"user_prompts": user_prompts, "review_error": "", "status": SessionStatus.PLANNED,
                "audit_log": _audit(state, "[Planner] Plan approved.")}
    command, _, args = text.partition(" ")
    if command == PLAN_COMMAND:
        try:
            changed = apply_plan_command(TddPlan.from_texts(list(state.get("plan") or [])), args.strip())
        except CommandError as exc:
            return {"user_prompts": user_prompts, "review_error": str(exc), "status": SessionStatus.PLAN_PROPOSED}
        return {"plan": [item.text for item in changed.items], "user_prompts": user_prompts, "review_error": "",
                "status": SessionStatus.PLAN_PROPOSED}
    return {"user_prompts": user_prompts, "planner_feedback": text, "review_error": "",
            "status": SessionStatus.PLANNING, "audit_log": _audit(state, "[Planner] The user asked for changes.")}


__all__ = [
    "EDITED",
    "EDIT_COMMAND",
    "PLAN_COMMAND",
    "node_engineer_review",
    "node_planner_review",
    "plan_payload",
    "specification_payload",
]
