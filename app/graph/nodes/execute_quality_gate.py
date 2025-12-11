import logging
from app.agents.langgraph.quality import evaluate_code_quality
from app.config import AgentState


def node_execute_quality_gate(state: AgentState) -> AgentState:
    """Nó final que avalia a qualidade do código produzido antes de encerrar."""
    logging.info("=" * 70)
    logging.info("🛡️  FASE FINAL: QUALITY GATE - Avaliação de Qualidade")
    logging.info("=" * 70)
    
    impl_code = state.get("implementation_code", "")
    spec = state.get("specification", "")
    
    logging.info(f"📊 Código a avaliar: {len(impl_code.split('\\n'))} linhas")
    logging.info(f"📝 Especificação: {len(spec.split('\\n'))} linhas")
    
    quality_report = evaluate_code_quality(
        implementation_code=impl_code,
        specification=spec
    )
    
    logging.info("=" * 70)
    logging.info("📋 RELATÓRIO DE QUALIDADE:")
    logging.info("=" * 70)
    for line in quality_report.split('\n'):
        logging.info(line)
    logging.info("=" * 70)
    logging.info("✅ Quality Gate concluído!")
    logging.info("=" * 70)
    
    new_state: AgentState = {
        **state,
        "feedback": quality_report,
        "status": "completed_with_review"
    }
    
    return new_state
