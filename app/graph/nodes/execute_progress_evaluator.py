import logging
from app.config import AgentState


def node_execute_progress_evaluator(state: AgentState) -> AgentState:
    """Nó do grafo que avalia o progresso e decide próximos passos."""
    logging.info("=" * 70)
    logging.info("♻️  FASE 6: PROGRESS EVALUATOR - Verificando progresso atual do plano TDD")
    logging.info("=" * 70)
    
    current_index = state["plan_index"]
    next_index = current_index + 1
    plan = state["plan"]
    total = len(plan)
    
    logging.info(f"✅ Sub-requisito [{current_index + 1}/{total}] COMPLETO!")
    
    if next_index < total:
        next_req = plan[next_index]
        logging.info(f"⏭️  Avançando para o próximo sub-requisito [{next_index + 1}/{total}]")
        logging.info(f"📝 Próximo: '{next_req}'")
        logging.info("=" * 70)
        
        new_state = {
            **state,
            "status": "next_req",
            "plan_index": next_index,
            "current_sub_req": next_req,
            "feedback": "",
            "iteration": 0
        }
    else:
        logging.info("=" * 70)
        logging.info("🎉 🎉 🎉 PLANO COMPLETO! 🎉 🎉 🎉")
        logging.info(f"✅ Todos os {total} sub-requisitos foram implementados!")
        logging.info("✅ Todos os testes passam cumulativamente!")
        logging.info("=" * 70)
        new_state: AgentState = {**state, "status": "plan_complete"}
    
    return new_state
