"""
An LLM-facing agent: the seam to the model.

**Exempt from the CLAUDE.md quality gate's unit- and mutation-testing requirement.**
Everything in this module resolves to a live model call; there is no seam beneath it to
fake. It is verified by an end-to-end pipeline run. Phase 2 replaces these modules with
declarative definitions plus a tool loop, at which point the loop itself becomes
testable and stops being exempt.
"""

import logging
from app.errors.agents.handler import handle_llm_exception
from app.utils.chat_model_factory import get_chat_model
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from app.config.config import Config
from app.utils.prompt_loader import load_prompt
from app.schema.schema import AgentAction
from app.utils.sandbox_utils import read_all_files_from_state

logger = logging.getLogger("TDDOrchestrator")


def generate_code_incremental(
    sub_req: str,
    specification: str,
    file_system: dict,
    feedback: str = "",
    conversation_history: list | None = None,
) -> tuple[AgentAction, list]:
    """
    Generates the structured action (files, dependencies, bash commands) to implement the code.
    """
    llm = get_chat_model(provider=Config.CHAT_MODEL, model=Config.MODEL, temperature=Config.TEMPERATURE)
    structured_llm = llm.with_structured_output(AgentAction)

    history: list = list(conversation_history) if conversation_history else []
    current_codebase = read_all_files_from_state(file_system)

    if not history:
        # First call: sends the full spec (no feedback)
        system_content = load_prompt(
            template_name='agents/langgraph/developer/sys_prompt_1.jinja2',
        )
        human_content = load_prompt(
            template_name='agents/langgraph/developer/hum_prompt_1.jinja2',
            sub_requsite=sub_req,
            specification=specification,
            current_codebase=current_codebase,
            feedback=""
        )
        history = [
            SystemMessage(content=system_content),
            HumanMessage(content=human_content),
        ]
    else:
        # Later calls: uses the same template, but passes the feedback
        human_content = load_prompt(
            template_name='agents/langgraph/developer/hum_prompt_1.jinja2',
            sub_requisite=sub_req,
            current_codebase=current_codebase,
            feedback=feedback
        )
        history.append(HumanMessage(content=human_content))

    try:
        # Invokes the LLM forcing AgentAction's structured output
        action: AgentAction = structured_llm.invoke(history)
    except Exception as exc:
        handle_llm_exception(exc, context="generate_code_incremental")

    # Stores the response formatted as JSON in the conversation history (for LangGraph)
    history.append(AIMessage(content=action.model_dump_json(indent=2)))

    return action, history
