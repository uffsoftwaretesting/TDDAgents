import logging
from e2b_code_interpreter import Sandbox
from app.config import Config
from app.schema.schema import AgentAction

logger = logging.getLogger("TDDOrchestrator")

def apply_agent_action_to_sandbox(sandbox_id: str, action: AgentAction, current_file_system: dict) -> tuple[dict, str]:
    """
    Aplica a ação estruturada (Pydantic) à sandbox.
    Retorna:
        updated_fs (dict): O estado atualizado do sistema de arquivos (file system).
        execution_logs (str): O stdout/stderr de quaisquer bash_commands executados.
    """
    sandbox = Sandbox.connect(sandbox_id, api_key=Config.E2B_API_KEY)
    updated_fs = current_file_system.copy()
    execution_logs = ""
    
    # 1. Instalar Dependências
    if action.dependencies:
        deps_str = " ".join(action.dependencies)
        logger.info(f"📦 Instalando dependências na Sandbox: {deps_str}")
        sandbox.commands.run(f"pip install {deps_str}")

    # 2. Escrever Arquivos (Models, Routers, Schemas, etc.)
    for file_obj in action.files_to_write:
        logger.info(f"💾 Escrevendo arquivo: {file_obj.filepath}")
        
        # Garante que os diretórios existam
        if "/" in file_obj.filepath:
            dir_path = file_obj.filepath.rsplit('/', 1)[0]
            sandbox.commands.run(f"mkdir -p {dir_path}")
            
        # Escreve o arquivo na Sandbox
        sandbox.files.write(file_obj.filepath, file_obj.content)
        # Atualiza o rastreador de estado do LangGraph
        updated_fs[file_obj.filepath] = file_obj.content

    # 3. Executar Comandos Bash de Configuração (Migrations, variáveis de ambiente, etc.)
    for cmd in action.bash_commands:
        logger.info(f"🔧 Executando comando: {cmd}")
        result = sandbox.commands.run(cmd, timeout=60)
        execution_logs += f"\n$ {cmd}\n{result.stdout}"
        if result.stderr:
            execution_logs += f"\nSTDERR:\n{result.stderr}"
            
    return updated_fs, execution_logs

def read_all_files_from_state(file_system: dict) -> str:
    """
    Formata os arquivos rastreados em uma string legível.
    Isso injeta o workspace atual inteiro diretamente no prompt do LLM.
    """
    if not file_system:
        return "O workspace está vazio no momento."
    
    return "\n".join(
        f"--- {filepath} ---\n```python\n{content}\n```\n" 
        for filepath, content in file_system.items()
    )