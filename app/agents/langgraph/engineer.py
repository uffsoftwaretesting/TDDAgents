"""
An LLM-facing agent: the seam to the model.

**Exempt from the CLAUDE.md quality gate's unit- and mutation-testing requirement.**
Everything in this module resolves to a live model call; there is no seam beneath it to
fake. It is verified by an end-to-end pipeline run. Phase 2 replaces these modules with
declarative definitions plus a tool loop, at which point the loop itself becomes
testable and stops being exempt.
"""

import logging
from langchain_core.messages import SystemMessage, HumanMessage
from app.config.config import Config
from app.errors.agents.handler import handle_llm_exception
from app.utils.chat_model_factory import get_chat_model
from app.utils.prompt_loader import load_prompt

logger = logging.getLogger("TDDOrchestrator")


def generate_specification(requirements: str, conversation_history: str = "") -> str:

    llm = get_chat_model(provider=Config.CHAT_MODEL, model=Config.MODEL, temperature=Config.TEMPERATURE)

    system_prompt = load_prompt(template_name='agents/langgraph/engineer/sys_prompt_1.jinja2')
    human_prompt = load_prompt(
        template_name='agents/langgraph/engineer/hum_prompt_1.jinja2',
        requirements=requirements,
        conversation_history=conversation_history
    )

    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ])
    except Exception as exc:
        logger.error("❌ ENGINEER: Failed to generate the technical specification")
        handle_llm_exception(exc, context="generate_specification")

    ''
    content = response.content.strip()
    logger.info(f"✅ Specification generated successfully! Total: {len(content)} characters.")
    return content
