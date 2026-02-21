import re
import logging
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from app.config import Config
from app.utils.prompt_loader import load_prompt

logger = logging.getLogger("TDDOrchestrator")

# Número máximo de tentativas de autocorreção quando o LLM produz output inválido
_MAX_SELF_CORRECTIONS = 2


def extract_code(text: str) -> str:
    match = re.search(r'```(?:python)?\s*(.*?)\s*```', text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def _looks_like_implementation(code: str, function_name: str) -> bool:
    """
    Retorna True se o LLM gerou acidentalmente código de implementação em vez
    de um arquivo de testes. O sinal característico é uma definição de função
    sem nenhuma função test_ e sem o import obrigatório.
    """
    has_impl_def = bool(re.search(rf"^\s*def {function_name}\s*\(", code, re.MULTILINE))
    has_test_fns = bool(re.search(r"^\s*def test_", code, re.MULTILINE))
    return has_impl_def and not has_test_fns


def _validate_and_fix(
    code: str,
    module_name: str,
    function_name: str,
) -> tuple[str, list[str]]:
    """
    Executa verificações estruturais no código de teste gerado e tenta
    correções automáticas leves quando possível.

    Retorna
    -------
    (fixed_code, list_of_errors)
        Se list_of_errors não estiver vazia, o chamador deve pedir ao LLM que
        corrija o código em vez de aceitá-lo.
    """
    errors: list[str] = []

    if not code:
        errors.append("O código gerado está vazio.")
        return code, errors

    if _looks_like_implementation(code, function_name):
        errors.append(
            f"Você gerou a IMPLEMENTAÇÃO de '{function_name}', não um arquivo de testes. "
            f"Preciso de um arquivo de testes pytest que importe e teste '{function_name}' "
            f"de '{module_name}'. NÃO defina '{function_name}' você mesmo."
        )
        return code, errors

    # Autocorreção: import do pytest ausente
    if "import pytest" not in code:
        code = "import pytest\n" + code

    # Autocorreção: import da função ausente — injeta logo após o bloco de imports
    import_line = f"from {module_name} import {function_name}"
    if import_line not in code:
        # Tenta inserir após o último import de nível superior
        lines = code.splitlines()
        insert_at = 0
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                insert_at = i + 1
        lines.insert(insert_at, import_line)
        code = "\n".join(lines)

    # Após a autocorreção, reverifica
    if import_line not in code:
        errors.append(
            f"O código de teste deve conter: `{import_line}`  "
            f"NÃO implemente a função — importe-a de '{module_name}'."
        )

    if not re.search(r"^\s*def test_", code, re.MULTILINE):
        errors.append(
            "O código de teste não contém funções de teste (funções começando com 'test_'). "
            "Escreva pelo menos uma função de teste pytest."
        )

    return code, errors


def generate_test_for_sub_req(
    sub_requirement: str,
    function_name: str,
    specification: str,
    all_tests_code: str = "",
    feedback: str = "",
    conversation_history: list | None = None,
    is_review_mode: bool = False,
) -> tuple[str, list]:
    """
    Gera (ou revisa/corrige) testes para um sub-requisito.

    Loop de autocorreção
    ────────────────────
    Se o LLM produzir código de implementação em vez de código de teste (uma
    alucinação comum quando a descrição do sub-requisito contém palavras-chave
    de código), o erro é anexado de volta como HumanMessage e há uma nova
    tentativa até _MAX_SELF_CORRECTIONS vezes antes de lançar exceção.

    Parâmetros
    ----------
    conversation_history:
        A lista `tester_messages` acumulada do AgentState.

    Retorna
    -------
    (clean_code, updated_history)
    """
    llm = ChatOpenAI(model=Config.MODEL, temperature=0.2)
    module_name = Config.IMPLEMENTATION_MODULE

    history: list = list(conversation_history) if conversation_history else []

    if not history:
        # ── Primeira chamada: system prompt + human prompt completos ──────────
        template_sys = (
            'agents/langgraph/tester/sys_prompt_review.jinja2'
            if is_review_mode
            else 'agents/langgraph/tester/sys_prompt_normal.jinja2'
        )
        template_hum = (
            'agents/langgraph/tester/hum_prompt_review.jinja2'
            if is_review_mode
            else 'agents/langgraph/tester/hum_prompt_normal.jinja2'
        )

        context = ""
        if all_tests_code:
            num_tests = len([l for l in all_tests_code.split('\n') if 'def test_' in l])
            context += f"TESTES EXISTENTES ({num_tests} funções):\n```python\n{all_tests_code}\n```\n\n"
        if feedback:
            context += f"FEEDBACK DO REVIEWER:\n{feedback}\n\n"

        system_content = load_prompt(
            template_name=template_sys,
            function_name=function_name,
            module_name=module_name,
        )
        human_content = load_prompt(
            template_name=template_hum,
            function_name=function_name,
            sub_requirement=sub_requirement,
            specification=specification,
            context=context,
        )
        history = [
            SystemMessage(content=system_content),
            HumanMessage(content=human_content),
        ]
    else:
        # ── Chamada seguinte (modo revisão): anexa apenas o que mudou ─────────
        follow_up_parts = []
        if feedback:
            follow_up_parts.append(f"FEEDBACK DO REVIEWER:\n{feedback}")
        if all_tests_code:
            follow_up_parts.append(
                f"TESTES ATUAIS PARA REFERÊNCIA:\n```python\n{all_tests_code}\n```"
            )
        follow_up_parts.append(
            "O developer não conseguiu passar nos testes. Por favor, revise e corrija "
            "o conjunto de testes caso eles próprios estejam incorretos."
        )
        history.append(HumanMessage(content="\n\n".join(follow_up_parts)))

    # ── Loop de geração com autocorreção ──────────────────────────────────────
    for attempt in range(_MAX_SELF_CORRECTIONS + 1):
        response = llm.invoke(history)
        raw = str(response.content).strip()
        history.append(AIMessage(content=raw))

        clean_code = extract_code(raw)
        clean_code, errors = _validate_and_fix(clean_code, module_name, function_name)

        if not errors:
            # Código válido — concluído
            return clean_code, history

        # Código inválido — informa ao LLM exatamente o que deu errado
        error_summary = "\n".join(f"- {e}" for e in errors)
        logger.warning(
            f"   ⚠️  Tentativa de autocorreção do Tester {attempt + 1}/{_MAX_SELF_CORRECTIONS}: "
            f"{len(errors)} problema(s) encontrado(s)."
        )

        if attempt < _MAX_SELF_CORRECTIONS:
            correction_prompt = (
                f"Sua resposta anterior continha os seguintes erros:\n{error_summary}\n\n"
                f"REGRAS ESTRITAS:\n"
                f"1. Produza APENAS um arquivo de testes pytest — sem código de implementação.\n"
                f"2. A primeira linha deve ser: `from {module_name} import {function_name}`\n"
                f"3. Todo nome de função de teste deve começar com `test_`.\n"
                f"4. NÃO defina `def {function_name}(...)` — essa função já existe "
                f"em '{module_name}', apenas importe-a e chame-a.\n\n"
                f"Por favor, produza agora um arquivo de testes corrigido."
            )
            history.append(HumanMessage(content=correction_prompt))
        else:
            raise ValueError(
                f"O Tester falhou em produzir código de teste válido após "
                f"{_MAX_SELF_CORRECTIONS + 1} tentativas.\n"
                f"Últimos erros:\n{error_summary}\n\n"
                f"Último código gerado:\n{clean_code}"
            )