import logging
from app.config import AgentState
from app.agents.langgraph.planner import generate_plan

logger = logging.getLogger("TDDOrchestrator")

def node_plan_task(state: AgentState) -> AgentState:
    logger.info("\n" + "=" * 80)
    logger.info("📋 FASE 1: PLANEJAMENTO (PLANNER)")
    logger.info("=" * 80)
    logger.info(f"📌 Especificação Original: {state['specification'][:100]}...")
    
    plan = generate_plan(state["specification"])
    
    if not plan:
        logger.error("❌ ERRO: O Planner falhou ao gerar o plano de tarefas.")
        return {**state, "status": "plan_failed", "plan": []}
    
    logger.info("-" * 80)
    logger.info(f"✅ PLANO GERADO COM {len(plan)} SUB-REQUISITOS:")
    for i, item in enumerate(plan, 1):
        logger.info(f"   {i}. {item}")
    logger.info("-" * 80)

    new_state: AgentState = {
        **state,
        "plan": plan,
        "plan_index": 0,
        "current_sub_req": plan[0],
        "iteration": 0,
        "status": "planning_complete",
        "messages": [] 
    }
    
    return new_state