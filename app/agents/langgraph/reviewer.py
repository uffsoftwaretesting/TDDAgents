import logging
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from pydantic import BaseModel, Field

from app.config.config import Config
from app.utils.chat_model_factory import get_chat_model
from app.utils.prompt_loader import load_prompt

logger = logging.getLogger("TDDOrchestrator")

# ── Pydantic Schema para o Reviewer ──────────────────────────────────────────
class ReviewAnalysis(BaseModel):
    thoughts: str = Field(
        description="Seu raciocínio interno lendo o stack trace do erro e comparando com os arquivos existentes no workspace."
    )
    is_test_fault: bool = Field(
        description="True se o erro ocorreu porque o código de teste está mal escrito, tentando importar algo que não deveria, ou testando a coisa errada. False se o teste for válido e a culpa for da implementação do Developer."
    )
    feedback_to_agent: str = Field(
        description="Uma instrução técnica, direta e clara para o Developer (ou Tester) sobre o que ele precisa alterar no código para os testes passarem. Indique os nomes dos arquivos."
    )

def analyze_failures(
    test_output: str,
    specification: str,
    sub_requirement: str,
    current_code: str,
    iteration: int,
    max_retries: int,
    conversation_history: list | None = None,
) -> tuple[str, list]:
    """
    Analisa a saída de erro do Pytest contra a base de código atual e fornece feedback estruturado.
    """
    llm = get_chat_model(model_name=Config.CHAT_MODEL, model=Config.MODEL)
    structured_llm = llm.with_structured_output(ReviewAnalysis)

    history: list = list(conversation_history) if conversation_history else []

    if not history:
        sys_prompt = load_prompt(template_name='agents/langgraph/reviewer/sys_prompt_1.jinja2')
        history.append(SystemMessage(content=sys_prompt))

    # Carrega o prompt humano com todo o contexto do erro
    human_content = load_prompt(
        template_name='agents/langgraph/reviewer/hum_prompt_1.jinja2',
        sub_requirement=sub_requirement,
        specification=specification,
        current_code=current_code,
        test_output=test_output,
        iteration=iteration,
        max_retries=max_retries
    )
    history.append(HumanMessage(content=human_content))

    try:
        # Invoca o LLM forçando a saída para o modelo Pydantic
        review: ReviewAnalysis = structured_llm.invoke(history)
        
        logger.info(f"🧐 Reviewer Thoughts: {review.thoughts}")
        
        # Adiciona uma tag visual no feedback para guiar o fluxo e o console
        prefix = "[ERRO NO TESTE]" if review.is_test_fault else "[ERRO NA IMPLEMENTAÇÃO]"
        final_feedback = f"{prefix} {review.feedback_to_agent}"

        history.append(AIMessage(content=final_feedback))
        return final_feedback, history
        
    except Exception as e:
        logger.error(f"❌ Reviewer falhou ao gerar análise: {e}")
        fallback = f"Falha sistêmica ao analisar os erros do Pytest: {e}. Revise a última alteração."
        history.append(AIMessage(content=fallback))
        return fallback, history