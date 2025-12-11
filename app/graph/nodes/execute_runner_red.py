import logging
from app.config import AgentState
from app.agents.langgraph.runner import run_pytest
from app.agents.langgraph.reviewer import analyze_failures
from app.utils.prompt_loader import load_prompt


def node_execute_runner_red(state: AgentState, max_retries: int = 10) -> AgentState:
    """Nó do grafo que verifica se o novo teste falha (RED)."""
    sub_req = state["current_sub_req"]
    plan_idx = state.get("plan_index", 0)
    iteration = state.get("iteration", 0)
    max_retries_state = state.get("max_retries", max_retries)
    red_attempts = state.get("red_attempts", 0)
    
    logging.info("=" * 70)
    logging.info(f"🔴 FASE 3: RUNNER RED - Verificando falha do NOVO teste")
    logging.info(f"🎯 Sub-requisito [{plan_idx + 1}]: '{sub_req}'")
    logging.info(f"🔄 Tentativa RED: {red_attempts + 1}/3")
    logging.info("=" * 70)
    
    output = run_pytest()
    logging.info(f"📊 Resultado pytest:\n{output}")
    
    has_failures = "failed" in output.lower() or "error" in output.lower()
    
    if has_failures:
        logging.info("✅ 🔴 RED confirmado! O novo teste falha como esperado.")
        feedback = analyze_failures(
            test_output=output,
            specification=state["specification"],
            sub_requirement=state["current_sub_req"],
            iteration=iteration,
            max_retries=max_retries_state,
            current_code=state.get("implementation_code", ""),
            test_code=state.get("tests_code", "")
        )
        new_state = {
            **state, 
            "status": "red_confirmed", 
            "feedback": feedback,
            "red_attempts": 0  # Reset contador RED
        }
    else:
        logging.warning("⚠️ ATENÇÃO: Nenhum teste falhou!")
        
        new_red_attempts = red_attempts + 1
        
        # PROTEÇÃO: Após 3 tentativas no RED sem falhas
        if new_red_attempts >= 3:
            logging.warning("=" * 70)
            logging.warning(f"⚠️ 3 TENTATIVAS NO RED SEM FALHAS DETECTADAS")
            logging.warning("⚠️ Comportamento já implementado ou teste inadequado")
            logging.warning("⚠️ Forçando progressão para DEVELOPER com feedback especial")
            logging.warning("=" * 70)
            
            feedback = load_prompt(
                template_name='agents/langgraph/orchestrator/feedback_red_not_confirmed.jinja2',
                sub_requirement=sub_req
            )
            
            new_state = {
                **state,
                "status": "red_confirmed",  # Força progressão
                "feedback": feedback,
                "red_attempts": 0  # Reset contador
            }
            
        # Primeira ou segunda tentativa no primeiro sub-requisito
        elif plan_idx == 0 and new_red_attempts < 3:
            feedback = load_prompt(
                template_name='agents/langgraph/orchestrator/feedback_invalid_test_first_attempts.jinja2',
                attempt=new_red_attempts,
                sub_requirement=sub_req
            )
            
            new_state = {
                **state,
                "status": "invalid_test",
                "feedback": feedback,
                "red_attempts": new_red_attempts
            }
            
        # Sub-requisitos posteriores ou já tentou corrigir
        else:
            logging.info("⏭️ Implementação existente cobre este caso. Prosseguindo...")
            feedback = load_prompt(
                template_name='agents/langgraph/orchestrator/feedback_existing_implementation.jinja2',
                attempt=new_red_attempts
            )
            
            new_state: AgentState = {
                **state,
                "status": "red_confirmed",
                "feedback": feedback,
                "red_attempts": 0  # Reset contador
            }
    
    return new_state
