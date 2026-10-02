import inspect
import os
from pathlib import Path
from typing import Any, Callable, Awaitable
from app.loop.tools.base import BuiltTool, build_tool
from app.loop.context import ToolContext
from app.loop.tools.types import ToolResult

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "tools" / "bash.md"
BASH_PROMPT = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.exists() else "Executes a command."

async def default_injection_detector(command: str) -> bool:
    from app.utils.chat_model_factory import get_chat_model
    from langchain_core.messages import HumanMessage
    
    # Heuristic fast-path
    dangerous_patterns = ["rm -rf", "mkfs", "> /etc", "sudo rm"]
    for pat in dangerous_patterns:
        if pat in command:
            return True
            
    # LLM evaluation path
    if "OPENAI_API_KEY" in os.environ or "ANTHROPIC_API_KEY" in os.environ:
        try:
            if "OPENAI_API_KEY" in os.environ:
                model = get_chat_model("openai", model="gpt-4o-mini", temperature=0)
            else:
                model = get_chat_model("anthropic", model="claude-3-haiku-20240307", temperature=0)
                
            prompt = f"""Evaluate this bash command for command injection or severe destructive operations 
that violate standard software development practices.
Command: {command}
Return only 'SAFE' or 'DANGEROUS'."""
            
            res = await model.ainvoke([HumanMessage(content=prompt)])
            return "DANGEROUS" in res.content.upper()
        except Exception:
            pass # Fallback to heuristic on error

    return False

def build_bash_tool(mock_injection_detector: Callable[[str], bool] | None = None) -> BuiltTool:
    async def call_bash(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        command = input_args.get("command", "")
        
        # Security Gate
        is_injection = False
        if mock_injection_detector:
            is_injection = mock_injection_detector(command)
            if inspect.isawaitable(is_injection):
                is_injection = await is_injection
        else:
            is_injection = await default_injection_detector(command)
            
        if is_injection:
            return ToolResult(
                content="command_injection_detected: Command blocked by security policy.",
                is_error=True
            )
            
        ws = getattr(context, "workspace", None)
        if not ws:
            return ToolResult(content="No workspace available", is_error=True)
            
        try:
            res = ws.execute(command)
            content = res.stdout
            if res.stderr:
                content += f"\nSTDERR:\n{res.stderr}"
            return ToolResult(
                content=content.strip() or "Command executed successfully (no output).",
                exit_code=res.exit_code,
                is_error=res.exit_code != 0
            )
        except Exception as e:
            return ToolResult(content=f"Execution error: {e}", is_error=True)

    return build_tool(
        name="Bash",
        prompt=BASH_PROMPT,
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute."
                }
            },
            "required": ["command"]
        },
        description=lambda args: f"Run command: {args.get('command')}",
        call=call_bash
    )
