import logging
from e2b_code_interpreter import Sandbox
from e2b import SandboxException
from app.config.config import Config
from app.errors.sandbox.handler import handle_e2b_exception
from app.schema.schema import AgentAction

logger = logging.getLogger("TDDOrchestrator")

def apply_agent_action_to_sandbox(sandbox_id: str, action: AgentAction, current_file_system: dict) -> tuple[dict, str]:
    """
    Applies the structured (Pydantic) action to the sandbox.
    Returns:
        updated_fs (dict): The updated state of the file system.
        execution_logs (str): The stdout/stderr of any executed bash_commands.
    Raises:
        TransientInfraError or FatalInfraError on failure communicating with the Sandbox.
    """
    updated_fs = current_file_system.copy()
    execution_logs = ""

    try:
        # The connection is started by the orchestrator; here we just connect to the active instance
        sandbox = Sandbox.connect(sandbox_id, api_key=Config.E2B_API_KEY)

        # 1. Install Dependencies
        if action.dependencies:
            deps_str = " ".join(action.dependencies)
            logger.info(f"📦 Installing dependencies in the Sandbox: {deps_str}")
            sandbox.commands.run(f"pip install {deps_str}", user="root")

        # 2. Write Files (Models, Routers, Schemas, etc.)
        for file_obj in action.files_to_write:
            logger.info(f"💾 Writing file: {file_obj.filepath}")

            # Ensures the directories exist
            if "/" in file_obj.filepath:
                dir_path = file_obj.filepath.rsplit('/', 1)[0]
                sandbox.commands.run(f"mkdir -p {dir_path}", user="root")

            # Writes the file to the Sandbox
            sandbox.files.write(file_obj.filepath, file_obj.content)
            # Updates LangGraph's state tracker
            updated_fs[file_obj.filepath] = file_obj.content

        # 3. Run Setup Bash Commands (Migrations, environment variables, etc.)
        for cmd in action.bash_commands:
            logger.info(f"🔧 Running command: {cmd}")
            result = sandbox.commands.run(cmd, user="root")
            execution_logs += f"\n$ {cmd}\n{result.stdout}"
            if result.stderr:
                execution_logs += f"\nSTDERR:\n{result.stderr}"

        return updated_fs, execution_logs

    except SandboxException as exc:
        handle_e2b_exception(exc, context="SandboxUtils")
    except Exception as exc:
        handle_e2b_exception(exc, context="Generic SandboxUtils")


def read_all_files_from_state(file_system: dict) -> str:
    """
    Formats the tracked files into a readable string.
    This injects the entire current workspace directly into the LLM prompt.
    """
    if not file_system:
        return "The workspace is currently empty."

    return "\n".join(
        f"--- {filepath} ---\n```python\n{content}\n```\n"
        for filepath, content in file_system.items()
    )