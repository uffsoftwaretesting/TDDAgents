"""
An LLM-facing agent: the seam to the model.

**Exempt from the CLAUDE.md quality gate's unit- and mutation-testing requirement.**
Everything in this module resolves to a live model call; there is no seam beneath it to
fake. It is verified by an end-to-end pipeline run. Phase 2 replaces these modules with
declarative definitions plus a tool loop, at which point the loop itself becomes
testable and stops being exempt.
"""

import logging
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from pydantic import BaseModel, Field

from app.config.config import Config
from app.errors.agents.handler import handle_llm_exception
from app.utils.chat_model_factory import get_chat_model
from app.utils.prompt_loader import load_prompt

logger = logging.getLogger("TDDOrchestrator")


# ── Pydantic Schema for the Reviewer ──────────────────────────────────────────
class ReviewAnalysis(BaseModel):
    thoughts: str = Field(
        description="Your internal reasoning reading the error's stack trace and comparing "
                    "it against the existing files in the workspace."
    )
    is_test_fault: bool = Field(
        description="True if the error occurred because the test code is poorly written, is "
                    "trying to import something it shouldn't, or is testing the wrong thing. "
                    "False if the test is valid and the fault lies with the Developer's "
                    "implementation."
    )
    feedback_to_agent: str = Field(
        description="A technical, direct, and clear instruction for the Developer (or Tester) "
                    "about what they need to change in the code to make the tests pass. "
                    "Indicate the file names."
    )


def analyze_failures(
    test_output: str,
    specification: str,
    sub_requirement: str,
    current_code: str,
    iteration: int,
    max_retries: int,
    conversation_history: list | None = None,
) -> tuple[str, list]:
    """
    Analyzes Pytest's error output against the current codebase and provides structured feedback.
    """
    llm = get_chat_model(provider=Config.CHAT_MODEL, model=Config.MODEL, temperature=Config.TEMPERATURE)
    structured_llm = llm.with_structured_output(ReviewAnalysis)

    history: list = list(conversation_history) if conversation_history else []

    if not history:
        sys_prompt = load_prompt(template_name='agents/langgraph/reviewer/sys_prompt_1.jinja2')
        history.append(SystemMessage(content=sys_prompt))

    # Loads the human prompt with the full error context
    human_content = load_prompt(
        template_name='agents/langgraph/reviewer/hum_prompt_1.jinja2',
        sub_requirement=sub_requirement,
        specification=specification,
        current_code=current_code,
        test_output=test_output,
        iteration=iteration,
        max_retries=max_retries
    )
    history.append(HumanMessage(content=human_content))

    try:
        # Invokes the LLM forcing the output into the Pydantic model
        review: ReviewAnalysis = structured_llm.invoke(history)

        logger.info(f"🧐 Reviewer Thoughts: {review.thoughts}")

        # Adds a visual tag to the feedback to guide the flow and the console
        prefix = "[TEST ERROR]" if review.is_test_fault else "[IMPLEMENTATION ERROR]"
        final_feedback = f"{prefix} {review.feedback_to_agent}"

        history.append(AIMessage(content=final_feedback))
        return final_feedback, history

    except Exception as exc:
        logger.error("❌ Reviewer failed to generate the analysis")
        handle_llm_exception(exc, context="analyze_failures")
