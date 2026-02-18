import logging
from typing import Literal, Optional
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig

from app.config import AgentState
from app.persistence import PersistenceStrategy, PersistenceFactory
from app.graph.nodes import (
    node_plan_task,
    node_execute_progress_evaluator,
    node_execute_quality_gate
)
from app.graph.subgraphs.build_tdd_subgraph import build_tdd_subgraph

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("TDDOrchestrator")

class TDDOrchestrator:
    def __init__(self, task_key: str = "tdd_task", persistence: Optional[PersistenceStrategy] = None):
        self.persistence = persistence or PersistenceFactory.create_persistence("redis")
        self.task_key = task_key
        self.graph = self._build_main_graph()

    def _build_main_graph(self):
        workflow = StateGraph(AgentState)
        workflow.add_node("planner", node_plan_task)
        workflow.add_node("evaluator", node_execute_progress_evaluator)
        workflow.add_node("quality_gate", node_execute_quality_gate)
        
        tdd_graph = build_tdd_subgraph()
        workflow.add_node("tdd_execution", tdd_graph)

        workflow.set_entry_point("planner")
        workflow.add_edge("planner", "tdd_execution")
        workflow.add_edge("tdd_execution", "evaluator")

        def route_evaluator(state: AgentState) -> Literal["tdd_execution", "quality_gate"]:
            if state.get("status") == "next_req":
                return "tdd_execution"
            return "quality_gate"

        workflow.add_conditional_edges("evaluator", route_evaluator)
        workflow.add_edge("quality_gate", END)

        return workflow.compile()

    def run(self, specification: str, function_name: str) -> AgentState:
        initial_state: AgentState = {
            "specification": specification,
            "function_name": function_name,
            "plan": [],
            "plan_index": 0,
            "current_sub_req": "",
            "tests_code": "",
            "implementation_code": "",
            "messages": [],
            "iteration": 0,
            "status": "starting",
            "max_retries": 10,
            "red_attempts": 0,
            "failed_requirements": [] 
        }
        
        logger.info("\n" + "#" * 80)
        logger.info("🚀 INICIANDO ORQUESTRADOR TDD (LangGraph)")
        logger.info(f"📂 Função Alvo: {function_name}")
        logger.info("#" * 80 + "\n")
        
        return self.graph.invoke(initial_state, config={"recursion_limit": 150})