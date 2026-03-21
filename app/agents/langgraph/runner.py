import logging
from e2b_code_interpreter import Sandbox
from app.config import Config

logger = logging.getLogger("TDDOrchestrator")

def run_pytest_in_sandbox(sandbox_id: str, test_path: str = ".") -> tuple[str, bool]:
    """
    Conecta à Sandbox E2B ativa e executa o pytest garantindo a captura completa dos logs.
    Identifica tanto retornos normais quanto exceções da SDK do E2B, extraindo o traceback completo.
    """
    logger.info(f"🏃 RUNNER: Executando testes na Sandbox {sandbox_id[:8]}...")
    
    try:
        sandbox = Sandbox.connect(sandbox_id, api_key=Config.E2B_API_KEY)
        
        # Garante APENAS a ferramenta básica de teste.
        sandbox.commands.run(
            "python -c 'import pytest' || pip install pytest", 
            timeout=60
        )
        
        # Comando fornecido: ultra-verboso para fornecer contexto máximo ao Developer e Reviewer
        cmd = (
            f"PYTHONPATH=. python -m pytest {test_path} "
            f"-vv -rA --tb=long --showlocals -s "
            f"-o asyncio_default_fixture_loop_scope=function -o asyncio_mode=auto"
        )
        
        # Aumentamos o timeout, pois tracebacks longos e logs detalhados (-rA / -s) podem levar mais tempo
        execution = sandbox.commands.run(cmd, timeout=120)
        
        output_str = ""
        if execution.stdout:
            output_str += f"{execution.stdout}\n"
        if execution.stderr:
            output_str += f"\nSTDERR:\n{execution.stderr}\n"
            
        # Algumas versões/comandos do E2B retornam erros de sistema na propriedade 'error'
        if hasattr(execution, 'error') and execution.error:
            output_str += f"\nEXECUTION ERROR:\n{execution.error}\n"

        if not output_str.strip():
            output_str = "ERRO: Nenhum output retornado pelo comando pytest."

        # A execução foi um sucesso se o exit_code for estritamente 0
        is_success = getattr(execution, 'exit_code', 1) == 0

        return output_str.strip(), is_success
        
    except Exception as e:
        # Aqui lidamos com o caso onde a SDK do E2B lança ativamente uma exceção 
        # (seja por Timeouts, falha de rede, ou se a SDK lançar exceção em exit_code != 0)
        logger.error(f"❌ Exceção da SDK do E2B capturada: {type(e).__name__} - {e}")
        
        # O pulo do gato: tentar extrair stdout e stderr diretamente do objeto da exceção
        # Isso evita perder o traceback gigante caso a exceção seja de falha de comando.
        stdout = getattr(e, 'stdout', '')
        stderr = getattr(e, 'stderr', '')
        
        error_msg = f"EXCEÇÃO DA INFRAESTRUTURA/E2B ({type(e).__name__}): {str(e)}"
        
        # Se conseguimos extrair os logs de dentro da exceção, nós os anexamos ao Reviewer
        if stdout or stderr:
            error_msg += "\n\n--- LOGS DA EXECUÇÃO ANTES DA EXCEÇÃO ---"
            if stdout:
                error_msg += f"\nSTDOUT:\n{stdout}"
            if stderr:
                error_msg += f"\nSTDERR:\n{stderr}"
                
        return error_msg.strip(), False