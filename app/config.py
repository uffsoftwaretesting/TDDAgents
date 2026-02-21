import os
from typing import TypedDict, Annotated
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

load_dotenv()

if not os.getenv("OPENAI_API_KEY"):
    raise Exception("Nenhuma chave de API configurada")

if not os.getenv("POSTGRES_URL"):
    raise Exception("Nenhuma POSTGRES_URL configurada")


class Config:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    PROMPTS_DIR = os.path.join(os.path.dirname(__file__), './prompts')
    POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://tdd_user:tdd_password@localhost:5432/tdd_db")
    MODEL = "gpt-4o-mini"
    MAX_ITERATIONS = 12
    WORKSPACE_PATH = "workspace"

    IMPLEMENTATION_MODULE = "app_code"
    TEST_FILE = "test_app.py"
    PLAN_KEY = "tdd_plan_queue"


class AgentState(TypedDict):
    # ── Dados principais da tarefa ────────────────────────────────────────────
    specification: str
    function_name: str
    plan: list[str]
    plan_index: int
    current_sub_req: str

    # ── Artefatos produzidos pelos agentes ────────────────────────────────────
    tests_code: str
    implementation_code: str

    # ── Históricos de conversa por agente ─────────────────────────────────────
    # Cada agente carrega sua própria lista crescente de BaseMessage que é
    # passada diretamente ao ChatOpenAI. O reducer add_messages do LangGraph
    # anexa novos turnos em vez de substituir a lista inteira, então o histórico
    # sobrevive a re-entradas de nós dentro de uma execução E a execuções
    # retomadas (via checkpointer).
    #
    # COMO O CHECKPOINTER USA ESSES CAMPOS:
    #   - A cada entrada de nó, o LangGraph carrega o último snapshot do
    #     AgentState para o thread_id atual do Postgres e o entrega ao nó.
    #   - A cada saída de nó, o LangGraph mescla o estado parcial retornado com
    #     o snapshot (usando add_messages para listas) e grava o resultado de
    #     volta no Postgres.
    #   - Se o processo falhar e for reiniciado com o mesmo thread_id, o grafo
    #     retoma a partir do último snapshot salvo com sucesso — incluindo todas
    #     as mensagens trocadas até então.
    #
    # POR QUE HISTÓRICOS SEPARADOS EM VEZ DE UMA LISTA COMPARTILHADA:
    #   - O tester, developer e reviewer têm papéis e personas distintos.
    #     Misturar os turnos de todos em uma única lista confundiria o LLM.
    #   - Sinais entre agentes (ex: "testes falharam, veja o output") trafegam
    #     por campos escalares compartilhados (status, tests_code, etc.) e pelo
    #     audit_log abaixo.
    tester_messages: Annotated[list[BaseMessage], add_messages]
    developer_messages: Annotated[list[BaseMessage], add_messages]
    reviewer_messages: Annotated[list[BaseMessage], add_messages]

    # ── Log de auditoria compartilhado ───────────────────────────────────────
    # Registro legível de cada evento significativo no workflow.
    # Os nós anexam um AIMessage / HumanMessage por turno aqui para que o
    # orquestrador (e qualquer pessoa inspecionando os snapshots do Postgres)
    # possa acompanhar toda a história. O reducer add_messages garante que
    # nenhuma entrada seja perdida.
    audit_log: Annotated[list[BaseMessage], add_messages]

    # ── Controle de fluxo ─────────────────────────────────────────────────────
    iteration: int
    status: str
    max_retries: int
    red_attempts: int
    failed_requirements: list[dict]


class RequirementsState(TypedDict):
    user_input: str
    conversation_history: str
    current_response: str
    needs_clarification: bool
    has_checklist: bool
    user_confirmed: bool
    final_specification: str
    function_name: str
    status: str
    interaction_count: int