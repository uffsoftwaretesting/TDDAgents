import time
import logging
from langchain_core.messages import AIMessage

from app.config.config import AgentState, Config
from app.agents.langgraph.developer import generate_code_incremental
from app.errors.agents.handler import handle_llm_exception
from app.utils.sandbox_utils import apply_agent_action_to_sandbox
from app.errors.exceptions import TransientInfraError, FatalInfraError

logger = logging.getLogger("TDDOrchestrator")

def node_execute_developer(state: AgentState) -> AgentState:
    sub_req = state["current_sub_req"]
    iteration = state.get("iteration", 1)
    max_retries = state.get("max_retries", Config.MAX_ITERATIONS)
    infra_retries = state.get("infra_retries", 0)

    logger.info("\n" + "=" * 80)
    logger.info(f"💻 FASE 4: DEVELOPER (IMPLEMENTAÇÃO) | Iteração {iteration}/{max_retries}")
    logger.info("=" * 80)
    logger.info(f"🎯 Objetivo: Implementar '{sub_req}'")

    reviewer_msgs = state.get("reviewer_messages", [])
    feedback = reviewer_msgs[-1].content if reviewer_msgs and hasattr(reviewer_msgs[-1], "content") else ""

    try:
        # 1. Tenta gerar o código via LLM
        try:
            action, updated_dev_history = generate_code_incremental(
                specification=state.get("specification", ""),
                file_system=state.get("file_system", {}),
                feedback=feedback,
                conversation_history=state.get("developer_messages", []),
            )
        except Exception as exc:
            # Converte exceções da LangChain/OpenAI para o nosso domínio
            handle_llm_exception(exc, context="Developer LLM API")

        logger.info(f"💭 Raciocínio do Developer: {action.thoughts}")

        # 2. Tenta aplicar o código gerado na Sandbox (Já lança as exceções de domínio)
        updated_fs, logs = apply_agent_action_to_sandbox(
            sandbox_id=state["sandbox_id"],
            action=action,
            current_file_system=state.get("file_system", {})
        )

    except TransientInfraError as exc:
        infra_retries += 1
        if infra_retries >= Config.MAX_INFRA_RETRIES:
            logger.error(f"❌ DEVELOPER: Falha de Infra (Limite Atingido): {exc.original_exc}")
            return {**state, "status": "developer_failed", "infra_retries": 0}
        
        logger.warning(f"⚠️ DEVELOPER: Erro Transiente. Tentativa {infra_retries}/{Config.MAX_INFRA_RETRIES}. Aguardando 3s... ({exc})")
        time.sleep(3)
        return {**state, "status": "infra_error_developer", "infra_retries": infra_retries}

    except FatalInfraError as exc:
        logger.error(f"❌ DEVELOPER: Falha Fatal de Infraestrutura: {exc.original_exc}")
        return {**state, "status": "developer_failed", "infra_retries": 0}

    # === Fluxo de Sucesso ===
    audit_entry = AIMessage(content=f"[Developer] Iteração #{iteration}: código escrito para '{sub_req}'.")
    existing_len = len(state.get("developer_messages", []))
    new_turns = updated_dev_history[existing_len:]

    return {
        **state,
        "file_system": updated_fs,
        "iteration": iteration,
        "infra_retries": 0,
        "status": "code_written",
        "developer_messages": new_turns,
        "audit_log": [audit_entry],
    }