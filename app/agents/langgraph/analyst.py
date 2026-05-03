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
        description="A sua resposta em texto para o usuário. Pode ser perguntas de esclarecimento ou o checklist final de requisitos."
    )
    needs_clarification: bool = Field(
        description="True se a solicitação for vaga e você precisar fazer perguntas. False se estiver tudo claro."
    )
    has_checklist: bool = Field(
        description="True APENAS se a sua resposta contiver o checklist estruturado final e a pergunta 'Posso prosseguir?'."
    )

def analyze_requirements(user_input: str, conversation_history: str = "") -> dict:
    """
    Analista de requisitos que faz perguntas para esclarecer regras de negócio.
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
        logger.error("❌ ANALISTA: Falha ao analisar requisitos")
        handle_llm_exception(exc, context="analyze_requirements")