import logging
from app.errors.agents.handler import handle_llm_exception
from app.utils.chat_model_factory import get_chat_model
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from app.config.config import Config
from app.utils.prompt_loader import load_prompt
from app.schema.schema import AgentAction
from app.utils.sandbox_utils import read_all_files_from_state

logger = logging.getLogger("TDDOrchestrator")

def generate_test_for_sub_req(
    sub_requirement: str,
    specification: str,
    file_system: dict,
    feedback: str = "",
    conversation_history: list | None = None,
    is_review_mode: bool = False,
) -> tuple[AgentAction, list]:
    """
    Generates the structured action containing the changes to the test suite (via Pytest).
    """
    llm = get_chat_model(provider=Config.CHAT_MODEL, model=Config.MODEL, temperature=Config.TEMPERATURE)
    structured_llm = llm.with_structured_output(AgentAction)

    history: list = list(conversation_history) if conversation_history else []
    current_codebase = read_all_files_from_state(file_system)

    template_sys = (
        'agents/langgraph/tester/sys_prompt_review.jinja2'
        if is_review_mode else 'agents/langgraph/tester/sys_prompt_normal.jinja2'
    )
    template_hum = (
        'agents/langgraph/tester/hum_prompt_review.jinja2'
        if is_review_mode else 'agents/langgraph/tester/hum_prompt_normal.jinja2'
    )

    if not history:
        # First call: sends the full spec (no feedback)
        system_content = load_prompt(template_name=template_sys)
        human_content = load_prompt(
            template_name=template_hum,
            sub_requirement=sub_requirement,
            specification=specification,
            current_codebase=current_codebase,
            feedback=""
        )

        history = [
            SystemMessage(content=system_content),
            HumanMessage(content=human_content),
        ]
    else:
        # Later calls: uses the same human template, but passes the feedback
        human_content = load_prompt(
            template_name=template_hum,
            sub_requirement=sub_requirement,
            current_codebase=current_codebase,
            feedback=feedback
        )
        history.append(HumanMessage(content=human_content))

    # Invokes the LLM forcing AgentAction's structured output
    try:
        action: AgentAction = structured_llm.invoke(history)
    except Exception as exc:
        handle_llm_exception(exc, context="generate_test_for_sub_req")

    # Stores the response formatted as JSON in the conversation history
    history.append(AIMessage(content=action.model_dump_json(indent=2)))

    return action, history
