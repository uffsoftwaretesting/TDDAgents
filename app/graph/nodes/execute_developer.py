import time
import logging
from langchain_core.messages import AIMessage

from app.config.config import AgentState, Config
from app.agents.langgraph.developer import generate_code_incremental
from app.utils.sandbox_utils import apply_agent_action_to_sandbox
from app.errors.exceptions import TransientInfraError, FatalInfraError

logger = logging.getLogger("TDDOrchestrator")

def node_execute_developer(state: AgentState) -> AgentState:
    sub_req = state["current_sub_req"]
    iteration = state.get("iteration", 1)
    max_retries = state.get("max_retries", Config.MAX_ITERATIONS)
    infra_retries = state.get("infra_retries", 0)

    logger.info("\n" + "=" * 80)
    logger.info(f"💻 STEP 3: DEVELOPER (IMPLEMENTATION) | Iteration {iteration}/{max_retries}")
    logger.info("=" * 80)
    logger.info(f"🎯 Goal: Implement '{sub_req}'")

    reviewer_msgs = state.get("reviewer_messages", [])
    feedback = reviewer_msgs[-1].content if reviewer_msgs and hasattr(reviewer_msgs[-1], "content") else ""

    try:
        # 1. Try to generate the code via LLM
        action, updated_dev_history = generate_code_incremental(
            sub_req=sub_req,
            specification=state.get("specification", ""),
            file_system=state.get("file_system", {}),
            feedback=feedback,
            conversation_history=state.get("developer_messages", []),
        )

        logger.info(f"💭 Developer's reasoning: {action.thoughts}")

        # 2. Try to apply the generated code to the Sandbox (already raises domain exceptions)
        updated_fs, logs = apply_agent_action_to_sandbox(
            sandbox_id=state["sandbox_id"],
            action=action,
            current_file_system=state.get("file_system", {})
        )

    except TransientInfraError as exc:
        infra_retries += 1
        if infra_retries >= Config.MAX_INFRA_RETRIES:
            logger.error(f"❌ DEVELOPER: Infra Failure (Limit Reached): {exc.original_exc}")
            return {**state, "status": "developer_failed", "infra_retries": 0}

        logger.warning(f"⚠️ DEVELOPER: Transient Error. Attempt {infra_retries}/{Config.MAX_INFRA_RETRIES}. Waiting 3s... ({exc.original_exc})")
        time.sleep(3)
        return {**state, "status": "infra_error_developer", "infra_retries": infra_retries}

    except FatalInfraError as exc:
        logger.error(f"❌ DEVELOPER: Fatal Infrastructure Failure: {exc.original_exc}")
        return {**state, "status": "developer_failed", "infra_retries": 0}

    except Exception as exc:
        logger.error(f"❌ DEVELOPER: Fatal Infrastructure Failure")
        return {**state, "status": "developer_failed", "infra_retries": 0}

    # === Success Flow ===
    audit_entry = AIMessage(content=f"[Developer] Iteration #{iteration}: code written for '{sub_req}'.")
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