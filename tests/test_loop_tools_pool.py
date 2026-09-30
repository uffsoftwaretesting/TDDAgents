"""
Tests for assemble_tool_pool with partition-sort and prompt-cache stability (Part B8).
"""

from __future__ import annotations

from app.loop.tools.base import build_tool
from app.loop.tools.pool import assemble_tool_pool, is_tool_denied
from app.loop.tools.types import ToolResult


def dummy_call(args, ctx):
    return ToolResult(content="ok")


class TestAssembleToolPool:
    """Part B8: assemble_tool_pool."""

    def test_partition_sort_prompt_cache_stability(self):
        """
        Built-in tools must remain a contiguous sorted prefix.
        Even if an MCP tool's name alphabetically precedes a built-in tool,
        the MCP tool must NOT interleave into the built-in prefix.
        """
        b_zeta = build_tool(name="zeta_builtin", prompt="p", call=dummy_call, is_mcp=False)
        b_alpha = build_tool(name="alpha_builtin", prompt="p", call=dummy_call, is_mcp=False)

        m_apple = build_tool(name="mcp__apple", prompt="p", call=dummy_call, is_mcp=True)
        m_zebra = build_tool(name="mcp__zebra", prompt="p", call=dummy_call, is_mcp=True)

        pool = assemble_tool_pool(
            built_in_tools=[b_zeta, b_alpha],
            mcp_tools=[m_zebra, m_apple],
        )

        names = [t.name for t in pool]
        # Invariant: built-ins sorted alphabetically, then MCPs sorted alphabetically
        assert names == ["alpha_builtin", "zeta_builtin", "mcp__apple", "mcp__zebra"]

    def test_deduplication_built_in_precedence_over_mcp(self):
        """When an MCP tool shares a name with a built-in tool, the built-in takes precedence."""
        built_in = build_tool(name="Read", prompt="builtin read", call=dummy_call, is_mcp=False)
        mcp_tool = build_tool(name="Read", prompt="mcp read", call=dummy_call, is_mcp=True)

        pool = assemble_tool_pool(built_in_tools=[built_in], mcp_tools=[mcp_tool])
        assert len(pool) == 1
        assert pool[0] is built_in
        assert pool[0].prompt == "builtin read"

    def test_deduplication_among_same_partition(self):
        t1 = build_tool(name="duplicate_tool", prompt="p1", call=dummy_call)
        t2 = build_tool(name="duplicate_tool", prompt="p2", call=dummy_call)

        pool = assemble_tool_pool(built_in_tools=[t1, t2])
        assert len(pool) == 1
        assert pool[0] is t1

    def test_filtering_disabled_tools(self):
        enabled = build_tool(name="enabled_tool", prompt="p", call=dummy_call, is_enabled=lambda: True)
        disabled = build_tool(name="disabled_tool", prompt="p", call=dummy_call, is_enabled=lambda: False)

        # Disabled tool placed first: must continue, not break
        pool = assemble_tool_pool(built_in_tools=[disabled, enabled])
        assert len(pool) == 1
        assert pool[0].name == "enabled_tool"

    def test_tool_without_is_mcp_attribute_defaults_to_builtin(self):
        class SimpleCustomTool:
            name = "custom"

            def is_enabled(self):
                return True

        pool = assemble_tool_pool(built_in_tools=[SimpleCustomTool()])  # type: ignore[list-item]
        assert len(pool) == 1
        assert pool[0].name == "custom"

    def test_filtering_by_exact_deny_rule(self):
        t1 = build_tool(name="allowed_tool", prompt="p", call=dummy_call)
        t2 = build_tool(name="denied_tool", prompt="p", call=dummy_call)

        pool = assemble_tool_pool(
            built_in_tools=[t1, t2],
            deny_rules=["denied_tool"],
        )
        assert len(pool) == 1
        assert pool[0].name == "allowed_tool"

    def test_filtering_by_mcp_server_prefix_deny_rule(self):
        m1 = build_tool(name="mcp__db__query", prompt="p", call=dummy_call, is_mcp=True)
        m2 = build_tool(name="mcp__db__schema", prompt="p", call=dummy_call, is_mcp=True)
        m3 = build_tool(name="mcp__git__status", prompt="p", call=dummy_call, is_mcp=True)

        # Deny the entire db MCP server
        pool = assemble_tool_pool(
            mcp_tools=[m1, m2, m3],
            deny_rules=["mcp__db"],
        )
        assert len(pool) == 1
        assert pool[0].name == "mcp__git__status"

    def test_auto_classification_of_mcp_tools_in_built_in_list(self):
        """Tools passed in built_in_tools with is_mcp=True are partitioned into MCP group."""
        b1 = build_tool(name="z_builtin", prompt="p", call=dummy_call, is_mcp=False)
        m1 = build_tool(name="a_mcp", prompt="p", call=dummy_call, is_mcp=True)

        pool = assemble_tool_pool(built_in_tools=[b1, m1])
        assert [t.name for t in pool] == ["z_builtin", "a_mcp"]

    def test_empty_pool(self):
        assert assemble_tool_pool() == ()

    def test_is_tool_denied_helper(self):
        tool = build_tool(name="Bash", prompt="p", call=dummy_call)
        assert is_tool_denied(tool, {"Bash"}) is True
        assert is_tool_denied(tool, {"Edit"}) is False
        assert is_tool_denied(tool, set()) is False
        # Empty string rule should not match non-empty tool names
        assert is_tool_denied(tool, {""}) is False
        # Non-matching prefix
        assert is_tool_denied(tool, {"Basher"}) is False
        # Matching prefix
        assert is_tool_denied(tool, {"Ba"}) is True
