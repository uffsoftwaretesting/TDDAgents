"""
Unit tests for I2: Per-agent tool resolution and filtering.
"""

from typing import Any

from app.loop.agents.definition import AgentDefinition
from app.loop.agents.resolution import (
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
