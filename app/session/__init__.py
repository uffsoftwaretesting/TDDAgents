"""
Session Shell module conforming to §3.4 and Part K.

Exports:
- SessionState, SessionStatus, PlanItemResult
- build_checkpointer
- node_analyst_interrupt
- node_plan, node_execute_plan_item, node_evaluator
- create_session_graph, SessionShell
"""

from __future__ import annotations

from app.session.analyst import node_analyst_interrupt
from app.session.checkpointer import build_checkpointer
from app.session.iteration import node_evaluator, node_execute_plan_item, node_plan
from app.session.shell import (
    SessionShell,
    build_session_run_config,
    create_initial_session_state,
    create_session_graph,
    route_after_analyst,
    route_after_evaluator,
    route_after_planner,
)
from app.session.state import PlanItemResult, SessionState, SessionStatus

__all__ = [
    "PlanItemResult",
    "SessionShell",
    "SessionState",
    "SessionStatus",
    "build_checkpointer",
    "build_session_run_config",
    "create_initial_session_state",
    "create_session_graph",
    "node_analyst_interrupt",
    "node_evaluator",
    "node_execute_plan_item",
    "node_plan",
    "route_after_analyst",
    "route_after_evaluator",
    "route_after_planner",
]
