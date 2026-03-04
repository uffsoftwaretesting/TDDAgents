import logging
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field

from app.config import Config
from app.utils.chat_model_factory import get_chat_model
from app.utils.prompt_loader import load_prompt

logger = logging.getLogger("TDDOrchestrator")

# O esquema Pydantic dita a forma como o LLM estrutura a resposta
class TDDPlan(BaseModel):
    tdd_plan: list[str] = Field(
        description="Uma lista ordenada de 'Epics' ou Módulos Arquiteturais. Cada string deve descrever claramente o que deve ser configurado/implementado (ex: arquivos, dependências, rotas) para aquela fatia do sistema."
    )

def generate_plan(specification: str) -> list[str]:
    """
    Gera um plano de engenharia de software (lista de módulos/arquivos) a partir da especificação.
    """
    llm = get_chat_model(model_name=Config.CHAT_MODEL, model=Config.MODEL)
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
    except Exception as e:
        logger.error(f"❌ Planner falhou ao gerar o plano estruturado: {e}")
        return []