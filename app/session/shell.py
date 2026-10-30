"""
The session shell: requirements and planning, before development (§3.4 and Part K of
`docs/transition_elaboration_plan.md`, Phases 9 and 11).

```
START → analyst ⇄ analyst_review (interrupt) → engineer ⇄ engineer_review (interrupt)
      → planner ⇄ planner_review (interrupt) → END
```

LangGraph keeps what the loop has no answer for: checkpointing (so a session survives a
restart and resumes by thread id) and the human-in-the-loop `interrupt()`s: the analyst's
checklist, then the user's review of the specification and of the plan (`app/session/review.py`,
upstream's plan approval). Once the user approves the plan the session is `planned`, and the sub-requirements are
worked on in one development conversation (`app/cli/print.py`), where the TDD barriers live.

`rescope` (`/requirements`, Phase 11) runs the same graph again on the same thread: the
analyst continues its dialogue with the new request, and the planner is told which
sub-requirements are already done.

**No ceiling on super-steps.** LangGraph's `recursion_limit` would bound the analyst rounds;
claude-code has no such bound and this codebase invents none, so it is `sys.maxsize`.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.session.analyst import node_analyst, node_analyst_review
from app.session.checkpointer import build_checkpointer
from app.session.engineer import node_engineer
from app.session.planning import node_plan
from app.session.review import node_engineer_review, node_planner_review
from app.session.state import SessionState, SessionStatus

logger = logging.getLogger(__name__)

#: LangGraph's super-step bound, lifted: the no-ceilings rule (see the module docstring).
RECURSION_LIMIT = sys.maxsize


def create_initial_session_state(thread_id: str, user_input: str) -> SessionState:
    """Construct initial state for a session thread."""
    return {
        "session_id": thread_id,
        "user_input": user_input,
        "user_prompts": [user_input],
        "specification": "",
        "requirements": "",
        "conversation_history": "",
        "needs_clarification": False,
        "has_checklist": False,
        "user_confirmed": False,
        "interaction_count": 0,
        "plan": [],
        "completed_items": [],
        "status": SessionStatus.INITIALIZING,
        "audit_log": [f"[Session] Started thread '{thread_id}'."],
    }


def build_session_run_config(thread_id: str) -> RunnableConfig:
    """The LangGraph run configuration for a thread."""
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
    }


def route_after_analyst(state: SessionState) -> str:
    status = state.get("status")
    if status == SessionStatus.ANALYSIS_COMPLETE:
        return "engineer"
    if status == SessionStatus.AWAITING_INPUT:
        return "analyst_review"
    return END


def route_after_review(state: SessionState) -> str:
    status = state.get("status")
    if status == SessionStatus.ANALYSIS_COMPLETE:
        return "engineer"
    if status == SessionStatus.GATHERING_REQUIREMENTS:
        return "analyst"
    return END


def route_after_engineer(state: SessionState) -> str:
    return "engineer_review" if state.get("status") == SessionStatus.SPECIFIED else END


def route_after_engineer_review(state: SessionState) -> str:
    status = state.get("status")
    if status == SessionStatus.PLANNING:
        return "planner"
    if status == SessionStatus.SPECIFYING:
        return "engineer"
    if status == SessionStatus.SPECIFIED:
        return "engineer_review"
    return END


def route_after_planner(state: SessionState) -> str:
    return "planner_review" if state.get("status") == SessionStatus.PLAN_PROPOSED else END


def route_after_planner_review(state: SessionState) -> str:
    status = state.get("status")
    if status == SessionStatus.PLANNING:
        return "planner"
    if status == SessionStatus.PLAN_PROPOSED:
        return "planner_review"
    return END


def create_session_graph(
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    *,
    analyzer_fn: Callable[[str, str], dict[str, Any]] | None = None,
    engineer_fn: Callable[[str, str, str, str], str] | None = None,
    planner_fn: Callable[[str, Sequence[str], Sequence[str], str], list[str]] | None = None,
    headless: bool = False,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build and compile the session graph (Parts K1, K2, K3)."""
    if checkpointer is None:
        checkpointer = build_checkpointer()

    workflow = StateGraph(SessionState)

    def _analyst_node(state: SessionState) -> dict[str, Any]:
        return node_analyst(state, analyzer_fn=analyzer_fn)

    def _review_node(state: SessionState) -> dict[str, Any]:
        return node_analyst_review(state, headless=headless)

    def _engineer_node(state: SessionState) -> dict[str, Any]:
        return node_engineer(state, engineer_fn=engineer_fn)

    def _planner_node(state: SessionState) -> dict[str, Any]:
        return node_plan(state, planner_fn=planner_fn)

    def _engineer_review_node(state: SessionState) -> dict[str, Any]:
        return node_engineer_review(state, headless=headless)

    def _planner_review_node(state: SessionState) -> dict[str, Any]:
        return node_planner_review(state, headless=headless)

    workflow.add_node("analyst", _analyst_node)
    workflow.add_node("analyst_review", _review_node)
    workflow.add_node("engineer", _engineer_node)
    workflow.add_node("planner", _planner_node)
    workflow.add_node("engineer_review", _engineer_review_node)
    workflow.add_node("planner_review", _planner_review_node)

    workflow.add_edge(START, "analyst")
    workflow.add_conditional_edges("analyst", route_after_analyst)
    workflow.add_conditional_edges("analyst_review", route_after_review)
    workflow.add_conditional_edges("engineer", route_after_engineer)
    workflow.add_conditional_edges("engineer_review", route_after_engineer_review)
    workflow.add_conditional_edges("planner", route_after_planner)
    workflow.add_conditional_edges("planner_review", route_after_planner_review)

    return workflow.compile(checkpointer=checkpointer)


class SessionShell:
    """
    One compiled session graph over one checkpointer: start a thread, answer its interrupts,
    and continue a thread a previous process left unfinished.
    """

    __slots__ = ("_checkpointer", "_graph")

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        *,
        analyzer_fn: Callable[[str, str], dict[str, Any]] | None = None,
        engineer_fn: Callable[[str, str, str, str], str] | None = None,
        planner_fn: Callable[[str, Sequence[str], Sequence[str], str], list[str]] | None = None,
        headless: bool = False,
    ) -> None:
        self._checkpointer = checkpointer or build_checkpointer()
        self._graph = create_session_graph(
            checkpointer=self._checkpointer,
            analyzer_fn=analyzer_fn,
            engineer_fn=engineer_fn,
            planner_fn=planner_fn,
            headless=headless,
        )

    @property
    def checkpointer(self) -> BaseCheckpointSaver[Any]:
        return self._checkpointer

    @property
    def graph(self) -> CompiledStateGraph[Any, Any, Any, Any]:
        return self._graph

    def run(self, initial_input: str, thread_id: str) -> dict[str, Any]:
        """Start a new session on `thread_id` from the user's initial request."""
        state = create_initial_session_state(thread_id, initial_input)
        return self._graph.invoke(state, config=build_session_run_config(thread_id))

    def rescope(self, thread_id: str, request: str, completed_items: Sequence[str]) -> dict[str, Any]:
        """
        `/requirements` (Phase 11): run the graph again on a planned thread. The analyst picks up
        its dialogue with `request`; the planner leaves `completed_items` out of the new plan.
        """
        prompts = list(self.get_state(thread_id).values.get("user_prompts") or [])
        update: SessionState = {
            "user_input": request,
            "user_prompts": [*prompts, request],
            "has_checklist": False,
            "needs_clarification": False,
            "user_confirmed": False,
            "interaction_count": 0,
            "completed_items": list(completed_items),
            "status": SessionStatus.GATHERING_REQUIREMENTS,
        }
        return self._graph.invoke(update, config=build_session_run_config(thread_id))

    def resume(self, thread_id: str, user_response: str | dict[str, str]) -> dict[str, Any]:
        """
        Answer the pending interrupt of `thread_id` (`Command(resume=…)`): the user's text, or
        `{"edited": text}` for an edit made in the editor (`app/session/review.py`).
        """
        return self._graph.invoke(Command(resume=user_response), config=build_session_run_config(thread_id))

    def continue_session(self, thread_id: str) -> dict[str, Any]:
        """
        Continue `thread_id` from its last checkpoint: the node that was running when the
        previous process stopped runs again. A thread waiting at an interrupt stays there.
        """
        return self._graph.invoke(None, config=build_session_run_config(thread_id))

    def get_state(self, thread_id: str) -> Any:
        """The thread's latest checkpoint snapshot."""
        return self._graph.get_state(build_session_run_config(thread_id))

    def has_session(self, thread_id: str) -> bool:
        """Whether a checkpoint exists for `thread_id`."""
        return bool(self.get_state(thread_id).values)

    def is_finished(self, thread_id: str) -> bool:
        """Whether `thread_id` has a checkpoint and nothing left to run."""
        snapshot = self.get_state(thread_id)
        return bool(snapshot.values) and not snapshot.next

    def pending_interrupts(self, thread_id: str) -> list[dict[str, Any]]:
        """The payloads of the interrupts `thread_id` is waiting on, oldest first."""
        snapshot = self.get_state(thread_id)
        return [dict(item.value) for task in snapshot.tasks for item in task.interrupts]
