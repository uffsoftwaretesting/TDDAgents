import re
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from app.config import Config
from app.utils.prompt_loader import load_prompt

def extract_relevant_spec_context(
    specification: str,
    sub_requirement: str,
    test_output: str,
    current_code: str
) -> str:
    """
    Usa LLM para extrair APENAS a parte relevante da especificação.
    Sem heurísticas frágeis, deixa a LLM decidir o que é relevante.
    """
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.1)
    
    rendered_sys_message = load_prompt(
        template_name='agents/langgraph/reviewer/sys_prompt_1.jinja2',
    )
    
    rendered_hum_message = load_prompt(
        template_name='agents/langgraph/reviewer/hum_prompt_1.jinja2',
        specification=specification,
        sub_requirement=sub_requirement,
        test_output=test_output,
        current_code=current_code,
    )
    
    response = llm.invoke([
        SystemMessage(content=rendered_sys_message),
        HumanMessage(content=rendered_hum_message),
    ])
    return str(response.content).strip()


def analyze_failures(
    test_output: str,
    specification: str,
    sub_requirement: str,
    iteration: int = 0,
    max_retries: int = 3,
    current_code: str = "",
    test_code: str = ""
) -> str:
    """
    Analisa falhas com feedback GRADUAL usando LLM para filtragem.
    """
    llm = ChatOpenAI(model=Config.MODEL, temperature=0.3)

    # --- Extrai métricas do pytest ---
    passed_match = re.search(r'(\d+)\s+passed', test_output)
    failed_match = re.search(r'(\d+)\s+failed', test_output)
    
    passed_count = int(passed_match.group(1)) if passed_match else 0
    failed_count = int(failed_match.group(1)) if failed_match else 0
    
    total = passed_count + failed_count
    
    # --- ESTRATÉGIA DE FEEDBACK GRADUAL ---
    if iteration == 0:
        feedback_mode = "MINIMAL"
        spec_context = ""  # Sem contexto de spec
        
    elif iteration == 1:
        feedback_mode = "CONTEXTUAL"
        # ⚠️ LLM extrai contexto relevante
        spec_context = extract_relevant_spec_context(
            specification=specification,
            sub_requirement=sub_requirement,
            test_output=test_output,
            current_code=current_code
        )
        
    else:  # iteration >= 2
        feedback_mode = "ARCHITECTURAL"
        spec_context = specification  # Spec completa para análise profunda

    # --- SYSTEM MESSAGE (instruções de comportamento) ---
    rendered_sys_message = load_prompt(
        template_name='agents/langgraph/reviewer/sys_prompt_2.jinja2',
        feedback_mode=feedback_mode,
        iteration=iteration,
        max_retries=max_retries
    )
    system_msg = SystemMessage(content=rendered_sys_message)

    # --- HUMAN MESSAGE (contexto específico por modo) ---
    if feedback_mode == "MINIMAL":
        rendered_hum_message = load_prompt(
            template_name='agents/langgraph/reviewer/hum_prompt_2_minimal.jinja2',
            sub_requirement=sub_requirement,
            passed_count=passed_count,
            failed_count=failed_count,
            test_output=test_output
        )
        
    elif feedback_mode == "CONTEXTUAL":
        rendered_hum_message = load_prompt(
            template_name='agents/langgraph/reviewer/hum_prompt_2_contextual.jinja2',
            sub_requirement=sub_requirement,
            passed_count=passed_count,
            failed_count=failed_count,
            attempt=iteration + 1,
            spec_context=spec_context,
            current_code=current_code,
            test_output=test_output
        )
        
    else:  # ARCHITECTURAL
        rendered_hum_message = load_prompt(
            template_name='agents/langgraph/reviewer/hum_prompt_2_architectural.jinja2',
            sub_requirement=sub_requirement,
            passed_count=passed_count,
            failed_count=failed_count,
            attempt=iteration + 1,
            max_retries=max_retries,
            specification=specification,
            current_code=current_code,
            test_code=test_code,
            test_output=test_output
        )
    
    human_msg = HumanMessage(content=rendered_hum_message)

    response = llm.invoke([system_msg, human_msg])
    return str(response.content).strip()
