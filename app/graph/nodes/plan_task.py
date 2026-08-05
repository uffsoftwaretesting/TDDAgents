import logging
import time
from langchain_core.messages import AIMessage

from app.config.config import AgentState, Config
from app.agents.langgraph.planner import generate_plan
from app.errors.agents.handler import handle_llm_exception
from app.errors.exceptions import FatalInfraError, TransientInfraError

logger = logging.getLogger("TDDOrchestrator")


def node_plan_task(state: AgentState) -> AgentState:
    logger.info("\n" + "=" * 80)
    logger.info("📋 PLANNING (PLANNER)")
    logger.info("=" * 80)
    logger.info(f"📌 Specification: {state['specification'][:100]}...")

    infra_retries = state.get("infra_retries", 0)

    try:
        plan = generate_plan(state["specification"])

    except TransientInfraError as exc:
        infra_retries += 1
        if infra_retries >= Config.MAX_INFRA_RETRIES:
            logger.error(f"❌ PLANNER: Infra Failure (Limit Reached): {exc.original_exc}")
            return {**state, "status": "plan_failed", "plan": [], "infra_retries": 0}

        logger.warning(f"⚠️ PLANNER: Transient Error. Attempt {infra_retries}/{Config.MAX_INFRA_RETRIES}. Waiting 3s... ({exc.original_exc})")
        time.sleep(3)
        return {**state, "status": "infra_error_planner", "infra_retries": infra_retries}

    except FatalInfraError as exc:
        logger.error(f"❌ PLANNER: Fatal Infrastructure Failure: {exc.original_exc}")
        return {**state, "status": "plan_failed", "plan": [], "infra_retries": 0}
    except Exception as exc:
        logger.error(f"❌ PLANNER: Fatal Infrastructure Failure")
        return {**state, "status": "plan_failed", "plan": [], "infra_retries": 0}

    if not plan:
        logger.error("❌ ERROR: The Planner failed to generate the task plan.")
        return {
            **state,
            "status": "plan_failed",
            "plan": [],
            "infra_retries": 0,
            "audit_log": [AIMessage(content="[Planner] Failed to generate the task plan.")],
        }

    logger.info("-" * 80)
    logger.info(f"✅ PLAN GENERATED WITH {len(plan)} SUB-REQUIREMENTS:")
    for i, item in enumerate(plan, 1):
        logger.info(f"   {i}. {item}")
    logger.info("-" * 80)

    plan_summary = "\n".join(f"{i+1}. {item}" for i, item in enumerate(plan))
    audit_entry = AIMessage(
        content=f"[Planner] Plan generated with {len(plan)} sub-requirements:\n{plan_summary}"
    )

    return {
        **state,
        "plan": plan,
        "plan_index": 0,
        "current_sub_req": plan[0],
        "iteration": 1,
        "infra_retries": 0,
        "status": "planning_complete",
        # Agent histories start empty; no memory should be
        # loaded for the first sub-requirement. They're already [] in initial_state.
        "tester_messages": [],
        "developer_messages": [],
        "reviewer_messages": [],
        "is_flow_type": [],
        "audit_log": [audit_entry],
    }