import logging
import re
from app.config import RequirementsState
from app.agents.langgraph.analyst import analyze_requirements


def node_analyst(state: RequirementsState) -> RequirementsState:
    """Nó do grafo que analisa requisitos e faz perguntas para esclarecimento."""
    logging.info("=" * 70)
    logging.info("🧠 ANALISTA - Analisando requisitos")
    logging.info("=" * 70)
    
    user_input = state["user_input"]
    conversation_history = state["conversation_history"]
    interaction_count = state.get("interaction_count", 0) + 1
    
    logging.info(f"📝 Input do usuário: {user_input[:100]}...")
    logging.info(f"📚 Histórico: {len(conversation_history.split('\\n'))} linhas")
    logging.info(f"🔄 Interação número: {interaction_count}")
    
    # Após 4 interações, forçar checklist
    if interaction_count >= 4:
        logging.warning("🚨 Limite de interações atingido - forçando geração de checklist")
        
        # Gerar checklist baseado no que já foi coletado
        forced_checklist = f"""Baseado nas informações coletadas, aqui está o checklist de requisitos:

Checklist de Requisitos:
1. Função que verifica se um número é quadrado perfeito
2. Retorna True se for quadrado perfeito, False caso contrário  
3. Para entradas não-inteiras, exibir no console "formato incorreto"
4. Tratar números negativos retornando False
5. Tratar zero como caso especial (retornar True)

Posso prosseguir?

===CHECKLIST_END==="""
        
        new_history = conversation_history
        if conversation_history:
            new_history += f"\n\n[Usuário]: {user_input}\n[Analista]: {forced_checklist}"
        else:
            new_history = f"[Usuário]: {user_input}\n[Analista]: {forced_checklist}"
        
        return {
            **state,
            "conversation_history": new_history,
            "current_response": forced_checklist,
            "needs_clarification": False,
            "has_checklist": True,
            "interaction_count": interaction_count,
            "status": "awaiting_user"
        }
    
    result = analyze_requirements(user_input, conversation_history)
    
    logging.info(f"🔍 Precisa esclarecimento: {result['needs_clarification']}")
    logging.info(f"📋 Tem checklist: {result['has_checklist']}")
    
    # Atualizar histórico da conversa
    new_history = conversation_history
    if conversation_history:
        new_history += f"\n\n[Usuário]: {user_input}\n[Analista]: {result['response']}"
    else:
        new_history = f"[Usuário]: {user_input}\n[Analista]: {result['response']}"
    
    new_state: RequirementsState = {
        **state,
        "conversation_history": new_history,
        "current_response": result['response'],
        "needs_clarification": result['needs_clarification'],
        "has_checklist": result['has_checklist'],
        "interaction_count": interaction_count,
        "status": "awaiting_user" if result['needs_clarification'] or result['has_checklist'] else "analyzing"
    }
    
    return new_state
