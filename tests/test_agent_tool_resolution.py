"""
Unit tests for I2: Per-agent tool resolution and filtering.
"""

import inspect
from typing import Any, cast
from unittest.mock import patch

from app.loop.agents.definition import AgentDefinition
from app.loop.agents.resolution import (
    _tool_matches_name,
    filter_tools_for_agent,
    parse_tool_spec,
    resolve_agent_tools,
)
from app.loop.tools.base import build_tool
from app.loop.tools.types import ToolResult


def _make_dummy_tool(name: str, aliases: tuple[str, ...] = ()) -> Any:
    return build_tool(
        name=name,
        prompt=f"Dummy {name}",
        call=lambda args, ctx: ToolResult(content=f"executed {name}"),
        aliases=aliases,
    )


def test_parse_tool_spec() -> None:
    assert parse_tool_spec("ReadFile") == ("ReadFile", None)
    assert parse_tool_spec("Agent(worker, researcher)") == ("Agent", "worker, researcher")
    assert parse_tool_spec("Agent:worker,researcher") == ("Agent", "worker,researcher")
    assert parse_tool_spec("  Grep  ") == ("Grep", None)


def test_filter_tools_for_agent_defaults() -> None:
    t_read = _make_dummy_tool("ReadFile")
    t_write = _make_dummy_tool("WriteFile")
    t_agent = _make_dummy_tool("Agent")
    t_ask = _make_dummy_tool("AskUserQuestion")
    t_mcp = _make_dummy_tool("mcp__github_search")

    tools = [t_read, t_write, t_agent, t_ask, t_mcp]

    # Standard subagent filter
    filtered = filter_tools_for_agent(tools)
    names = [t.name for t in filtered]
    assert "ReadFile" in names
    assert "WriteFile" in names
    assert "mcp__github_search" in names
    assert "Agent" not in names
    assert "AskUserQuestion" not in names


def test_filter_tools_allow_nested_agent() -> None:
    t_agent = _make_dummy_tool("Agent")
    filtered = filter_tools_for_agent([t_agent], allow_nested_agent=True)
    assert len(filtered) == 1
    assert filtered[0].name == "Agent"


def test_filter_tools_async_restrictions() -> None:
    t_read = _make_dummy_tool("ReadFile")
    t_write = _make_dummy_tool("WriteFile")
    t_grep = _make_dummy_tool("Grep")

    filtered = filter_tools_for_agent([t_read, t_write, t_grep], is_async=True)
    names = [t.name for t in filtered]
    assert names == ["ReadFile", "Grep"]
    assert "WriteFile" not in names


def test_filter_tools_plan_mode_exit_plan() -> None:
    t_exit = _make_dummy_tool("ExitPlanMode")
    assert filter_tools_for_agent([t_exit], permission_mode="read_only") == []
    assert filter_tools_for_agent([t_exit], permission_mode="plan") == [t_exit]


def test_resolve_agent_tools_wildcard() -> None:
    t_read = _make_dummy_tool("ReadFile")
    t_write = _make_dummy_tool("WriteFile")
    defn = AgentDefinition(
        name="wild",
        description="wildcard",
        prompt="do wild things",
        tools=("*",),
    )
    resolved = resolve_agent_tools(defn, [t_read, t_write])
    assert resolved.has_wildcard is True
    assert resolved.valid_tools == ()
    assert resolved.invalid_tools == ()
    assert len(resolved.resolved_tools) == 2


def test_resolve_agent_tools_explicit_list() -> None:
    t_read = _make_dummy_tool("ReadFile")
    t_write = _make_dummy_tool("WriteFile")
    t_bash = _make_dummy_tool("Bash")

    defn = AgentDefinition(
        name="reader",
        description="reads only",
        prompt="read",
        tools=("ReadFile", "Grep"),
    )
    resolved = resolve_agent_tools(defn, [t_read, t_write, t_bash])
    assert resolved.has_wildcard is False
    assert resolved.valid_tools == ("ReadFile",)
    assert resolved.invalid_tools == ("Grep",)
    assert len(resolved.resolved_tools) == 1
    assert resolved.resolved_tools[0].name == "ReadFile"


def test_resolve_agent_tools_disallowed_tools() -> None:
    t_read = _make_dummy_tool("ReadFile")
    t_bash = _make_dummy_tool("Bash")

    defn = AgentDefinition(
        name="safe",
        description="no bash",
        prompt="do safe things",
        tools=("ReadFile", "Bash"),
        disallowed_tools=("Bash",),
    )
    resolved = resolve_agent_tools(defn, [t_read, t_bash])
    assert resolved.valid_tools == ("ReadFile",)
    assert resolved.invalid_tools == ("Bash",)
    assert [t.name for t in resolved.resolved_tools] == ["ReadFile"]


def test_resolve_agent_tools_with_agent_spec_allowed_types() -> None:
    t_agent = _make_dummy_tool("Agent")
    t_read = _make_dummy_tool("ReadFile")

    defn = AgentDefinition(
        name="manager",
        description="manages workers",
        prompt="manage",
        tools=("Agent(worker, researcher)", "ReadFile"),
    )
    resolved = resolve_agent_tools(defn, [t_agent, t_read], allow_nested_agent=True)
    assert resolved.allowed_agent_types == ("worker", "researcher")
    assert "Agent(worker, researcher)" in resolved.valid_tools
    assert "ReadFile" in resolved.valid_tools


def test_resolve_agent_tools_alias_matching() -> None:
    t_bash = _make_dummy_tool("Bash", aliases=("execute_command", "sh"))
    defn = AgentDefinition(
        name="runner",
        description="runs shell",
        prompt="run",
        tools=("sh",),
    )
    resolved = resolve_agent_tools(defn, [t_bash])
    assert resolved.valid_tools == ("sh",)
    assert len(resolved.resolved_tools) == 1
    assert resolved.resolved_tools[0].name == "Bash"


# ── Mutation defense tests (Phase I) ─────────────────────────────────────────


def test_tool_matches_name_alias_fallback_default() -> None:
    """Kill mutants 11, 14: getattr(tool, 'aliases', ()) -> None or empty."""
    # A tool with no aliases attribute should not crash
    class BareTool:
        name = "Bash"
    bare = cast(Any, BareTool())  # deliberately has no `aliases`
    assert _tool_matches_name(bare, "Bash") is True
    assert _tool_matches_name(bare, "sh") is False


def test_filter_defaults_are_false() -> None:
    """Kill mutants 1, 2 on filter_tools_for_agent: default bool args flipped."""
    sig = inspect.signature(filter_tools_for_agent)
    assert sig.parameters["is_async"].default is False
    assert sig.parameters["allow_nested_agent"].default is False


def test_resolve_defaults_are_false() -> None:
    """Kill mutants 1, 2, 3 on resolve_agent_tools: default bool args flipped."""
    sig = inspect.signature(resolve_agent_tools)
    assert sig.parameters["is_async"].default is False
    assert sig.parameters["is_main_thread"].default is False
    assert sig.parameters["allow_nested_agent"].default is False


def test_filter_mcp_continue_not_break() -> None:
    """Kill mutant 8: continue -> break on MCP tools causes only first MCP tool to pass."""
    t_mcp1 = _make_dummy_tool("mcp__tool_a")
    t_mcp2 = _make_dummy_tool("mcp__tool_b")
    t_read = _make_dummy_tool("ReadFile")
    filtered = filter_tools_for_agent([t_mcp1, t_mcp2, t_read])
    names = [t.name for t in filtered]
    assert "mcp__tool_a" in names
    assert "mcp__tool_b" in names
    assert "ReadFile" in names


def test_filter_plan_exit_continue_not_break() -> None:
    """Kill mutant 18: continue -> break after ExitPlanMode causes early exit."""
    t_exit = _make_dummy_tool("ExitPlanMode")
    t_read = _make_dummy_tool("ReadFile")
    filtered = filter_tools_for_agent([t_exit, t_read], permission_mode="plan")
    names = [t.name for t in filtered]
    assert "ExitPlanMode" in names
    assert "ReadFile" in names


def test_resolve_permission_mode_forwarded() -> None:
    """Kill mutants 8, 12: permission_mode=None or removed from filter_tools_for_agent."""
    t_exit = _make_dummy_tool("ExitPlanMode")
    t_read = _make_dummy_tool("ReadFile")

    defn = AgentDefinition(
        name="planner",
        description="plan agent",
        prompt="plan",
        tools=("ExitPlanMode", "ReadFile"),
        permission_mode="plan",
    )
    # is_main_thread=False so filter_tools_for_agent is called
    resolved = resolve_agent_tools(defn, [t_exit, t_read])
    names = [t.name for t in resolved.resolved_tools]
    assert "ExitPlanMode" in names


def test_resolve_wildcard_allowed_agent_types_is_none() -> None:
    """Kill mutant 52: removing allowed_agent_types=None from wildcard branch."""
    defn = AgentDefinition(name="w", description="w", prompt="w", tools=("*",))
    resolved = resolve_agent_tools(defn, [_make_dummy_tool("ReadFile")])
    assert resolved.allowed_agent_types is None


def test_resolve_initial_allowed_agent_types_is_none() -> None:
    """Kill mutant 59: allowed_agent_types = None -> ''."""
    defn = AgentDefinition(
        name="x", description="x", prompt="x",
        tools=("ReadFile",),
    )
    resolved = resolve_agent_tools(defn, [_make_dummy_tool("ReadFile")])
    assert resolved.allowed_agent_types is None


def test_resolve_disallowed_alias_getattr_default() -> None:
    """Kill mutants 29, 32: getattr(t, 'aliases', ()) -> None or empty in disallowed check."""
    class BareTool:
        name = "CustomTool"
    defn = AgentDefinition(
        name="y", description="y", prompt="y",
        tools=("*",),
        disallowed_tools=("CustomTool",),
    )
    # BareTool has no aliases; should still be filtered by name
    resolved = resolve_agent_tools(defn, [cast(Any, BareTool())], is_main_thread=True)
    assert len(resolved.resolved_tools) == 0


def test_resolve_invalid_tool_logs_warning() -> None:
    """Kill mutant 93: logger.warning string mutation."""
    defn = AgentDefinition(
        name="logger_test", description="test", prompt="test",
        tools=("NonExistent",),
    )
    with patch("app.loop.agents.resolution.logger") as mock_logger:
        resolve_agent_tools(defn, [_make_dummy_tool("ReadFile")])
        mock_logger.warning.assert_called_once()
        args = mock_logger.warning.call_args[0]
        assert "Agent" in args[0]
        assert args[1] == "logger_test"
        assert args[2] == "NonExistent"
