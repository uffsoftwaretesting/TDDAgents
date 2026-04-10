import logging
from langchain_core.messages import AIMessage, RemoveMessage

from app.config.config import AgentState

logger = logging.getLogger("TDDOrchestrator")

_FAILURE_STATUSES = {
    "max_retries_exceeded", 
    "tester_failed", 
    "developer_failed",
    "sandbox_failed"
}

_FAILURE_REASONS = {
    "max_retries_exceeded": "Excedeu o número máximo de tentativas do ciclo TDD.",
    "tester_failed":        "O Tester falhou catastroficamente (erro de execução ou API).",
    "developer_failed":     "O Developer falhou catastroficamente (erro de execução ou API).",
    "sandbox_failed":       "A execução falhou por erro fatal ou instabilidade contínua na E2B/API."
}


def _clear_agent_histories(state: AgentState) -> dict:
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
    logger.info("\n" + "=" * 80)
    logger.info("📊 FASE 6: AVALIADOR DE PROGRESSO")
    logger.info("=" * 80)

    current_index = state.get("plan_index", 0)
    plan = state.get("plan", [])
    total = len(plan)
    status = state.get("status", "")
    failed_requirements: list[dict] = list(state.get("failed_requirements", []))
    current_req = plan[current_index] if plan else ""

    audit_entries: list = []

    # 1. Verifica se falhou (incluindo falha de sandbox/infra)
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
                content=f"[Evaluator] FALHOU '{current_req}' (status={status}): {reason}"
            )
        )

        if status == "sandbox_failed":
            logger.warning("🛑 Erro fatal de infraestrutura. Abortando o fluxo TDD.")
            return {
                **state,
                "status": "sandbox_failed", 
                "failed_requirements": failed_requirements,
                "audit_log": audit_entries,
            }
        elif status == "developer_failed":
            logger.warning("🛑 Erro fatal de infraestrutura ao chamar o Developer. Abortando o fluxo TDD.")
            return {
                **state,
                "status": "developer_failed", 
                "failed_requirements": failed_requirements,
                "audit_log": audit_entries,
            }
        elif status == "tester_failed":
            logger.warning("🛑 Erro fatal de infraestrutura ao chamar o Tester. Abortando o fluxo TDD.")
            return {
                **state,
                "status": "tester_failed", 
                "failed_requirements": failed_requirements,
                "audit_log": audit_entries,
            }
        elif status == "max_retries_exceeded":
            logger.warning("Excedeu o número máximo de tentativas para este sub-requisito. Abortando o fluxo TDD.")

    else:
        logger.info(f"✅ Sub-requisito concluído: '{current_req}'")
        audit_entries.append(
            AIMessage(content=f"[Evaluator] Concluído '{current_req}'.")
        )

    logger.info(
        f"📈 Progresso geral: {current_index + 1}/{total} "
        f"({(current_index + 1) / total * 100:.0f}%)"
    )

    next_index = current_index + 1

    if next_index < total:
        next_req = plan[next_index]

        logger.info("-" * 80)
        logger.info(f"⏭️  PRÓXIMA TAREFA: '{next_req}'")
        logger.info("   🧹 Resetando históricos de conversa dos agentes para novo contexto...")

        history_resets = _clear_agent_histories(state)

        audit_entries.append(
            AIMessage(
                content=f"[Evaluator] Avançando para o sub-requisito {next_index + 1}/{total}: '{next_req}'. Históricos limpos."
            )
        )

        return {
            **state,
            "status": "next_req",
            "plan_index": next_index,
            "current_sub_req": next_req,
            "iteration": 1,
            "failed_requirements": failed_requirements,
            **history_resets,
            "audit_log": audit_entries,
        }

    else:
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
                content=f"[Evaluator] Plano finalizado. Status: {final_status}. Requisitos com falha: {len(failed_requirements)}/{total}."
            )
        )

        return {
            **state,
            "status": final_status,
            "failed_requirements": failed_requirements,
            "audit_log": audit_entries,
        }