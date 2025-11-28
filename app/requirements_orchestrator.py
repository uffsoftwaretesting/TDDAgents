import logging
from typing import cast
from langgraph.graph import StateGraph, END
from app.config import RequirementsState
from app.graph.nodes.requirements_analyst import node_analyst
from app.graph.nodes.requirements_user_input import node_user_input
from app.graph.nodes.requirements_engineer import node_engineer


class RequirementsOrchestrator:
    """Orquestrador LangGraph para levantamento de requisitos."""
    
    def __init__(self):
        self.graph = self._create_graph()
        
    def _create_graph(self):
        """Cria o grafo LangGraph para levantamento de requisitos."""
        workflow = StateGraph(RequirementsState)
        
        # Adicionar nós
        workflow.add_node("analyst", node_analyst)
        workflow.add_node("user_input", node_user_input)
        workflow.add_node("engineer", node_engineer)
        
        # Definir ponto de entrada
        workflow.set_entry_point("analyst")
        
        # Definir arestas condicionais
        def decide_after_analyst(state: RequirementsState) -> str:
            """Decide o próximo passo após o analista."""
            if state["needs_clarification"] or state["has_checklist"]:
                return "user_input"
            return "analyst"  # Continue analisando
        
        def decide_after_user(state: RequirementsState) -> str:
            """Decide o próximo passo após input do usuário."""
            if state["user_confirmed"]:
                return "engineer"
            return "analyst"  # Voltar para mais análise
        
        # Adicionar arestas
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
        
        # Engenheiro sempre termina
        workflow.add_edge("engineer", END)
        
        return workflow.compile()
    
    def run(self, initial_user_input: str) -> RequirementsState:
        """
        Executa o fluxo de levantamento de requisitos.
        
        Args:
            initial_user_input: Input inicial do usuário
            
        Returns:
            RequirementsState: Estado final com especificação gerada
        """
        logging.info("🚀 Iniciando levantamento de requisitos:")
        
        initial_state: RequirementsState = {
            "user_input": initial_user_input,
            "conversation_history": "",
            "current_response": "",
            "needs_clarification": False,
            "has_checklist": False,
            "user_confirmed": False,
            "final_specification": "",
            "function_name": "",
            "status": "started",
            "interaction_count": 0
        }
        
        final_state = self.graph.invoke(initial_state)
        return cast(RequirementsState, final_state)
