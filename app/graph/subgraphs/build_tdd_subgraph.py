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

    Passagem de estado pelo subgrafo
    ─────────────────────────────────
    Como o subgrafo recebe e retorna o *AgentState completo* (mesmo tipo do grafo
    pai), cada lista de mensagens por agente (tester_messages, developer_messages,
    reviewer_messages) e o audit_log se acumulam ao longo das múltiplas iterações
    deste loop — e tudo isso é salvo em checkpoint no Postgres pelo PostgresSaver
    do grafo pai após cada nó.
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("tester", node_execute_tester)
    workflow.add_node("runner_red", node_execute_runner_red)
    workflow.add_node("developer", node_execute_developer)
    workflow.add_node("runner_green", node_execute_runner_green)

    workflow.add_edge(START, "tester")

    def route_after_tester(state: AgentState) -> Literal["runner_red", END]:
        # Se o tester esgotou suas tentativas de autocorreção, sai imediatamente
        # em vez de entregar um arquivo de testes quebrado ao runner_red.
        if state.get("status") == "tester_failed":
            return END
        return "runner_red"

    workflow.add_conditional_edges("tester", route_after_tester)

    def route_after_red(state: AgentState) -> Literal["developer", "tester", END]:
        status = state.get("status")
        if status == "red_confirmed":
            return "developer"
        if status == "tester_failed":
            return END
        return "tester"

    workflow.add_conditional_edges("runner_red", route_after_red)
    workflow.add_edge("developer", "runner_green")

    def route_after_green(state: AgentState) -> Literal[END, "developer", "tester"]:
        status = state.get("status")
        if status == "green_passed":
            return END
        if status == "test_review_needed":
            return "tester"
        if status in ("max_retries_exceeded", "tester_failed"):
            return END
        return "developer"

    workflow.add_conditional_edges("runner_green", route_after_green)

    return workflow.compile()