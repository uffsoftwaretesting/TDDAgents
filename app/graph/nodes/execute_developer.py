import os
import logging
from langchain_core.messages import AIMessage

from app.config import AgentState, Config
from app.agents.langgraph.developer import generate_code_incremental

logger = logging.getLogger("TDDOrchestrator")


def node_execute_developer(state: AgentState) -> AgentState:
    """
    Nó DEVELOPER — gera ou corrige a implementação.

    Fluxo de mensagens
    ──────────────────
    state["developer_messages"] contém a *conversa completa* que este agente teve
    até agora (system prompt + todos os turnos Human/AI). Passamos diretamente para
    generate_code_incremental(), que anexa o novo turno Human e a resposta AI do LLM,
    e retorna a lista atualizada.

    O reducer add_messages do LangGraph anexará apenas os *novos* turnos (os
    retornados por este nó) ao que já está armazenado no checkpoint do Postgres —
    o histórico nunca é duplicado nem perdido, mesmo que o processo seja reiniciado
    no meio de uma execução.
    """
    sub_req = state["current_sub_req"]
    specification = state.get("specification", "")
    iteration = state.get("iteration", 0) + 1
    max_retries = state.get("max_retries", 10)

    logger.info("\n" + "=" * 80)
    logger.info(f"💻 FASE 4: DEVELOPER (IMPLEMENTAÇÃO) | Iteração {iteration}/{max_retries}")
    logger.info("=" * 80)
    logger.info(f"🎯 Objetivo: Implementar '{sub_req}'")

    # ── Extrai o feedback da última análise do reviewer ───────────────────────
    # O reviewer grava sua análise em state["reviewer_messages"] como turnos AI.
    # Extraímos o mais recente para passar como dica de feedback.
    # O LLM do developer também verá seu próprio raciocínio anterior via seu
    # histórico de conversa — não é necessário reconstruir manualmente um log.
    reviewer_msgs = state.get("reviewer_messages", [])
    feedback = ""
    if reviewer_msgs:
        # Percorre de trás para frente para encontrar o último AIMessage do reviewer
        for msg in reversed(reviewer_msgs):
            if hasattr(msg, "type") and msg.type == "ai":
                feedback = msg.content
                break

    if iteration < 3:
        logger.info("   🧠 Estratégia de contexto: CURTA (apenas última análise do reviewer)")
    else:
        logger.info("   🧠 Estratégia de contexto: COMPLETA (developer vê seu próprio histórico de conversa)")

    # ── Gera o código passando o histórico real de conversa ───────────────────
    new_code, updated_dev_history = generate_code_incremental(
        test_code=state["tests_code"],
        function_name=state.get("function_name", "process"),
        specification=specification,
        feedback=feedback,
        previous_code=state.get("implementation_code", ""),
        conversation_history=state.get("developer_messages", []),
    )

    # ── Persiste em disco ─────────────────────────────────────────────────────
    impl_path = os.path.join(Config.WORKSPACE_PATH, f"{Config.IMPLEMENTATION_MODULE}.py")
    os.makedirs(Config.WORKSPACE_PATH, exist_ok=True)
    with open(impl_path, "w", encoding="utf-8") as f:
        f.write(new_code)

    logger.info(f"💾 Implementação salva ({len(new_code.splitlines())} linhas).")

    # ── Registra no audit log ─────────────────────────────────────────────────
    audit_entry = AIMessage(
        content=f"[Developer] Tentativa #{iteration}: código escrito para '{sub_req}'."
    )

    # IMPORTANTE: retornamos apenas as *novas* mensagens anexadas neste turno.
    # O add_messages vai mesclá-las com o histórico existente no Postgres.
    # updated_dev_history é a lista COMPLETA; fatiamos a porção pré-existente.
    existing_len = len(state.get("developer_messages", []))
    new_turns = updated_dev_history[existing_len:]

    return {
        **state,
        "implementation_code": new_code,
        "iteration": iteration,
        "status": "code_written",
        # add_messages anexa new_turns ao que já está no Postgres
        "developer_messages": new_turns,
        "audit_log": [audit_entry],
    }