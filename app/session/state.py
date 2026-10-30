"""
Session state and status: what the LangGraph session shell checkpoints (Part K, Phase 11).

The shell gathers requirements and plans; the sub-requirements are then worked on in one
development conversation (`app/cli/print.py`), whose plan — which items are done — lives in
that run's `AppState` and its saved plan file, not here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypedDict


class SessionStatus(StrEnum):
    """Lifecycle status of the session shell."""

    INITIALIZING = "initializing"
    GATHERING_REQUIREMENTS = "gathering_requirements"
    AWAITING_INPUT = "awaiting_input"
    ANALYSIS_COMPLETE = "analysis_complete"
    #: The specification is written and waits for the user's review (`app/session/review.py`).
    SPECIFIED = "specified"
    #: The user asked for changes: the engineer writes the specification again.
    SPECIFYING = "specifying"
    #: The specification is approved (or the user asked for plan changes): the planner plans.
    PLANNING = "planning"
    #: The plan is written and waits for the user's review.
    PLAN_PROPOSED = "plan_proposed"
    PLAN_FAILED = "plan_failed"
    #: The user approved the plan: the session moves on to development.
    PLANNED = "planned"
    FAILED = "failed"


class SessionState(TypedDict, total=False):
    """
    Top-level session state persisted by LangGraph checkpointing (Part K1).
    """

    session_id: str
    specification: str
    requirements: str
    user_input: str
    #: Everything the user typed, in order: the initial request, then each answer to the analyst.
    user_prompts: list[str]
    conversation_history: str
    current_response: str
    needs_clarification: bool
    has_checklist: bool
    user_confirmed: bool
    interaction_count: int
    plan: list[str]
    #: On `/requirements` (Phase 11): the sub-requirements already done, which the planner must
    #: not plan again.
    completed_items: list[str]
    #: The user's feedback on the specification or the plan, for the next engineer or planner call.
    engineer_feedback: str
    planner_feedback: str
    #: Why the last `/plan` command at the plan review could not apply (shown with the plan).
    review_error: str
    status: str
    audit_log: list[str]
