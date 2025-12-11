import os
import logging
from app.config import AgentState, Config
from app.agents.langgraph.developer import generate_code_incremental


def node_execute_developer(state: AgentState) -> AgentState:
    """Nó do grafo que implementa código para passar nos testes."""
    sub_req = state["current_sub_req"]
    iteration = state.get("iteration", 0) + 1
    plan_idx = state.get("plan_index", 0)
    total = len(state.get("plan", []))
    function_name = state.get("function_name", "process")
    
    logging.info("=" * 70)
    logging.info(f"💻 FASE 4: DEVELOPER - [{plan_idx + 1}/{total}] Iteração {iteration}")
    logging.info(f"🎯 Implementando: '{sub_req}'")
    logging.info(f"🎯 Função: {function_name}")
    logging.info(f"📦 Código anterior: {len(state.get('implementation_code', '').split('\\n'))} linhas")
    logging.info("=" * 70)
    
    tests_code = state["tests_code"]
    feedback = state["feedback"]
    previous_code = state.get("implementation_code", "")
    
    new_code = generate_code_incremental(
        test_code=tests_code,
        function_name=function_name,
        feedback=feedback,
        previous_code=previous_code
    )
    
    impl_path = os.path.join(Config.WORKSPACE_PATH, f"{Config.IMPLEMENTATION_MODULE}.py")
    with open(impl_path, "w", encoding="utf-8") as f:
        f.write(new_code)
    
    new_state: AgentState = {
        **state,
        "implementation_code": new_code,
        "iteration": iteration,
        "feedback": "",
        "status": "code_written"
    }
    
    return new_state
