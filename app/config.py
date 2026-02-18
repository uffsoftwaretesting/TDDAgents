import os
import operator
from typing import TypedDict, Annotated, List, Optional, Union
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, AnyMessage
from langgraph.graph.message import add_messages

load_dotenv()

if not os.getenv("OPENAI_API_KEY"):
    raise Exception("No token configured")

class Config:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    PROMPTS_DIR = os.path.join(os.path.dirname(__file__), './prompts')
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    MODEL = "gpt-4o-mini"
    MAX_ITERATIONS = 12
    WORKSPACE_PATH = "workspace"

    IMPLEMENTATION_MODULE = "app_code"
    TEST_FILE = "test_app.py"
    PLAN_KEY = "tdd_plan_queue"

class AgentState(TypedDict):
    specification: str
    function_name: str 
    plan: list[str]
    plan_index: int
    current_sub_req: str
    
    tests_code: str
    implementation_code: str
    
    messages: Annotated[list[BaseMessage], add_messages]
    
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