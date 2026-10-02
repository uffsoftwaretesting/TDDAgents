import logging
from typing import AsyncIterator, Any
from langchain_core.messages import AIMessage, SystemMessage

from app.loop.config import RunConfig
from app.loop.state import LoopState
from app.loop.messages import Message
from app.loop.context.assembly import assemble_6_layer_prompt
from app.loop.prompts.sections import resolve_system_prompt_sections
from app.utils.chat_model_factory import get_chat_model
from app.loop.tools.base import Tool

logger = logging.getLogger(__name__)

def convert_tool_to_langchain(tool: Tool) -> dict[str, Any]:
    """Convert a BuiltTool into an OpenAI/Anthropic tool dictionary."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.prompt,
            "parameters": tool.input_schema
        }
    }

async def stream_call_model(state: LoopState, config: RunConfig) -> AsyncIterator[Message]:
    """
    Real CallModel implementation for the loop architecture.
    """
    phase_ledger = state.phase_ledger
    
    # 1. Assemble 6-Layer Memory System Prompt
    workspace_root = "." # Defaults to current working directory
    if state.tool_context and state.tool_context.workspace:
        workspace_root = getattr(state.tool_context.workspace, "root", ".")
        
    sections = assemble_6_layer_prompt(workspace_root, phase_ledger)
    
    # Resolve all blocks to text
    resolved_text = "\n\n".join(resolve_system_prompt_sections(sections))
    sys_msg = SystemMessage(content=resolved_text)
    
    # 2. Combine with history
    messages_to_send = [sys_msg] + list(state.messages)
    
    # 3. Resolve tools
    lc_tools = []
    if state.tool_context and state.tool_context.tools:
        lc_tools = [convert_tool_to_langchain(t) for t in state.tool_context.tools]
    
    # 4. Invoke LLM

    # 4. Invoke LLM
    model = get_chat_model("anthropic", model="claude-3-5-sonnet-20241022", temperature=0)
    if lc_tools:
        model = model.bind_tools(lc_tools)
        
    async for chunk in model.astream(messages_to_send):
        yield chunk


