from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from app.config import Config

from app.utils.prompt_loader import load_prompt

def remove_test_imports(code: str) -> str:
    lines = code.split('\n')
    return '\n'.join([line for line in lines if not (line.strip().startswith('import pytest') or line.strip().startswith('from pytest'))])

def generate_code_incremental(
    test_code: str,
    function_name: str,
    specification: str,
    feedback: str = "",
    previous_code: str = ""
) -> str:
    llm = ChatOpenAI(model=Config.MODEL, temperature=0.3)
    
    context_parts = []
    if feedback:
        context_parts.append(f"FEEDBACK DO REVISOR:\n{feedback}")
    if previous_code:
        clean_prev = previous_code.strip()
        if clean_prev and clean_prev != "# Implementação incremental via TDD":
            context_parts.append(f"CÓDIGO ANTERIOR:\n```python\n{clean_prev}\n```")
    
    context = "\n\n".join(context_parts) if context_parts else ""
    
    rendered_sys_message = load_prompt(
        template_name='agents/langgraph/developer/sys_prompt_1.jinja2',
        function_name=function_name,
    )
    
    rendered_hum_message = load_prompt(
        template_name='agents/langgraph/developer/hum_prompt_1.jinja2',
        function_name=function_name,
        specification=specification,
        context=context,
        test_code=test_code,
    )
    
    response = llm.invoke([
        SystemMessage(content=rendered_sys_message),
        HumanMessage(content=rendered_hum_message)
    ])
    raw_code = str(response.content).strip()
    
    clean_code = remove_test_imports(raw_code)
    
    if not clean_code.strip():
        raise ValueError("Developer gerou código vazio")
    
    if f"def {function_name}" not in clean_code:
        raise ValueError(
            f"Código não contém a função {function_name}.\n\n"
            f"RAW RESPONSE:\n{raw_code}\n\n"
            f"FINAL CODE:\n{clean_code}"
        )
    
    try:
        compile(clean_code, '<string>', 'exec')
    except SyntaxError as e:
        raise ValueError(f"Erro de sintaxe: {e}\n\nCódigo:\n{clean_code}")
    
    return clean_code