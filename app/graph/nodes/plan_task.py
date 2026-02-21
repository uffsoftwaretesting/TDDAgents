import logging
from langchain_core.messages import AIMessage

from app.config import AgentState
from app.agents.langgraph.planner import generate_plan

logger = logging.getLogger("TDDOrchestrator")


def node_plan_task(state: AgentState) -> AgentState:
    logger.info("\n" + "=" * 80)
    logger.info("📋 FASE 1: PLANEJAMENTO (PLANNER)")
    logger.info("=" * 80)
    logger.info(f"📌 Especificação: {state['specification'][:100]}...")

    plan = generate_plan(state["specification"])

    if not plan:
        logger.error("❌ ERRO: O Planner falhou ao gerar o plano de tarefas.")
        return {
            **state,
            "status": "plan_failed",
            "plan": [],
            "audit_log": [AIMessage(content="[Planner] Falha ao gerar o plano de tarefas.")],
        }

    logger.info("-" * 80)
    logger.info(f"✅ PLANO GERADO COM {len(plan)} SUB-REQUISITOS:")
    for i, item in enumerate(plan, 1):
        logger.info(f"   {i}. {item}")
    logger.info("-" * 80)

    plan_summary = "\n".join(f"{i+1}. {item}" for i, item in enumerate(plan))
    audit_entry = AIMessage(
        content=f"[Planner] Plano gerado com {len(plan)} sub-requisitos:\n{plan_summary}"
    )

    return {
        **state,
        "plan": plan,
        "plan_index": 0,
        "current_sub_req": plan[0],
        "iteration": 0,
        "red_attempts": 0,
        "status": "planning_complete",
        # Os históricos dos agentes começam vazios — nenhuma memória deve ser
        # carregada para o primeiro sub-requisito. Já são [] no initial_state,
        # mas ser explícito aqui torna o plan_task seguro mesmo em retomadas parciais.
        "tester_messages": [],
        "developer_messages": [],
        "reviewer_messages": [],
        "audit_log": [audit_entry],
    }