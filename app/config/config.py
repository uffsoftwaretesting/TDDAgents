import os
from typing import TypedDict, Annotated
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

load_dotenv()

if not os.getenv("OPENAI_API_KEY"):
    raise Exception("No API key configured")

if not os.getenv("E2B_API_KEY"):
    raise Exception("No E2B API key configured")

if not os.getenv("POSTGRES_URL"):
    raise Exception("No POSTGRES_URL configured")


class Config:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
    E2B_API_KEY = os.getenv("E2B_API_KEY")
    PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")
    POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://tdd_user:tdd_password@localhost:5432/tdd_db")
    
    CHAT_MODEL = "openai"
    MODEL = "o4-mini"
    TEMPERATURE = 1.0
    MAX_ITERATIONS = 15
    MAX_INFRA_RETRIES = 3
    WORKSPACE_PATH = "workspace"
    PLAN_KEY = "tdd_plan_queue"


class AgentState(TypedDict):
    # ── Main task data ────────────────────────────────────────────
    specification: str
    requirements: str
    plan: list[str]
    plan_index: int
    current_sub_req: str

    # ── Cloud Sandbox and file persistence ──────────────────────────────
    # Instead of loading code into LangGraph, agents use the E2B Code Interpreter.
    # These fields track the container and the current directory tree.
    sandbox_id: str
    file_system: dict[str, str]

    # ── Per-agent conversation histories ─────────────────────────────────────
    tester_messages: Annotated[list[BaseMessage], add_messages]
    developer_messages: Annotated[list[BaseMessage], add_messages]
    reviewer_messages: Annotated[list[BaseMessage], add_messages]

    # ── Shared audit log ────────────────────────────────────────
    audit_log: Annotated[list[BaseMessage], add_messages]

    # ── Flow control ─────────────────────────────────────────────────────
    iteration: int
    infra_retries: int
    status: str
    max_retries: int
    failed_requirements: list[dict]

    # ── Self-correction metrics ──────────────────────────────────────────────
    total_detected_failures: int
    autonomously_corrected_failures: int
    current_subreq_failures: int
    is_type_fault: str
    test_faults: int
    implementation_faults: int

    # ── Per-sub-requirement metrics ────────────────────────────────────────────
    subreq_success_count: int
    subreq_failure_count: int
    subreq_results: list[dict]
    is_flow_type: list[str]
    


class RequirementsState(TypedDict):
    user_input: str
    user_prompts: list[str]
    conversation_history: str
    current_response: str
    needs_clarification: bool
    has_checklist: bool
    user_confirmed: bool
    final_specification: str
    status: str
    interaction_count: int
    infra_retries: int