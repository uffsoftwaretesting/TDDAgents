import re
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from app.config import Config
from app.utils.prompt_loader import load_prompt


def extract_relevant_spec_context(
    specification: str,
    sub_requirement: str,
    test_output: str,
    current_code: str,
) -> str:
    """Chamada one-shot para extrair a parte relevante da spec. Sem estado."""
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.1)

    rendered_sys = load_prompt(
        template_name='agents/langgraph/reviewer/sys_prompt_1.jinja2',
    )
    rendered_hum = load_prompt(
        template_name='agents/langgraph/reviewer/hum_prompt_1.jinja2',
        specification=specification,
        sub_requirement=sub_requirement,
        test_output=test_output,
        current_code=current_code,
    )

    response = llm.invoke([
        SystemMessage(content=rendered_sys),
        HumanMessage(content=rendered_hum),
    ])
    return str(response.content).strip()


def analyze_failures(
    test_output: str,
    specification: str,
    sub_requirement: str,
    iteration: int = 0,
    max_retries: int = 3,
    current_code: str = "",
    test_code: str = "",
    conversation_history: list | None = None,
) -> tuple[str, list]:
    """
    Analisa falhas nos testes e retorna feedback acionável para o developer.

    Parâmetros
    ----------
    conversation_history:
        A lista `reviewer_messages` acumulada do AgentState. O reviewer vê suas
        próprias análises anteriores como turnos reais de AI, o que lhe permite
        rastrear padrões ao longo de múltiplas tentativas falhas e escalar seu
        diagnóstico.

    Retorna
    -------
    (analysis_text, updated_history)
        updated_history é a lista completa atualizada; o nó fatia os novos turnos.
    """
    llm = ChatOpenAI(model=Config.MODEL, temperature=0.3)

    history: list = list(conversation_history) if conversation_history else []

    # ── Determina a profundidade do feedback com base na iteração ─────────────
    passed_match = re.search(r'(\d+)\s+passed', test_output)
    failed_match = re.search(r'(\d+)\s+failed', test_output)
    passed_count = int(passed_match.group(1)) if passed_match else 0
    failed_count = int(failed_match.group(1)) if failed_match else 0

    if iteration == 0:
        feedback_mode = "MINIMAL"
        spec_context = ""
    elif iteration == 1:
        feedback_mode = "CONTEXTUAL"
        spec_context = extract_relevant_spec_context(
            specification=specification,
            sub_requirement=sub_requirement,
            test_output=test_output,
            current_code=current_code,
        )
    else:
        feedback_mode = "ARCHITECTURAL"
        spec_context = specification

    # ── Primeira chamada: configura a persona do sistema ──────────────────────
    if not history:
        system_content = load_prompt(
            template_name='agents/langgraph/reviewer/sys_prompt_2.jinja2',
            feedback_mode=feedback_mode,
            iteration=iteration,
            max_retries=max_retries,
        )
        history = [SystemMessage(content=system_content)]

    # ── Construção do turno Human para este relatório de falha ────────────────
    if feedback_mode == "MINIMAL":
        human_content = load_prompt(
            template_name='agents/langgraph/reviewer/hum_prompt_2_minimal.jinja2',
            sub_requirement=sub_requirement,
            passed_count=passed_count,
            failed_count=failed_count,
            test_output=test_output,
        )
    elif feedback_mode == "CONTEXTUAL":
        human_content = load_prompt(
            template_name='agents/langgraph/reviewer/hum_prompt_2_contextual.jinja2',
            sub_requirement=sub_requirement,
            passed_count=passed_count,
            failed_count=failed_count,
            attempt=iteration + 1,
            spec_context=spec_context,
            current_code=current_code,
            test_output=test_output,
        )
    else:
        human_content = load_prompt(
            template_name='agents/langgraph/reviewer/hum_prompt_2_architectural.jinja2',
            sub_requirement=sub_requirement,
            passed_count=passed_count,
            failed_count=failed_count,
            attempt=iteration + 1,
            max_retries=max_retries,
            specification=specification,
            current_code=current_code,
            test_code=test_code,
            test_output=test_output,
        )

    history.append(HumanMessage(content=human_content))

    # ── Chamada ao LLM ────────────────────────────────────────────────────────
    response = llm.invoke(history)
    analysis = str(response.content).strip()
    history.append(AIMessage(content=analysis))

    return analysis, history