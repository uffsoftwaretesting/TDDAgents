import os
from typing import Annotated, Any, TypedDict
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

    # ── Sandbox lifecycle ────────────────────────────────────────────────────
    # The E2B SDK defaults to a 300s sandbox lifetime, which is far shorter than a
    # real run. The adapter creates with SANDBOX_TIMEOUT and slides the window
    # forward with set_timeout() whenever SANDBOX_REFRESH_INTERVAL has elapsed, so a
    # run's total length is unbounded while the sandbox still dies promptly if the
    # process crashes. 3600 is the cap on an E2B Hobby account.
    SANDBOX_TIMEOUT = 3600
    SANDBOX_REFRESH_INTERVAL = 600

    # ── Command execution ────────────────────────────────────────────────────
    # E2B's commands.run() defaults to timeout=60, which silently truncates long
    # pip installs and test suites. Both values below are adapter defaults and can
    # be overridden per call.
    COMMAND_TIMEOUT = 300
    TEST_TIMEOUT = 600

    # ── Workspace roots ──────────────────────────────────────────────────────
    # Every workspace-relative path resolves against these. Pinning the sandbox root
    # explicitly is what makes the path contract in app/workspace/base.py a
    # guarantee rather than an accident of the E2B default working directory.
    SANDBOX_WORKSPACE_ROOT = "/home/user"
    LOCAL_WORKSPACE_ROOT = ".tddagents/runs"

    # ── Tool layer ───────────────────────────────────────────────────────────
    # A tool result larger than that tool's max_result_chars is written here inside the
    # sandbox and returned as a head/tail preview plus the path. It is a workspace-relative
    # path so ReadFile reaches it with no special case, and it lives under .tddagents/ so
    # the sync exclusion below keeps tooling scratch out of workspace_output_*.
    TOOL_RESULTS_DIR = ".tddagents/tool_results"

    # Concurrency cap for a batch of concurrency-safe tool calls, matching claude-code's
    # own default. Results are re-sorted into the model's original call order afterwards,
    # so parallelism never makes a run's message list or event log unreproducible.
    MAX_TOOL_CONCURRENCY = 10

    # Default wall-clock budget for one hook process. Overridable per hook via `timeout`.
    HOOK_TIMEOUT = 60.0

    # ── Web tools ────────────────────────────────────────────────────────────
    # WebSearch and WebFetch self-disable when this is unset, so the offline suite and
    # anyone without a key simply never sees them in a resolved toolbelt.
    TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

    # ── Sync ─────────────────────────────────────────────────────────────────
    # The sync engine prefers a .gitignore found inside the generated workspace and
    # falls back to this list when there is none. ".git" is excluded unconditionally
    # in either case — mirroring a repository into itself is never intended.
    SYNC_EXCLUDE_FALLBACK = [
        ".tddagents/",
        "__pycache__/",
        "*.pyc",
        ".pytest_cache/",
        ".mypy_cache/",
        ".venv/",
        "venv/",
        "node_modules/",
        ".coverage",
        "htmlcov/",
    ]


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
    failed_requirements: list[dict[str, Any]]

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
    subreq_results: list[dict[str, Any]]
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
