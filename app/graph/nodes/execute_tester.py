import time
import logging
from langchain_core.messages import AIMessage

from app.config import AgentState, Config
from app.agents.langgraph.tester import generate_test_for_sub_req
from app.errors.agents.handler import handle_llm_exception
from app.utils.sandbox_utils import apply_agent_action_to_sandbox
from app.errors.exceptions import TransientInfraError, FatalInfraError

logger = logging.getLogger("TDDOrchestrator")

def node_execute_tester(state: AgentState) -> AgentState:
    sub_req = state["current_sub_req"]
    is_review_mode = state.get("status") == "test_review_needed"
    infra_retries = state.get("infra_retries", 0)

    if is_review_mode:
        logger.info("🔧 TESTER: MODO REVISÃO — corrigindo testes potencialmente incorretos")
    else:
        logger.info("✍️  TESTER: Escrevendo testes para o sub-requisito atual")

    reviewer_msgs = state.get("reviewer_messages", [])
    feedback = reviewer_msgs[-1].content if is_review_mode and reviewer_msgs and hasattr(reviewer_msgs[-1], "content") else ""

    existing_len = len(state.get("tester_messages", []))
    iteration = state.get("iteration", 1) 

    try:
        # 1. Tenta gerar os testes via LLM
        try:
            action, updated_history = generate_test_for_sub_req(
                sub_requirement=sub_req,
                specification=state.get("specification", ""),
                file_system=state.get("file_system", {}),
                feedback=feedback,
                conversation_history=state.get("tester_messages", []),
                is_review_mode=is_review_mode,
            )
        except Exception as exc:
            # Converte exceções da LangChain/OpenAI para o nosso domínio
            handle_llm_exception(exc, context="Tester LLM API")
            
        logger.info(f"💭 Raciocínio do Tester: {action.thoughts}")
        
        # 2. Tenta aplicar os testes gerados na Sandbox
        updated_fs, logs = apply_agent_action_to_sandbox(
            sandbox_id=state["sandbox_id"],
            action=action,
            current_file_system=state.get("file_system", {})
        )

    except TransientInfraError as exc:
        infra_retries += 1
        if infra_retries >= Config.MAX_INFRA_RETRIES:
            logger.error(f"❌ TESTER: Falha de Infra (Limite Atingido): {exc.original_exc}")
            return {**state, "status": "tester_failed", "infra_retries": 0}
            
        logger.warning(f"⚠️ TESTER: Erro Transiente. Tentativa {infra_retries}/{Config.MAX_INFRA_RETRIES}. Retentando em 3s... ({exc})")
        time.sleep(3)
        return {**state, "status": "infra_error_tester", "infra_retries": infra_retries}

    except FatalInfraError as exc:
        logger.error(f"❌ TESTER: Falha Fatal de Infraestrutura: {exc.original_exc}")
        return {**state, "status": "tester_failed", "infra_retries": 0}

    # === Fluxo de Sucesso ===
    new_turns = updated_history[existing_len:]
    audit_entry = AIMessage(content=f"[Tester] Testes {'revisados' if is_review_mode else 'escritos'} para '{sub_req}'.")

    return {
        **state,
        "file_system": updated_fs,
        "iteration": iteration,
        "infra_retries": 0,
        "status": "tests_written",
        "tester_messages": new_turns,
        "audit_log": [audit_entry],
    }