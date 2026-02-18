import logging
from langchain_core.messages import RemoveMessage
from app.config import AgentState

logger = logging.getLogger("TDDOrchestrator")

def node_execute_progress_evaluator(state: AgentState) -> AgentState:
    """Nó do grafo que avalia o progresso e decide próximos passos."""
    logger.info("\n" + "=" * 80)
    logger.info("📊 FASE 6: AVALIADOR DE PROGRESSO")
    logger.info("=" * 80)
    
    current_index = state.get("plan_index", 0)
    plan = state.get("plan", [])
    total = len(plan)
    status = state.get("status", "")
    failed_requirements = state.get("failed_requirements", [])
    
    # Verificar se a tarefa atual falhou
    if status == "max_retries_exceeded":
        failure_info = {
            "requirement": plan[current_index],
            "index": current_index,
            "reason": "Excedeu o limite de 10 tentativas",
            "last_iteration": state.get("iteration", 0)
        }
        failed_requirements.append(failure_info)
        logger.error(f"❌ Tarefa FALHOU: {plan[current_index]}")
        logger.error(f"   Motivo: {failure_info['reason']}")
    else:
        # Log de sucesso da tarefa atual
        logger.info(f"✅ Tarefa Concluída: {plan[current_index]}")
    
    logger.info(f"📈 Progresso Geral: {current_index + 1}/{total} ({(current_index + 1)/total*100:.0f}%)")
    
    next_index = current_index + 1
    
    if next_index < total:
        next_req = plan[next_index]
        logger.info("-" * 80)
        logger.info(f"⏭️  PRÓXIMA TAREFA: '{next_req}'")
        logger.info("   🧹 Limpando memória de conversação para novo contexto...")
        
        # Limpa memória
        messages = state.get("messages", [])
        delete_messages = [RemoveMessage(id=m.id) for m in messages if m.id]
        
        new_state = {
            **state,
            "status": "next_req",
            "plan_index": next_index,
            "current_sub_req": next_req,
            "iteration": 0,
            "failed_requirements": failed_requirements,
            "messages": delete_messages 
        }
    else:
        # Determinar status final baseado em falhas
        if failed_requirements:
            logger.warning(f"\n⚠️  PLANO EXECUTADO COM {len(failed_requirements)} FALHA(S)")
            logger.warning("➡️  Encaminhando para Quality Gate com relatório de falhas...")
            final_status = "plan_complete_with_failures"
        else:
            logger.info("\n✅ PLANO EXECUTADO COM SUCESSO!")
            logger.info("➡️  Encaminhando para Quality Gate para validação final...")
            final_status = "plan_complete"
        
        new_state = {
            **state, 
            "status": final_status,
            "failed_requirements": failed_requirements
        }
    
    return new_state