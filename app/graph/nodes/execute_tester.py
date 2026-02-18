import os
import logging
from langchain_core.messages import AIMessage, HumanMessage
from app.config import AgentState, Config
from app.agents.langgraph.tester import generate_test_for_sub_req

logger = logging.getLogger("TDDOrchestrator")

def node_execute_tester(state: AgentState) -> AgentState:
    sub_req = state["current_sub_req"]
    specification = state.get("specification", "")
    messages = state.get("messages", [])
    status = state.get("status", "")
    
    is_review_mode = status == "test_review_needed"
    
    context = ""
    if is_review_mode and messages:
        logger.info("🔧 TESTER: Running in REVIEW MODE (Fixing potentially bad tests)")
        context = "\n".join([m.content for m in messages])
    
    new_tests = generate_test_for_sub_req(
        sub_requirement=sub_req,
        function_name=state.get("function_name", "process"),
        specification=specification,
        all_tests_code=state.get("tests_code", ""),
        feedback=context 
    )
    
    test_path = os.path.join(Config.WORKSPACE_PATH, Config.TEST_FILE)
    with open(test_path, "w", encoding="utf-8") as f:
        f.write(new_tests)
        
    iteration = state.get("iteration", 0)
    if is_review_mode:
        iteration += 1
        
    return {
        **state,
        "tests_code": new_tests,
        "iteration": iteration,
        "status": "tests_written",
        "messages": [AIMessage(content=f"Tests updated for '{sub_req}'")]
    }