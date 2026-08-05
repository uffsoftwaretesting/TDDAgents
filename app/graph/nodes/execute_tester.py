import time
import logging
from langchain_core.messages import AIMessage

from app.config.config import AgentState, Config
from app.agents.langgraph.tester import generate_test_for_sub_req
from app.errors.agents.handler import handle_llm_exception
from app.utils.sandbox_utils import apply_agent_action_to_sandbox
from app.errors.exceptions import TransientInfraError, FatalInfraError

logger = logging.getLogger("TDDOrchestrator")

def node_execute_tester(state: AgentState) -> AgentState:
    sub_req = state["current_sub_req"]
    is_first_sub_req = state.get("plan_index", 0) == 0
    is_review_mode = state.get("status") == "test_review_needed"
    infra_retries = state.get("infra_retries", 0)

    if is_review_mode:
        logger.info("🔧 TESTER: REVIEW MODE — fixing potentially incorrect tests")
    else:
        if is_first_sub_req and state.get("iteration", 1) == 1:
            logger.info("\n" + "=" * 80)
            logger.info(f"⏭️  NEXT TASK: '{sub_req}'")
            logger.info("=" * 80)
        logger.info("✍️ STEP 1: TESTER - Writing tests for the current sub-requirement")

    reviewer_msgs = state.get("reviewer_messages", [])
    feedback = reviewer_msgs[-1].content if is_review_mode and reviewer_msgs and hasattr(reviewer_msgs[-1], "content") else ""

    existing_len = len(state.get("tester_messages", []))
    iteration = state.get("iteration", 1)

    try:
        # 1. Try to generate the tests via LLM
        action, updated_history = generate_test_for_sub_req(
            sub_requirement=sub_req,
            specification=state.get("specification", ""),
            file_system=state.get("file_system", {}),
            feedback=feedback,
            conversation_history=state.get("tester_messages", []),
            is_review_mode=is_review_mode,
        )

        logger.info(f"💭 Tester's reasoning: {action.thoughts}")

        # 2. Try to apply the generated tests to the Sandbox
        updated_fs, logs = apply_agent_action_to_sandbox(
            sandbox_id=state["sandbox_id"],
            action=action,
            current_file_system=state.get("file_system", {})
        )

    except TransientInfraError as exc:
        infra_retries += 1
        if infra_retries >= Config.MAX_INFRA_RETRIES:
            logger.error(f"❌ TESTER: Infra Failure (Limit Reached): {exc.original_exc}")
            return {**state, "status": "tester_failed", "infra_retries": 0}

        logger.warning(f"⚠️ TESTER: Transient Error. Attempt {infra_retries}/{Config.MAX_INFRA_RETRIES}. Retrying in 3s... ({exc.original_exc})")
        time.sleep(3)
        return {**state, "status": "infra_error_tester", "infra_retries": infra_retries}

    except FatalInfraError as exc:
        logger.error(f"❌ TESTER: Fatal Infrastructure Failure: {exc.original_exc}")
        return {**state, "status": "tester_failed", "infra_retries": 0}
    except Exception as exc:
        logger.error(f"❌ TESTER: Fatal Infrastructure Failure")
        return {**state, "status": "tester_failed", "infra_retries": 0}

    # === Success Flow ===
    new_turns = updated_history[existing_len:]
    audit_entry = AIMessage(content=f"[Tester] Tests {'revised' if is_review_mode else 'written'} for '{sub_req}'.")

    return {
        **state,
        "file_system": updated_fs,
        "iteration": iteration,
        "infra_retries": 0,
        "status": "tests_written",
        "tester_messages": new_turns,
        "audit_log": [audit_entry],
    }