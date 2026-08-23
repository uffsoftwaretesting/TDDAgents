"""
An LLM-facing agent: the seam to the model.

**Exempt from the CLAUDE.md quality gate's unit- and mutation-testing requirement.**
Everything in this module resolves to a live model call; there is no seam beneath it to
fake. It is verified by an end-to-end pipeline run. Phase 2 replaces these modules with
declarative definitions plus a tool loop, at which point the loop itself becomes
testable and stops being exempt.
"""

import logging
from app.utils.chat_model_factory import get_chat_model
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field

from app.config.config import Config
from app.errors.agents.handler import handle_llm_exception
from app.utils.prompt_loader import load_prompt

logger = logging.getLogger("TDDOrchestrator")


class AnalystResponse(BaseModel):
    response: str = Field(
        description="Your text response to the user. Can be clarifying questions or the final requirements checklist."
    )
    needs_clarification: bool = Field(
        description="True if the request is vague and you need to ask questions. False if everything is clear."
    )
    has_checklist: bool = Field(
        description="True ONLY if your response contains the final structured checklist "
                    "and the question 'Can I proceed?'."
    )


def analyze_requirements(user_input: str, conversation_history: str = "") -> dict:
    """
    Requirements analyst that asks questions to clarify business rules.
    """
    llm = get_chat_model(provider=Config.CHAT_MODEL, model=Config.MODEL, temperature=Config.TEMPERATURE)
    structured_llm = llm.with_structured_output(AnalystResponse)

    system_prompt = load_prompt(template_name='agents/langgraph/analyst/sys_prompt_1.jinja2')
    human_prompt = load_prompt(
        template_name='agents/langgraph/analyst/hum_prompt_1.jinja2',
        user_input=user_input,
        conversation_history=conversation_history
    )

    try:
        result: AnalystResponse = structured_llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ])
        return result.model_dump()
    except Exception as exc:
        logger.error("❌ ANALYST: Failed to analyze requirements")
        handle_llm_exception(exc, context="analyze_requirements")
