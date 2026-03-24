import logging
import time
from langchain_core.messages import HumanMessage, AIMessage

from app.config import AgentState, Config
from app.agents.langgraph.runner import run_pytest_in_sandbox
from app.agents.langgraph.reviewer import analyze_failures
from app.errors.exceptions import FatalInfraError, TransientInfraError
from app.utils.sandbox_utils import read_all_files_from_state

logger = logging.getLogger("TDDOrchestrator")


def node_execute_runner_green(state: AgentState) -> AgentState:
    sub_req = state["current_sub_req"]
    iteration = state.get("iteration", 1)
    max_retries_state = state.get("max_retries", Config.MAX_ITERATIONS)
    infra_retries = state.get("infra_retries", 0)

    logger.info("\n" + "-" * 80)
    logger.info(f"🟢 FASE 5: RUNNER GREEN (Validação) | Iteração {iteration}/{max_retries_state}")
    logger.info("-" * 80)

    try:
        # 1. Executa os testes na E2B Sandbox ativa desempacotando a tupla
        output, is_success = run_pytest_in_sandbox(sandbox_id=state["sandbox_id"])

    except TransientInfraError as exc:
        infra_retries += 1
        if infra_retries >= Config.MAX_INFRA_RETRIES:
            logger.error(f"❌ RUNNER GREEN: Falha de Infra (Limite Atingido): {exc.original_exc}")
            return {**state, "status": "sandbox_failed", "infra_retries": 0}
        
        logger.warning(f"⚠️ RUNNER GREEN: Erro Transiente. Tentativa {infra_retries}/{Config.MAX_INFRA_RETRIES}. Aguardando 3s... ({exc})")
        time.sleep(3)
        return {**state, "status": "infra_error_green", "infra_retries": infra_retries}

    except FatalInfraError as exc:
        logger.error(f"❌ RUNNER GREEN: Falha Fatal de Infraestrutura: {exc.original_exc}")
        return {**state, "status": "sandbox_failed", "infra_retries": 0}
    
    all_passed = is_success

    if all_passed:
        logger.info("✅ SUCESSO: Todos os testes passaram! 🚀")
        audit = AIMessage(content=f"[RunnerGreen] Todos os testes passaram para '{sub_req}' na iteração {iteration}.")
        return {
            **state,
            "status": "green_passed",
            "iteration": iteration,
            "audit_log": [audit],
            "infra_retries": 0,
        }

    # ── Falha nos testes ──────────────────────────────────────────────────────
    logger.warning("❌ FALHA: Testes não passaram.")

    existing_reviewer_len = len(state.get("reviewer_messages", []))
    
    # Injeta a base de código atual inteira para o Reviewer
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
    logger.info(f"🧐 ANÁLISE DO REVIEWER — Iteração {iteration}")
    logger.info("=" * 80)
    for line in analysis.split('\n'):
        logger.info(f"   {line}")
    logger.info("=" * 80 + "\n")

    new_reviewer_turns = updated_reviewer_history[existing_reviewer_len:]
    extra_audit: list = []

    # ── Decisão de Roteamento e Lógica de Incremento ──
    if "[ERRO NO TESTE]" in analysis:
        logger.warning(f"   ⚠️  Reviewer identificou falha no teste. Solicitando REVISÃO ao Tester.")
        status = "test_review_needed"
        extra_audit.append(
            HumanMessage(content=f"[RunnerGreen] Iteração {iteration}: Reviewer apontou erro no código de teste. Escalando para Tester.")
        )
        next_iteration = iteration + 1  # Incrementa porque volta pro Tester
        
    elif iteration >= max_retries_state:
        logger.error(f"   ⛔  Limite de tentativas excedido ({max_retries_state}). Abortando.")
        status = "max_retries_exceeded"
        next_iteration = iteration
        
    else:
        logger.info("   🔄  Retornando ao Developer para correção da implementação.")
        status = "green_failed"
        next_iteration = iteration + 1  # Incrementa porque volta pro Developer

    audit_entry = AIMessage(
        content=f"[RunnerGreen] Iteração {iteration}: {status}. Análise armazenada."
    )

    return {
        **state,
        "status": status,
        "iteration": next_iteration,
        "infra_retries": 0,
        "reviewer_messages": new_reviewer_turns,
        "audit_log": [audit_entry] + extra_audit,
    }