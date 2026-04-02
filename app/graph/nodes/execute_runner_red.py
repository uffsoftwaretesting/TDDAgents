import logging
import time
from langchain_core.messages import HumanMessage, AIMessage

from app.config.config import AgentState, Config
from app.agents.langgraph.runner import run_pytest_in_sandbox
from app.agents.langgraph.reviewer import analyze_failures
from app.errors.exceptions import FatalInfraError, TransientInfraError
from app.utils.prompt_loader import load_prompt
from app.utils.sandbox_utils import read_all_files_from_state

logger = logging.getLogger("TDDOrchestrator")


def node_execute_runner_red(state: AgentState) -> AgentState:
    sub_req = state["current_sub_req"]
    iteration = state.get("iteration", 1)
    max_retries_state = state.get("max_retries", Config.MAX_ITERATIONS)
    infra_retries = state.get("infra_retries", 0)

    logger.info("\n" + "-" * 80)
    logger.info(f"🔴 FASE 3: RUNNER RED (Verificação de Falha) | Iteração {iteration}/{max_retries_state}")
    logger.info("-" * 80)

    # 1. Executa os testes na E2B Sandbox ativa
    
    try:
        output, is_success = run_pytest_in_sandbox(sandbox_id=state["sandbox_id"], is_red_phase=True)

    except TransientInfraError as exc:
        infra_retries += 1
        if infra_retries >= Config.MAX_INFRA_RETRIES:
            logger.error(f"❌ Falha de Infra (Limite de Retries Atingido): {exc.original_exc}")
            return {**state, "status": "sandbox_failed", "infra_retries": 0}
        
        logger.warning(f"⚠️ Erro de Infra Transiente. Tentativa {infra_retries}. Aguardando 3s... ({exc.original_exc})")
        time.sleep(3)
        return {**state, "status": "infra_error_red", "infra_retries": infra_retries}

    except FatalInfraError as exc:
        logger.error(f"❌ Falha Fatal de Infraestrutura: {exc.original_exc}")
        return {**state, "status": "sandbox_failed", "infra_retries": 0}
    
    except Exception as exc:
        logger.error(f"❌ RUNNER RED: Erro inesperado")
        return {**state, "status": "sandbox_failed", "infra_retries": 0}

    has_failures = not is_success

    existing_reviewer_len = len(state.get("reviewer_messages", []))
    audit_entries: list = []

    if has_failures:
        logger.info("✅ SUCESSO (RED CONFIRMADO): O teste falhou como esperado.")
        logger.info("   ➡️  Executando análise de confirmação...")

        # Puxa o código atual do file_system para contexto do Reviewer
        current_codebase = read_all_files_from_state(state.get("file_system", {}))

        try:
            analysis, updated_reviewer_history = analyze_failures(
                test_output=output,
                specification=state["specification"],
                sub_requirement=sub_req,
                iteration=iteration,
                max_retries=max_retries_state,
                current_code=current_codebase,
                conversation_history=state.get("reviewer_messages", []),
            )
        except TransientInfraError as exc:
            infra_retries += 1
            if infra_retries >= Config.MAX_INFRA_RETRIES:
                logger.error(f"❌ RUNNER RED: Falha de Infra no Reviewer (Limite Atingido): {exc.original_exc}")
                return {**state, "status": "sandbox_failed", "infra_retries": 0}

            logger.warning(
                f"⚠️ RUNNER RED: Erro transiente no Reviewer. "
                f"Tentativa {infra_retries}/{Config.MAX_INFRA_RETRIES}. Aguardando 3s... ({exc.original_exc})"
            )
            time.sleep(3)
            return {**state, "status": "infra_error_red", "infra_retries": infra_retries}

        except FatalInfraError as exc:
            logger.error(f"❌ RUNNER RED: Falha fatal no Reviewer: {exc.original_exc}")
            return {**state, "status": "sandbox_failed", "infra_retries": 0}

        except Exception as exc:
            logger.error("❌ RUNNER RED: Erro inesperado durante análise do Reviewer")
            return {**state, "status": "sandbox_failed", "infra_retries": 0}

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
            "infra_retries": 0,
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
            "infra_retries": 0,
            "reviewer_messages": [alert_message],
            "audit_log": audit_entries,
        }