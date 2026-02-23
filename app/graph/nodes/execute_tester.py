import logging
from langchain_core.messages import AIMessage
from app.config import AgentState
from app.agents.langgraph.tester import generate_test_for_sub_req
from app.utils.sandbox_utils import apply_agent_action_to_sandbox

logger = logging.getLogger("TDDOrchestrator")

def node_execute_tester(state: AgentState) -> AgentState:
    sub_req = state["current_sub_req"]
    is_review_mode = state.get("status") == "test_review_needed"

    if is_review_mode:
        logger.info("🔧 TESTER: MODO REVISÃO — corrigindo testes potencialmente incorretos")
    else:
        logger.info("✍️  TESTER: Escrevendo testes para o sub-requisito atual")

    reviewer_msgs = state.get("reviewer_messages", [])
    feedback = reviewer_msgs[-1].content if is_review_mode and reviewer_msgs and hasattr(reviewer_msgs[-1], "content") else ""

    existing_len = len(state.get("tester_messages", []))
    iteration = state.get("iteration", 0)

    try:
        # O LLM agora retorna a estrutura AgentAction validada
        action, updated_history = generate_test_for_sub_req(
            sub_requirement=sub_req,
            specification=state.get("specification", ""),
            file_system=state.get("file_system", {}),
            feedback=feedback,
            conversation_history=state.get("tester_messages", []),
            is_review_mode=is_review_mode,
        )
        
        logger.info(f"💭 Raciocínio do Tester: {action.thoughts}")
        
        # ⚠️ A mágica acontece aqui: aplicamos a ação na E2B Sandbox real
        updated_fs, logs = apply_agent_action_to_sandbox(
            sandbox_id=state["sandbox_id"],
            action=action,
            current_file_system=state.get("file_system", {})
        )

    except Exception as exc:
        logger.error(f"❌ TESTER: falhou em produzir ou aplicar testes.\n{exc}")
        return {**state, "iteration": iteration + 1, "status": "tester_failed"}

    if is_review_mode:
        iteration += 1

    new_turns = updated_history[existing_len:]
    audit_entry = AIMessage(content=f"[Tester] Testes {'revisados' if is_review_mode else 'escritos'} para '{sub_req}'.")

    return {
        **state,
        "file_system": updated_fs,  # Estado rastreado atualizado!
        "iteration": iteration,
        "status": "tests_written",
        "tester_messages": new_turns,
        "audit_log": [audit_entry],
    }