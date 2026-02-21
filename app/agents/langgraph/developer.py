from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from app.config import Config
from app.utils.prompt_loader import load_prompt


def remove_test_imports(code: str) -> str:
    lines = code.split('\n')
    return '\n'.join(
        line for line in lines
        if not (
            line.strip().startswith('import pytest')
            or line.strip().startswith('from pytest')
        )
    )


def generate_code_incremental(
    test_code: str,
    function_name: str,
    specification: str,
    feedback: str = "",
    previous_code: str = "",
    conversation_history: list | None = None,
) -> tuple[str, list]:
    """
    Gera ou corrige o código de implementação.

    Parâmetros
    ----------
    conversation_history:
        A lista `developer_messages` acumulada do AgentState. Esta é a conversa
        real do LangChain que cresce a cada chamada — o LLM vê seu próprio
        raciocínio anterior e o feedback do reviewer como turnos reais de chat,
        não como strings coladas.

    Retorna
    -------
    (clean_code, updated_history)
        updated_history é a nova lista para armazenar de volta no AgentState,
        permitindo que o reducer add_messages do LangGraph a persista via
        checkpointer.
    """
    llm = ChatOpenAI(model=Config.MODEL, temperature=0.3)

    history: list = list(conversation_history) if conversation_history else []

    # ── Construção do turno Human para esta iteração ──────────────────────────
    # Na primeira chamada não há histórico, então incluímos o contexto completo
    # do sistema. Nas chamadas seguintes o LLM já conhece a spec e o conjunto de
    # testes dos turnos anteriores — enviamos apenas o que mudou.
    if not history:
        # Primeira chamada: inclui o system prompt + contexto humano completo
        system_content = load_prompt(
            template_name='agents/langgraph/developer/sys_prompt_1.jinja2',
            function_name=function_name,
        )
        human_content = load_prompt(
            template_name='agents/langgraph/developer/hum_prompt_1.jinja2',
            function_name=function_name,
            specification=specification,
            context="",          # sem feedback na primeira tentativa
            test_code=test_code,
        )
        history = [
            SystemMessage(content=system_content),
            HumanMessage(content=human_content),
        ]
    else:
        # Chamadas seguintes: o LLM já tem a spec e o código anterior.
        # Enviamos apenas o feedback do reviewer e o conjunto de testes atualizado
        # para manter a janela de contexto enxuta.
        context_parts = []
        if feedback:
            context_parts.append(f"FEEDBACK DO REVIEWER:\n{feedback}")
        if previous_code:
            context_parts.append(
                f"SUA IMPLEMENTAÇÃO ANTERIOR:\n```python\n{previous_code.strip()}\n```"
            )
        if test_code:
            context_parts.append(
                f"CONJUNTO DE TESTES ATUAL (pode ter sido atualizado pelo Tester):\n"
                f"```python\n{test_code}\n```"
            )

        follow_up = "\n\n".join(context_parts) + (
            "\n\nPor favor, produza uma implementação corrigida que passe em todos os testes."
        )
        history.append(HumanMessage(content=follow_up))

    # ── Chamada ao LLM com o histórico completo até agora ────────────────────
    response = llm.invoke(history)
    raw_code = str(response.content).strip()

    # Registra a resposta do assistente para que a próxima iteração a veja
    history.append(AIMessage(content=raw_code))

    # ── Pós-processamento ─────────────────────────────────────────────────────
    clean_code = remove_test_imports(raw_code)

    if not clean_code.strip():
        raise ValueError("O Developer gerou código vazio")

    if f"def {function_name}" not in clean_code:
        raise ValueError(
            f"O código não contém a função '{function_name}'.\n\n"
            f"RESPOSTA BRUTA:\n{raw_code}\n\nCÓDIGO FINAL:\n{clean_code}"
        )

    try:
        compile(clean_code, '<string>', 'exec')
    except SyntaxError as e:
        raise ValueError(f"Erro de sintaxe: {e}\n\nCódigo:\n{clean_code}")

    return clean_code, history