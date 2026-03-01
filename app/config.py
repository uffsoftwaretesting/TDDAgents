import os
from typing import TypedDict, Annotated, Any
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

load_dotenv()

if not os.getenv("OPENAI_API_KEY"):
    raise Exception("Nenhuma chave de API configurada")

if not os.getenv("E2B_API_KEY"):
    raise Exception("Nenhuma chave de API do E2B configurada")

if not os.getenv("POSTGRES_URL"):
    raise Exception("Nenhuma POSTGRES_URL configurada")


class Config:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    E2B_API_KEY = os.getenv("E2B_API_KEY")
    PROMPTS_DIR = os.path.join(os.path.dirname(__file__), './prompts')
    POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://tdd_user:tdd_password@localhost:5432/tdd_db")
    
    # Model natively trained for advanced tool calling
    CHAT_MODEL = "openai"
    MODEL = "o4-mini"
    MAX_ITERATIONS = 12
    WORKSPACE_PATH = "workspace"
    PLAN_KEY = "tdd_plan_queue"


class AgentState(TypedDict):
    # ── Dados principais da tarefa ────────────────────────────────────────────
    specification: str
    plan: list[str]
    plan_index: int
    current_sub_req: str

    # ── Cloud Sandbox e Persistência de Arquivos ──────────────────────────────
    # Em vez de carregar código no LangGraph, os agentes usarão o E2B Code Interpreter.
    # Estes campos rastreiam o container e a árvore de diretórios atual.
    sandbox_id: str
    file_system: dict[str, str]

    # ── Históricos de conversa por agente ─────────────────────────────────────
    tester_messages: Annotated[list[BaseMessage], add_messages]
    developer_messages: Annotated[list[BaseMessage], add_messages]
    reviewer_messages: Annotated[list[BaseMessage], add_messages]

    # ── Log de auditoria compartilhado ────────────────────────────────────────
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
    status: str
    interaction_count: int