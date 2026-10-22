"""
Subpackage for Part I: Agents and delegation.

Provides:
- I1: Agent definitions and multi-tier override loader.
- I2: Per-agent tool resolution and filtering.
- I3: The Agent tool (`agents/tool.py`) and the worker run (`agents/run_agent.py`), imported
  directly: they pull in the loop, which this package's light modules must not.
- I4: Context forking with incomplete-call filtering.
- I5: Run-scoped agent memory with automatic run-end purge.
"""

from app.loop.agents.definition import AgentDefinition, AgentFrontmatterError
from app.loop.agents.fork import (
    FORK_AGENT,
    FORK_BOILERPLATE_TAG,
    FORK_DIRECTIVE_PREFIX,
    FORK_PLACEHOLDER_RESULT,
    FORK_SUBAGENT_TYPE,
    build_child_message,
    build_forked_messages,
    build_worktree_notice,
    filter_incomplete_tool_calls,
    is_in_fork_child,
)
from app.loop.agents.loader import (
    get_agent_definitions_with_overrides,
    load_agent_definition,
    load_agent_definition_from_path,
    parse_markdown_frontmatter,
    render_prompt,
)
from app.loop.agents.memory import AgentMemoryScope, AgentMemoryStore
from app.loop.agents.resolution import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    CUSTOM_AGENT_DISALLOWED_TOOLS,
    ResolvedAgentTools,
    filter_tools_for_agent,
    resolve_agent_tools,
)

__all__ = [
    "ALL_AGENT_DISALLOWED_TOOLS",
    "ASYNC_AGENT_ALLOWED_TOOLS",
    "CUSTOM_AGENT_DISALLOWED_TOOLS",
    "FORK_AGENT",
    "FORK_BOILERPLATE_TAG",
    "FORK_DIRECTIVE_PREFIX",
    "FORK_PLACEHOLDER_RESULT",
    "FORK_SUBAGENT_TYPE",
    "AgentDefinition",
    "AgentFrontmatterError",
    "AgentMemoryScope",
    "AgentMemoryStore",
    "ResolvedAgentTools",
    "build_child_message",
    "build_forked_messages",
    "build_worktree_notice",
    "filter_incomplete_tool_calls",
    "filter_tools_for_agent",
    "get_agent_definitions_with_overrides",
    "is_in_fork_child",
    "load_agent_definition",
    "load_agent_definition_from_path",
    "parse_markdown_frontmatter",
    "render_prompt",
    "resolve_agent_tools",
]
