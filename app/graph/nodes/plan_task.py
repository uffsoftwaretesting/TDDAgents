import logging
from app.config import AgentState
from app.agents.langgraph.planner import generate_plan


def node_plan_task(state: AgentState) -> AgentState:
    """Nó do grafo que gera o plano de sub-requisitos TDD."""
    logging.info("=" * 70)
    logging.info("🧠 FASE 1: PLANNER - Gerando plano de sub-requisitos TDD")
    logging.info("=" * 70)
    
    plan = generate_plan(state["specification"])
    
    if not plan:
        logging.error("❌ Planner falhou ao gerar o plano.")
        return {**state, "status": "plan_failed", "plan": []}
    
    logging.info(f"✅ Plano TDD gerado com {len(plan)} sub-requisitos:")
    for idx, step in enumerate(plan, 1):
        logging.info(f"  {idx}. {step}")
    
    new_state: AgentState = {
        **state,
        "plan": plan,
        "plan_index": 0,
        "current_sub_req": plan[0],
        "iteration": 0,
        "status": "planning_complete"
    }
    
    return new_state
