"""
Requirements analyst node with human-in-the-loop interrupt() conforming to §3.4 and Part K2.

Replaces blocking terminal stdin input with LangGraph's native interrupt() protocol:
- Yields structured question / checklist payload to caller on clarification or approval.
- Resumes execution statefully via `Command(resume=...)` across process restarts and turns.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langgraph.types import interrupt

from app.session.state import SessionState, SessionStatus

logger = logging.getLogger(__name__)

_DEFAULT_AFFIRMATIVES = frozenset({"/yes", "yes", "confirm", "proceed", "y", "approved", "ok"})


def node_analyst_interrupt(
    state: SessionState,
    *,
    analyzer_fn: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Analyst node that clarifies requirements and obtains checklist approval using interrupt() (Part K2).

    If needs_clarification or has_checklist is True:
        Pauses graph via `interrupt(...)` yielding prompt & checklist metadata.
        Resumes when caller supplies response via `Command(resume=...)`.
    """
    if state.get("user_confirmed"):
        return {
            "status": SessionStatus.ANALYSIS_COMPLETE,
            "user_confirmed": True,
        }

    user_input = str(state.get("user_input") or "").strip()
    conversation_history = str(state.get("conversation_history") or "")
    interaction_count = int(state.get("interaction_count") or 0) + 1

    # Use injected analyzer or fallback to default analyze_requirements
    if analyzer_fn is None:
        from app.agents.langgraph.analyst import analyze_requirements

        analyzer_fn = analyze_requirements

    try:
        result = analyzer_fn(user_input, conversation_history)
    except Exception as exc:
        logger.error("Analyst execution error: %s", exc)
        return {
            "status": SessionStatus.FAILED,
            "audit_log": [f"Analyst error: {exc}"],
        }

    response_text = str(result.get("response") or "").strip()
    needs_clarification = bool(result.get("needs_clarification"))
    has_checklist = bool(result.get("has_checklist"))

    # Interaction limit guard: if reached, enforce checklist presentation
    if interaction_count >= 5 and not has_checklist:
        response_text += "\n\nRequirements defined. Here is the final Checklist. May we proceed?"
        has_checklist = True
        needs_clarification = False

    # Append to history
    new_turn = f"[User]: {user_input}\n[Analyst]: {response_text}"
    new_history = f"{conversation_history}\n\n{new_turn}".strip() if conversation_history else new_turn

    # If the initial prompt was fully comprehensive with no clarification needed
    if not needs_clarification and not has_checklist:
        logger.info("Analyst accepted requirements directly without clarification.")
        return {
            "specification": response_text,
            "requirements": new_history,
            "conversation_history": new_history,
            "current_response": response_text,
            "needs_clarification": False,
            "has_checklist": True,
            "user_confirmed": True,
            "interaction_count": interaction_count,
            "status": SessionStatus.ANALYSIS_COMPLETE,
        }

    # Pause and interrupt for human input
    interrupt_payload = {
        "type": "checklist_approval" if has_checklist else "clarification",
        "prompt": response_text,
        "has_checklist": has_checklist,
        "needs_clarification": needs_clarification,
        "interaction_count": interaction_count,
    }

    # PAUSE GRAPH HERE: LangGraph saves checkpoint and returns payload to caller
    user_feedback = interrupt(interrupt_payload)

    # RESUMED HERE: user_feedback is provided by Command(resume=...)
    user_feedback_str = str(user_feedback).strip()
    logger.info("Resumed from interrupt with user feedback: '%s'", user_feedback_str)

    is_confirmed = user_feedback_str.lower() in _DEFAULT_AFFIRMATIVES

    if is_confirmed:
        return {
            "specification": response_text,
            "requirements": new_history,
            "conversation_history": new_history,
            "current_response": response_text,
            "user_input": user_feedback_str,
            "needs_clarification": False,
            "has_checklist": True,
            "user_confirmed": True,
            "interaction_count": interaction_count,
            "status": SessionStatus.ANALYSIS_COMPLETE,
        }

    # User provided adjustments, answers, or new requirements
    return {
        "requirements": new_history,
        "conversation_history": new_history,
        "current_response": response_text,
        "user_input": user_feedback_str,
        "needs_clarification": True,
        "has_checklist": False,
        "user_confirmed": False,
        "interaction_count": interaction_count,
        "status": SessionStatus.AWAITING_INPUT,
    }
