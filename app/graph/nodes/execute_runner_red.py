import logging
import time
from langchain_core.messages import HumanMessage, AIMessage

from app.config.config import AgentState, Config
from app.agents.langgraph.runner import run_pytest_in_sandbox
from app.agents.langgraph.reviewer import analyze_failures
from app.errors.exceptions import FatalInfraError, TransientInfraError
from app.utils.prompt_loader import load_prompt
from app.utils.sandbox_utils import read_all_files_from_state

logger = logging.getLogger("TDDOrchestrator")


def node_execute_runner_red(state: AgentState) -> AgentState:
    sub_req = state["current_sub_req"]
    plan_index = state.get("plan_index", 0)
    iteration = state.get("iteration", 1)
    max_retries_state = state.get("max_retries", Config.MAX_ITERATIONS)
    infra_retries = state.get("infra_retries", 0)

    logger.info("\n" + "-" * 80)
    logger.info(f"🔴 STEP 2: RUNNER RED (Failure Verification) | Iteration {iteration}/{max_retries_state}")
    logger.info("-" * 80)

    # 1. Runs the tests in the active E2B Sandbox

    try:
        output, is_success = run_pytest_in_sandbox(sandbox_id=state["sandbox_id"], is_red_phase=True)

    except TransientInfraError as exc:
        infra_retries += 1
        if infra_retries >= Config.MAX_INFRA_RETRIES:
            logger.error(f"❌ Infra Failure (Retry Limit Reached): {exc.original_exc}")
            return {**state, "status": "sandbox_failed", "infra_retries": 0}

        logger.warning(f"⚠️ Transient Infra Error. Attempt {infra_retries}. Waiting 3s... ({exc.original_exc})")
        time.sleep(3)
        return {**state, "status": "infra_error_red", "infra_retries": infra_retries}

    except FatalInfraError as exc:
        logger.error(f"❌ Fatal Infrastructure Failure: {exc.original_exc}")
        return {**state, "status": "sandbox_failed", "infra_retries": 0}

    except Exception as exc:
        logger.error(f"❌ RUNNER RED: Unexpected error")
        return {**state, "status": "sandbox_failed", "infra_retries": 0}

    has_failures = not is_success

    existing_reviewer_len = len(state.get("reviewer_messages", []))
    audit_entries: list = []

    flow_types = list(state.get("is_flow_type", []))

    while len(flow_types) <= plan_index:
        flow_types.append("")

    if has_failures:
        logger.info("✅ SUCCESS (RED CONFIRMED): The test failed as expected.")
        logger.info("   ➡️  Running confirmation analysis...")

        flow_types[plan_index] = "F1"

        # Pulls the current code from file_system for the Reviewer's context
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
                logger.error(f"❌ RUNNER RED: Infra Failure in Reviewer (Limit Reached): {exc.original_exc}")
                return {**state, "status": "sandbox_failed", "infra_retries": 0}

            logger.warning(
                f"⚠️ RUNNER RED: Transient error in Reviewer. "
                f"Attempt {infra_retries}/{Config.MAX_INFRA_RETRIES}. Waiting 3s... ({exc.original_exc})"
            )
            time.sleep(3)
            return {**state, "status": "infra_error_red", "infra_retries": infra_retries}

        except FatalInfraError as exc:
            logger.error(f"❌ RUNNER RED: Fatal failure in Reviewer: {exc.original_exc}")
            return {**state, "status": "sandbox_failed", "infra_retries": 0}

        except Exception as exc:
            logger.error("❌ RUNNER RED: Unexpected error during Reviewer analysis")
            return {**state, "status": "sandbox_failed", "infra_retries": 0}

        logger.info("\n" + "=" * 80)
        logger.info("🧐 REVIEWER ANALYSIS (RED CONFIRMATION)")
        logger.info("=" * 80)
        for line in analysis.split('\n'):
            logger.info(f"   {line}")
        logger.info("=" * 80 + "\n")

        new_reviewer_turns = updated_reviewer_history[existing_reviewer_len:]

        if "[TEST ERROR]" in analysis:
            if iteration >= max_retries_state:
                logger.error(f"   ⛔ Retry limit exceeded on Tester ({max_retries_state}). Aborting.")
                status = "max_retries_exceeded"
                next_iteration = iteration
            else:
                status = "test_review_needed"
                logger.warning("   ⚠️ The test failed invalidly. Returning to Tester.")
                next_iteration = iteration + 1
        else:
            status = "red_confirmed"
            next_iteration = iteration

        audit_entries.append(
            AIMessage(content=f"[RunnerRed] Red confirmed for '{sub_req}'. Status: {status}.")
        )

        return {
            **state,
            "status": status,
            "iteration": next_iteration,
            "infra_retries": 0,
            "reviewer_messages": new_reviewer_turns,
            "is_flow_type": flow_types,
            "audit_log": audit_entries,
        }

    else:
        # ── GREEN IN RED: We force the flow to the Developer to guarantee the cycle ───
        logger.warning("⚠️  ALERT: The test PASSED immediately (Green in Red).")
        logger.info("   ℹ️  Proceeding — sending an alert for the Developer to inspect.")

        # Loads the alert template
        feedback_text = load_prompt(
            'agents/langgraph/orchestrator/feedback_existing_implementation.jinja2',
            attempt=1,
        )

        audit_entries.append(
            AIMessage(
                content=f"[RunnerRed] Test passed immediately for '{sub_req}'. Alert sent to Developer."
            )
        )

        # LangGraph will automatically append to this list and the Developer will read it as the current `feedback`.
        alert_message = HumanMessage(content=feedback_text)

        flow_types[plan_index] = "F2"

        return {
            **state,
            "status": "red_confirmed",
            "iteration": iteration,
            "infra_retries": 0,
            "reviewer_messages": [alert_message],
            "is_flow_type": flow_types,
            "audit_log": audit_entries,
        }