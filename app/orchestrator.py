import logging
import os
from typing import Literal

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres import PostgresSaver

from app.config import AgentState, Config
from app.graph.nodes import (
    node_plan_task,
    node_execute_progress_evaluator,
    node_execute_quality_gate,
)
from app.graph.subgraphs.build_tdd_subgraph import build_tdd_subgraph


logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("TDDOrchestrator")


# ─────────────────────────────────────────────────────────────────────────────
# COMO O CHECKPOINTER FUNCIONA
# ─────────────────────────────────────────────────────────────────────────────
# PostgresSaver é o backend de persistência de estado do LangGraph. Você nunca
# o chama diretamente — o LangGraph o chama automaticamente em torno de cada
# execução de nó:
#
#   1. ANTES de um nó executar:
#      O LangGraph busca o último snapshot do AgentState armazenado no Postgres
#      sob a chave (thread_id, checkpoint_id) e o entrega ao nó como argumento
#      `state`.
#
#   2. DEPOIS que um nó retorna:
#      O LangGraph pega o dict parcial retornado pelo nó, mescla com o snapshot
#      atual usando o reducer de cada campo (add_messages para listas, sobrescrita
#      simples para todo o resto), e grava o resultado de volta no Postgres como
#      uma nova linha de checkpoint.
#
# COMO O thread_id DELIMITA O CONTEXTO
# ─────────────────────────────────────────────────────────────────────────────
# Toda chamada graph.invoke / graph.stream recebe um dict `config`:
#
#   config={"configurable": {"thread_id": "algum-id-unico"}}
#
# Todas as linhas de checkpoint gravadas durante essa chamada são marcadas com
# esse thread_id. Na próxima vez que graph.invoke for chamado com o *mesmo*
# thread_id, o LangGraph lê exatamente essas linhas — retomando de onde parou,
# com todas as mensagens e estado acumulados intactos.
#
# Usar um thread_id *diferente* dá uma execução completamente independente com
# seu próprio AgentState em branco — sem contaminação cruzada.
#
# O QUE ISSO SIGNIFICA PARA O LLM
# ─────────────────────────────────────────────────────────────────────────────
# O checkpointer NÃO envia nada ao LLM. Ele persiste o AgentState.
# Cada agente (tester, developer, reviewer) tem sua própria lista `*_messages`
# no AgentState. O agente lê essa lista do estado, adiciona seus novos turnos
# de System + Human, chama `llm.invoke(messages)`, recebe a resposta de AI e
# retorna a lista atualizada. O reducer add_messages do LangGraph garante que
# cada novo turno seja anexado — nunca perdido — em todas as re-entradas de nós
# e reinicializações do processo.
# ─────────────────────────────────────────────────────────────────────────────


class TDDOrchestrator:
    def __init__(self, task_key: str = "tdd_task"):
        # task_key vira o thread_id — mude-o para iniciar uma execução limpa.
        self.task_key = task_key

    def _build_main_graph(self, checkpointer):
        workflow = StateGraph(AgentState)

        workflow.add_node("planner", node_plan_task)
        workflow.add_node("evaluator", node_execute_progress_evaluator)
        workflow.add_node("quality_gate", node_execute_quality_gate)

        tdd_graph = build_tdd_subgraph()
        workflow.add_node("tdd_execution", tdd_graph)

        workflow.set_entry_point("planner")

        workflow.add_edge("planner", "tdd_execution")
        workflow.add_edge("tdd_execution", "evaluator")

        def route_evaluator(
            state: AgentState,
        ) -> Literal["tdd_execution", "quality_gate"]:
            if state.get("status") == "next_req":
                return "tdd_execution"
            return "quality_gate"

        workflow.add_conditional_edges("evaluator", route_evaluator)
        workflow.add_edge("quality_gate", END)

        return workflow.compile(checkpointer=checkpointer)

    def run(self, specification: str, function_name: str) -> AgentState:
        # Garante que o diretório de workspace existe antes de qualquer nó
        # tentar gravar arquivos.
        os.makedirs(Config.WORKSPACE_PATH, exist_ok=True)

        initial_state: AgentState = {
            "specification": specification,
            "function_name": function_name,
            "plan": [],
            "plan_index": 0,
            "current_sub_req": "",
            "tests_code": "",
            "implementation_code": "",
            # Históricos de conversa por agente — começam vazios; o add_messages
            # acumula os turnos a cada re-entrada de nó.
            "tester_messages": [],
            "developer_messages": [],
            "reviewer_messages": [],
            # Log de auditoria compartilhado, visível ao orquestrador e no Postgres.
            "audit_log": [],
            "iteration": 0,
            "status": "starting",
            "max_retries": 10,
            "red_attempts": 0,
            "failed_requirements": [],
        }

        logger.info("\n" + "#" * 80)
        logger.info("🚀 INICIANDO ORQUESTRADOR TDD (LangGraph + Postgres)")
        logger.info(f"📂 Função Alvo: {function_name}")
        logger.info(f"🔑 Thread ID : {self.task_key}  (mude para iniciar uma execução limpa)")
        logger.info("#" * 80 + "\n")

        with PostgresSaver.from_conn_string(Config.POSTGRES_URL) as checkpointer:
            # setup() é idempotente — cria as tabelas de checkpoint se ainda não
            # existirem. Seguro chamar a cada inicialização.
            checkpointer.setup()

            graph = self._build_main_graph(checkpointer)

            return graph.invoke(
                initial_state,
                config={
                    "recursion_limit": 150,
                    "configurable": {
                        # Esta é a chave que delimita TODAS as leituras/gravações
                        # no Postgres para esta execução. O LangGraph só carregará
                        # checkpoints gravados sob este mesmo thread_id.
                        "thread_id": self.task_key
                    },
                },
            )