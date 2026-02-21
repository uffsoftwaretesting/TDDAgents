import logging
from langchain_core.messages import HumanMessage, AIMessage

from app.config import AgentState
from app.agents.langgraph.runner import run_pytest
from app.agents.langgraph.reviewer import analyze_failures
from app.utils.prompt_loader import load_prompt

logger = logging.getLogger("TDDOrchestrator")


def node_execute_runner_red(state: AgentState, max_retries: int = 10) -> AgentState:
    """
    Nó RUNNER RED — confirma que o teste recém-escrito realmente FALHA antes de
    passar para o Developer (esta é a etapa 'Red' do ciclo Red/Green/Refactor).

    Fluxo de mensagens
    ──────────────────
    Se o teste falhar como esperado (red confirmado), chamamos analyze_failures()
    com state["reviewer_messages"] para que o reviewer acumule contexto ao longo
    de todo o ciclo TDD para este sub-requisito. Apenas os novos turnos são
    retornados para que o add_messages possa anexar corretamente.
    """
    sub_req = state["current_sub_req"]
    iteration = state.get("iteration", 0)
    red_attempts = state.get("red_attempts", 0)

    logger.info("\n" + "-" * 80)
    logger.info(f"🔴 FASE 3: RUNNER RED (Verificação de Falha) | Tentativa {red_attempts + 1}")
    logger.info("-" * 80)

    output = run_pytest()
    has_failures = "failed" in output.lower() or "error" in output.lower()

    existing_reviewer_len = len(state.get("reviewer_messages", []))
    audit_entries: list = []

    if has_failures:
        logger.info("✅ SUCESSO (RED CONFIRMADO): O teste falhou como esperado.")
        logger.info("   ➡️  Executando análise de confirmação...")

        analysis, updated_reviewer_history = analyze_failures(
            test_output=output,
            specification=state["specification"],
            sub_requirement=sub_req,
            iteration=iteration,
            max_retries=state.get("max_retries", max_retries),
            current_code=state.get("implementation_code", ""),
            test_code=state.get("tests_code", ""),
            conversation_history=state.get("reviewer_messages", []),
        )

        logger.info("\n" + "=" * 80)
        logger.info("🧐 ANÁLISE DO REVIEWER (CONFIRMAÇÃO RED)")
        logger.info("=" * 80)
        for line in analysis.split('\n'):
            logger.info(f"   {line}")
        logger.info("=" * 80 + "\n")

        new_reviewer_turns = updated_reviewer_history[existing_reviewer_len:]

        audit_entries.append(
            AIMessage(content=f"[RunnerRed] Red confirmado para '{sub_req}'. Análise do reviewer armazenada.")
        )

        return {
            **state,
            "status": "red_confirmed",
            "red_attempts": 0,
            "reviewer_messages": new_reviewer_turns,
            "audit_log": audit_entries,
        }

    else:
        logger.warning("⚠️  ALERTA: O teste PASSOU imediatamente (Green no Red).")
        logger.info("   ℹ️  Avançando — tratando como red_confirmed.")

        feedback_text = load_prompt(
            'agents/langgraph/orchestrator/feedback_existing_implementation.jinja2',
            attempt=1,
        )

        audit_entries.append(
            HumanMessage(
                content=f"[RunnerRed] Teste passou imediatamente para '{sub_req}'. {feedback_text}"
            )
        )

        # Sem chamada ao reviewer — apenas avança para que o developer possa refinar
        return {
            **state,
            "status": "red_confirmed",
            "red_attempts": 0,
            "audit_log": audit_entries,
        }