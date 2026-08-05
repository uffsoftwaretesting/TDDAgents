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
)
from app.graph.subgraphs.build_tdd_subgraph import build_tdd_subgraph
from app.utils.token_metrics import GlobalTokenTracker

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("TDDOrchestrator")

# ─────────────────────────────────────────────────────────────────────────────
# HOW THE CHECKPOINTER WORKS
# ─────────────────────────────────────────────────────────────────────────────
# PostgresSaver is LangGraph's state persistence backend. You never call it
# directly — LangGraph calls it automatically around every node execution:
#
#   1. BEFORE a node runs:
#      LangGraph fetches the latest AgentState snapshot stored in Postgres
#      under the key (thread_id, checkpoint_id) and hands it to the node as
#      the `state` argument.
#
#   2. AFTER a node returns:
#      LangGraph takes the partial dict returned by the node, merges it with
#      the current snapshot using each field's reducer (add_messages for
#      lists, plain overwrite for everything else), and writes the result
#      back to Postgres as a new checkpoint row.
#
# HOW thread_id SCOPES CONTEXT
# ─────────────────────────────────────────────────────────────────────────────
# Every graph.invoke / graph.stream call receives a `config` dict:
#
#   config={"configurable": {"thread_id": "some-unique-id"}}
#
# All checkpoint rows written during that call are tagged with that
# thread_id. The next time graph.invoke is called with the *same* thread_id,
# LangGraph reads exactly those rows — resuming where it left off, with all
# accumulated messages and state intact.
#
# Using a *different* thread_id gives a completely independent run with its
# own blank AgentState — no cross-contamination.
#
# WHAT THIS MEANS FOR THE LLM
# ─────────────────────────────────────────────────────────────────────────────
# The checkpointer does NOT send anything to the LLM. It persists the
# AgentState. Each agent (tester, developer, reviewer) has its own
# `*_messages` list in the AgentState. The agent reads that list from the
# state, appends its new System + Human turns, calls `llm.invoke(messages)`,
# receives the AI response, and returns the updated list. LangGraph's
# add_messages reducer guarantees each new turn is appended — never lost —
# across node re-entries and process restarts.
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

        def route_evaluator(state: AgentState) -> Literal["tdd_execution", END]:
            status = state.get("status")
            if status in ("sandbox_failed", "tester_failed", "developer_failed"):
                return END
            if status == "next_req":
                return "tdd_execution"
            return END

        workflow.add_conditional_edges("evaluator", route_evaluator)

        return workflow.compile(checkpointer=checkpointer)

    def run(self, specification: str, requirements: str) -> AgentState:
        logger.info("📦 Initializing E2B Cloud Sandbox...")
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
            "subreq_success_count": 0,
            "subreq_failure_count": 0,
            "subreq_results": [],
            "is_flow_type": [],
        }

        logger.info("\n" + "#" * 80)
        logger.info("🚀 STARTING TDD ORCHESTRATOR (LangGraph + Postgres + E2B Sandbox)")
        logger.info(f"☁️  Active Sandbox ID: {sandbox_id}")
        logger.info("#" * 80 + "\n")

        try:
            try:
                with PostgresSaver.from_conn_string(Config.POSTGRES_URL) as checkpointer:
                    checkpointer.setup()
                    logger.info("🗄️ Active checkpointer: PostgresSaver")
                    return self._invoke_graph(initial_state, checkpointer)
            except OperationalError as exc:
                logger.warning(
                    "⚠️ Could not connect to Postgres (%s). "
                    "Running with InMemorySaver, no persistence across restarts.",
                    exc,
                )
                memory_checkpointer = InMemorySaver()
                logger.info("🧠 Active checkpointer: InMemorySaver (fallback)")
                return self._invoke_graph(initial_state, memory_checkpointer)
        finally:
            if 'sandbox' in locals():
                logger.info(f"🧹 Shutting down Sandbox {sandbox_id}...")
                sandbox.kill()