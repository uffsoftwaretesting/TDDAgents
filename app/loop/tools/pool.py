"""
Tool pool assembly with partition-sorting for prompt-cache stability (Part B8).

Ported from:
- `reference/claude-code/src/tools.ts` -> `assembleToolPool`, `filterToolsByDenyRules`
- `reference/claude-code/src/utils/toolPool.ts` -> `mergeAndFilterTools`
"""

from __future__ import annotations

from typing import Sequence

from app.loop.tools.base import Tool


def is_tool_denied(tool: Tool, deny_rules: set[str]) -> bool:
    """
    Check if a tool is blanket-denied by name or MCP prefix.
    """
    if tool.name in deny_rules:
        return True

    # Check for prefix rules, e.g. "mcp__server" blanket-denying all tools from that server
    for rule in deny_rules:
        if rule and tool.name.startswith(rule):
            return True

    return False


def assemble_tool_pool(
    built_in_tools: Sequence[Tool] = (),
    mcp_tools: Sequence[Tool] = (),
    *,
    deny_rules: Sequence[str] | set[str] = (),
) -> tuple[Tool, ...]:
    """
    Assemble the full tool pool with partition-sorting (Part B8).

    Partition-sort for prompt-cache stability:
    1. Separate into built-in tools and MCP tools.
    2. Filter out disabled tools and tools matching deny rules.
    3. Sort built-in tools alphabetically by name.
    4. Sort MCP tools alphabetically by name.
    5. Concatenate with built-ins as a contiguous prefix.
    6. Deduplicate by name, preserving insertion order (built-ins win on name conflicts).
    """
    deny_set = set(deny_rules)

    # Collect and classify all candidate tools
    all_candidates = list(built_in_tools) + list(mcp_tools)

    built_ins: list[Tool] = []
    mcps: list[Tool] = []

    for tool in all_candidates:
        if not tool.is_enabled():
            continue
        if is_tool_denied(tool, deny_set):
            continue

        if getattr(tool, "is_mcp", False):
            mcps.append(tool)
        else:
            built_ins.append(tool)

    # Sort each partition alphabetically by name
    sorted_built_ins = sorted(built_ins, key=lambda t: t.name)
    sorted_mcps = sorted(mcps, key=lambda t: t.name)

    # Concatenate: built-ins stay as a contiguous prefix
    concatenated = sorted_built_ins + sorted_mcps

    # Deduplicate by tool name, preserving insertion order
    seen_names: set[str] = set()
    deduped: list[Tool] = []
    for tool in concatenated:
        if tool.name not in seen_names:
            seen_names.add(tool.name)
            deduped.append(tool)

    return tuple(deduped)
