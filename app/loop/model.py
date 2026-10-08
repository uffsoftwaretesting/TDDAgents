"""
The streamed model call the loop consumes (Part F1): `deps.call_model(state, config)`.

Request shape follows claude-code's `queryModel` path: the system prompt is
`getSystemPrompt` sections with `appendSystemContext`; the history is preceded by
`prependUserContext` (memory files and date) and followed by any attachment this turn
produces — here the TDD state delta (`compute_tdd_state_attachment`).

The provider and model id are resolved from `Config.CHAT_MODEL` / `Config.MODEL`, the one
place the rest of the pipeline resolves them; this module never names a model itself.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from langchain_core.messages import SystemMessage

from app.config.config import Config
from app.loop.config import RunConfig
from app.loop.context.assembly import (
    append_system_context,
    get_session_context,
    get_system_prompt,
    prepend_user_context,
)
from app.loop.context.attachments import compute_tdd_state_attachment
from app.loop.messages import Message
from app.loop.prompts.sections import resolve_system_prompt_sections
from app.loop.state import LoopState
from app.loop.tools.base import Tool
from app.utils.chat_model_factory import get_chat_model

logger = logging.getLogger(__name__)

TODO_FILE = "TODO.md"


def convert_tool_to_langchain(tool: Tool) -> dict[str, Any]:
    """Convert a loop tool into an OpenAI-style function tool dictionary."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.prompt,
            "parameters": tool.input_schema,
        },
    }


def read_todo(ws: Any) -> str | None:
    if ws is None:
        return None
    try:
        content: str = ws.read_file(TODO_FILE)
    except Exception:
        return None
    return content


def build_request_messages(state: LoopState, config: RunConfig) -> list[Message]:
    """System prompt + system context, user context, history, then this turn's attachment."""
    from app.loop.context.compact import apply_tool_result_budget, microcompact_tool_results

    ws = state.tool_context.workspace
    session = get_session_context(config.run_id, ws)
    sections = resolve_system_prompt_sections(get_system_prompt(ws, Config.MODEL), session.section_cache)
    system = SystemMessage(content="\n\n".join(append_system_context(sections, session.system_context)))
    compacted = microcompact_tool_results(state.messages, keep_recent_turns=2)
    budgeted = apply_tool_result_budget(compacted)
    history = prepend_user_context(budgeted, session.user_context)
    attachment = compute_tdd_state_attachment(state.messages, state.phase_ledger, read_todo(ws))
    return [system, *history, *([attachment] if attachment is not None else [])]


from app.utils.token_metrics import GlobalTokenTracker

_global_token_tracker = GlobalTokenTracker()


def get_global_token_tracker() -> GlobalTokenTracker:
    """Return the global token tracker instance (Plan D1)."""
    return _global_token_tracker


def get_token_usage_summary() -> dict[str, Any]:
    """Return structured token usage metrics summary (Plan D1)."""
    return _global_token_tracker.summary()

async def stream_call_model(state: LoopState, config: RunConfig) -> AsyncIterator[Message]:
    """Real `call_model`: assemble the request, bind the context's tools, stream the reply."""
    messages_to_send = build_request_messages(state, config)
    lc_tools = [convert_tool_to_langchain(t) for t in state.tool_context.tools]

    model = get_chat_model(Config.CHAT_MODEL, model=Config.MODEL, temperature=Config.TEMPERATURE)
    if lc_tools:
        model = model.bind_tools(lc_tools)

    try:
        stream = model.astream(messages_to_send, config={"callbacks": [_global_token_tracker]})
    except TypeError:
        stream = model.astream(messages_to_send)

    async for chunk in stream:
        yield chunk
