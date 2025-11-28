import logging
import re
from app.config import RequirementsState


def node_user_input(state: RequirementsState) -> RequirementsState:
    """Nó do grafo que coleta input do usuário."""
    logging.info("=" * 70)
    logging.info("👤 USER INPUT - Coletando resposta do usuário")
    logging.info("=" * 70)
    
    current_response = state["current_response"]
    print(f"\n[Analista]: {current_response}")
    
    user_response = input("\n[Sua Resposta]: ").strip()
    
    logging.info(f"📝 Resposta do usuário: {user_response}")
    
    # Verificar se é uma confirmação (sim, s, yes, ok, confirm, confirmado)
    confirm_re = re.compile(r"^(sim|s|yes|y|ok|confirm|confirmado)$", re.IGNORECASE)
    is_confirmation = confirm_re.match(user_response)
    
    has_checklist = state["has_checklist"]
    user_confirmed = has_checklist and is_confirmation
    
    logging.info(f"✅ É confirmação: {is_confirmation}")
    logging.info(f"📋 Tinha checklist: {has_checklist}")
    logging.info(f"🎯 Usuário confirmou: {user_confirmed}")
    
    new_state: RequirementsState = {
        **state,
        "user_input": user_response,
        "user_confirmed": user_confirmed,
        "status": "confirmed" if user_confirmed else "continue_analysis"
    }
    
    return new_state
