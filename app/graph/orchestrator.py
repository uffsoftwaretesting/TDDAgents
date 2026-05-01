import logging
from typing import Literal

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.memory import InMemorySaver
from e2b_code_interpreter import Sandbox
from psycopg import OperationalError

from app.config.config import AgentState, Config
from app.graph.nodes import (
    node_plan_task,
    node_execute_progress_evaluator,
    node_execute_quality_gate,
)
from app.graph.subgraphs.build_tdd_subgraph import build_tdd_subgraph
from app.utils.callbacks import GlobalTokenTracker

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
        self.task_key = task_key
        self.token_tracker = GlobalTokenTracker()

    def _build_config(self) -> dict:
        """
        Single place for LangGraph run config.
        To add LangSmith tracing, extra callbacks, or change limits — do it here only.
        """
        return {
            "recursion_limit": 150,
            "configurable": {"thread_id": self.task_key},
            "callbacks": [self.token_tracker],
        }

    def _invoke_graph(self, initial_state: AgentState, checkpointer) -> AgentState:
        self.token_tracker.reset()  # clean slate for each invocation
        graph = self._build_main_graph(checkpointer)
        return graph.invoke(initial_state, config=self._build_config())

    def _build_main_graph(self, checkpointer):
        workflow = StateGraph(AgentState)

        workflow.add_node("planner", node_plan_task)
        workflow.add_node("tdd_execution", build_tdd_subgraph())
        workflow.add_node("evaluator", node_execute_progress_evaluator)
        workflow.add_node("quality_gate", node_execute_quality_gate)

        workflow.set_entry_point("planner")

        def route_after_planner(state: AgentState) -> Literal["planner", "tdd_execution", END]:
            status = state.get("status")
            if status == "infra_error_planner":
                return "planner"
            if status == "plan_failed":
                return END
            return "tdd_execution"

        workflow.add_conditional_edges("planner", route_after_planner)

        workflow.add_edge("tdd_execution", "evaluator")

        def route_evaluator(state: AgentState) -> Literal["tdd_execution", "quality_gate", END]:
            status = state.get("status")
            if status in ("sandbox_failed", "tester_failed", "developer_failed"):
                return END
            if status == "next_req":
                return "tdd_execution"
            return "quality_gate"

        workflow.add_conditional_edges("evaluator", route_evaluator)
        workflow.add_edge("quality_gate", END)

        return workflow.compile(checkpointer=checkpointer)

    def run(self, specification: str, requirements: str) -> AgentState:
        logger.info("📦 Inicializando E2B Cloud Sandbox...")
        sandbox = Sandbox.create(api_key=Config.E2B_API_KEY)
        sandbox_id = sandbox.sandbox_id

        initial_state: AgentState = {
            "specification": specification,
            "requirements": requirements,
            "plan": [],
            "plan_index": 0,
            "current_sub_req": "",
            "sandbox_id": sandbox_id,
            "file_system": {},
            "tester_messages": [],
            "developer_messages": [],
            "reviewer_messages": [],
            "audit_log": [],
            "iteration": 1,
            "infra_retries": 0,
            "status": "starting",
            "max_retries": Config.MAX_ITERATIONS,
            "failed_requirements": [],
            "total_detected_failures": 0,
            "autonomously_corrected_failures": 0,
            "current_subreq_failures": 0,
            "is_type_fault": "",
            "test_faults": 0,        
            "implementation_faults": 0,
        }

        logger.info("\n" + "#" * 80)
        logger.info("🚀 INICIANDO ORQUESTRADOR TDD (LangGraph + Postgres + E2B Sandbox)")
        logger.info(f"☁️  Sandbox ID ativa: {sandbox_id}")
        logger.info("#" * 80 + "\n")

        try:
            try:
                with PostgresSaver.from_conn_string(Config.POSTGRES_URL) as checkpointer:
                    checkpointer.setup()
                    logger.info("🗄️ Checkpointer ativo: PostgresSaver")
                    return self._invoke_graph(initial_state, checkpointer)
            except OperationalError as exc:
                logger.warning(
                    "⚠️ Não foi possível conectar ao Postgres (%s). "
                    "Executando com InMemorySaver sem persistência entre reinícios.",
                    exc,
                )
                memory_checkpointer = InMemorySaver()
                logger.info("🧠 Checkpointer ativo: InMemorySaver (fallback)")
                return self._invoke_graph(initial_state, memory_checkpointer)
        finally:
            if 'sandbox' in locals():
                logger.info(f"🧹 Encerrando Sandbox {sandbox_id}...")
                sandbox.kill()