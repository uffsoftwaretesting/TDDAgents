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
    """
    Constrói o ciclo TDD interno:  Tester → RunnerRed → Developer → RunnerGreen
                                                ↑_____________________________|
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("tester", node_execute_tester)
    workflow.add_node("runner_red", node_execute_runner_red)
    workflow.add_node("developer", node_execute_developer)
    workflow.add_node("runner_green", node_execute_runner_green)

    workflow.add_edge(START, "tester")

    def route_after_tester(state: AgentState) -> Literal["runner_red", END]:
        if state.get("status") == "tester_failed":
            return END
        return "runner_red"

    workflow.add_conditional_edges("tester", route_after_tester)

    def route_after_red(state: AgentState) -> Literal["developer", "tester", END]:
        status = state.get("status")
        
        # Envia para o Developer de qualquer forma (Falha real ou Green no Red)
        if status == "red_confirmed":
            return "developer"
        
        # Se o Reviewer notou que o teste escrito está quebrado/inválido
        if status == "test_review_needed":
            return "tester"
            
        if status == "tester_failed":
            return END
            
        return "tester"

    workflow.add_conditional_edges("runner_red", route_after_red)
    
    workflow.add_edge("developer", "runner_green")

    def route_after_green(state: AgentState) -> Literal[END, "developer", "tester"]:
        status = state.get("status")
        if status == "green_passed":
            return END
        # Roteamento dinâmico baseado no is_test_fault do Reviewer
        if status == "test_review_needed":
            return "tester"
        if status in ("max_retries_exceeded", "tester_failed"):
            return END
        # Se falhou e a culpa não é do teste, volta para a implementação
        return "developer"

    workflow.add_conditional_edges("runner_green", route_after_green)

    return workflow.compile()