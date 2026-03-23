import logging
from langchain_core.messages import AIMessage
from app.config import AgentState, Config
from app.agents.langgraph.developer import generate_code_incremental
from app.utils.sandbox_utils import apply_agent_action_to_sandbox

logger = logging.getLogger("TDDOrchestrator")

def node_execute_developer(state: AgentState) -> AgentState:
    sub_req = state["current_sub_req"]
    iteration = state.get("iteration", 1)
    max_retries = state.get("max_retries", Config.MAX_ITERATIONS)

    logger.info("\n" + "=" * 80)
    logger.info(f"💻 FASE 4: DEVELOPER (IMPLEMENTAÇÃO) | Iteração {iteration}/{max_retries}")
    logger.info("=" * 80)
    logger.info(f"🎯 Objetivo: Implementar '{sub_req}'")

    reviewer_msgs = state.get("reviewer_messages", [])
    feedback = reviewer_msgs[-1].content if reviewer_msgs and hasattr(reviewer_msgs[-1], "content") else ""

    try:
        # Developer gera sua ação de código estruturada
        action, updated_dev_history = generate_code_incremental(
            specification=state.get("specification", ""),
            file_system=state.get("file_system", {}),
            feedback=feedback,
            conversation_history=state.get("developer_messages", []),
        )
        
        logger.info(f"💭 Raciocínio do Developer: {action.thoughts}")

        # Aplica a implementação e setup na Sandbox E2B
        updated_fs, logs = apply_agent_action_to_sandbox(
            sandbox_id=state["sandbox_id"],
            action=action,
            current_file_system=state.get("file_system", {})
        )
        
    except Exception as exc:
        logger.error(f"❌ Falha crítica no Developer: {exc}")
        return {**state, "status": "developer_failed", "iteration": iteration}

    audit_entry = AIMessage(content=f"[Developer] Iteração #{iteration}: código escrito para '{sub_req}'.")
    existing_len = len(state.get("developer_messages", []))
    new_turns = updated_dev_history[existing_len:]

    return {
        **state,
        "file_system": updated_fs, # Novo código mapeado no estado
        "iteration": iteration,
        "status": "code_written",
        "developer_messages": new_turns,
        "audit_log": [audit_entry],
    }