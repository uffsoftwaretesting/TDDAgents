import logging
import sys

from app.config.config import RequirementsState

logger = logging.getLogger("TDDOrchestrator")


def node_user_input(state: RequirementsState) -> RequirementsState:
    """Graph node that interacts with the user in a robust and flexible way."""
    logger.info("=" * 70)
    logger.info("👤 USER INPUT - Awaiting user feedback")
    logger.info("=" * 70)

    current_response = state["current_response"]

    # Displays the Analyst's response with clear formatting
    print(f"\n🤖 [Requirements Analyst]:\n{current_response}\n")

    if state.get("has_checklist"):
        print("💡 TIP: The Analyst has generated the Final Checklist.")
        print("   - Type '/yes' to proceed to Engineering.")
        print("   - Or type the changes you'd like to make (e.g. 'Missing validation X').")
    else:
        print("💡 TIP: The Analyst needs more details.")
        print("   - Answer the question to help define the scope.")

    try:
        user_response = input("\n👤 [Your Response] (or '/exit'): ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n👋 Operation cancelled by the user.")
        sys.exit(0)

    if user_response.lower() in ["/exit", "/quit"]:
        print("\n👋 Exiting requirements gathering...")
        sys.exit(0)

    logger.info(f"📝 User response: {user_response}")

    user_prompts = list(state.get("user_prompts", []))
    user_prompts.append(user_response)

    # Static, explicit confirmation: only '/yes' confirms.
    is_confirmation = bool(state.get("has_checklist")) and user_response.lower() == "/yes"

    new_state: RequirementsState = {
        **state,
        "user_input": user_response,
        "user_prompts": user_prompts,
        "user_confirmed": is_confirmation,
        "status": "confirmed" if is_confirmation else "continue_analysis",
    }

    # If there was a checklist and the user did not confirm, reopen the analysis.
    if state.get("has_checklist") and not is_confirmation:
        logger.info("🔄 User requested changes to the checklist. Reopening analysis.")
        new_state["has_checklist"] = False

    return new_state