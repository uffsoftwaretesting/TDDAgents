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
    Gera a ação estruturada (arquivos, dependências, comandos bash) para implementar o código.
    """
    llm = get_chat_model(provider=Config.CHAT_MODEL, model=Config.MODEL, temperature=Config.TEMPERATURE)
    structured_llm = llm.with_structured_output(AgentAction)

    history: list = list(conversation_history) if conversation_history else []
    current_codebase = read_all_files_from_state(file_system)

    if not history:
        # Primeira chamada: envia a spec completa (sem feedback)
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
        # Próximas chamadas: usa o mesmo template, mas passa o feedback
        human_content = load_prompt(
            template_name='agents/langgraph/developer/hum_prompt_1.jinja2',
            sub_requisite=sub_req,
            current_codebase=current_codebase,
            feedback=feedback
        )
        history.append(HumanMessage(content=human_content))

    try:
        # Invoca o LLM forçando a saída estruturada do AgentAction
        action: AgentAction = structured_llm.invoke(history)
    except Exception as exc:
        handle_llm_exception(exc, context="generate_code_incremental")

    # Armazena a resposta formatada como JSON no histórico de conversa (para o LangGraph)
    history.append(AIMessage(content=action.model_dump_json(indent=2)))

    return action, history