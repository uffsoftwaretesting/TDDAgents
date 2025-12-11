import os
import logging
from dotenv import load_dotenv

# --- LangGraph Orchestrator Imports ---
from app.requirements_orchestrator import RequirementsOrchestrator
from app.orchestrator import TDDOrchestrator 

# ==============================================================================
# 🔧 CONFIGURAÇÃO DE LOGS
# ==============================================================================
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("autogen_core").setLevel(logging.ERROR)
logging.getLogger("autogen_agentchat").setLevel(logging.ERROR)

logger = logging.getLogger("MeuApp")
logger.setLevel(logging.INFO)

def run_requirements_gathering() -> str:
    """Executa o levantamento de requisitos usando LangGraph."""
    load_dotenv()
    
    print("\n" + "="*60)
    print("🤖 INICIANDO LEVANTAMENTO DE REQUISITOS (LANGGRAPH)")
    print("="*60)
    print("Por favor, descreva o que você gostaria de implementar:")
    
    initial_input = input("\n[Sua Solicitação]: ").strip()
    
    if not initial_input:
        raise ValueError("Solicitação inicial não pode estar vazia")
    
    # Usar LangGraph para levantamento de requisitos
    requirements_orchestrator = RequirementsOrchestrator()
    final_state = requirements_orchestrator.run(initial_input)
    
    return final_state["final_specification"]

def main():
    try:
        refined_spec = run_requirements_gathering()
    except KeyboardInterrupt:
        print("\nOperação cancelada pelo usuário.")
        return

    if not refined_spec:
        logger.error("❌ Falha ao gerar especificação ou fluxo interrompido.")
        return

    print("\n" + "="*60)
    print("🔄 INICIANDO FASE TDD COM LANGGRAPH")
    print("="*60)
    print(f"📜 Especificação Gerada:\n{refined_spec[:200]}...\n(truncada para visualização)")
    
    # Extrair função do estado final
    function_name = "generated_function"
    for line in refined_spec.split('\n'):
        if line.strip().startswith("#"):
            parts = line.replace("#", "").strip().split()
            if parts:
                function_name = parts[0].lower().replace(" ", "_")
                break
    
    logger.info(f"Nome da função detectado para TDD: {function_name}")

    # Fase de LangGraph (Execução TDD)
    orchestrator = TDDOrchestrator()
    
    final_state = orchestrator.run(
        specification=refined_spec,
        function_name=function_name,
        resume=False
    )
    
    if final_state.get("status") == "plan_complete":
        print("\n✅ Fluxo Completo com Sucesso!")
    else:
        print(f"\n⚠️ O fluxo terminou com status: {final_state.get('status')}")

if __name__ == "__main__":
    main()
