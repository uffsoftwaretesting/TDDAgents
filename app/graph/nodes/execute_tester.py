import os
import logging
from app.config import AgentState, Config
from app.agents.langgraph.tester import generate_test_for_sub_req


def node_execute_tester(state: AgentState) -> AgentState:
    """Nó do grafo que gera ou revisa testes."""
    sub_req = state["current_sub_req"]
    plan_idx = state.get("plan_index", 0)
    total = len(state.get("plan", []))
    function_name = state.get("function_name", "process")
    feedback = state.get("feedback", "")
    iteration = state.get("iteration", 0)
    
    # Detecta modo de revisão de testes
    is_test_review = "REVISÃO DE TESTES NECESSÁRIA" in feedback
    
    if is_test_review:
        logging.info("=" * 70)
        logging.info(f"🔧 FASE 2B: TESTER (REVISÃO) - [{plan_idx + 1}/{total}]")
        logging.info(f"🔄 Iteração atual: {iteration}")
        logging.info(f"🎯 Revisando testes para: '{sub_req}'")
        logging.info("=" * 70)
    else:
        logging.info("=" * 70)
        logging.info(f"📝 FASE 2: TESTER - [{plan_idx + 1}/{total}]")
        logging.info(f"🎯 Criando teste para: '{sub_req}'")
        logging.info(f"🎯 Função: {function_name}")
        logging.info("=" * 70)
    
    previous_tests = state.get("tests_code", "")
    
    new_tests = generate_test_for_sub_req(
        sub_requirement=sub_req,
        function_name=function_name,
        all_tests_code=previous_tests,
        feedback=feedback
    )
    
    test_path = os.path.join(Config.WORKSPACE_PATH, Config.TEST_FILE)
    with open(test_path, "w", encoding="utf-8") as f:
        f.write(new_tests)
    
    # ⚠️ INCREMENTA ITERATION APENAS EM MODO REVISÃO
    # Isso permite que o workflow continue após revisões em iteration 6 e 9
    if is_test_review:
        new_iteration = iteration + 1
        logging.info(f"🔄 Tester incrementou iteration: {iteration} → {new_iteration}")
    else:
        new_iteration = iteration
    
    new_state: AgentState = {
        **state,
        "tests_code": new_tests,
        "feedback": "",
        "status": "tests_written",
        "iteration": new_iteration
    }
    
    return new_state