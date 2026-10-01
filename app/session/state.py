"""
Session state models, status enumerations, and result dataclasses conforming to §3.4 and Part K.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypedDict


class SessionStatus(StrEnum):
    """Lifecycle status of the session shell."""

    INITIALIZING = "initializing"
    GATHERING_REQUIREMENTS = "gathering_requirements"
    AWAITING_INPUT = "awaiting_input"
    ANALYSIS_COMPLETE = "analysis_complete"
    PLANNING = "planning"
    PLAN_FAILED = "plan_failed"
    EXECUTING_PLAN_ITEM = "executing_plan_item"
    ITEM_COMPLETE = "item_complete"
    ITEM_FAILED = "item_failed"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PlanItemResult:
    """Outcome of a single plan item execution."""

    index: int
    sub_requirement: str
    status: str  # "success" | "failed"
    terminal_reason: str
    red_confirmed: bool
    green_passed: bool
    iterations: int = 1
    error_message: str | None = None
    audit_notes: str = ""


class SessionState(TypedDict, total=False):
    """
    Top-level session state persisted by LangGraph checkpointing (Part K1).
    """

    session_id: str
    specification: str
    requirements: str
    user_input: str
    conversation_history: str
    current_response: str
    needs_clarification: bool
    has_checklist: bool
    user_confirmed: bool
    interaction_count: int
    plan: list[str]
    plan_index: int
    current_sub_req: str
    subreq_results: list[dict[str, Any]]
    status: str
    audit_log: list[str]
    success_count: int
    failure_count: int
