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
from pydantic import BaseModel, Field

from app.config.config import Config
from app.errors.agents.handler import handle_llm_exception
from app.utils.chat_model_factory import get_chat_model
from app.utils.prompt_loader import load_prompt

logger = logging.getLogger("TDDOrchestrator")


class TDDPlan(BaseModel):
    tdd_plan: list[str] = Field(
        description="An ordered list of 'Epics' or Architectural Modules. Each string must "
                    "clearly describe what needs to be set up/implemented (e.g. files, "
                    "dependencies, routes) for that slice of the system."
    )


def generate_plan(specification: str) -> list[str]:
    """
    Generates a software engineering plan (list of modules/files) from the specification.
    """
    llm = get_chat_model(provider=Config.CHAT_MODEL, model=Config.MODEL, temperature=Config.TEMPERATURE)
    structured_llm = llm.with_structured_output(TDDPlan)

    rendered_sys = load_prompt(
        template_name='agents/langgraph/planner/sys_prompt_1.jinja2',
    )
    rendered_hum = load_prompt(
        template_name='agents/langgraph/planner/hum_prompt_1.jinja2',
        specification=specification,
    )

    try:
        response: TDDPlan = structured_llm.invoke([
            SystemMessage(content=rendered_sys),
            HumanMessage(content=rendered_hum),
        ])
        return response.tdd_plan
    except Exception as exc:
        logger.error(f"❌ Planner failed to generate the structured plan: {exc}")
        handle_llm_exception(exc, context="generate_plan")
