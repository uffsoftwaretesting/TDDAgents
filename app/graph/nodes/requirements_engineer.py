import logging
import re
from app.config import RequirementsState
from app.agents.langgraph.engineer import generate_specification


def node_engineer(state: RequirementsState) -> RequirementsState:
    """Nó do grafo que gera a especificação técnica final."""
    logging.info("=" * 70)
    logging.info("⚙️ ENGENHEIRO - Gerando especificação técnica")
    logging.info("=" * 70)
    
    conversation_history = state["conversation_history"]
    
    logging.info(f"📚 Processando histórico: {len(conversation_history.split('\\n'))} linhas")
    
    # Extrair os requisitos do histórico (última mensagem do analista que contém checklist)
    requirements = ""
    lines = conversation_history.split('\n')
    for i, line in enumerate(lines):
        if "[Analista]:" in line and "===CHECKLIST_END===" in conversation_history[conversation_history.find(line):]:
            # Encontrar o final desta mensagem do analista
            analyst_message = line.replace("[Analista]:", "").strip()
            j = i + 1
            while j < len(lines) and not lines[j].startswith("["):
                analyst_message += "\n" + lines[j]
                j += 1
            requirements = analyst_message
            break
    
    if not requirements:
        requirements = conversation_history  # fallback
    
    logging.info(f"📋 Requisitos extraídos: {len(requirements)} caracteres")
    
    specification = generate_specification(requirements)
    
    # Extrair nome da função da especificação
    function_name = "generated_function"
    for line in specification.split('\n'):
        if line.strip().startswith("#"):
            parts = line.replace("#", "").strip().split()
            if parts:
                function_name = parts[0].lower().replace(" ", "_")
                break
    
    logging.info(f"🎯 Nome da função detectado: {function_name}")
    logging.info(f"📄 Especificação gerada: {len(specification)} caracteres")
    
    new_state: RequirementsState = {
        **state,
        "final_specification": specification,
        "function_name": function_name,
        "status": "specification_ready"
    }
    
    return new_state
