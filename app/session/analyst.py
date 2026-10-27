"""
Requirements gathering: the analyst and the human review of its answer (Part K2, Phase 9).

Two nodes, because LangGraph re-runs an interrupted node from its first line on resume:
with the model call and the `interrupt()` in one node, the analyst was asked again on every
resume and the checklist the user approved was not the one the session kept. Here
`node_analyst` makes the call and stores the answer; `node_analyst_review` only asks.

* **Approval** is the documented rule: `/yes`, and only while a checklist is pending.
  Anything else — "yes" included — is feedback that sends the request back to the analyst.
  There is no cap on the number of rounds (the no-ceilings rule): the analysis ends when
  the user approves a checklist.
* **Headless** (upstream `claude -p`: nobody to ask) never interrupts. A checklist is
  approved as if `/yes` were typed; a clarifying question fails the session, because an
  unresolved question has no one to answer it — the research protocol discards those
  attempts anyway.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langgraph.types import interrupt

from app.session.llm import call_with_retry
from app.session.state import SessionState, SessionStatus

logger = logging.getLogger(__name__)

#: The one answer that approves a pending checklist.
APPROVAL = "/yes"

#: Why a headless session stops when the analyst asks instead of answering.
HEADLESS_CLARIFICATION_ERROR = "analyst needs clarification, and a headless session has no user to answer it"


def _audit(state: SessionState, line: str) -> list[str]:
    return [*(state.get("audit_log") or []), line]


def node_analyst(
    state: SessionState,
    *,
    analyzer_fn: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One analyst turn: answer the latest user input in the light of the conversation so far."""
    user_input = str(state.get("user_input") or "").strip()
    conversation_history = str(state.get("conversation_history") or "")
    interaction_count = int(state.get("interaction_count") or 0) + 1

    if analyzer_fn is None:
        from app.session.agents.analyst import analyze_requirements

        analyzer_fn = analyze_requirements

    try:
        result = call_with_retry(analyzer_fn, user_input, conversation_history)
    except Exception as exc:  # noqa: BLE001 - any model failure ends the session as FAILED
        logger.error("Analyst execution error: %s", exc)
        return {"status": SessionStatus.FAILED, "audit_log": _audit(state, f"[Analyst] Error: {exc}")}

    response_text = str(result.get("response") or "").strip()
    needs_clarification = bool(result.get("needs_clarification"))
    has_checklist = bool(result.get("has_checklist"))
    new_turn = f"[User]: {user_input}\n[Analyst]: {response_text}"
    new_history = f"{conversation_history}\n\n{new_turn}" if conversation_history else new_turn
    update: dict[str, Any] = {
        "conversation_history": new_history,
        "requirements": new_history,
        "current_response": response_text,
        "needs_clarification": needs_clarification,
        "has_checklist": has_checklist,
        "interaction_count": interaction_count,
    }

    if not needs_clarification and not has_checklist:
        logger.info("Analyst accepted requirements directly without clarification.")
        return {**update, "user_confirmed": True, "status": SessionStatus.ANALYSIS_COMPLETE}
    return {**update, "user_confirmed": False, "status": SessionStatus.AWAITING_INPUT}


def review_payload(state: SessionState) -> dict[str, Any]:
    """What the caller is shown at the interrupt: the analyst's answer and what it asks for."""
    has_checklist = bool(state.get("has_checklist"))
    return {
        "type": "checklist_approval" if has_checklist else "clarification",
        "prompt": str(state.get("current_response") or ""),
        "has_checklist": has_checklist,
        "needs_clarification": bool(state.get("needs_clarification")),
        "interaction_count": int(state.get("interaction_count") or 0),
    }


def node_analyst_review(state: SessionState, *, headless: bool = False) -> dict[str, Any]:
    """The user's answer to the analyst: approval of a checklist, or feedback for another turn."""
    payload = review_payload(state)
    if headless:
        if not payload["has_checklist"]:
            logger.error("Headless session stopped: %s", HEADLESS_CLARIFICATION_ERROR)
            return {
                "status": SessionStatus.FAILED,
                "audit_log": _audit(state, f"[Analyst] {HEADLESS_CLARIFICATION_ERROR}."),
            }
        feedback = APPROVAL
    else:
        # LangGraph checkpoints here and hands `payload` to the caller; `Command(resume=…)`
        # re-enters this node and `interrupt` returns the caller's answer.
        feedback = str(interrupt(payload)).strip()
        logger.info("Resumed from interrupt with user feedback: '%s'", feedback)

    user_prompts = [*(state.get("user_prompts") or []), feedback]
    if payload["has_checklist"] and feedback.lower() == APPROVAL:
        return {
            "user_prompts": user_prompts,
            "user_confirmed": True,
            "needs_clarification": False,
            "status": SessionStatus.ANALYSIS_COMPLETE,
        }
    return {
        "user_prompts": user_prompts,
        "user_input": feedback,
        "user_confirmed": False,
        "has_checklist": False,
        "status": SessionStatus.GATHERING_REQUIREMENTS,
    }


__all__ = [
    "APPROVAL",
    "HEADLESS_CLARIFICATION_ERROR",
    "node_analyst",
    "node_analyst_review",
    "review_payload",
]
