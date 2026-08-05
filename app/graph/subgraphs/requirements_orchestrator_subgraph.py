import logging
from typing import cast, Literal
from langgraph.graph import StateGraph, END

from app.config.config import RequirementsState
from app.graph.nodes.requirements_analyst import node_analyst
from app.graph.nodes.requirements_user_input import node_user_input
from app.graph.nodes.requirements_engineer import node_engineer

logger = logging.getLogger("TDDOrchestrator")

class RequirementsOrchestrator:
    """LangGraph orchestrator for requirements gathering."""
    
    def __init__(self):
        self.graph = self._create_graph()
        
    def _create_graph(self):
        workflow = StateGraph(RequirementsState)
        
        workflow.add_node("analyst", node_analyst)
        workflow.add_node("user_input", node_user_input)
        workflow.add_node("engineer", node_engineer)
        
        workflow.set_entry_point("analyst")
        
        def router_after_analyst(state: RequirementsState) -> Literal["user_input", "analyst", END]:
            status = state.get("status")
            
            if status == "infra_error_analyst":
                return "analyst"
            if status == "analyst_failed":
                return END
                
            if state.get("needs_clarification") or state.get("has_checklist"):
                return "user_input"
            return "analyst"  
        
        def router_after_user(state: RequirementsState) -> Literal["engineer", "analyst"]:
            if state.get("user_confirmed"):
                return "engineer"
            return "analyst"
            
        def router_after_engineer(state: RequirementsState) -> Literal["engineer", END]:
            status = state.get("status")
            
            if status == "infra_error_engineer":
                return "engineer"
                
            return END
            
        workflow.add_conditional_edges("analyst", router_after_analyst)
        workflow.add_conditional_edges("user_input", router_after_user)
        workflow.add_conditional_edges("engineer", router_after_engineer)
        
        return workflow.compile()
    
    def run(self, initial_user_input: str) -> RequirementsState:
        logger.info("\n🚀 Starting Interactive Requirements Orchestration...")
        
        initial_state: RequirementsState = {
            "user_input": initial_user_input,
            "user_prompts": [initial_user_input],
            "conversation_history": "",
            "current_response": "",
            "needs_clarification": False,
            "has_checklist": False,
            "user_confirmed": False,
            "final_specification": "",
            "status": "started",
            "interaction_count": 0,
            "infra_retries": 0
        }
        
        config = {"recursion_limit": 50}
        
        final_state = self.graph.invoke(initial_state, config=config)
        return cast(RequirementsState, final_state)