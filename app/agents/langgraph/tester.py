import re
import ast
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from app.config import Config
from app.utils.prompt_loader import load_prompt

def extract_code(text: str) -> str:
    match = re.search(r'```(?:python)?\s*(.*?)\s*```', text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()

def generate_test_for_sub_req(
    sub_requirement: str,
    function_name: str,
    specification: str,
    all_tests_code: str = "",
    feedback: str = ""
) -> str:
    llm = ChatOpenAI(model=Config.MODEL, temperature=0.2)
    module_name = Config.IMPLEMENTATION_MODULE

    is_test_review = "REVISÃO DE TESTES NECESSÁRIA" in feedback
    
    context = ""
    if all_tests_code:
        num_tests = len([l for l in all_tests_code.split('\n') if 'def test_' in l])
        context += f"TESTES EXISTENTES ({num_tests} funções):\n```python\n{all_tests_code}\n```\n\n"
    if feedback:
        context += f"FEEDBACK DO REVISOR:\n{feedback}\n\n"

    if is_test_review:
        rendered_sys_message = load_prompt(
            template_name='agents/langgraph/tester/sys_prompt_review.jinja2',
            function_name=function_name,
            module_name=module_name
        )
        system_msg = SystemMessage(content=rendered_sys_message)

        rendered_hum_message = load_prompt(
            template_name='agents/langgraph/tester/hum_prompt_review.jinja2',
            function_name=function_name,
            sub_requirement=sub_requirement,
            specification=specification,
            context=context
        )
        human_msg = HumanMessage(content=rendered_hum_message)

    else:
        rendered_sys_message = load_prompt(
            template_name='agents/langgraph/tester/sys_prompt_normal.jinja2',
            function_name=function_name,
            module_name=module_name
        )
        system_msg = SystemMessage(content=rendered_sys_message)

        rendered_hum_message = load_prompt(
            template_name='agents/langgraph/tester/hum_prompt_normal.jinja2',
            function_name=function_name,
            sub_requirement=sub_requirement,
            specification=specification,
            context=context
        )
        human_msg = HumanMessage(content=rendered_hum_message)

    response = llm.invoke([system_msg, human_msg])
    clean_code = extract_code(str(response.content).strip())

    if not clean_code:
        raise ValueError("LLM retornou código vazio")

    if "import pytest" not in clean_code:
        clean_code = "import pytest\n" + clean_code

    if f"from {module_name} import {function_name}" not in clean_code:
        raise ValueError(
            f"Código de teste não importa a função {function_name} corretamente.\n"
            f"Código gerado:\n{clean_code}"
        )

    return clean_code