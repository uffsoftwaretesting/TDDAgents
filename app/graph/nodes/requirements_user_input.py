import logging
import sys

from app.config.config import RequirementsState

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
        print("   - Digite '/sim' para prosseguir para a Engenharia.")
        print("   - Ou digite as alterações que deseja fazer (ex: 'Faltou a validação X').")
    else:
        print("💡 DICA: O Analista precisa de mais detalhes.")
        print("   - Responda à pergunta para ajudar a definir o escopo.")

    try:
        user_response = input("\n👤 [Sua Resposta] (ou '/sair'): ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n👋 Operação cancelada pelo usuário.")
        sys.exit(0)

    if user_response.lower() in ["/sair", "/exit", "/quit"]:
        print("\n👋 Saindo do levantamento de requisitos...")
        sys.exit(0)

    logger.info(f"📝 Resposta do usuário: {user_response}")

    user_prompts = list(state.get("user_prompts", []))
    user_prompts.append(user_response)

    # Confirmação estática e explícita: apenas '/sim' confirma.
    is_confirmation = bool(state.get("has_checklist")) and user_response.lower() == "/sim"

    new_state: RequirementsState = {
        **state,
        "user_input": user_response,
        "user_prompts": user_prompts,
        "user_confirmed": is_confirmation,
        "status": "confirmed" if is_confirmation else "continue_analysis",
    }

    # Se havia checklist e o usuário não confirmou, reabre a análise.
    if state.get("has_checklist") and not is_confirmation:
        logger.info("🔄 Usuário solicitou alterações no checklist. Reabrindo análise.")
        new_state["has_checklist"] = False

    return new_state