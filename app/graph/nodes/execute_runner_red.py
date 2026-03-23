import logging
from langchain_core.messages import HumanMessage, AIMessage

from app.config import AgentState, Config
from app.agents.langgraph.runner import run_pytest_in_sandbox
from app.agents.langgraph.reviewer import analyze_failures
from app.utils.prompt_loader import load_prompt
from app.utils.sandbox_utils import read_all_files_from_state

logger = logging.getLogger("TDDOrchestrator")


def node_execute_runner_red(state: AgentState) -> AgentState:
    sub_req = state["current_sub_req"]
    iteration = state.get("iteration", 1)
    max_retries_state = state.get("max_retries", Config.MAX_ITERATIONS)

    logger.info("\n" + "-" * 80)
    logger.info(f"🔴 FASE 3: RUNNER RED (Verificação de Falha) | Iteração {iteration}/{max_retries_state}")
    logger.info("-" * 80)

    # 1. Executa os testes na E2B Sandbox ativa
    output, is_success = run_pytest_in_sandbox(sandbox_id=state["sandbox_id"])
    has_failures = not is_success

    existing_reviewer_len = len(state.get("reviewer_messages", []))
    audit_entries: list = []

    if has_failures:
        logger.info("✅ SUCESSO (RED CONFIRMADO): O teste falhou como esperado.")
        logger.info("   ➡️  Executando análise de confirmação...")

        # Puxa o código atual do file_system para contexto do Reviewer
        current_codebase = read_all_files_from_state(state.get("file_system", {}))

        analysis, updated_reviewer_history = analyze_failures(
            test_output=output,
            specification=state["specification"],
            sub_requirement=sub_req,
            iteration=iteration,
            max_retries=max_retries_state,
            current_code=current_codebase, 
            conversation_history=state.get("reviewer_messages", []),
        )

        logger.info("\n" + "=" * 80)
        logger.info("🧐 ANÁLISE DO REVIEWER (CONFIRMAÇÃO RED)")
        logger.info("=" * 80)
        for line in analysis.split('\n'):
            logger.info(f"   {line}")
        logger.info("=" * 80 + "\n")

        new_reviewer_turns = updated_reviewer_history[existing_reviewer_len:]

        if "[ERRO NO TESTE]" in analysis:
            if iteration >= max_retries_state:
                logger.error(f"   ⛔ Limite de tentativas excedido no Tester ({max_retries_state}). Abortando.")
                status = "max_retries_exceeded"
                next_iteration = iteration
            else:
                status = "test_review_needed"
                logger.warning("   ⚠️ O teste falhou de forma inválida. Devolvendo ao Tester.")
                next_iteration = iteration + 1
        else:
            status = "red_confirmed"
            next_iteration = iteration

        audit_entries.append(
            AIMessage(content=f"[RunnerRed] Red confirmado para '{sub_req}'. Status: {status}.")
        )

        return {
            **state,
            "status": status,
            "iteration": next_iteration,
            "reviewer_messages": new_reviewer_turns,
            "audit_log": audit_entries,
        }

    else:
        # ── GREEN NO RED: Forçamos a ida ao Developer para garantir o ciclo ───
        logger.warning("⚠️  ALERTA: O teste PASSOU imediatamente (Green no Red).")
        logger.info("   ℹ️  Avançando — enviando alerta para o Developer inspecionar.")

        # Carrega o template de alerta
        feedback_text = load_prompt(
            'agents/langgraph/orchestrator/feedback_existing_implementation.jinja2',
            attempt=1,
        )

        audit_entries.append(
            AIMessage(
                content=f"[RunnerRed] Teste passou imediatamente para '{sub_req}'. Alerta enviado ao Developer."
            )
        )

        # O LangGraph fará o append automático nessa lista e o Developer a lerá como o `feedback` atual.
        alert_message = HumanMessage(content=feedback_text)

        return {
            **state,
            "status": "red_confirmed", 
            "iteration": iteration, 
            "reviewer_messages": [alert_message], #
            "audit_log": audit_entries,
        }