"""
The tool registry and `resolve_tools`.

Ported from claude-code's `resolveAgentTools` (`agentToolUtils.ts`) and `assembleToolPool`
(`tools.ts`). The resolution order is load-bearing and is kept exactly:

    1. build the denylist from `disallowed_tools`, parsing each spec for its tool name
    2. remove denied tools from what is available
    3. short-circuit on a wildcard (`tools` absent, or exactly `["*"]`)
    4. otherwise intersect the allowlist with what remains

**Unknown names are collected, never fatal.** One typo in a definition file costs that one
entry, not the agent's whole toolbelt — an agent silently left with no tools would look
like a model failure and be debugged as one.

The `Agent(...)` spec is special-cased the way the original special-cases it: the
parenthesized payload carries the allowed delegation targets rather than a permission
pattern. Phase 1B has no `Agent` tool yet, so the targets are parsed, returned, and unused;
capturing them here means Phase 5 adds a tool rather than a resolution rule.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.tools.base import AnyTool
from app.tools.rules import parse_rule, split_targets

logger = logging.getLogger("TDDOrchestrator.Tools")

AGENT_TOOL_NAME = "Agent"


@dataclass(frozen=True)
class ResolvedTools:
    """
    The outcome of resolving one agent's declared tools.

    `invalid` is the reason this is a result object rather than a plain list: the caller
    logs it, and a definition with a typo stays diagnosable.
    """

    has_wildcard: bool = False
    valid: tuple[str, ...] = ()
    invalid: tuple[str, ...] = ()
    resolved: tuple[AnyTool, ...] = ()
    allowed_agent_types: tuple[str, ...] = ()


class ToolRegistry:
    """Name -> Tool, with a cache-stable ordering."""

    def __init__(self, tools: list[AnyTool] | None = None) -> None:
        self._tools: dict[str, AnyTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: AnyTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"A tool named '{tool.name}' is already registered.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> AnyTool | None:
        return self._tools.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[AnyTool]:
        """
        Every registered tool, sorted by name.

        Sorting is not cosmetic: the tool list is serialized into the system prompt, and an
        unstable order would invalidate the provider's prompt cache on every run.
        """
        return [self._tools[name] for name in sorted(self._tools)]

    def enabled(self) -> list[AnyTool]:
        """Registered tools that are actually usable in this environment."""
        return [tool for tool in self.all() if tool.is_enabled()]


def resolve_tools(
    tools: list[str] | None,
    registry: ToolRegistry,
    disallowed_tools: list[str] | None = None,
) -> ResolvedTools:
    """
    Resolves an agent's declared tool specs against the registry.

    Args:
        tools: The `tools:` frontmatter list. `None` or `["*"]` means "everything
               available", after the denylist is applied.
        registry: The tool pool to resolve against.
        disallowed_tools: The `disallowedTools:` list. Applied first and unconditionally,
                          so a denial cannot be undone by an allowlist entry.
    """
    denied = {parse_rule(spec).tool_name for spec in disallowed_tools or []}
    available = [tool for tool in registry.enabled() if tool.name not in denied]

    if tools is None or tools == ["*"]:
        return ResolvedTools(has_wildcard=True, resolved=tuple(available))

    by_name = {tool.name: tool for tool in available}

    valid: list[str] = []
    invalid: list[str] = []
    resolved: list[AnyTool] = []
    seen: set[str] = set()
    agent_targets: tuple[str, ...] = ()

    for spec in tools:
        rule = parse_rule(spec)

        if rule.tool_name == AGENT_TOOL_NAME and rule.rule_content:
            # The payload is a delegation target list, not a permission pattern.
            agent_targets = tuple(split_targets(rule.rule_content))

        tool = by_name.get(rule.tool_name)
        if tool is None:
            invalid.append(spec)
            continue

        valid.append(spec)
        if tool.name not in seen:
            seen.add(tool.name)
            resolved.append(tool)

    if invalid:
        logger.warning(
            "Unknown tool name(s) ignored: %s. Known tools: %s",
            ", ".join(invalid),
            ", ".join(registry.names()),
        )

    # Sorted for the same prompt-cache reason as `all()`; dedup already happened above.
    resolved.sort(key=lambda tool: tool.name)

    return ResolvedTools(
        has_wildcard=False,
        valid=tuple(valid),
        invalid=tuple(invalid),
        resolved=tuple(resolved),
        allowed_agent_types=agent_targets,
    )
