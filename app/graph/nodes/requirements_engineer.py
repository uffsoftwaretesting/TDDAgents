import logging
import re
from app.config import RequirementsState
from app.agents.langgraph.engineer import generate_specification


def node_engineer(state: RequirementsState) -> RequirementsState:
    logging.info("=" * 70)
    logging.info("⚙️ ENGENHEIRO - Gerando especificação técnica")
    logging.info("=" * 70)
    
    conversation_history = state["conversation_history"]
    
    logging.info(f"📚 Processando histórico: {len(conversation_history)} caracteres")
    
    requirements = ""
    
    if "Checklist de Requisitos" in conversation_history and "===CHECKLIST_END===" in conversation_history:
        last_checklist_start = conversation_history.rfind("Checklist de Requisitos")
        last_checklist_end = conversation_history.rfind("===CHECKLIST_END===")
        
        if last_checklist_start != -1 and last_checklist_end != -1:
            requirements = conversation_history[last_checklist_start:last_checklist_end + len("===CHECKLIST_END===")]
            logging.info(f"✅ Checklist extraído com sucesso: {len(requirements)} caracteres")
        else:
            logging.warning("⚠️ Não foi possível extrair checklist - usando histórico completo")
            requirements = conversation_history
    else:
        logging.warning("⚠️ Checklist não encontrado - usando histórico completo")
        requirements = conversation_history
    
    logging.info(f"📋 Requisitos extraídos: {requirements[:200]}...")
    
    specification = generate_specification(requirements, conversation_history)
    
    function_name = _extract_function_name(specification)
    
    logging.info(f"🎯 Nome da função/sistema detectado: {function_name}")
    logging.info(f"📄 Especificação gerada: {len(specification)} caracteres")
    
    new_state: RequirementsState = {
        **state,
        "final_specification": specification,
        "function_name": function_name,
        "status": "specification_ready"
    }
    
    return new_state


def _extract_function_name(specification: str) -> str:
    """Extrai o nome da função ou sistema da especificação."""
    function_name = "generated_function"
    for line in specification.split('\n'):
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("##"):
            name = stripped.lstrip("#").strip()
            if name:
                name = re.sub(r'[^a-zA-Z0-9_\s]', '', name)
                name = re.sub(r'\s+', '_', name).strip('_').lower()
                if name:
                    function_name = name
                    break
    return function_name