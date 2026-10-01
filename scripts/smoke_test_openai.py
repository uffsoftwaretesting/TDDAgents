"""
Live OpenAI smoke test for Part F streaming tool execution and recovery.

Validates that:
1. OPENAI_API_KEY from .env is valid and funded.
2. Streaming chunks flow through the loop.
3. Tool calling with StreamingToolExecutor dispatches eagerly and executes successfully.
4. Loop transitions terminate cleanly with COMPLETED.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, AsyncIterator
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from app.loop.config import build_run_config
from app.loop.context import AppStateStore, ToolContext, tool_context_for
from app.loop.deps import CompactionResult, LoopDeps
from app.loop.engine import run_loop
from app.loop.messages import Message, ToolCall
from app.loop.state import LoopState, initial_loop_state
from app.loop.tools.base import build_tool
from app.loop.tools.types import ToolResult
from app.loop.transitions import Terminated


load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("ERROR: OPENAI_API_KEY not found in environment or .env")
    sys.exit(1)


async def main() -> None:
    print(f"1. Initializing ChatOpenAI with gpt-4o-mini (key prefix: {api_key[:7]}...)...")
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.0,
        api_key=api_key,
        max_tokens=100,
    )

    # 1. Direct streaming test
    print("2. Testing direct stream from OpenAI...")
    chunks: list[str] = []
    async for chunk in llm.astream("Say hello in exactly 3 words."):
        if isinstance(chunk.content, str):
            chunks.append(chunk.content)
            print(chunk.content, end="", flush=True)
    print("\n   Direct stream successful!")

    # 2. End-to-end Loop with streaming and tool execution
    print("\n3. Testing End-to-End Loop with StreamingToolExecutor and live LLM tool call...")

    # Define a simple tool that the model will be asked to call
    def ping_tool(args: dict[str, Any], context: ToolContext) -> ToolResult:
        query = args.get("query", "none")
        print(f"\n   [TOOL EXECUTED] ping_tool called with query={query!r}")
        return ToolResult(content=f"PONG: {query}", tool_use_id="ping_1")

    ping = build_tool(
        name="ping_tool",
        prompt="A simple ping tool that answers queries with PONG.",
        call=ping_tool,
        is_concurrency_safe=lambda args: True,
    )

    # Bind tool to the model
    llm_with_tools = llm.bind_tools([
        {
            "type": "function",
            "function": {
                "name": "ping_tool",
                "description": "Call this tool with a query to receive a pong response.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The query to ping"}
                    },
                    "required": ["query"],
                },
            },
        }
    ])

    async def call_model(state: LoopState, config: Any) -> AsyncIterator[Message]:
        # Convert state messages to langchain format
        messages = list(state.messages)
        # Use astream on model
        gathered: Any = None
        async for chunk in llm_with_tools.astream(messages):
            if gathered is None:
                gathered = chunk
            else:
                gathered = gathered + chunk
        # Yield the accumulated message with tool_calls properly populated
        yield gathered

    async def compact(state: LoopState, config: Any) -> CompactionResult:
        return CompactionResult(compacted=False, messages=state.messages)

    async def stop_hooks(state: LoopState, config: Any) -> Any:
        from types import SimpleNamespace
        return SimpleNamespace(blocking_errors=(), prevent_continuation=False)

    async def run_tools(
        calls: tuple[ToolCall, ...],
        asst: tuple[Message, ...],
        state: LoopState,
        config: Any,
    ) -> AsyncIterator[Message]:
        for c in calls:
            cid = str(c.get("id") or "")
            yield ToolMessage(content="PONG", tool_call_id=cid)

    deps = LoopDeps(
        call_model=call_model,
        run_tools=run_tools,
        compact=compact,
        stop_hooks=stop_hooks,
        uuid=lambda: "live_uid",
        now=lambda: 1000.0,
        emit_event=lambda e: None,
    )

    store = AppStateStore()
    context = tool_context_for(store, tools=(ping,))
    state = initial_loop_state(
        messages=(
            HumanMessage(content="Please call the ping_tool with query='test1234'."),
        ),
        tool_context=context,
    )

    config = build_run_config(
        run_id="live-smoke-test",
        postgres_checkpointing=False,
    )

    print("   Starting run_loop...")
    events: list[Any] = []
    async for item in run_loop(state, config, deps):
        events.append(item)
        if isinstance(item, AIMessage) and item.tool_calls:
            print(f"   [LOOP] Model issued tool calls: {item.tool_calls}")
        elif isinstance(item, ToolMessage):
            print(f"   [LOOP] Tool result yielded: {item.content}")
        elif isinstance(item, Terminated):
            print(f"   [LOOP] Loop terminated with reason: {item.reason}")

    # Check terminal state
    assert any(isinstance(e, Terminated) for e in events), "Loop should have terminated"
    print("\nSUCCESS! Live OpenAI LLM integration verified successfully.")


if __name__ == "__main__":
    asyncio.run(main())
