"""
Re-export of Agent delegation tool from app/loop/agents.
"""

from app.loop.agents.tool import (
    AGENT_TOOL_NAME,
    AGENT_TOOL_SCHEMA,
    LEGACY_AGENT_TOOL_NAME,
    build_agent_tool,
    default_subagent_runner,
)

__all__ = [
    "AGENT_TOOL_NAME",
    "LEGACY_AGENT_TOOL_NAME",
    "AGENT_TOOL_SCHEMA",
    "build_agent_tool",
    "default_subagent_runner",
]
