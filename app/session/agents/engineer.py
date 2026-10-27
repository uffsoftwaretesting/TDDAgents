"""
An LLM-facing agent: the seam to the model.

**Exempt from the CLAUDE.md quality gate's unit- and mutation-testing requirement.**
Everything in this module resolves to a live model call; there is no seam beneath it to
fake. It is verified by an end-to-end pipeline run. The prompts are built by
`app/session/prompts.py` (tested); provider errors propagate raw, so the session shell's
`with_retry` can classify them (`app/session/llm.py`).
"""

from langchain_core.messages import HumanMessage, SystemMessage

from app.config.config import Config
from app.session.prompts import engineer_prompts
from app.utils.chat_model_factory import get_chat_model


def generate_specification(requirements: str, conversation_history: str = "") -> str:
    """The technical specification, in Markdown, from the confirmed requirements."""
    llm = get_chat_model(provider=Config.CHAT_MODEL, model=Config.MODEL, temperature=Config.TEMPERATURE)
    system_prompt, human_prompt = engineer_prompts(requirements, conversation_history)
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt),
    ])
    return str(response.content).strip()
