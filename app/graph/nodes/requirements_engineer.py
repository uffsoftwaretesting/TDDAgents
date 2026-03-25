import logging
from app.config.config import RequirementsState
from app.agents.langgraph.engineer import generate_specification

logger = logging.getLogger("TDDOrchestrator")

def node_engineer(state: RequirementsState) -> RequirementsState:
    logger.info("=" * 70)
    logger.info("⚙️ ENGENHEIRO - Consolidando a Especificação")
    logger.info("=" * 70)
    
    conversation_history = state["conversation_history"]
    
    # O modelo lida muito bem com o histórico inteiro, sem precisar de substring regex
    requirements = conversation_history
    specification = generate_specification(requirements, conversation_history)
    
    return {
        **state,
        "final_specification": specification,
        "status": "specification_ready"
    }