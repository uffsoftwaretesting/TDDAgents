import os
import logging
from typing import Optional, Dict, Any, cast
from langgraph.graph import StateGraph, END, START
from langchain_core.runnables import RunnableConfig
from app.config import AgentState, Config
from app.graph.nodes.execute_quality_gate import node_execute_quality_gate
from app.persistence import PersistenceStrategy, PersistenceFactory
from app.utils.workspace import WorkspaceService
from app.graph.nodes import (
    node_plan_task,
    node_execute_tester,
    node_execute_runner_red,
    node_execute_developer,
    node_execute_runner_green,
    node_execute_progress_evaluator,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class TDDOrchestrator:
    def __init__(
        self, 
        task_key: str = "tdd_task",
        persistence: Optional[PersistenceStrategy] = None,
        max_retries: int = 10
    ):
        self.persistence = persistence or PersistenceFactory.create_persistence("redis")
        self.state_key = f"state:{task_key}"
        self.task_key = task_key
        self.max_retries = max_retries
        self.graph = self._build_graph()

    def _save_state(self, state: AgentState):
        """Persiste o estado atual."""
        self.persistence.save_state(self.task_key, state)
        current = state.get('plan_index', 0) + 1
        total = len(state.get('plan', []))
        logging.info(f"💾 Estado salvo: [{current}/{total}] {state.get('status')}")

    def _load_state(self) -> Optional[AgentState]:
        """Carrega o estado persistido."""
        state = self.persistence.load_state(self.task_key)
        if state:
            current = state.get('plan_index', 0) + 1
            total = len(state.get('plan', []))
            logging.info(f"📂 Estado carregado: [{current}/{total}] {state.get('status')}")
        return state

    def _restore_files_from_state(self, state: AgentState):
        """Restaura arquivos de teste e implementação do estado."""
        if state.get("tests_code"):
            test_path = os.path.join(Config.WORKSPACE_PATH, Config.TEST_FILE)
            with open(test_path, "w", encoding="utf-8") as f:
                f.write(state["tests_code"])
            logging.info(f"📋 Testes restaurados: {len(state['tests_code'])} chars")
        
        if state.get("implementation_code"):
            impl_path = os.path.join(Config.WORKSPACE_PATH, f"{Config.IMPLEMENTATION_MODULE}.py")
            with open(impl_path, "w", encoding="utf-8") as f:
                f.write(state["implementation_code"])
            logging.info(f"💻 Código restaurado: {len(state['implementation_code'])} chars")

    def _build_graph(self):
        """Constrói o grafo de workflow TDD usando nós modulares."""
        
        # Wrappers para adicionar persistência aos nós
        def plan_task(state: AgentState) -> AgentState:
            new_state = node_plan_task(state)
            self._save_state(new_state)
            return new_state

        def execute_tester(state: AgentState) -> AgentState:
            new_state = node_execute_tester(state)
            self._save_state(new_state)
            return new_state

        def execute_runner_red(state: AgentState) -> AgentState:
            new_state = node_execute_runner_red(state, self.max_retries)
            self._save_state(new_state)
            return new_state
            
        def execute_developer(state: AgentState) -> AgentState:
            new_state = node_execute_developer(state)
            self._save_state(new_state)
            return new_state

        def execute_runner_green(state: AgentState) -> AgentState:
            new_state = node_execute_runner_green(state, self.max_retries)
            self._save_state(new_state)
            return new_state

        def execute_progress_evaluator(state: AgentState) -> AgentState:
            new_state = node_execute_progress_evaluator(state)
            self._save_state(new_state)
            return new_state

        def execute_quality_gate(state: AgentState) -> AgentState:
            new_state = node_execute_quality_gate(state)
            self._save_state(new_state)
            return new_state

        # ==================== ROTAS DO GRAFO ====================
        
        def route_after_planner(state: AgentState) -> str:
            status = state.get("status")
            has_plan = state.get("plan") and len(state.get("plan", [])) > 0
            
            if status == "planning_complete" and has_plan:
                logging.info("🔀 Rota: PLANNER → TESTER")
                return "execute_tester"
            else:
                logging.error("🔀 Rota: PLANNER → END (sem plano)")
                return END

        def route_after_tester(state: AgentState) -> str:
            logging.info("🔀 Rota: TESTER → RUNNER_RED")
            return "execute_runner_red"
        
        def route_after_red(state: AgentState) -> str:
            status = state.get("status")
            
            if status == "red_confirmed":
                logging.info("🔀 Rota: RUNNER_RED → DEVELOPER (implementar)")
                return "execute_developer"
            elif status == "invalid_test":
                logging.info("🔀 Rota: RUNNER_RED → TESTER (corrigir teste)")
                return "execute_tester"
            else:
                logging.error(f"🔀 Rota: RUNNER_RED → END (status: {status})")
                return END
        
        def route_after_developer(state: AgentState) -> str:
            logging.info("🔀 Rota: DEVELOPER → RUNNER_GREEN")
            return "execute_runner_green"
        
        def route_after_green(state: AgentState) -> str:
            status = state.get("status")
            
            if status == "green_passed":
                logging.info("🔀 Rota: RUNNER_GREEN → PROGRESS_EVALUATOR (testes passaram!)")
                return "execute_progress_evaluator"
            elif status == "test_review_needed":
                logging.info("🔀 Rota: RUNNER_GREEN → TESTER (revisar testes)")
                return "execute_tester"
            elif status == "green_failed":
                logging.info("🔀 Rota: RUNNER_GREEN → DEVELOPER (corrigir código)")
                return "execute_developer"
            elif status == "max_retries_exceeded":
                logging.error("🔀 Rota: RUNNER_GREEN → END (excedeu tentativas)")
                return END
            else:
                logging.error(f"🔀 Rota: RUNNER_GREEN → END (status: {status})")
                return END

        def route_after_progress_evaluator(state: AgentState) -> str:
            status = state.get("status")
            
            if status == "next_req":
                logging.info("🔀 Rota: PROGRESS_EVALUATOR → TESTER (próximo sub-requisito)")
                return "execute_tester"
                
            elif status == "plan_complete":
                logging.info("🔀 Rota: PROGRESS_EVALUATOR → QUALITY_GATE (Plano completo, avaliando qualidade...)")
                return "execute_quality_gate" 
                
            else:
                logging.error(f"🔀 Rota: PROGRESS_EVALUATOR → END (status: {status})")
                return END

        # ==================== CONSTRUÇÃO DO GRAFO ====================
        
        workflow = StateGraph(AgentState)
        
        workflow.add_node("plan_task", plan_task)
        workflow.add_node("execute_tester", execute_tester)
        workflow.add_node("execute_runner_red", execute_runner_red)
        workflow.add_node("execute_developer", execute_developer)
        workflow.add_node("execute_runner_green", execute_runner_green)
        workflow.add_node("execute_progress_evaluator", execute_progress_evaluator)
        workflow.add_node("execute_quality_gate", execute_quality_gate)
        
        workflow.add_edge(START, "plan_task")
        
        workflow.add_conditional_edges("plan_task", route_after_planner)
        workflow.add_conditional_edges("execute_tester", route_after_tester)
        workflow.add_conditional_edges("execute_runner_red", route_after_red)
        workflow.add_conditional_edges("execute_developer", route_after_developer)
        workflow.add_conditional_edges("execute_runner_green", route_after_green)
        workflow.add_conditional_edges("execute_progress_evaluator", route_after_progress_evaluator)
        
        workflow.add_edge("execute_quality_gate", END)
        
        return workflow.compile()

    def run(self, specification: str, resume: bool = False, function_name: str = "function") -> AgentState:
        """
        Executa o workflow TDD incremental e cumulativo.
        
        Args:
            specification: Especificação completa do projeto (obrigatória se resume=False)
            resume: Se True, retoma do estado salvo
            function_name: Nome explícito da função (opcional, extraído da spec se None)
        
        Returns:
            Estado final do workflow
        """
        
        if resume:
            logging.info("🔄 RETOMANDO WORKFLOW TDD INCREMENTAL DO ESTADO SALVO")
            logging.info("🚀 " * 25)
            
            saved_state = self._load_state()
            
            if not saved_state:
                logging.error("❌ Nenhum estado salvo encontrado.")
                return {
                    "specification": "",
                    "function_name": "",
                    "plan": [],
                    "current_sub_req": "",
                    "tests_code": "",
                    "implementation_code": "",
                    "feedback": "Specification is required",
                    "iteration": 0,
                    "plan_index": 0,
                    "status": "error",
                    "max_retries": self.max_retries,
                    "red_attempts": 0
                }
            
            WorkspaceService.setup_workspace(clean=False)
            self._restore_files_from_state(saved_state)
            
            initial_state = saved_state
            
        else:
            logging.info("🚀 INICIANDO WORKFLOW TDD INCREMENTAL E CUMULATIVO")
            logging.info("🚀 " * 25)
            
            if not specification:
                logging.error("❌ Especificação é obrigatória para novo workflow.")
                return {
                    "specification": "",
                    "function_name": "",
                    "plan": [],
                    "current_sub_req": "",
                    "tests_code": "",
                    "implementation_code": "",
                    "feedback": "Specification is required",
                    "iteration": 0,
                    "plan_index": 0,
                    "status": "error",
                    "max_retries": self.max_retries,
                    "red_attempts": 0
                }
            
            WorkspaceService.setup_workspace(clean=True)
            
            logging.info(f"🎯 Função principal detectada: {function_name}")
            
            initial_state: AgentState = {
                "specification": specification,
                "function_name": function_name,
                "plan": [],
                "current_sub_req": "",
                "tests_code": "",
                "implementation_code": "",
                "feedback": "",
                "iteration": 0,
                "plan_index": 0,
                "status": "starting",
                "max_retries": self.max_retries,
                "red_attempts": 0 
            }
        
        final_state: AgentState
        
        try:
            config: RunnableConfig = {
                "recursion_limit": 1000
            }
            
            final_state = cast(AgentState, self.graph.invoke(initial_state, config=config))
            
        except Exception as e:
            logging.error(f"❌ Erro crítico no workflow: {e}")
            import traceback
            logging.error(traceback.format_exc())
            
            final_state: AgentState = {
                **initial_state,
                "status": "error",
            }
            
            self._save_state(final_state)
        
        logging.info("\n" + "=" * 70)
        logging.info("📊 RESULTADO FINAL DO WORKFLOW TDD INCREMENTAL")
        logging.info("=" * 70)
        logging.info(f"✅ Status: {final_state.get('status', 'unknown')}")
        completed = final_state.get('plan_index', 0)
        total = len(final_state.get('plan', []))
        if final_state.get('status') == 'plan_complete':
            completed = total
        logging.info(f"🔢 Sub-requisitos completos: {completed}/{total}")
        logging.info(f"📄 Implementação: {Config.WORKSPACE_PATH}/{Config.IMPLEMENTATION_MODULE}.py")
        logging.info(f"📋 Testes: {Config.WORKSPACE_PATH}/{Config.TEST_FILE}")
        logging.info("=" * 70)
        
        return final_state
