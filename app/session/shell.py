"""
The session shell: what LangGraph still does once the loop runs each sub-requirement
(§3.4 and Part K of `docs/transition_elaboration_plan.md`, Phase 9).

```
START → analyst ⇄ analyst_review (interrupt) → engineer → planner
      → execute_plan_item → evaluator ⟲ → END
```

LangGraph keeps the three things the loop has no answer for: checkpointing (so a session
survives a restart and resumes by thread id), the analyst's human-in-the-loop `interrupt()`,
and iteration over plan items. Each plan item is one `run_loop` session
(`app/cli/print.py::tdd_loop_runner`); the TDD barriers live there, not in this graph.

**No ceiling on super-steps.** LangGraph's `recursion_limit` would bound the analyst rounds
and the plan length; claude-code has no such bound and this codebase invents none, so it is
set to `sys.maxsize` and the session ends when the plan does.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.session.analyst import node_analyst, node_analyst_review
from app.session.checkpointer import build_checkpointer
from app.session.engineer import node_engineer
from app.session.iteration import (
    node_evaluator,
    node_execute_plan_item,
    node_plan,
)
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
        "plan_index": 0,
        "subreq_results": [],
        "status": SessionStatus.INITIALIZING,
        "audit_log": [f"[Session] Started thread '{thread_id}'."],
        "success_count": 0,
        "failure_count": 0,
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
    return "planner" if state.get("status") == SessionStatus.PLANNING else END


def route_after_planner(state: SessionState) -> str:
    status = state.get("status")
    plan = state.get("plan", [])
    if status == SessionStatus.PLAN_FAILED or not plan:
        return END
    return "execute_plan_item"


def route_after_evaluator(state: SessionState) -> str:
    status = state.get("status")
    if status == SessionStatus.EXECUTING_PLAN_ITEM:
        return "execute_plan_item"
    return END


def create_session_graph(
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    *,
    analyzer_fn: Callable[[str, str], dict[str, Any]] | None = None,
    engineer_fn: Callable[[str, str], str] | None = None,
    planner_fn: Callable[[str], list[str]] | None = None,
    loop_runner: Callable[..., Any] | None = None,
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

    def _execute_item_node(state: SessionState) -> dict[str, Any]:
        return node_execute_plan_item(state, loop_runner=loop_runner)

    workflow.add_node("analyst", _analyst_node)
    workflow.add_node("analyst_review", _review_node)
    workflow.add_node("engineer", _engineer_node)
    workflow.add_node("planner", _planner_node)
    workflow.add_node("execute_plan_item", _execute_item_node)
    workflow.add_node("evaluator", node_evaluator)

    workflow.add_edge(START, "analyst")
    workflow.add_conditional_edges("analyst", route_after_analyst)
    workflow.add_conditional_edges("analyst_review", route_after_review)
    workflow.add_conditional_edges("engineer", route_after_engineer)
    workflow.add_conditional_edges("planner", route_after_planner)
    workflow.add_edge("execute_plan_item", "evaluator")
    workflow.add_conditional_edges("evaluator", route_after_evaluator)

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
        engineer_fn: Callable[[str, str], str] | None = None,
        planner_fn: Callable[[str], list[str]] | None = None,
        loop_runner: Callable[..., Any] | None = None,
        headless: bool = False,
    ) -> None:
        self._checkpointer = checkpointer or build_checkpointer()
        self._graph = create_session_graph(
            checkpointer=self._checkpointer,
            analyzer_fn=analyzer_fn,
            engineer_fn=engineer_fn,
            planner_fn=planner_fn,
            loop_runner=loop_runner,
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

    def resume(self, thread_id: str, user_response: str) -> dict[str, Any]:
        """Answer the pending interrupt of `thread_id` (`Command(resume=…)`)."""
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
