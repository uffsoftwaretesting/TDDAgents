import logging
import time
from app.config.config import Config, RequirementsState
from app.agents.langgraph.analyst import analyze_requirements
from app.errors.agents.handler import handle_llm_exception
from app.errors.exceptions import FatalInfraError, TransientInfraError

logger = logging.getLogger("TDDOrchestrator")

def node_analyst(state: RequirementsState) -> RequirementsState:
    logger.info("=" * 70)
    logger.info("🧠 ANALISTA - Coletando e Analisando Requisitos")
    logger.info("=" * 70)
    
    user_input = state["user_input"]
    conversation_history = state["conversation_history"]
    interaction_count = state.get("interaction_count", 0)
    infra_retries = state.get("infra_retries", 0)

    # Incrementa o contador de interações do usuário
    interaction_count += 1

    try:
        try:
            result = analyze_requirements(user_input, conversation_history)
        except Exception as exc:
            handle_llm_exception(exc, context="Analyst LLM API")
            
    except TransientInfraError as exc:
        infra_retries += 1
        if infra_retries >= Config.MAX_INFRA_RETRIES:
            logger.error(f"❌ ANALISTA: Falha de Infra (Limite Atingido): {exc.original_exc}")
            return {**state, "status": "analyst_failed", "infra_retries": 0}
        
        logger.warning(f"⚠️ ANALISTA: Erro Transiente. Tentativa {infra_retries}/{Config.MAX_INFRA_RETRIES}. Aguardando 3s... ({exc})")
        time.sleep(3)
        return {**state, "status": "infra_error_analyst", "infra_retries": infra_retries}

    except FatalInfraError as exc:
        logger.error(f"❌ ANALISTA: Falha Fatal de Infraestrutura: {exc}")
        return {**state, "status": "analyst_failed", "infra_retries": 0}
    
    logger.info(f"🔍 Precisa esclarecimento: {result['needs_clarification']}")
    logger.info(f"📋 Tem checklist: {result['has_checklist']}")
    
    # Prevenção contra loop infinito do usuário
    if interaction_count >= 5 and not result['has_checklist']:
        logger.warning("🚨 Limite de interações atingido. Sugerindo aprovação.")
        result['response'] += "\n\nJá temos bastantes informações. Aqui está o Checklist de Requisitos atual. Posso prosseguir para a equipe de engenharia?"
        result['has_checklist'] = True
        result['needs_clarification'] = False

    new_history = conversation_history
    if conversation_history:
        new_history += f"\n\n[Usuário]: {user_input}\n[Analista]: {result['response']}"
    else:
        new_history = f"[Usuário]: {user_input}\n[Analista]: {result['response']}"
    
    return {
        **state,
        "conversation_history": new_history,
        "current_response": result['response'],
        "needs_clarification": result['needs_clarification'],
        "has_checklist": result['has_checklist'],
        "interaction_count": interaction_count,
        "status": "awaiting_user" if result['needs_clarification'] or result['has_checklist'] else "analyzing"
    }