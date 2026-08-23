import logging

from app.sandbox.adapter import E2BAdapter
from app.errors.sandbox.handler import handle_workspace_exception
from app.schema.schema import AgentAction
from app.workspace.base import WorkspaceError, WorkspaceTransportError

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
        adapter = E2BAdapter.connect(sandbox_id)

        # 1. Install Dependencies
        if action.dependencies:
            deps_str = " ".join(action.dependencies)
            logger.info(f"📦 Installing dependencies in the Sandbox: {deps_str}")
            _run_or_raise(adapter, f"pip install {deps_str}")

        # 2. Write Files (Models, Routers, Schemas, etc.)
        for file_obj in action.files_to_write:
            logger.info(f"💾 Writing file: {file_obj.filepath}")

            # The adapter's single-file write creates missing parent directories, so the
            # explicit `mkdir -p` this loop used to run is no longer needed.
            adapter.write(file_obj.filepath, file_obj.content)
            # Updates LangGraph's state tracker
            updated_fs[file_obj.filepath] = file_obj.content

        # 3. Run Setup Bash Commands (Migrations, environment variables, etc.)
        for cmd in action.bash_commands:
            logger.info(f"🔧 Running command: {cmd}")
            result = _run_or_raise(adapter, cmd)
            execution_logs += f"\n$ {cmd}\n{result.stdout}"
            if result.stderr:
                execution_logs += f"\nSTDERR:\n{result.stderr}"

        return updated_fs, execution_logs

    except WorkspaceError as exc:
        handle_workspace_exception(exc, context="SandboxUtils")
    except Exception as exc:
        handle_workspace_exception(exc, context="Generic SandboxUtils")


def _run_or_raise(adapter: E2BAdapter, cmd: str):
    """
    Runs a command and treats a non-zero exit as an infrastructure failure.

    The adapter now returns non-zero exits as data rather than raising, which is what
    the tool layer needs. This helper deliberately restores the *old* behavior for this
    call site so Phase 1A changes nothing observable: a failing agent-authored command
    still becomes a TransientInfraError here.

    Phase 1B reclassifies it — a non-zero exit from an agent-authored command becomes a
    tool result carrying `exit_code`, returned to the agent to reason about, instead of
    burning three LLM retries and hard-failing the pipeline. This function goes away
    with `apply_agent_action_to_sandbox` itself in Phase 2.
    """
    result = adapter.execute(cmd)
    if result.exit_code != 0:
        raise WorkspaceTransportError(
            f"Internal command execution failed in the Sandbox "
            f"(exit {result.exit_code}): {cmd}\n{result.stderr}"
        )
    return result


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
