"""
Session shell workflow and graph assembly conforming to §3.4 and Part K.

Provides:
- `create_session_graph`: Compiles StateGraph with checkpointer, analyst interrupt(), and plan-item iteration.
- `SessionShell`: High-level interface managing sessions, interrupts, and cross-restart resumption.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.session.analyst import node_analyst_interrupt
from app.session.checkpointer import build_checkpointer
from app.session.iteration import (
    node_evaluator,
    node_execute_plan_item,
    node_plan,
)
from app.session.state import SessionState, SessionStatus

logger = logging.getLogger(__name__)


def create_initial_session_state(
    thread_id: str,
    user_input: str,
    specification: str = "",
) -> SessionState:
    """Construct initial state for a session thread."""
    return {
        "session_id": thread_id,
        "user_input": user_input,
        "specification": specification,
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
    """Build LangGraph runnable configuration with recursion limit and thread ID."""
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 200,
    }


def route_after_analyst(state: SessionState) -> str:
    status = state.get("status")
    if status == SessionStatus.ANALYSIS_COMPLETE:
        return "planner"
    if status == SessionStatus.AWAITING_INPUT:
        return "analyst"
    if status in (SessionStatus.FAILED, SessionStatus.PLAN_FAILED):
        return END
    return "planner"


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
    planner_fn: Callable[[str], list[str]] | None = None,
    loop_runner: Callable[..., Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """
    Build and compile the Session Shell LangGraph workflow (Parts K1, K2, K3).

    Workflow structure:
    START -> analyst -(interrupt/resume)-> planner -> execute_plan_item -> evaluator -(loop back)-> END
    """
    if checkpointer is None:
        checkpointer = build_checkpointer()

    workflow = StateGraph(SessionState)

    # 1. Analyst node with interrupt() (Part K2)
    def _analyst_node(state: SessionState) -> dict[str, Any]:
        return node_analyst_interrupt(state, analyzer_fn=analyzer_fn)

    # 2. Planner node (Part K3)
    def _planner_node(state: SessionState) -> dict[str, Any]:
        return node_plan(state, planner_fn=planner_fn)

    # 3. Plan item executor node (Part K3)
    def _execute_item_node(state: SessionState) -> dict[str, Any]:
        return node_execute_plan_item(state, loop_runner=loop_runner)

    # 4. Progress evaluator node (Part K3)
    def _evaluator_node(state: SessionState) -> dict[str, Any]:
        return node_evaluator(state)

    workflow.add_node("analyst", _analyst_node)
    workflow.add_node("planner", _planner_node)
    workflow.add_node("execute_plan_item", _execute_item_node)
    workflow.add_node("evaluator", _evaluator_node)

    # Entry point
    workflow.add_edge(START, "analyst")
    workflow.add_conditional_edges("analyst", route_after_analyst)
    workflow.add_conditional_edges("planner", route_after_planner)
    workflow.add_edge("execute_plan_item", "evaluator")
    workflow.add_conditional_edges("evaluator", route_after_evaluator)

    return workflow.compile(checkpointer=checkpointer)


class SessionShell:
    """
    Session shell coordinator driving the high-level workflow across checkpointer,
    human-in-the-loop analyst, and plan-item iteration.
    """

    __slots__ = ("_checkpointer", "_graph", "_config")

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        *,
        analyzer_fn: Callable[[str, str], dict[str, Any]] | None = None,
        planner_fn: Callable[[str], list[str]] | None = None,
        loop_runner: Callable[..., Any] | None = None,
    ) -> None:
        self._checkpointer = checkpointer or build_checkpointer()
        self._graph = create_session_graph(
            checkpointer=self._checkpointer,
            analyzer_fn=analyzer_fn,
            planner_fn=planner_fn,
            loop_runner=loop_runner,
        )

    @property
    def checkpointer(self) -> BaseCheckpointSaver[Any]:
        return self._checkpointer

    @property
    def graph(self) -> CompiledStateGraph[Any, Any, Any, Any]:
        return self._graph

    def run(
        self,
        initial_input: str,
        thread_id: str,
        *,
        specification: str = "",
    ) -> dict[str, Any]:
        """
        Execute or advance session from initial input under thread_id.
        """
        config = build_session_run_config(thread_id)
        state = create_initial_session_state(thread_id, initial_input, specification)
        return self._graph.invoke(state, config=config)

    async def arun(
        self,
        initial_input: str,
        thread_id: str,
        *,
        specification: str = "",
    ) -> dict[str, Any]:
        """
        Asynchronously execute session under thread_id.
        """
        config = build_session_run_config(thread_id)
        state = create_initial_session_state(thread_id, initial_input, specification)
        return await self._graph.ainvoke(state, config=config)

    def resume(self, thread_id: str, user_response: str) -> dict[str, Any]:
        """
        Resume session paused at an interrupt using Command(resume=user_response).
        """
        config = build_session_run_config(thread_id)
        return self._graph.invoke(Command(resume=user_response), config=config)

    async def aresume(self, thread_id: str, user_response: str) -> dict[str, Any]:
        """
        Asynchronously resume session paused at an interrupt using Command(resume=user_response).
        """
        config = build_session_run_config(thread_id)
        return await self._graph.ainvoke(Command(resume=user_response), config=config)

    def get_state(self, thread_id: str) -> Any:
        """
        Get state snapshot for the given thread_id.
        """
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        return self._graph.get_state(config)
