import logging
from typing import cast
from langgraph.graph import StateGraph, END
from app.config import RequirementsState
from app.graph.nodes.requirements_analyst import node_analyst
from app.graph.nodes.requirements_user_input import node_user_input
from app.graph.nodes.requirements_engineer import node_engineer

logger = logging.getLogger("TDDOrchestrator")

class RequirementsOrchestrator:
    """Orquestrador LangGraph para levantamento de requisitos de nível Enterprise."""
    
    def __init__(self):
        self.graph = self._create_graph()
        
    def _create_graph(self):
        workflow = StateGraph(RequirementsState)
        
        workflow.add_node("analyst", node_analyst)
        workflow.add_node("user_input", node_user_input)
        workflow.add_node("engineer", node_engineer)
        
        workflow.set_entry_point("analyst")
        
        def decide_after_analyst(state: RequirementsState) -> str:
            if state.get("needs_clarification") or state.get("has_checklist"):
                return "user_input"
            return "analyst"  
        
        def decide_after_user(state: RequirementsState) -> str:
            if state.get("user_confirmed"):
                return "engineer"
            return "analyst"
            
        workflow.add_conditional_edges(
            "analyst",
            decide_after_analyst,
            {
                "user_input": "user_input",
                "analyst": "analyst"
            }
        )
        
        workflow.add_conditional_edges(
            "user_input", 
            decide_after_user,
            {
                "engineer": "engineer",
                "analyst": "analyst"
            }
        )
        
        workflow.add_edge("engineer", END)
        return workflow.compile()
    
    def run(self, initial_user_input: str) -> RequirementsState:
        logger.info("🚀 Iniciando Orquestração de Requisitos Interativa...")
        
        initial_state: RequirementsState = {
            "user_input": initial_user_input,
            "conversation_history": "",
            "current_response": "",
            "needs_clarification": False,
            "has_checklist": False,
            "user_confirmed": False,
            "final_specification": "",
            "status": "started",
            "interaction_count": 0
        }
        
        # Limite de recursão adicionado para proteger a aplicação de loops infinitos
        config = {"recursion_limit": 50}
        
        final_state = self.graph.invoke(initial_state, config=config)
        return cast(RequirementsState, final_state)