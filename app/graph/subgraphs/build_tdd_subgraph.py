from typing import Literal
from langgraph.graph import StateGraph, END, START

from app.config.config import AgentState
from app.graph.nodes import (
    node_execute_tester,
    node_execute_runner_red,
    node_execute_developer,
    node_execute_runner_green,
)

def build_tdd_subgraph():
    """
    Constrói o ciclo TDD interno:  Tester → RunnerRed → Developer → RunnerGreen
                                                ↑_____________________________|
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("tester", node_execute_tester)
    workflow.add_node("runner_red", node_execute_runner_red)
    workflow.add_node("developer", node_execute_developer)
    workflow.add_node("runner_green", node_execute_runner_green)

    # ── 1. Entrada ──
    workflow.add_edge(START, "tester")

    # ── 2. Saída do Tester ──
    def route_after_tester(state: AgentState) -> Literal["tester", "runner_red", END]:
        status = state.get("status")
        if status == "infra_error_tester":
            return "tester"
        if status in ("tester_failed", "sandbox_failed"):
            return END
        return "runner_red"

    workflow.add_conditional_edges("tester", route_after_tester)

    # ── 3. Saída do Runner Red ──
    def route_after_red(state: AgentState) -> Literal["developer", "tester", "runner_red", END]:
        status = state.get("status")
        
        if status == "infra_error_red":
            return "runner_red"
            
        if status == "red_confirmed":
            return "developer"
            
        if status == "test_review_needed":
            return "tester"
            
        if status in ("tester_failed", "max_retries_exceeded", "sandbox_failed"):
            return END
            
        return "tester"

    workflow.add_conditional_edges("runner_red", route_after_red)
    
    # ── 4. Saída do Developer ──
    def route_after_developer(state: AgentState) -> Literal["developer", "runner_green", END]:
        status = state.get("status")
        
        if status == "infra_error_developer":
            return "developer"
            
        if status in ("developer_failed", "sandbox_failed"):
            return END
            
        return "runner_green"
        
    workflow.add_conditional_edges("developer", route_after_developer)

    # ── 5. Saída do Runner Green ──
    def route_after_green(state: AgentState) -> Literal[END, "developer", "tester", "runner_green"]:
        status = state.get("status")
        
        if status == "infra_error_green":
            return "runner_green"
            
        if status == "green_passed":
            return END
            
        if status == "test_review_needed":
            return "tester"
            
        if status in ("max_retries_exceeded", "tester_failed", "developer_failed", "sandbox_failed"):
            return END
            
        return "developer"

    workflow.add_conditional_edges("runner_green", route_after_green)

    return workflow.compile()