import os
import logging
from langchain_core.messages import AIMessage

from app.config import AgentState, Config
from app.agents.langgraph.tester import generate_test_for_sub_req

logger = logging.getLogger("TDDOrchestrator")


def node_execute_tester(state: AgentState) -> AgentState:
    """
    Nó TESTER — escreve (ou revisa) o conjunto de testes para o sub-requisito atual.

    Fluxo de mensagens
    ──────────────────
    state["tester_messages"] é o histórico completo de conversa do agente tester.
    Passamos para generate_test_for_sub_req() que anexa os novos turnos Human + AI
    e retorna a lista atualizada. Retornamos *apenas os novos turnos* para que o
    reducer add_messages do LangGraph possa anexá-los ao checkpoint do Postgres
    sem duplicação.
    """
    sub_req = state["current_sub_req"]
    specification = state.get("specification", "")
    status = state.get("status", "")
    is_review_mode = status == "test_review_needed"

    if is_review_mode:
        logger.info("🔧 TESTER: MODO REVISÃO — corrigindo testes potencialmente incorretos")
    else:
        logger.info("✍️  TESTER: Escrevendo testes para o sub-requisito")

    # Extrai o último AIMessage do reviewer como feedback (se houver)
    reviewer_msgs = state.get("reviewer_messages", [])
    feedback = ""
    if is_review_mode and reviewer_msgs:
        for msg in reversed(reviewer_msgs):
            if hasattr(msg, "type") and msg.type == "ai":
                feedback = msg.content
                break

    existing_len = len(state.get("tester_messages", []))
    iteration = state.get("iteration", 0)

    try:
        new_tests, updated_history = generate_test_for_sub_req(
            sub_requirement=sub_req,
            function_name=state.get("function_name", "process"),
            specification=specification,
            all_tests_code=state.get("tests_code", ""),
            feedback=feedback,
            conversation_history=state.get("tester_messages", []),
            is_review_mode=is_review_mode,
        )
    except ValueError as exc:
        # O loop de autocorreção dentro de generate_test_for_sub_req esgotou suas
        # tentativas. Em vez de derrubar todo o grafo, marcamos este sub-requisito
        # como falho e deixamos o evaluator decidir se pula ou aborta.
        logger.error(f"❌ TESTER: falhou em produzir testes válidos após todas as tentativas.\n{exc}")
        audit_entry = AIMessage(
            content=f"[Tester] FALHOU ao gerar testes válidos para '{sub_req}': {exc}"
        )
        return {
            **state,
            "iteration": iteration + 1,
            "status": "tester_failed",
            "audit_log": [audit_entry],
        }

    # Persiste o arquivo de testes em disco
    test_path = os.path.join(Config.WORKSPACE_PATH, Config.TEST_FILE)
    os.makedirs(Config.WORKSPACE_PATH, exist_ok=True)
    with open(test_path, "w", encoding="utf-8") as f:
        f.write(new_tests)

    if is_review_mode:
        iteration += 1

    # Retorna apenas os turnos recém-anexados — o add_messages gerencia a mesclagem
    new_turns = updated_history[existing_len:]

    audit_entry = AIMessage(
        content=f"[Tester] Testes {'revisados' if is_review_mode else 'escritos'} para '{sub_req}'."
    )

    return {
        **state,
        "tests_code": new_tests,
        "iteration": iteration,
        "status": "tests_written",
        "tester_messages": new_turns,
        "audit_log": [audit_entry],
    }