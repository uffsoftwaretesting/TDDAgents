"""
The adapter from the `Tool` protocol to whatever `llm.bind_tools()` wants.

The custom protocol stays the source of truth. LangChain's tool type has nowhere to put
`max_result_chars`, `is_concurrency_safe`, `required_capability`, or `check_permissions` —
and those are not decoration, they are what the executor partitions on and what the
permission gates read. Flattening the protocol into a LangChain tool would throw away the
half of each definition that the runtime actually uses.

So the conversion is deliberately lossy and one-directional: it emits the schema the model
needs to *ask* for a call, and nothing else. Answering the call goes back through
`execute_tool`, which still has the real tool in hand.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import AnyTool


def to_langchain_tool(tool: AnyTool) -> dict[str, Any]:
    """
    Renders one tool as an OpenAI-style function schema.

    A plain dict rather than a `StructuredTool`: `bind_tools` accepts this shape directly,
    and it keeps LangChain out of the type signature of everything upstream.
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.prompt(),
            "parameters": tool.args_schema.model_json_schema(),
        },
    }


def to_langchain_tools(tools: tuple[AnyTool, ...] | list[AnyTool]) -> list[dict[str, Any]]:
    """Converts a resolved tool list, preserving its (cache-stable) order."""
    return [to_langchain_tool(tool) for tool in tools]
