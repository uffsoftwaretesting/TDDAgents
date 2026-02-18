import os
import logging
from langchain_core.messages import HumanMessage, AIMessage
from app.config import AgentState, Config
from app.agents.langgraph.developer import generate_code_incremental

logger = logging.getLogger("TDDOrchestrator")

def node_execute_developer(state: AgentState) -> AgentState:
    sub_req = state["current_sub_req"]
    specification = state.get("specification", "")
    iteration = state.get("iteration", 0) + 1
    max_retries = state.get("max_retries", 10)
    
    logger.info("\n" + "=" * 80)
    logger.info(f"💻 FASE 4: DEVELOPER (IMPLEMENTAÇÃO) | Iteração {iteration}/{max_retries}")
    logger.info("=" * 80)
    logger.info(f"🎯 Objetivo: Implementar '{sub_req}'")
    
    messages = state.get("messages", [])
    
    context_feedback = ""
    if iteration < 3:
        logger.info("   🧠 Estratégia de Contexto: CURTO (Apenas último erro)")
        if messages:
            last_msg = messages[-1]
            context_feedback = f"LAST ERROR:\n{last_msg.content}"
    else:
        logger.info("   🧠 Estratégia de Contexto: COMPLETO (Histórico de falhas)")
        history_str = "\n".join([f"[{m.type.upper()}]: {m.content}" for m in messages])
        context_feedback = f"--- HISTÓRICO DE FALHAS ACUMULADAS ---\n{history_str}\n\nINSTRUCTION: Analyze the history above. You have failed {iteration-1} times. Try a radically different approach."

    new_code = generate_code_incremental(
        test_code=state["tests_code"],
        function_name=state.get("function_name", "process"),
        specification=specification,
        feedback=context_feedback,
        previous_code=state.get("implementation_code", "")
    )
    
    impl_path = os.path.join(Config.WORKSPACE_PATH, f"{Config.IMPLEMENTATION_MODULE}.py")
    with open(impl_path, "w", encoding="utf-8") as f:
        f.write(new_code)
    
    logger.info(f"💾 Código implementado salvo ({len(new_code.splitlines())} linhas).")
    
    action_log = AIMessage(content=f"Tentativa de correção #{iteration}: Código refatorado para '{sub_req}'.")
    
    return {
        **state,
        "implementation_code": new_code,
        "iteration": iteration,
        "status": "code_written",
        "messages": [action_log]
    }