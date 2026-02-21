import logging
from langchain_core.messages import AIMessage, RemoveMessage

from app.config import AgentState

logger = logging.getLogger("TDDOrchestrator")

# Status que indicam que o sub-requisito atual não pôde ser concluído.
_FAILURE_STATUSES = {"max_retries_exceeded", "tester_failed"}

# Motivos legíveis para cada status de falha.
_FAILURE_REASONS = {
    "max_retries_exceeded": "Excedeu o número máximo de tentativas do developer (10).",
    "tester_failed":        "O Tester não conseguiu gerar testes válidos após todas as tentativas de autocorreção.",
}


def _clear_agent_histories(state: AgentState) -> dict:
    """
    Retorna um dict de estado parcial que zera todos os históricos de conversa
    dos agentes para o próximo sub-requisito.

    POR QUE USAR RemoveMessage AQUI:
    RemoveMessage opera em campos anotados com add_messages por ID de mensagem.
    É a ferramenta certa quando queremos um reset total — o tester, developer e
    reviewer devem iniciar uma conversa completamente nova para o próximo
    sub-requisito, sem nenhuma memória do anterior. A forma mais limpa de fazer
    isso com add_messages é emitir um RemoveMessage para cada ID de mensagem
    existente, fazendo o reducer esvaziar a lista.

    O audit_log é intencionalmente NÃO limpo — ele é o registro persistente de
    tudo que aconteceu em todos os sub-requisitos.
    """
    removals: dict[str, list] = {
        "tester_messages": [],
        "developer_messages": [],
        "reviewer_messages": [],
    }

    for field in removals:
        msgs = state.get(field, [])
        removals[field] = [RemoveMessage(id=m.id) for m in msgs if getattr(m, "id", None)]

    return removals


def node_execute_progress_evaluator(state: AgentState) -> AgentState:
    """
    Nó avaliador de progresso — executado após cada sub-ciclo TDD.

    Responsabilidades
    ─────────────────
    1. Registrar sucesso ou falha do sub-requisito recém-concluído.
    2. Avançar o plan_index e preparar o estado para o próximo sub-requisito, OU
       determinar o status final quando o plano estiver esgotado.
    3. Resetar os históricos de conversa dos agentes entre sub-requisitos para que
       cada agente comece com contexto limpo.
    4. Preservar o audit_log em todos os sub-requisitos.
    """
    logger.info("\n" + "=" * 80)
    logger.info("📊 FASE 6: AVALIADOR DE PROGRESSO")
    logger.info("=" * 80)

    current_index = state.get("plan_index", 0)
    plan = state.get("plan", [])
    total = len(plan)
    status = state.get("status", "")
    failed_requirements: list[dict] = list(state.get("failed_requirements", []))
    current_req = plan[current_index] if plan else ""

    # ── 1. Avalia o resultado do sub-requisito atual ───────────────────────────
    audit_entries: list = []

    if status in _FAILURE_STATUSES:
        reason = _FAILURE_REASONS.get(status, f"Status de falha desconhecido: '{status}'")
        failure_info = {
            "requirement": current_req,
            "index": current_index,
            "status": status,
            "reason": reason,
            "last_iteration": state.get("iteration", 0),
        }
        failed_requirements.append(failure_info)

        logger.error(f"❌ Sub-requisito FALHOU: '{current_req}'")
        logger.error(f"   Motivo : {reason}")
        logger.error(f"   Status : {status}")

        audit_entries.append(
            AIMessage(
                content=(
                    f"[Evaluator] FALHOU '{current_req}' "
                    f"(status={status}): {reason}"
                )
            )
        )
    else:
        logger.info(f"✅ Sub-requisito concluído: '{current_req}'")
        audit_entries.append(
            AIMessage(content=f"[Evaluator] Concluído '{current_req}'.")
        )

    logger.info(
        f"📈 Progresso geral: {current_index + 1}/{total} "
        f"({(current_index + 1) / total * 100:.0f}%)"
    )

    # ── 2. Avança ou finaliza ─────────────────────────────────────────────────
    next_index = current_index + 1

    if next_index < total:
        # ── Ainda há sub-requisitos a processar ───────────────────────────────
        next_req = plan[next_index]

        logger.info("-" * 80)
        logger.info(f"⏭️  PRÓXIMA TAREFA: '{next_req}'")
        logger.info("   🧹 Resetando históricos de conversa dos agentes para novo contexto...")

        # Constrói as listas de remoção para cada campo de histórico dos agentes.
        history_resets = _clear_agent_histories(state)

        audit_entries.append(
            AIMessage(
                content=(
                    f"[Evaluator] Avançando para o sub-requisito {next_index + 1}/{total}: "
                    f"'{next_req}'. Históricos dos agentes limpos."
                )
            )
        )

        return {
            **state,
            "status": "next_req",
            "plan_index": next_index,
            "current_sub_req": next_req,
            "iteration": 0,
            "red_attempts": 0,
            "failed_requirements": failed_requirements,
            # Históricos dos agentes: as entradas RemoveMessage esvaziam cada lista
            # via o reducer add_messages. O audit_log é mantido intocado.
            **history_resets,
            # Anexa as entradas de auditoria desta etapa de avaliação.
            "audit_log": audit_entries,
        }

    else:
        # ── Plano esgotado ────────────────────────────────────────────────────
        if failed_requirements:
            n = len(failed_requirements)
            logger.warning(f"\n⚠️  PLANO CONCLUÍDO COM {n} FALHA(S).")
            logger.warning("➡️  Encaminhando para o Quality Gate com relatório de falhas...")
            final_status = "plan_complete_with_failures"
        else:
            logger.info("\n✅ PLANO CONCLUÍDO COM SUCESSO.")
            logger.info("➡️  Encaminhando para o Quality Gate para validação final...")
            final_status = "plan_complete"

        audit_entries.append(
            AIMessage(
                content=(
                    f"[Evaluator] Plano finalizado. Status: {final_status}. "
                    f"Requisitos com falha: {len(failed_requirements)}/{total}."
                )
            )
        )

        return {
            **state,
            "status": final_status,
            "failed_requirements": failed_requirements,
            "audit_log": audit_entries,
        }