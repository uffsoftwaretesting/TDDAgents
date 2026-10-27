"""
An LLM-facing agent: the seam to the model.

**Exempt from the CLAUDE.md quality gate's unit- and mutation-testing requirement.**
Everything in this module resolves to a live model call; there is no seam beneath it to
fake. It is verified by an end-to-end pipeline run. The prompts are built by
`app/session/prompts.py` (tested); provider errors propagate raw, so the session shell's
`with_retry` can classify them (`app/session/llm.py`).
"""

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config.config import Config
from app.session.prompts import planner_prompts
from app.utils.chat_model_factory import get_chat_model


class TDDPlan(BaseModel):
    tdd_plan: list[str] = Field(
        description="An ordered list of 'Epics' or Architectural Modules. Each string must "
                    "clearly describe what needs to be set up/implemented (e.g. files, "
                    "dependencies, routes) for that slice of the system."
    )


def generate_plan(specification: str) -> list[str]:
    """The ordered sub-requirements (the TDD plan) for the specification."""
    llm = get_chat_model(provider=Config.CHAT_MODEL, model=Config.MODEL, temperature=Config.TEMPERATURE)
    structured_llm = llm.with_structured_output(TDDPlan)
    system_prompt, human_prompt = planner_prompts(specification)
    response: TDDPlan = structured_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt),
    ])
    return response.tdd_plan
