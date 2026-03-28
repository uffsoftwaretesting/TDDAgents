import time
import logging
from app.config.config import Config, RequirementsState
from app.agents.langgraph.engineer import generate_specification
from app.errors.agents.handler import handle_llm_exception
from app.errors.exceptions import TransientInfraError, FatalInfraError

logger = logging.getLogger("TDDOrchestrator")

def node_engineer(state: RequirementsState) -> RequirementsState:
    logger.info("=" * 70)
    logger.info("⚙️ ENGENHEIRO - Consolidando a Especificação")
    logger.info("=" * 70)
    
    conversation_history = state["conversation_history"]
    infra_retries = state.get("infra_retries", 0)
    
    try:
        requirements = conversation_history
        specification = generate_specification(requirements, conversation_history)
            
    except TransientInfraError as exc:
        infra_retries += 1
        if infra_retries >= Config.MAX_INFRA_RETRIES:
            logger.error(f"❌ ENGENHEIRO: Falha de Infra (Limite Atingido): {exc.original_exc}")
            return {**state, "status": "engineer_failed", "infra_retries": 0}
        
        logger.warning(f"⚠️ ENGENHEIRO: Erro Transiente. Tentativa {infra_retries}/{Config.MAX_INFRA_RETRIES}. Aguardando 3s... ({exc.original_exc})")
        time.sleep(3)
        return {**state, "status": "infra_error_engineer", "infra_retries": infra_retries}

    except FatalInfraError as exc:
        logger.error(f"❌ ENGENHEIRO: Falha Fatal de Infraestrutura: {exc.original_exc}")
        return {**state, "status": "engineer_failed", "infra_retries": 0}
    
    except Exception as exc:
        logger.error(f"❌ ENGENHEIRO: Falha Fatal de Infraestrutura")
        return {**state, "status": "engineer_failed", "infra_retries": 0}
    
    return {
        **state,
        "final_specification": specification,
        "infra_retries": 0,
        "status": "specification_ready"
    }