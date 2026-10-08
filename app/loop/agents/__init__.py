"""
Subpackage for Part I: Agents and delegation.

Provides:
- I1: Agent definitions and multi-tier override loader.
- I2: Per-agent tool resolution and filtering.
- I3: Agent delegation tool with independent worker pool assembly.
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
    parse_tool_spec,
    resolve_agent_tools,
)
from app.loop.agents.subagent import SubagentInstance, create_subagent
from app.loop.agents.tool import (
    AGENT_TOOL_NAME,
    AGENT_TOOL_SCHEMA,
    LEGACY_AGENT_TOOL_NAME,
    SubagentRunner,
    build_agent_tool,
    default_subagent_runner,
)

__all__ = [
    "AgentDefinition",
    "AgentFrontmatterError",
    "load_agent_definition",
    "load_agent_definition_from_path",
    "get_agent_definitions_with_overrides",
    "parse_markdown_frontmatter",
    "render_prompt",
    "ResolvedAgentTools",
    "resolve_agent_tools",
    "filter_tools_for_agent",
    "parse_tool_spec",
    "ALL_AGENT_DISALLOWED_TOOLS",
    "CUSTOM_AGENT_DISALLOWED_TOOLS",
    "ASYNC_AGENT_ALLOWED_TOOLS",
    "filter_incomplete_tool_calls",
    "build_forked_messages",
    "build_child_message",
    "build_worktree_notice",
    "is_in_fork_child",
    "FORK_AGENT",
    "FORK_SUBAGENT_TYPE",
    "FORK_BOILERPLATE_TAG",
    "FORK_DIRECTIVE_PREFIX",
    "FORK_PLACEHOLDER_RESULT",
    "AgentMemoryScope",
    "AgentMemoryStore",
    "AGENT_TOOL_NAME",
    "LEGACY_AGENT_TOOL_NAME",
    "AGENT_TOOL_SCHEMA",
    "SubagentRunner",
    "SubagentInstance",
    "create_subagent",
    "build_agent_tool",
    "default_subagent_runner",
]
