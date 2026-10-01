"""
Tool pool assembly with partition-sorting for prompt-cache stability (Parts B8 & D3).

Ported from:
- `reference/claude-code/src/tools.ts` -> `assembleToolPool`, `filterToolsByDenyRules`
- `reference/claude-code/src/utils/toolPool.ts` -> `mergeAndFilterTools`
- §3.3 of `docs/transition_elaboration_plan.md` -> phase-derived deny rules feeding pool assembly
"""

from __future__ import annotations

from typing import Sequence

from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.tdd import (
    get_phase_deny_rules,
    is_implementation_writing_tool,
    is_test_writing_tool,
)
from app.loop.permissions.types import PermissionRule
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
    deny_rules: Sequence[str | PermissionRule] | set[str] = (),
    phase_ledger: PhaseLedger | None = None,
) -> tuple[Tool, ...]:
    """
    Assemble the full tool pool with partition-sorting and TDD phase deny rules (Parts B8 & D3).

    Partition-sort for prompt-cache stability:
    1. Separate into built-in tools and MCP tools.
    2. Filter out disabled tools, phase-denied tools, and tools matching deny rules.
       - In RED: implementation writers are stripped.
       - In GREEN/REFACTOR: test writers are stripped.
       - RunTests is NEVER stripped by phase deny rules.
    3. Sort built-in tools alphabetically by name.
    4. Sort MCP tools alphabetically by name.
    5. Concatenate with built-ins as a contiguous prefix.
    6. Deduplicate by name, preserving insertion order (built-ins win on name conflicts).
    """
    deny_set: set[str] = set()
    for rule in deny_rules:
        if isinstance(rule, PermissionRule):
            if rule.rule_content is None:
                deny_set.add(rule.tool_name)
        else:
            deny_set.add(rule)

    if phase_ledger is not None:
        for phase_rule in get_phase_deny_rules(phase_ledger):
            if phase_rule.rule_content is None:
                deny_set.add(phase_rule.tool_name)

    # Collect and classify all candidate tools
    all_candidates = list(built_in_tools) + list(mcp_tools)

    built_ins: list[Tool] = []
    mcps: list[Tool] = []

    for tool in all_candidates:
        if not tool.is_enabled():
            continue

        # RunTests is never denied by phase rules
        if tool.name != "RunTests":
            if is_tool_denied(tool, deny_set):
                continue

            if phase_ledger is not None:
                if phase_ledger.phase == TddPhase.RED and is_implementation_writing_tool(tool):
                    continue
                if phase_ledger.phase in (TddPhase.GREEN, TddPhase.REFACTOR) and is_test_writing_tool(tool):
                    continue
        else:
            # If explicitly in user-provided deny_rules (non-phase), still check
            user_denies = {
                r.tool_name if isinstance(r, PermissionRule) else r
                for r in deny_rules
                if not isinstance(r, PermissionRule) or r.rule_content is None
            }
            if tool.name in user_denies:
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
