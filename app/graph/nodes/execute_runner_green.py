import logging
import time
from langchain_core.messages import HumanMessage, AIMessage

from app.config.config import AgentState, Config
from app.agents.langgraph.runner import run_pytest_in_sandbox
from app.agents.langgraph.reviewer import analyze_failures
from app.errors.exceptions import FatalInfraError, TransientInfraError
from app.utils.sandbox_utils import read_all_files_from_state

logger = logging.getLogger("TDDOrchestrator")


def node_execute_runner_green(state: AgentState) -> AgentState:
    sub_req = state["current_sub_req"]
    iteration = state.get("iteration", 1)
    max_retries_state = state.get("max_retries", Config.MAX_ITERATIONS)
    infra_retries = state.get("infra_retries", 0)
    is_type_fault = state.get("is_type_fault", "")

    logger.info("\n" + "-" * 80)
    logger.info(f"🟢 STEP 4: RUNNER GREEN (Validation) | Iteration {iteration}/{max_retries_state}")
    logger.info("-" * 80)

    try:
        # 1. Runs the tests in the active E2B Sandbox, unpacking the tuple
        output, is_success = run_pytest_in_sandbox(sandbox_id=state["sandbox_id"], is_red_phase=False)

    except TransientInfraError as exc:
        infra_retries += 1
        if infra_retries >= Config.MAX_INFRA_RETRIES:
            logger.error(f"❌ RUNNER GREEN: Infra Failure (Limit Reached): {exc.original_exc}")
            return {**state, "status": "sandbox_failed", "infra_retries": 0}

        logger.warning(f"⚠️ RUNNER GREEN: Transient Error. Attempt {infra_retries}/{Config.MAX_INFRA_RETRIES}. Waiting 3s... ({exc.original_exc})")
        time.sleep(3)
        return {**state, "status": "infra_error_green", "infra_retries": infra_retries}

    except FatalInfraError as exc:
        logger.error(f"❌ RUNNER GREEN: Fatal Infrastructure Failure: {exc.original_exc}")
        return {**state, "status": "sandbox_failed", "infra_retries": 0}

    except Exception as exc:
        logger.error(f"❌ RUNNER GREEN: Unexpected error")
        return {**state, "status": "sandbox_failed", "infra_retries": 0}

    all_passed = is_success

    if all_passed:
        logger.info("✅ SUCCESS: All tests passed! 🚀")
        audit = AIMessage(content=f"[RunnerGreen] All tests passed for '{sub_req}' on iteration {iteration}.")
        return {
            **state,
            "status": "green_passed",
            "iteration": iteration,
            "audit_log": [audit],
            "infra_retries": 0,
        }

    # ── Test failure ──────────────────────────────────────────────────────
    logger.warning("❌ FAILURE: Tests did not pass.")

    existing_reviewer_len = len(state.get("reviewer_messages", []))

    # Injects the entire current codebase for the Reviewer
    current_codebase = read_all_files_from_state(state.get("file_system", {}))

    try:
        analysis, updated_reviewer_history = analyze_failures(
            test_output=output,
            specification=state["specification"],
            sub_requirement=sub_req,
            iteration=iteration,
            max_retries=max_retries_state,
            current_code=current_codebase,
            conversation_history=state.get("reviewer_messages", []),
        )
    except TransientInfraError as exc:
        infra_retries += 1
        if infra_retries >= Config.MAX_INFRA_RETRIES:
            logger.error(f"❌ RUNNER GREEN: Infra Failure in Reviewer (Limit Reached): {exc.original_exc}")
            return {**state, "status": "sandbox_failed", "infra_retries": 0}

        logger.warning(
            f"⚠️ RUNNER GREEN: Transient error in Reviewer. "
            f"Attempt {infra_retries}/{Config.MAX_INFRA_RETRIES}. Waiting 3s... ({exc.original_exc})"
        )
        time.sleep(3)
        return {**state, "status": "infra_error_green", "infra_retries": infra_retries}

    except FatalInfraError as exc:
        logger.error(f"❌ RUNNER GREEN: Fatal failure in Reviewer: {exc.original_exc}")
        return {**state, "status": "sandbox_failed", "infra_retries": 0}

    except Exception as exc:
        logger.error("❌ RUNNER GREEN: Unexpected error during Reviewer analysis")
        return {**state, "status": "sandbox_failed", "infra_retries": 0}

    logger.info("\n" + "=" * 80)
    logger.info(f"🧐 REVIEWER ANALYSIS — Iteration {iteration}")
    logger.info("=" * 80)
    for line in analysis.split('\n'):
        logger.info(f"   {line}")
    logger.info("=" * 80 + "\n")

    new_reviewer_turns = updated_reviewer_history[existing_reviewer_len:]
    extra_audit: list = []

    if "[TEST ERROR]" in analysis:
        if iteration >= max_retries_state:
            logger.error(f"   ⛔  Retry limit exceeded ({max_retries_state}). Aborting.")
            status = "max_retries_exceeded"
            is_type_fault = "test_faults"
            next_iteration = iteration
        else:
            logger.warning(f"   ⚠️  Reviewer identified a test failure. Requesting REVIEW from Tester.")
            status = "test_review_needed"
            extra_audit.append(
                HumanMessage(content=f"[RunnerGreen] Iteration {iteration}: Reviewer flagged an error in the test code. Escalating to Tester.")
            )
            next_iteration = iteration + 1  # Increments because it goes back to the Tester
    else: # Implementation failure
        if iteration >= max_retries_state:
            logger.error(f"   ⛔  Retry limit exceeded ({max_retries_state}). Aborting.")
            status = "max_retries_exceeded"
            is_type_fault = "implementation_faults"
            next_iteration = iteration
        else:
            logger.info("   🔄  Returning to Developer to fix the implementation.")
            status = "green_failed"
            next_iteration = iteration + 1  # Increments because it goes back to the Developer

    audit_entry = AIMessage(
        content=f"[RunnerGreen] Iteration {iteration}: {status}. Analysis stored."
    )

    return {
        **state,
        "status": status,
        "iteration": next_iteration,
        "infra_retries": 0,
        "reviewer_messages": new_reviewer_turns,
        "audit_log": [audit_entry] + extra_audit,
        "is_type_fault": is_type_fault,
    }