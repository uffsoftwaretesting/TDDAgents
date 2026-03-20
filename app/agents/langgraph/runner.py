import logging
from e2b_code_interpreter import Sandbox
from app.config import Config

logger = logging.getLogger("TDDOrchestrator")

def run_pytest_in_sandbox(sandbox_id: str, test_path: str = ".") -> tuple[str, bool]:
    """
    Conecta à Sandbox E2B ativa e executa o pytest com variáveis de ambiente preparadas para frameworks.
    Como a sandbox é persistente, os arquivos gravados pelo Developer/Tester já estão presentes no container.
    """
    logger.info(f"🏃 RUNNER: Executando testes na Sandbox {sandbox_id[:8]}...")
    
    try:
        sandbox = Sandbox.connect(sandbox_id, api_key=Config.E2B_API_KEY)
        
        # Garantir APENAS a ferramenta básica de teste.
        # Os agentes devem pedir explicitamente (via 'dependencies') se precisarem de pytest-asyncio ou pytest-mock.
        sandbox.commands.run(
            "python -c 'import pytest' || pip install pytest", 
            timeout=60
        )
        
        # Executar o pytest. 
        # PYTHONPATH=. garante que imports absolutos (como 'from app.crud import...') funcionem perfeitamente.
        execution = sandbox.commands.run(
            f"PYTHONPATH=. python -m pytest {test_path} -v --tb=short --no-header -p no:warnings",
            timeout=60
        )
        
        output = execution.stdout
        if execution.stderr:
            output += f"\n\nSTDERR:\n{execution.stderr}"
            
        output_str = output.strip() if output.strip() else "ERRO: Nenhum output retornado pelo pytest."

        is_success = (execution.exit_code == 0)

        return output_str, is_success
        
    except Exception as e:
        logger.error(f"❌ Erro ao executar pytest na sandbox: {e}")
        return f"ERRO FATAL DE EXECUÇÃO: {e}", False