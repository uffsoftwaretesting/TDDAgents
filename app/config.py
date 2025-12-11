import os
from typing import TypedDict
from dotenv import load_dotenv

load_dotenv()

if not os.getenv("OPENAI_API_KEY"):
    raise Exception("No token configured")

class Config:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    PROMPTS_DIR = os.path.join(os.path.dirname(__file__), './prompts')
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    MODEL = "gpt-4o-mini"
    MAX_ITERATIONS = 10  # Aumentado para o ciclo incremental
    WORKSPACE_PATH = "workspace"

    IMPLEMENTATION_MODULE = "app_code"  # Nome do arquivo de implementação (app_code.py)
    TEST_FILE = "test_app.py"          # Nome do arquivo de teste
    # Chave para armazenar o estado do plano no Redis
    PLAN_KEY = "tdd_plan_queue"

class AgentState(TypedDict):
    specification: str
    function_name: str 
    plan: list[str]
    current_sub_req: str
    tests_code: str
    implementation_code: str
    feedback: str
    iteration: int
    plan_index: int
    status: str
    max_retries: int
    red_attempts: int

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
