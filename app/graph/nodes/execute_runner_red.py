import logging
from langchain_core.messages import HumanMessage
from app.config import AgentState
from app.agents.langgraph.runner import run_pytest
from app.agents.langgraph.reviewer import analyze_failures
from app.utils.prompt_loader import load_prompt

logger = logging.getLogger("TDDOrchestrator")

def node_execute_runner_red(state: AgentState, max_retries: int = 10) -> AgentState:
    sub_req = state["current_sub_req"]
    iteration = state.get("iteration", 0)
    red_attempts = state.get("red_attempts", 0)
    
    logger.info("\n" + "-" * 80)
    logger.info(f"🔴 FASE 3: RUNNER RED (Verificação de Falha) | Tentativa {red_attempts + 1}")
    logger.info("-" * 80)
    
    output = run_pytest()
    
    has_failures = "failed" in output.lower() or "error" in output.lower()
    
    new_messages = []
    
    if has_failures:
        logger.info("✅ SUCESSO (RED CONFIRMADO): O teste falhou como esperado.")
        logger.info("   ➡️  Executando análise de confirmação...")
        
        feedback = analyze_failures(
            test_output=output,
            specification=state["specification"],
            sub_requirement=state["current_sub_req"],
            iteration=iteration,
            max_retries=state.get("max_retries", max_retries),
            current_code=state.get("implementation_code", ""),
            test_code=state.get("tests_code", "")
        )
        
        logger.info("\n" + "=" * 80)
        logger.info("🧐 ANÁLISE DO AGENTE ANALISTA (CONFIRMAÇÃO RED)")
        logger.info("=" * 80)
        for line in feedback.split('\n'):
            logger.info(f"   {line}")
        logger.info("=" * 80 + "\n")

        new_state = {
            **state, 
            "status": "red_confirmed", 
            "red_attempts": 0
        }
    else:
        logger.warning("⚠️  ALERTA: O teste PASSOU imediatamente (Green no Red).")
        logger.info("   ℹ️  Avançando !")
        
        feedback_text = load_prompt('agents/langgraph/orchestrator/feedback_existing_implementation.jinja2', attempt=1)

        new_messages.append(HumanMessage(content=f"RED PHASE RESULT: The test passed immediately. {feedback_text}"))
        
        new_state = {
            **state,
            "status": "red_confirmed",
            "messages": new_messages,
            "red_attempts": 0
        }
    
    return new_state