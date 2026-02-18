from typing import Literal
from langgraph.graph import StateGraph, END, START

from app.config import AgentState
from app.graph.nodes import (
    node_execute_tester,
    node_execute_runner_red,
    node_execute_developer,
    node_execute_runner_green,
)

def build_tdd_subgraph():
    workflow = StateGraph(AgentState)
    workflow.add_node("tester", node_execute_tester)
    workflow.add_node("runner_red", node_execute_runner_red)
    workflow.add_node("developer", node_execute_developer)
    workflow.add_node("runner_green", node_execute_runner_green)

    workflow.add_edge(START, "tester")
    workflow.add_edge("tester", "runner_red")

    def route_after_red(state: AgentState) -> Literal["developer", "tester"]:
        if state.get("status") == "red_confirmed":
            return "developer"
        return "tester"

    workflow.add_conditional_edges("runner_red", route_after_red)
    workflow.add_edge("developer", "runner_green")

    def route_after_green(state: AgentState) -> Literal[END, "developer", "tester"]:
        status = state.get("status")
        if status == "green_passed":
            return END
        if status == "test_review_needed":
            return "tester"
        if status == "max_retries_exceeded":
            return END
        return "developer"

    workflow.add_conditional_edges("runner_green", route_after_green)
    return workflow.compile()