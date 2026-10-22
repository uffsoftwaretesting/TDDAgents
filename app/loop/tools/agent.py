"""
Re-export of the Agent delegation tool from app/loop/agents.
"""

from app.loop.agents.tool import (
    AGENT_TOOL_NAME,
    AGENT_TOOL_SCHEMA,
    LEGACY_AGENT_TOOL_NAME,
    build_agent_tool,
)

__all__ = [
    "AGENT_TOOL_NAME",
    "AGENT_TOOL_SCHEMA",
    "LEGACY_AGENT_TOOL_NAME",
    "build_agent_tool",
]
