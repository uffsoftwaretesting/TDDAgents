import logging
from langchain_core.messages import HumanMessage, AIMessage

from app.config import AgentState
from app.agents.langgraph.runner import run_pytest
from app.agents.langgraph.reviewer import analyze_failures

logger = logging.getLogger("TDDOrchestrator")


def node_execute_runner_green(state: AgentState, max_retries: int = 10) -> AgentState:
    """
    Nó RUNNER GREEN — valida que todos os testes passam após a tentativa do developer.

    Fluxo de mensagens
    ──────────────────
    Em caso de falha, chamamos analyze_failures() com state["reviewer_messages"] para
    que o LLM do reviewer veja todas as suas análises anteriores como histórico real
    de conversa. Retornamos apenas os *novos* turnos para que o add_messages anexe
    em vez de substituir.
    """
    sub_req = state["current_sub_req"]
    iteration = state.get("iteration", 0)
    max_retries_state = state.get("max_retries", max_retries)

    logger.info("\n" + "-" * 80)
    logger.info(f"🟢 FASE 5: RUNNER GREEN (Validação) | Tentativa {iteration}/{max_retries_state}")
    logger.info("-" * 80)

    output = run_pytest()
    all_passed = (
        "passed" in output.lower()
        and "failed" not in output.lower()
        and "error" not in output.lower()
    )

    if all_passed:
        logger.info("✅ SUCESSO: Todos os testes passaram! 🚀")
        audit = AIMessage(content=f"[RunnerGreen] Todos os testes passaram para '{sub_req}' na iteração {iteration}.")
        return {
            **state,
            "status": "green_passed",
            "iteration": 0,
            "audit_log": [audit],
        }

    # ── Falha nos testes ──────────────────────────────────────────────────────
    logger.warning("❌ FALHA: Testes não passaram.")

    existing_reviewer_len = len(state.get("reviewer_messages", []))

    analysis, updated_reviewer_history = analyze_failures(
        test_output=output,
        specification=state["specification"],
        sub_requirement=sub_req,
        iteration=iteration,
        max_retries=max_retries_state,
        current_code=state.get("implementation_code", ""),
        test_code=state.get("tests_code", ""),
        conversation_history=state.get("reviewer_messages", []),
    )

    logger.info("\n" + "=" * 80)
    logger.info(f"🧐 ANÁLISE DO REVIEWER — Iteração {iteration}")
    logger.info("=" * 80)
    for line in analysis.split('\n'):
        logger.info(f"   {line}")
    logger.info("=" * 80 + "\n")

    new_reviewer_turns = updated_reviewer_history[existing_reviewer_len:]

    # ── Decisão de roteamento ─────────────────────────────────────────────────
    extra_audit: list = []

    if iteration in (6, 9):
        logger.warning(f"   ⚠️  Iteração crítica ({iteration}): solicitando REVISÃO DE TESTES ao Tester.")
        status = "test_review_needed"
        extra_audit.append(
            HumanMessage(
                content=f"[RunnerGreen] Iteração {iteration}: falha persistente — escalando para o Tester revisar os testes."
            )
        )
    elif iteration >= max_retries_state:
        logger.error(f"   ⛔  Limite de tentativas excedido ({max_retries_state}). Abortando.")
        status = "max_retries_exceeded"
    else:
        logger.info("   🔄  Retornando ao Developer para correção.")
        status = "green_failed"

    audit_entry = AIMessage(
        content=f"[RunnerGreen] Iteração {iteration}: {status}. Análise do reviewer armazenada em reviewer_messages."
    )

    return {
        **state,
        "status": status,
        # Apenas os novos turnos do reviewer — o add_messages mescla com o estado do Postgres
        "reviewer_messages": new_reviewer_turns,
        "audit_log": [audit_entry] + extra_audit,
    }