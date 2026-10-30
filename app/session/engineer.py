"""
The engineer: the confirmed requirements become the technical specification (Phase 9).

The planner plans from this specification, never from the analyst's checklist, as the
pre-loop pipeline did: the specification is the document the TDD agents treat as the only
source of truth for their tests.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.session.llm import call_with_retry
from app.session.state import SessionState, SessionStatus

logger = logging.getLogger(__name__)


def node_engineer(
    state: SessionState,
    *,
    engineer_fn: Callable[[str, str, str, str], str] | None = None,
) -> dict[str, Any]:
    """
    Write the specification from the requirements conversation; after the user's review asked
    for changes, from the previous specification and that feedback (`app/session/review.py`).
    """
    requirements = str(state.get("requirements") or "")
    conversation_history = str(state.get("conversation_history") or "")
    feedback = str(state.get("engineer_feedback") or "")
    previous = str(state.get("specification") or "") if feedback else ""
    audit_log = list(state.get("audit_log") or [])

    if engineer_fn is None:
        from app.session.agents.engineer import generate_specification

        engineer_fn = generate_specification

    try:
        specification = str(
            call_with_retry(engineer_fn, requirements, conversation_history, previous, feedback)
        ).strip()
    except Exception as exc:  # noqa: BLE001 - any model failure ends the session as FAILED
        logger.error("Engineer execution error: %s", exc)
        return {"status": SessionStatus.FAILED, "audit_log": [*audit_log, f"[Engineer] Error: {exc}"]}

    if not specification:
        return {"status": SessionStatus.FAILED, "audit_log": [*audit_log, "[Engineer] Empty specification."]}

    logger.info("Specification generated: %d characters.", len(specification))
    return {
        "specification": specification,
        "engineer_feedback": "",
        "status": SessionStatus.SPECIFIED,
        "audit_log": [*audit_log, f"[Engineer] Specification ready ({len(specification)} characters)."],
    }


__all__ = ["node_engineer"]
