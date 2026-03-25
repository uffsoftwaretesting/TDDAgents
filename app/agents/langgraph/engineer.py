import logging
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from app.config.config import Config
from app.utils.chat_model_factory import get_chat_model
from app.utils.prompt_loader import load_prompt

logger = logging.getLogger("TDDOrchestrator")

def generate_specification(requirements: str, conversation_history: str = "") -> str:
    logger.info("=" * 70)
    logger.info("⚙️ ENGENHEIRO - Escrevendo Especificação Técnica Formal")
    logger.info("=" * 70)
    
    llm = get_chat_model(model_name=Config.CHAT_MODEL, model=Config.MODEL)
    
    system_prompt = load_prompt(template_name='agents/langgraph/engineer/sys_prompt_1.jinja2')
    human_prompt = load_prompt(
        template_name='agents/langgraph/engineer/hum_prompt_1.jinja2',
        requirements=requirements,
        conversation_history=conversation_history
    )
    
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt)
    ])
    
    content = response.content.strip()
    logger.info(f"✅ Especificação gerada com sucesso! Total: {len(content)} caracteres.")
    return content