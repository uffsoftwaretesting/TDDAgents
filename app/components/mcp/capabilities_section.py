"""
A connected server's capabilities, as the `/mcp` server menus list them.

Ported from `reference/claude-code/src/components/mcp/CapabilitiesSection.tsx` ->
`CapabilitiesSection` (its `Byline` joins with ` · `).
"""

from __future__ import annotations


def capabilities_line(server_tools_count: int, server_prompts_count: int, server_resources_count: int) -> str:
    """`Capabilities: tools · resources · prompts`, the ones the server has, else `none`."""
    capabilities = [name for name, count in (("tools", server_tools_count), ("resources", server_resources_count),
                                             ("prompts", server_prompts_count)) if count > 0]
    return f"Capabilities: {' · '.join(capabilities) if capabilities else 'none'}"


__all__ = ["capabilities_line"]
