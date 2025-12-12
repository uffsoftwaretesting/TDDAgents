import logging
from app.config import AgentState
from app.agents.langgraph.runner import run_pytest
from app.agents.langgraph.reviewer import analyze_failures
from app.utils.prompt_loader import load_prompt


def node_execute_runner_green(state: AgentState, max_retries: int = 10) -> AgentState:
    """Nó do grafo que verifica se todos os testes passam (GREEN)."""
    sub_req = state["current_sub_req"]
    iteration = state.get("iteration", 0)  # Developer's attempt number
    max_retries_state = state.get("max_retries", max_retries)
    plan_idx = state.get("plan_index", 0)
    
    logging.info("=" * 70)
    logging.info(f"🟢 FASE 5: RUNNER GREEN - TODOS os testes devem passar!")
    logging.info(f"🔄 Tentativa do Developer: {iteration}/{max_retries_state}")
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
        # Reset iteration for next sub-requirement
        new_state = {**state, "status": "green_passed", "feedback": "", "iteration": 0}
    else:
        # ==============================================================================
        # ESTRATÉGIA DE RETRIES INTERCALADA (DEV/TESTER)
        # Baseada no número da tentativa do Developer (iteration)
        # 1-5: Developer corrige código
        # 6:   Tester revisa testes (sem incrementar iteration)
        # 7-8: Developer corrige código
        # 9:   Tester revisa testes (sem incrementar iteration)
        # 10:  Developer última tentativa
        # >10: FALHA
        # ==============================================================================
        
        # Rota para o TESTER (Após tentativas 6 e 9 do Developer)
        if iteration == 6 or iteration == 9:
            logging.warning("=" * 70)
            logging.warning(f"⚠️ REVISÃO DE TESTES NECESSÁRIA (Após {iteration} tentativas do Developer)")
            logging.warning("🔧 Voltando para o Tester revisar os testes...")
            logging.warning("⚠️ Iteration permanece em {iteration} (Tester não incrementa)")
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

            # Keep iteration unchanged - Tester doesn't count as a Developer attempt
            new_state = {**state, "status": "test_review_needed", "feedback": test_review_feedback}

        # Rota de FALHA
        elif iteration > max_retries_state:
            logging.error("=" * 70)
            logging.error(f"❌ FALHA CRÍTICA: Developer excedeu {max_retries_state} tentativas!")
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

            # Keep iteration for final state reporting
            new_state = {**state, "status": "max_retries_exceeded", "feedback": feedback}

        # Rota Padrão: DEVELOPER (1-5, 7-8, 10)
        else:
            logging.warning("=" * 70)
            logging.warning(f"❌ GREEN FALHOU! (Developer tentativa {iteration}/{max_retries_state})")
            logging.warning("🔧 Voltando para o Developer com feedback...")
            logging.warning("➡️  Developer incrementará iteration para {iteration + 1}")
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

            # Keep iteration unchanged - Developer will increment on next entry
            new_state: AgentState = {**state, "status": "green_failed", "feedback": feedback}
    
    return new_state