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

    # ── 1. Entrada ──
    workflow.add_edge(START, "tester")

    # ── 2. Saída do Tester ──
    def route_after_tester(state: AgentState) -> Literal["runner_red", END]:
        if state.get("status") == "tester_failed":
            return END
        return "runner_red"

    workflow.add_conditional_edges("tester", route_after_tester)

    # ── 3. Saída do Runner Red ──
    def route_after_red(state: AgentState) -> Literal["developer", "tester", END]:
        status = state.get("status")
        
        # Envia para o Developer de qualquer forma (Falha real ou Green no Red alertado)
        if status == "red_confirmed":
            return "developer"
        
        # Se o Reviewer notou que o teste escrito está quebrado/inválido
        if status == "test_review_needed":
            return "tester"
            
        # Aborta se o Tester falhar na API ou estourar o limite de tentativas de corrigir o teste
        if status in ("tester_failed", "max_retries_exceeded"):
            return END
            
        return "tester"

    workflow.add_conditional_edges("runner_red", route_after_red)
    
    # ── 4. Saída do Developer (CORRIGIDA) ──
    # Removido: workflow.add_edge("developer", "runner_green")
    def route_after_developer(state: AgentState) -> Literal["runner_green", END]:
        # Se o Developer sofreu um erro sistêmico/API, aborta imediatamente
        if state.get("status") == "developer_failed":
            return END
        return "runner_green"
        
    workflow.add_conditional_edges("developer", route_after_developer)

    # ── 5. Saída do Runner Green ──
    def route_after_green(state: AgentState) -> Literal[END, "developer", "tester"]:
        status = state.get("status")
        
        # Passou! Sai do subgrafo rumo ao próximo requisito.
        if status == "green_passed":
            return END
            
        # Roteamento dinâmico baseado no is_test_fault do Reviewer
        if status == "test_review_needed":
            return "tester"
            
        # Aborta se exceder o limite de iterações ou se algum erro grave vazou
        if status in ("max_retries_exceeded", "tester_failed", "developer_failed"):
            return END
            
        # Se falhou e a culpa é da implementação (green_failed), volta para o Developer
        return "developer"

    workflow.add_conditional_edges("runner_green", route_after_green)

    return workflow.compile()