import logging
import re
import sys
from app.config import RequirementsState

logger = logging.getLogger("TDDOrchestrator")

def node_user_input(state: RequirementsState) -> RequirementsState:
    """Nó do grafo que interage com o usuário de forma robusta e flexível."""
    logger.info("=" * 70)
    logger.info("👤 USER INPUT - Aguardando feedback do usuário")
    logger.info("=" * 70)
    
    current_response = state["current_response"]
    
    # Exibe a resposta do Analista com formatação clara
    print(f"\n🤖 [Analista de Requisitos]:\n{current_response}\n")
    
    if state.get("has_checklist"):
        print("💡 DICA: O Analista gerou o Checklist Final.")
        print("   - Digite 'sim', 'ok' ou 'aprovado' para prosseguir para a Engenharia.")
        print("   - Ou digite as alterações que deseja fazer (ex: 'Faltou a validação X').")
    else:
        print("💡 DICA: O Analista precisa de mais detalhes.")
        print("   - Responda à pergunta para ajudar a definir o escopo.")

    try:
        user_response = input("\n👤 [Sua Resposta] (ou '/sair'): ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n👋 Operação cancelada pelo usuário.")
        sys.exit(0)
        
    if user_response.lower() in ['/sair', '/exit', '/quit']:
        print("\n👋 Saindo do levantamento de requisitos...")
        sys.exit(0)
        
    logger.info(f"📝 Resposta do usuário: {user_response}")
    
    # ── Lógica Robusta de Confirmação ─────────────────────────────────────────
    is_confirmation = False
    
    if state.get("has_checklist"):
        # Uma regex mais permissiva, mas que exige que a resposta seja curta e exata.
        # Se o usuário escrever um parágrafo (ex: "sim, mas adicione o JWT"), o regex falha
        # de propósito, para que a nova regra seja enviada de volta ao Analista!
        positive_pattern = r"^(sim|s|yes|y|ok|confirmado|pode prosseguir|aprovado|perfeito|beleza|manda bala)$"
        if re.match(positive_pattern, user_response.lower()):
            is_confirmation = True

    new_state: RequirementsState = {
        **state,
        "user_input": user_response,
        "user_confirmed": is_confirmation,
        "status": "confirmed" if is_confirmation else "continue_analysis"
    }
    
    # SEGREDO DA FLEXIBILIDADE: Se tínhamos um checklist, mas o usuário não confirmou
    # (ex: ele fez um adendo ou pediu alteração), forçamos o has_checklist para False.
    # Isso avisa ao Analista que o escopo foi reaberto para uma nova análise.
    if state.get("has_checklist") and not is_confirmation:
        logger.info("🔄 Usuário solicitou alterações no checklist. Reabrindo análise.")
        new_state["has_checklist"] = False
        
    return new_state