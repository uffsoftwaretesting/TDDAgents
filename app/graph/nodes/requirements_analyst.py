import logging
import time
from app.config.config import Config, RequirementsState
from app.agents.langgraph.analyst import analyze_requirements
from app.errors.exceptions import FatalInfraError, TransientInfraError

logger = logging.getLogger("TDDOrchestrator")

def node_analyst(state: RequirementsState) -> RequirementsState:
    logger.info("=" * 70)
    logger.info("🧠 ANALYST - Gathering and Analyzing Requirements")
    logger.info("=" * 70)

    user_input = state["user_input"]
    conversation_history = state["conversation_history"]
    interaction_count = state.get("interaction_count", 0)
    infra_retries = state.get("infra_retries", 0)

    # Increments the user interaction counter
    interaction_count += 1

    try:
        result = analyze_requirements(user_input, conversation_history)
    except TransientInfraError as exc:
        infra_retries += 1
        if infra_retries >= Config.MAX_INFRA_RETRIES:
            logger.error(f"❌ ANALYST: Infra Failure (Limit Reached): {exc.original_exc}")
            return {**state, "status": "analyst_failed", "infra_retries": 0}

        logger.warning(f"⚠️ ANALYST: Transient Error. Attempt {infra_retries}/{Config.MAX_INFRA_RETRIES}. Waiting 3s... ({exc.original_exc})")
        time.sleep(3)
        return {**state, "status": "infra_error_analyst", "infra_retries": infra_retries}

    except FatalInfraError as exc:
        logger.error(f"❌ ANALYST: Fatal Infrastructure Failure: {exc.original_exc}")
        return {**state, "status": "analyst_failed", "infra_retries": 0}

    logger.info(f"🔍 Needs clarification: {result['needs_clarification']}")
    logger.info(f"📋 Has checklist: {result['has_checklist']}")

    # Guard against an infinite user loop
    if interaction_count >= 5 and not result['has_checklist']:
        logger.warning("🚨 Interaction limit reached. Suggesting approval.")
        result['response'] += "\n\nWe already have plenty of information. Here is the current Requirements Checklist. Can I proceed to the engineering team?"
        result['has_checklist'] = True
        result['needs_clarification'] = False

    new_history = conversation_history
    if conversation_history:
        new_history += f"\n\n[User]: {user_input}\n[Analyst]: {result['response']}"
    else:
        new_history = f"[User]: {user_input}\n[Analyst]: {result['response']}"

    return {
        **state,
        "conversation_history": new_history,
        "current_response": result['response'],
        "needs_clarification": result['needs_clarification'],
        "has_checklist": result['has_checklist'],
        "interaction_count": interaction_count,
        "status": "awaiting_user" if result['needs_clarification'] or result['has_checklist'] else "analyzing"
    }