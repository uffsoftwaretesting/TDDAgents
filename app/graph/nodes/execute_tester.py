import os
import logging
from app.config import AgentState, Config
from app.agents.langgraph.tester import generate_test_for_sub_req


def node_execute_tester(state: AgentState) -> AgentState:
    """Nó do grafo que gera testes para o sub-requisito atual."""
    sub_req = state["current_sub_req"]
    plan_idx = state.get("plan_index", 0)
    total = len(state.get("plan", []))
    tests_code = state.get("tests_code", "")
    feedback = state.get("feedback", "")
    function_name = state.get("function_name", "process")
    
    logging.info("=" * 70)
    logging.info(f"🧪 FASE 2: TESTER - Sub-requisito [{plan_idx + 1}/{total}]")
    logging.info(f"📝 '{sub_req}'")
    logging.info(f"🎯 Função: {function_name}")
    logging.info(f"📊 Testes existentes: {len([l for l in tests_code.split('\\n') if 'def test_' in l])} funções")
    logging.info("=" * 70)
    
    new_tests_code = generate_test_for_sub_req(
        sub_requirement=sub_req,
        function_name=function_name,
        all_tests_code=tests_code,
        feedback=feedback
    )
    
    test_path = os.path.join(Config.WORKSPACE_PATH, Config.TEST_FILE)
    with open(test_path, "w", encoding="utf-8") as f:
        f.write(new_tests_code)
    
    num_tests = len([l for l in new_tests_code.split('\n') if 'def test_' in l])
    logging.info(f"✅ Total de testes agora: {num_tests}")
    
    new_state: AgentState = {
        **state,
        "tests_code": new_tests_code,
        "feedback": "",
        "status": "test_written"
    }
    
    return new_state
