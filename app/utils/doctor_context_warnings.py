"""
`/doctor`'s context warnings: large memory files, large agent descriptions, large MCP tool context, and
permission rules that can never apply.

Ported from `reference/claude-code/src/utils/doctorContextWarnings.ts` -> `ContextWarning`,
`ContextWarnings`, `checkClaudeMdFiles`, `checkAgentDescriptions`, `checkMcpTools`,
`checkUnreachableRules`, `checkContextWarnings`, `MCP_TOOLS_THRESHOLD`. The memory files are the
session's (`app/utils/claudemd.py` -> `get_memory_files`; "CLAUDE.md" is "TDDAGENTS.md", the product
substitution); the agents are the run's active agents; the MCP tools are counted with the counter the
caller passes (`countMcpToolTokens`); the unreachable rules come from
`app/utils/permissions/shadowed_rule_detection.py`, without a sandbox (this build has none).

*Divergence:* when counting the MCP tools fails, upstream estimates from each tool's name length plus
its `description` function's `length` (its arity); here the description text's length is used.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.tool import Tool
from app.types.permissions import ToolPermissionContext
from app.utils.analyze_context import TokenCounter, count_mcp_tool_tokens
from app.utils.claudemd import MAX_MEMORY_CHARACTER_COUNT, MemoryFileInfo, get_large_memory_files
from app.utils.permissions.permission_rule_parser import permission_rule_value_to_string
from app.utils.permissions.shadowed_rule_detection import detect_unreachable_rules
from app.utils.status_notice_helpers import (
    AGENT_DESCRIPTIONS_THRESHOLD,
    agent_description_tokens,
    get_agent_descriptions_total_tokens,
)
from app.utils.string_utils import plural
from app.utils.tokens import rough_token_count_estimation

#: `MCP_TOOLS_THRESHOLD`.
MCP_TOOLS_THRESHOLD = 25_000
#: How many agents or servers a warning names before "(N more …)".
TOP_CONTRIBUTORS = 5


@dataclass(frozen=True, slots=True)
class ContextWarning:
    """`ContextWarning`."""

    type: Literal["claudemd_files", "agent_descriptions", "mcp_tools", "unreachable_rules"]
    severity: Literal["warning", "error"]
    message: str
    details: tuple[str, ...]
    current_value: int
    threshold: int


@dataclass(frozen=True, slots=True)
class ContextWarnings:
    """`ContextWarnings`."""

    claude_md_warning: ContextWarning | None = None
    agent_warning: ContextWarning | None = None
    mcp_warning: ContextWarning | None = None
    unreachable_rules_warning: ContextWarning | None = None


def check_claude_md_files(memory_files: Sequence[MemoryFileInfo]) -> ContextWarning | None:
    """`checkClaudeMdFiles`: the memory files over `MAX_MEMORY_CHARACTER_COUNT` characters, largest first."""
    large = sorted(get_large_memory_files(list(memory_files)), key=lambda f: len(f.content), reverse=True)
    if not large:
        return None
    details = tuple(f"{f.path}: {len(f.content):,} chars" for f in large)
    if len(large) == 1:
        message = (f"Large TDDAGENTS.md file detected ({len(large[0].content):,} chars > "
                   f"{MAX_MEMORY_CHARACTER_COUNT:,})")
    else:
        message = f"{len(large)} large TDDAGENTS.md files detected (each > {MAX_MEMORY_CHARACTER_COUNT:,} chars)"
    return ContextWarning("claudemd_files", "warning", message, details, len(large), MAX_MEMORY_CHARACTER_COUNT)


def check_agent_descriptions(active_agents: Iterable[Any] | None) -> ContextWarning | None:
    """`checkAgentDescriptions`: the custom agents' descriptions over `AGENT_DESCRIPTIONS_THRESHOLD` tokens."""
    if active_agents is None:
        return None
    agents = list(active_agents)
    total = get_agent_descriptions_total_tokens(agents)
    if total <= AGENT_DESCRIPTIONS_THRESHOLD:
        return None
    tokens = sorted(((a.name, agent_description_tokens(a)) for a in agents if a.source != "built-in"),
                    key=lambda pair: pair[1], reverse=True)
    details = [f"{name}: ~{count:,} tokens" for name, count in tokens[:TOP_CONTRIBUTORS]]
    if len(tokens) > TOP_CONTRIBUTORS:
        details.append(f"({len(tokens) - TOP_CONTRIBUTORS} more custom agents)")
    return ContextWarning("agent_descriptions", "warning",
                          f"Large agent descriptions (~{total:,} tokens > {AGENT_DESCRIPTIONS_THRESHOLD:,})",
                          tuple(details), total, AGENT_DESCRIPTIONS_THRESHOLD)


async def check_mcp_tools(tools: Sequence[Tool], count_tokens: TokenCounter) -> ContextWarning | None:
    """`checkMcpTools`: the MCP tools' definitions over `MCP_TOOLS_THRESHOLD` tokens, by server."""
    mcp_tools = [tool for tool in tools if tool.is_mcp]
    if not mcp_tools:
        return None
    try:
        total, tool_details = await count_mcp_tool_tokens(tools, count_tokens)
    except Exception:  # noqa: BLE001 - upstream falls back to a character-based estimate
        estimated = sum(rough_token_count_estimation(str(len(tool.name) + len(tool.prompt))) for tool in mcp_tools)
        if estimated <= MCP_TOOLS_THRESHOLD:
            return None
        return ContextWarning(
            "mcp_tools", "warning",
            f"Large MCP tools context (~{estimated:,} tokens estimated > {MCP_TOOLS_THRESHOLD:,})",
            (f"{len(mcp_tools)} MCP tools detected (token count estimated)",), estimated, MCP_TOOLS_THRESHOLD)
    if total <= MCP_TOOLS_THRESHOLD:
        return None
    by_server: dict[str, list[int]] = {}
    for detail in tool_details:
        parts = detail["name"].split("__")
        server = (parts[1] if len(parts) > 1 else "") or "unknown"
        counts = by_server.setdefault(server, [0, 0])
        counts[0] += 1
        counts[1] += detail["tokens"]
    servers = sorted(by_server.items(), key=lambda item: item[1][1], reverse=True)
    details = [f"{name}: {count} tools (~{tokens:,} tokens)" for name, (count, tokens) in servers[:TOP_CONTRIBUTORS]]
    if len(servers) > TOP_CONTRIBUTORS:
        details.append(f"({len(servers) - TOP_CONTRIBUTORS} more servers)")
    return ContextWarning("mcp_tools", "warning",
                          f"Large MCP tools context (~{total:,} tokens > {MCP_TOOLS_THRESHOLD:,})",
                          tuple(details), total, MCP_TOOLS_THRESHOLD)


def check_unreachable_rules(context: ToolPermissionContext) -> ContextWarning | None:
    """`checkUnreachableRules`: each unreachable allow rule, why, and how to fix it."""
    unreachable = detect_unreachable_rules(context, sandbox_auto_allow_enabled=False)
    if not unreachable:
        return None
    details: list[str] = []
    for item in unreachable:
        details.append(f"{permission_rule_value_to_string(item.rule.tool_name, item.rule.rule_content)}: "
                       f"{item.reason}")
        details.append(f"  Fix: {item.fix}")
    count = len(unreachable)
    return ContextWarning("unreachable_rules", "warning",
                          f"{count} {plural(count, 'unreachable permission rule')} detected",
                          tuple(details), count, 0)


async def check_context_warnings(
    tools: Sequence[Tool],
    active_agents: Iterable[Any] | None,
    permission_context: ToolPermissionContext,
    memory_files: Sequence[MemoryFileInfo],
    count_tokens: TokenCounter,
) -> ContextWarnings:
    """`checkContextWarnings`."""
    return ContextWarnings(
        claude_md_warning=check_claude_md_files(memory_files),
        agent_warning=check_agent_descriptions(active_agents),
        mcp_warning=await check_mcp_tools(tools, count_tokens),
        unreachable_rules_warning=check_unreachable_rules(permission_context),
    )


__all__ = [
    "MCP_TOOLS_THRESHOLD",
    "ContextWarning",
    "ContextWarnings",
    "check_agent_descriptions",
    "check_claude_md_files",
    "check_context_warnings",
    "check_mcp_tools",
    "check_unreachable_rules",
]
