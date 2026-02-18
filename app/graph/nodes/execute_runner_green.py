import logging
from langchain_core.messages import HumanMessage
from app.config import AgentState
from app.agents.langgraph.runner import run_pytest
from app.agents.langgraph.reviewer import analyze_failures

logger = logging.getLogger("TDDOrchestrator")

def node_execute_runner_green(state: AgentState, max_retries: int = 10) -> AgentState:
    """Nó do grafo que verifica se todos os testes passam (GREEN)."""
    sub_req = state["current_sub_req"]
    iteration = state.get("iteration", 0)
    max_retries_state = state.get("max_retries", max_retries)
    
    logger.info("\n" + "-" * 80)
    logger.info(f"🟢 FASE 5: RUNNER GREEN (Validação) | Tentativa {iteration}/{max_retries_state}")
    logger.info("-" * 80)
    
    output = run_pytest()
    all_passed = "passed" in output.lower() and "failed" not in output.lower() and "error" not in output.lower()
    
    if all_passed:
        logger.info("✅ SUCESSO: Todos os testes passaram! 🚀")
        return {**state, "status": "green_passed", "iteration": 0}
    
    # --- TRATAMENTO DE FALHAS ---
    logger.warning(f"❌ FALHA: Testes não passaram.")
    
    # Executa a Análise (O Agente "Reviewer" entra em cena aqui)
    analysis = analyze_failures(
        test_output=output,
        specification=state["specification"],
        sub_requirement=sub_req,
        iteration=iteration,
        max_retries=max_retries_state,
        current_code=state.get("implementation_code", ""),
        test_code=state.get("tests_code", "")
    )
    
    # --- LOG DA ANÁLISE (NOVO) ---
    logger.info("\n" + "=" * 80)
    logger.info(f"🧐 ANÁLISE DO AGENTE ANALISTA (REVIEWER) - Iteração {iteration}")
    logger.info("=" * 80)
    for line in analysis.split('\n'):
        logger.info(f"   {line}")
    logger.info("=" * 80 + "\n")
    # -----------------------------
    
    failure_msg = HumanMessage(content=f"FAILURE ANALYSIS (Iter {iteration}):\n{analysis}\n\nRAW OUTPUT:\n{output[-500:]}")
    messages_update = [failure_msg]
    status = ""

    # Lógica de Roteamento
    if iteration == 6 or iteration == 9:
        logger.warning(f"   ⚠️  Iteração Crítica ({iteration}): Solicitando REVISÃO DO TESTE ao Tester.")
        status = "test_review_needed"
        special_instruction = HumanMessage(content="SYSTEM ALERT: Failure persists. Please review the TEST CASES themselves. They may be incorrect.")
        messages_update.append(special_instruction)
        
    elif iteration >= max_retries_state:
        logger.error(f"   ⛔  Limite de tentativas excedido ({max_retries_state}). Abortando tarefa.")
        status = "max_retries_exceeded"
        
    else:
        logger.info(f"   🔄  Retornando ao Developer para correção baseada na análise acima.")
        status = "green_failed"

    return {
        **state,
        "status": status,
        "messages": messages_update 
    }