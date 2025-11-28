import logging
from app.config import AgentState
from app.agents.langgraph.runner import run_pytest
from app.agents.langgraph.reviewer import analyze_failures
from app.utils.prompt_loader import load_prompt


def node_execute_runner_green(state: AgentState, max_retries: int = 10) -> AgentState:
    """Nó do grafo que verifica se todos os testes passam (GREEN)."""
    sub_req = state["current_sub_req"]
    iteration = state.get("iteration", 0)
    max_retries_state = state.get("max_retries", max_retries)
    plan_idx = state.get("plan_index", 0)
    
    logging.info("=" * 70)
    logging.info(f"🟢 FASE 5: RUNNER GREEN - TODOS os testes devem passar!")
    logging.info(f"🔄 Iteração {iteration}/{max_retries_state}")
    logging.info(f"🎯 Sub-requisito [{plan_idx + 1}]: '{sub_req}'")
    logging.info("=" * 70)
    
    output = run_pytest()
    logging.info(f"📊 Resultado pytest:\n{output}")
    
    all_passed = "passed" in output.lower() and "failed" not in output.lower() and "error" not in output.lower()
    
    if all_passed:
        logging.info("=" * 70)
        logging.info("✅✅✅ GREEN COMPLETO! TODOS OS TESTES PASSARAM! ✅✅✅")
        logging.info(f"✅ Sub-requisito [{plan_idx + 1}] completado com sucesso!")
        logging.info("=" * 70)
        new_state = {**state, "status": "green_passed", "feedback": "", "iteration": 0}
    else:
        # Após 5 iterações, volta ao Tester para revisar testes
        if iteration >= 5 and iteration < max_retries_state:
            logging.warning("=" * 70)
            logging.warning(f"⚠️ REVISÃO DE TESTES NECESSÁRIA!")
            logging.warning(f"🔄 Tentativa {iteration}/{max_retries_state} - Falhas persistentes")
            logging.warning("🔧 Voltando para o Tester revisar os testes...")
            logging.warning("=" * 70)
            
            reviewer_feedback = analyze_failures(
                test_output=output,
                specification=state["specification"],
                sub_requirement=state["current_sub_req"],
                iteration=iteration,
                max_retries=max_retries_state,
                current_code=state.get("implementation_code", ""),
                test_code=state.get("tests_code", "")
            )
            
            test_review_feedback = load_prompt(
                template_name='agents/langgraph/orchestrator/feedback_test_review_needed.jinja2',
                iteration=iteration,
                max_retries=max_retries_state,
                reviewer_feedback=reviewer_feedback
            )

            logging.info("=" * 70)
            logging.info("🔍 FEEDBACK DA REVISÃO DOS TESTES:")
            logging.info("=" * 70)
            for line in test_review_feedback.split('\n'):
                logging.info(line)
            logging.info("=" * 70)

            new_state = {**state, "status": "test_review_needed", "feedback": test_review_feedback}
            
        elif iteration >= max_retries_state:
            logging.error("=" * 70)
            logging.error(f"❌ FALHA CRÍTICA: Excedeu {max_retries_state} tentativas!")
            logging.error(f"❌ Sub-requisito [{plan_idx + 1}] NÃO pôde ser completado.")
            logging.error("=" * 70)
            
            feedback = analyze_failures(
                test_output=output,
                specification=state["specification"],
                sub_requirement=state["current_sub_req"],
                iteration=iteration,
                max_retries=max_retries_state,
                current_code=state.get("implementation_code", ""),
                test_code=state.get("tests_code", "")
            )

            logging.info("=" * 70)
            logging.info("🔍 FEEDBACK ESTRUTURADO DO REVIEWER:")
            logging.info("=" * 70)
            for line in feedback.split('\n'):
                logging.info(line)
            logging.info("=" * 70)

            new_state = {**state, "status": "max_retries_exceeded", "feedback": feedback}
        else:
            logging.warning("=" * 70)
            logging.warning(f"❌ GREEN FALHOU! Alguns testes não passaram.")
            logging.warning(f"🔄 Tentativa {iteration}/{max_retries_state}")
            logging.warning("🔧 Voltando para o Developer com feedback...")
            logging.warning("=" * 70)
            
            feedback = analyze_failures(
                test_output=output,
                specification=state["specification"],
                sub_requirement=state["current_sub_req"],
                iteration=iteration,
                max_retries=max_retries_state,
                current_code=state.get("implementation_code", ""),
                test_code=state.get("tests_code", "")
            )

            logging.info("=" * 70)
            logging.info("🔍 FEEDBACK ESTRUTURADO DO REVIEWER SOBRE O CÓDIGO:")
            logging.info("=" * 70)
            for line in feedback.split('\n'):
                logging.info(line)
            logging.info("=" * 70)

            new_state: AgentState = {**state, "status": "green_failed", "feedback": feedback}
    
    return new_state
