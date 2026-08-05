import logging
from langchain_core.messages import AIMessage, RemoveMessage

from app.config.config import AgentState

logger = logging.getLogger("TDDOrchestrator")

_FAILURE_STATUSES = {
    "max_retries_exceeded",
    "tester_failed",
    "developer_failed",
    "sandbox_failed"
}

_FAILURE_REASONS = {
    "max_retries_exceeded": "Exceeded the maximum number of attempts in the TDD cycle.",
    "tester_failed":        "The Tester failed catastrophically (execution or API error).",
    "developer_failed":     "The Developer failed catastrophically (execution or API error).",
    "sandbox_failed":       "Execution failed due to a fatal error or continuous instability in E2B/API."
}


def _clear_agent_histories(state: AgentState) -> dict:
    removals: dict[str, list] = {
        "tester_messages": [],
        "developer_messages": [],
        "reviewer_messages": [],
    }

    for field in removals:
        msgs = state.get(field, [])
        removals[field] = [RemoveMessage(id=m.id) for m in msgs if getattr(m, "id", None)]

    return removals


def node_execute_progress_evaluator(state: AgentState) -> AgentState:
    logger.info("\n" + "=" * 80)
    logger.info("📊 STEP 5: PROGRESS EVALUATOR")
    logger.info("=" * 80)

    current_index = state.get("plan_index", 0)
    plan = state.get("plan", [])
    total = len(plan)
    status = state.get("status", "")
    failed_requirements: list[dict] = list(state.get("failed_requirements", []))
    current_req = plan[current_index] if plan else ""
    subreq_results: list[dict] = list(state.get("subreq_results", []))
    subreq_success_count = state.get("subreq_success_count", 0)
    subreq_failure_count = state.get("subreq_failure_count", 0)
    flow_types = list(state.get("is_flow_type", []))

    audit_entries: list = []

    # 1. Checks whether it failed (including sandbox/infra failure)
    if status in _FAILURE_STATUSES:
        reason = _FAILURE_REASONS.get(status, f"Unknown failure status: '{status}'")
        failure_info = {
            "requirement": current_req,
            "index": current_index,
            "status": status,
            "reason": reason,
            "last_iteration": state.get("iteration", 0),
        }
        failed_requirements.append(failure_info)
        subreq_results.append(
            {
                "index": current_index,
                "requirement": current_req,
                "status": "failed",
                "reason": reason,
                "last_iteration": state.get("iteration", 0),
                "flow_type": "",
            }
        )
        subreq_failure_count += 1

        logger.error(f"❌ Sub-requirement FAILED: '{current_req}'")
        logger.error(f"   Reason : {reason}")
        logger.error(f"   Status : {status}")

        audit_entries.append(
            AIMessage(
                content=f"[Evaluator] FAILED '{current_req}' (status={status}): {reason}"
            )
        )

        if status == "sandbox_failed":
            logger.warning("🛑 Fatal infrastructure error. Aborting the TDD flow.")
            return {
                **state,
                "status": "sandbox_failed",
                "failed_requirements": failed_requirements,
                "subreq_results": subreq_results,
                "subreq_success_count": subreq_success_count,
                "subreq_failure_count": subreq_failure_count,
                "audit_log": audit_entries,
            }
        elif status == "developer_failed":
            logger.warning("🛑 Fatal infrastructure error calling the Developer. Aborting the TDD flow.")
            return {
                **state,
                "status": "developer_failed",
                "failed_requirements": failed_requirements,
                "subreq_results": subreq_results,
                "subreq_success_count": subreq_success_count,
                "subreq_failure_count": subreq_failure_count,
                "audit_log": audit_entries,
            }
        elif status == "tester_failed":
            logger.warning("🛑 Fatal infrastructure error calling the Tester. Aborting the TDD flow.")
            return {
                **state,
                "status": "tester_failed",
                "failed_requirements": failed_requirements,
                "subreq_results": subreq_results,
                "subreq_success_count": subreq_success_count,
                "subreq_failure_count": subreq_failure_count,
                "audit_log": audit_entries,
            }
        elif status == "max_retries_exceeded":
            logger.warning("Exceeded the maximum number of attempts for this sub-requirement. Aborting the TDD flow.")

    else:
        logger.info(f"✅ Sub-requirement completed: '{current_req}'")
        audit_entries.append(
            AIMessage(content=f"[Evaluator] Completed '{current_req}'.")
        )
        flow_type = ""
        if "F2" in flow_types:
            flow_type = "F2"
        elif "F1" in flow_types:
            flow_type = "F1"
        subreq_results.append(
            {
                "index": current_index,
                "requirement": current_req,
                "status": "success",
                "reason": "ok",
                "last_iteration": state.get("iteration", 0),
                "flow_type": flow_type,
            }
        )
        subreq_success_count += 1

    logger.info(
        f"📈 Overall progress: {current_index + 1}/{total} "
        f"({(current_index + 1) / total * 100:.0f}%)"
    )

    next_index = current_index + 1

    if next_index < total:
        next_req = plan[next_index]

        logger.info("-" * 80)
        logger.info(f"⏭️  NEXT TASK: '{next_req}'")
        logger.info("   🧹 Resetting agent conversation histories for the new context...")

        history_resets = _clear_agent_histories(state)

        audit_entries.append(
            AIMessage(
                content=f"[Evaluator] Advancing to sub-requirement {next_index + 1}/{total}: '{next_req}'. Histories cleared."
            )
        )

        return {
            **state,
            "status": "next_req",
            "plan_index": next_index,
            "current_sub_req": next_req,
            "iteration": 1,
            "failed_requirements": failed_requirements,
            "subreq_results": subreq_results,
            "subreq_success_count": subreq_success_count,
            "subreq_failure_count": subreq_failure_count,
            "is_flow_type": [],
            **history_resets,
            "audit_log": audit_entries,
        }

    else:
        if failed_requirements:
            n = len(failed_requirements)
            logger.warning(f"\n⚠️  PLAN COMPLETED WITH {n} FAILURE(S).")
            logger.warning("🧾 Ending the TDD flow with recorded failures.")
            final_status = "plan_complete_with_failures"
        else:
            logger.info("\n✅ PLAN COMPLETED SUCCESSFULLY.")
            logger.info("🧾 Ending the TDD flow successfully.")
            final_status = "plan_complete"

        audit_entries.append(
            AIMessage(
                content=f"[Evaluator] Plan finalized. Status: {final_status}. Failed requirements: {len(failed_requirements)}/{total}."
            )
        )

        return {
            **state,
            "status": final_status,
            "failed_requirements": failed_requirements,
            "subreq_results": subreq_results,
            "subreq_success_count": subreq_success_count,
            "subreq_failure_count": subreq_failure_count,
            "audit_log": audit_entries,
        }