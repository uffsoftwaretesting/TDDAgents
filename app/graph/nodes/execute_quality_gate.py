import logging
from langchain_core.messages import AIMessage
from app.agents.langgraph.quality import evaluate_code_quality
from app.config.config import AgentState

logger = logging.getLogger("TDDOrchestrator")

def node_execute_quality_gate(state: AgentState) -> AgentState:
    """Nó final que avalia a qualidade do código produzido antes de encerrar."""
    logger.info("\n" + "=" * 80)
    logger.info("🛡️  FASE FINAL: QUALITY GATE (Auditoria de Código)")
    logger.info("=" * 80)
    
    impl_code = state.get("implementation_code", "")
    spec = state.get("specification", "")
    failed_requirements = state.get("failed_requirements", [])
    status = state.get("status", "")
    
    # Relatório de Falhas (se houver)
    failure_report = ""
    if failed_requirements:
        logger.error("\n" + "🔴" * 40)
        logger.error("⚠️  REQUISITOS QUE FALHARAM:")
        logger.error("=" * 80)
        for idx, failure in enumerate(failed_requirements, 1):
            logger.error(f"\n{idx}. Requisito: {failure['requirement']}")
            logger.error(f"   Índice: {failure['index']}")
            logger.error(f"   Motivo: {failure['reason']}")
            logger.error(f"   Última Iteração: {failure['last_iteration']}")
        logger.error("=" * 80)
        logger.error("🔴" * 40 + "\n")
        
        failure_report = "\n\n--- REQUISITOS QUE FALHARAM ---\n"
        for failure in failed_requirements:
            failure_report += f"\n• {failure['requirement']}\n"
            failure_report += f"  Motivo: {failure['reason']}\n"
            failure_report += f"  Tentativas: {failure['last_iteration']}\n"
    
    logger.info("🔍 Analisando conformidade com a especificação e boas práticas...")
    quality_report = evaluate_code_quality(
        implementation_code=impl_code,
        specification=spec
    )
    
    logger.info("-" * 80)
    logger.info("📋 RELATÓRIO DE QUALIDADE:")
    for line in quality_report.split('\n'):
        logger.info(f"   {line}")
    logger.info("-" * 80)
    
    # Combinar relatórios
    full_report = quality_report
    if failure_report:
        full_report += failure_report
    
    report_message = AIMessage(content=f"Quality Report:\n{full_report}")
    
    # Determinar status final
    if failed_requirements:
        final_status = "completed_with_failures"
        logger.warning(f"\n⚠️  CONCLUSÃO: Projeto finalizado com {len(failed_requirements)} requisito(s) não implementado(s)")
    else:
        final_status = "completed_successfully"
        logger.info("\n✅ CONCLUSÃO: Projeto finalizado com sucesso!")
    
    new_state = {
        **state,
        "status": final_status,
        "messages": [report_message]
    }
    
    return new_state