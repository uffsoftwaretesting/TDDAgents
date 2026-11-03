"""
Context usage (`app/utils/analyze_context.py`, upstream `analyzeContext.ts`) and `/context`
(`app/commands/commands.py`, upstream `context-noninteractive.ts`), with a deterministic fake counter:
nothing here reaches a model or a token-counting endpoint.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import app.utils.analyze_context as ac
from app.commands.commands import (
    HELP_TEXT,
    collect_context_data,
    format_context_as_markdown_table,
    parse_slash_command,
)
from app.context import SessionContext, reset_session_context
from app.services.compact.compact import create_compact_boundary_message
from app.skills.load_skills_dir import SkillCommand, SkillRegistry
from app.state.app_state_store import AppStateStore
from app.tool import build_tool, tool_context_for
from app.tools.agent_tool.load_agents_dir import AgentDefinition
from app.utils.format import format_number, format_tokens
from app.utils.messages import ATTACHMENT_KEY, Message
from app.utils.settings.constants import get_source_display_name
from app.utils.system_prompt import build_effective_system_prompt
from app.utils.tokens import get_current_usage, rough_token_count_estimation
from tests.conftest import FakeWorkspace

AUTO_COMPACT_ENV = ("TDDAGENTS_DISABLE_COMPACT", "TDDAGENTS_DISABLE_AUTO_COMPACT", "TDDAGENTS_AUTO_COMPACT_WINDOW",
                    "TDDAGENTS_MAX_OUTPUT_TOKENS", "TDDAGENTS_DISABLE_1M_CONTEXT", "TDDAGENTS_SIMPLE")


@pytest.fixture(autouse=True)
def _clean(monkeypatch: Any, tmp_path: Any) -> Any:
    for name in AUTO_COMPACT_ENV:
        monkeypatch.delenv(name, raising=False)
    # No host memory file (`~/.tddagents/TDDAGENTS.md`) may leak into a count.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    yield
    reset_session_context()


async def fake_count(messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]) -> int | None:
    """Characters of the messages, and 1000 per tool schema."""
    return sum(len(str(m.content)) for m in messages) + 1000 * len(tools)


def _tool(name: str, prompt: str = "p", *, is_mcp: bool = False) -> Any:
    return build_tool(name=name, prompt=prompt, call=_never, is_mcp=is_mcp)


def _never(args: dict[str, Any], ctx: Any) -> Any:
    raise AssertionError("no tool runs while context is analysed")


def _ctx(**kw: Any) -> Any:
    kw.setdefault("agent_system_prompt", ("# Identity\nYou are TDDAgents.", "plain words"))
    kw.setdefault("workspace", FakeWorkspace())
    return tool_context_for(AppStateStore(), run_id="ctx-run", **kw)


def _skill(name: str, description: str = "Does x", when_to_use: str | None = None, source: str = "bundled") -> Any:
    return SkillCommand(name=name, description=description, markdown_content="body " * 50, source=source,
                        loaded_from="skills", skill_root=None, when_to_use=when_to_use)


def _agent(name: str, description: str, source: str) -> AgentDefinition:
    return AgentDefinition(name=name, description=description, prompt="", source=source)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ── the small ports ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, number, tokens", [
    (0, "0", "0"), (900, "900", "900"), (12.25, "12.3", "12.3"), (1000, "1.0k", "1k"), (1049, "1.0k", "1k"),
    (1050, "1.1k", "1.1k"), (1321, "1.3k", "1.3k"), (200_000, "200.0k", "200k"), (999_949, "999.9k", "999.9k"),
    (999_950, "1.0m", "1m"), (1_500_000, "1.5m", "1.5m"), (2_000_000_000, "2.0b", "2b"), (999.96, "1k", "1k"),
])
def test_format_number_and_tokens(value: float, number: str, tokens: str):
    assert format_number(value) == number
    assert format_tokens(value) == tokens


@pytest.mark.parametrize("source, shown", [
    ("userSettings", "User"), ("projectSettings", "Project"), ("localSettings", "Local"), ("flagSettings", "Flag"),
    ("policySettings", "Managed"), ("plugin", "Plugin"), ("built-in", "Built-in"), ("bundled", "bundled"),
])
def test_get_source_display_name(source: str, shown: str):
    assert get_source_display_name(source) == shown


def test_get_current_usage_is_the_last_real_response_in_anthropics_shape():
    usage = {"input_tokens": 100, "output_tokens": 7,
             "input_token_details": {"cache_read": 60, "cache_creation": 30}}
    older = AIMessage(content="a", usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6})
    last = AIMessage(content="b", usage_metadata={**usage, "total_tokens": 107})
    synthetic = AIMessage(content="c", additional_kwargs={"is_api_error_message": True},
                          usage_metadata={"input_tokens": 9, "output_tokens": 9, "total_tokens": 18})
    assert get_current_usage([older, last, synthetic, HumanMessage(content="q")]) == {
        "input_tokens": 10, "output_tokens": 7, "cache_creation_input_tokens": 30, "cache_read_input_tokens": 60}
    assert get_current_usage([HumanMessage(content="q"), AIMessage(content="no usage")]) is None


def test_build_effective_system_prompt():
    default = ["one", "two"]
    assert build_effective_system_prompt(agent_system_prompt=None, custom_system_prompt=None,
                                         default_system_prompt=default, append_system_prompt=None) == default
    assert build_effective_system_prompt(agent_system_prompt=None, custom_system_prompt="custom",
                                         default_system_prompt=default, append_system_prompt="tail") == [
        "custom", "tail"]
    assert build_effective_system_prompt(agent_system_prompt=None, custom_system_prompt="custom",
                                         default_system_prompt=default, append_system_prompt="tail",
                                         tdd_contract="TDD") == ["custom", "TDD", "tail"]
    assert build_effective_system_prompt(agent_system_prompt=None, custom_system_prompt=None,
                                         default_system_prompt=default, append_system_prompt=None,
                                         tdd_contract="TDD") == default
    assert build_effective_system_prompt(agent_system_prompt=("agent",), custom_system_prompt="custom",
                                         default_system_prompt=default, append_system_prompt="") == ["agent"]


@pytest.mark.parametrize("content, name", [
    ("intro\n## Doing tasks  \nbody", "Doing tasks"),
    ("# Title\n# Second", "Title"),
    ("\n\n  first line\nsecond", "  first line"),
    ("x" * 41, "x" * 40 + "…"),
    ("x" * 40, "x" * 40),
    ("", ""),
])
def test_extract_section_name(content: str, name: str):
    assert ac.extract_section_name(content) == name


def test_rough_count_tokens_adds_the_tool_preamble_once():
    messages = [HumanMessage(content="a" * 40)]
    assert run(ac.rough_count_tokens(messages, [])) == 10
    schema = {"name": "T", "description": "d", "input_schema": {}}
    with_tools = run(ac.rough_count_tokens([], [schema, schema]))
    assert with_tools == rough_token_count_estimation(ac.json_stringify([schema, schema])) + 500


def test_a_failing_counter_counts_nothing():
    async def broken(messages: Any, tools: Any) -> int | None:
        raise RuntimeError("endpoint down")

    assert run(ac.count_tokens_with_fallback([], [], broken)) is None
    assert run(ac.count_tool_definition_tokens([_tool("A")], broken)) == 0


# ── the categories ─────────────────────────────────────────────────────────────

def test_mcp_tools_share_one_count_less_the_preamble():
    tools = [_tool("Bash"), _tool("mcp__srv__small", "s", is_mcp=True),
             _tool("mcp__srv__big", "x" * 400, is_mcp=True), _tool("odd", "o", is_mcp=True)]

    async def counter(messages: Any, tools: Sequence[Mapping[str, Any]]) -> int | None:
        return 500 + 300 if tools else 0

    total, details = run(ac.count_mcp_tool_tokens(tools, counter))
    assert total == 300
    assert [(d["name"], d["serverName"], d["isLoaded"]) for d in details] == [
        ("mcp__srv__small", "srv", False), ("mcp__srv__big", "srv", False), ("odd", "unknown", False)]
    estimates = [rough_token_count_estimation(ac.json_stringify(ac.tool_to_api_schema(t))) for t in tools[1:]]
    assert [d["tokens"] for d in details] == [ac._js_round(e / sum(estimates) * 300) for e in estimates]
    assert details[1]["tokens"] > details[0]["tokens"]
    assert run(ac.count_mcp_tool_tokens([_tool("Bash")], counter)) == (0, [])


def test_custom_agents_are_the_non_built_in_ones_under_upstreams_source_names():
    agents = {"tester": _agent("tester", "writes tests", "built-in"),
              "mine": _agent("mine", "my agent", "user"), "ours": _agent("ours", "team agent", "project")}
    total, details = run(ac.count_custom_agent_tokens(agents, fake_count))
    assert details == [{"agentType": "mine", "source": "userSettings", "tokens": len("mine my agent")},
                       {"agentType": "ours", "source": "projectSettings", "tokens": len("ours team agent")}]
    assert total == len("mine my agent") + len("ours team agent")
    assert run(ac.count_custom_agent_tokens(list(agents.values()), fake_count)) == (total, details)


def test_memory_files_are_counted_one_by_one_unless_simple(monkeypatch):
    ws = FakeWorkspace(files={"TDDAGENTS.md": "Be nice.", "TDDAGENTS.local.md": "Mine."})
    total, files = run(ac.count_memory_file_tokens(ws, fake_count))
    assert [(f["type"], f["path"].rsplit("/", 1)[-1], f["tokens"]) for f in files] == [
        ("Project", "TDDAGENTS.md", len("Be nice.")), ("Local", "TDDAGENTS.local.md", len("Mine."))]
    assert total == len("Be nice.") + len("Mine.")
    monkeypatch.setenv("TDDAGENTS_SIMPLE", "1")
    assert run(ac.count_memory_file_tokens(ws, fake_count)) == (0, [])


def test_system_tokens_name_each_section_and_the_system_context():
    total, sections = run(ac.count_system_tokens(["# Intro\nhi", "", "plain"], {"gitStatus": "clean", "x": ""},
                                                 fake_count))
    assert sections == [{"name": "Intro", "tokens": len("# Intro\nhi")}, {"name": "plain", "tokens": 5},
                        {"name": "gitStatus", "tokens": 5}]
    assert total == len("# Intro\nhi") + 10
    assert run(ac.count_system_tokens([], {}, fake_count)) == (0, [])


def test_skill_frontmatter_is_counted_only_with_the_skill_tool():
    registry = SkillRegistry([_skill("tdd-red", "Write a failing test", "In RED"), _skill("other", "Other")])
    frontmatter = ac.count_skill_tokens([_tool("Skill")], registry)
    assert frontmatter == [
        {"name": "tdd-red", "source": "bundled",
         "tokens": rough_token_count_estimation("tdd-red Write a failing test In RED")},
        {"name": "other", "source": "bundled", "tokens": rough_token_count_estimation("other Other")}]
    assert ac.count_skill_tokens([_tool("Bash")], registry) == []
    assert ac.count_skill_tokens([_tool("Skill")], None) == []

    class Broken:
        def get_skill_tool_commands(self) -> list[Any]:
            raise RuntimeError("bad skills dir")

    assert ac.count_skill_tokens([_tool("Skill")], Broken()) == []
    assert run(ac.count_slash_command_tokens([_tool("Skill")], registry, fake_count)) == (1000, 2)
    assert run(ac.count_slash_command_tokens([_tool("Bash")], registry, fake_count)) == (0, 0)


# ── messages ───────────────────────────────────────────────────────────────────

def _conversation() -> list[Message]:
    return [
        HumanMessage(content="build a calculator"),
        AIMessage(content="I'll read it", id="r1",
                  tool_calls=[{"name": "ReadFile", "args": {"file_path": "a.py"}, "id": "t1"}]),
        ToolMessage(content="def add(): ...", tool_call_id="t1"),
        HumanMessage(content="<system-reminder>todo</system-reminder>",
                     additional_kwargs={"is_meta": True, ATTACHMENT_KEY: {"type": "todo_reminder", "n": 1}}),
        AIMessage(content=[{"type": "text", "text": "running"},
                           {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "ls"}}],
                  tool_calls=[{"name": "Bash", "args": {"command": "ls"}, "id": "t2"}]),
        ToolMessage(content="boom", tool_call_id="t2", status="error"),
        ToolMessage(content="orphan", tool_call_id="nobody"),
        HumanMessage(content=[{"type": "text", "text": "and tests"}, "raw"]),
        SystemMessage(content="ignored"),
    ]


def test_the_message_breakdown_follows_upstreams_kinds():
    breakdown = run(ac.approximate_message_tokens(_conversation(), fake_count))
    rough = rough_token_count_estimation
    js = ac.json_stringify
    read_call = {"type": "tool_use", "id": "t1", "name": "ReadFile", "input": {"file_path": "a.py"}}
    bash_call = {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "ls"}}
    assert breakdown.tool_calls_by_type == {"ReadFile": rough(js(read_call)), "Bash": rough(js(bash_call))}
    assert breakdown.tool_call_tokens == rough(js(read_call)) + rough(js(bash_call))
    assert breakdown.assistant_message_tokens == (rough(js({"type": "text", "text": "I'll read it"}))
                                                  + rough(js({"type": "text", "text": "running"})))
    read_result = {"type": "tool_result", "tool_use_id": "t1", "content": "def add(): ..."}
    bash_result = {"type": "tool_result", "tool_use_id": "t2", "content": "boom", "is_error": True}
    orphan = {"type": "tool_result", "tool_use_id": "nobody", "content": "orphan"}
    assert breakdown.tool_results_by_type == {"ReadFile": rough(js(read_result)), "Bash": rough(js(bash_result)),
                                              "unknown": rough(js(orphan))}
    assert breakdown.attachments_by_type == {"todo_reminder": rough(js({"type": "todo_reminder", "n": 1}))}
    assert breakdown.attachment_tokens == rough(js({"type": "todo_reminder", "n": 1}))
    assert breakdown.user_message_tokens == (rough("build a calculator") + rough(js("raw"))
                                             + rough(js({"type": "text", "text": "and tests"})))
    # The total is the counter's, over the messages as the request sends them (no system record).
    assert breakdown.total_tokens == sum(len(str(m.content)) for m in _conversation()[:-1])


def test_split_blocks_of_one_response_are_counted_as_the_request_merges_them():
    seen: list[list[Message]] = []

    async def counter(messages: Sequence[Message], tools: Any) -> int | None:
        seen.append(list(messages))
        return 1

    split = [AIMessage(content="a", id="same"), AIMessage(content="b", id="same")]
    run(ac.approximate_message_tokens(split, counter))
    assert [m.content for m in seen[0]] == ["ab"]


# ── the whole analysis ─────────────────────────────────────────────────────────

def _analyze(ctx: Any = None, *, tools: Sequence[Any] = (), agents: Any = (), messages: Sequence[Message] = (),
             **kw: Any) -> Any:
    return run(ac.analyze_context_usage(messages, kw.pop("model", "claude-sonnet-4-6"), tools, agents,
                                        ctx if ctx is not None else _ctx(), count_tokens=fake_count, **kw))


def test_the_categories_in_upstreams_order_with_the_buffer_and_free_space():
    registry = SkillRegistry([_skill("tdd-red", "Write a failing test")])
    tools = [_tool("Bash"), _tool("Skill"), _tool("mcp__srv__x", is_mcp=True)]
    ws = FakeWorkspace(files={"TDDAGENTS.md": "Rules."})
    agents = [_agent("mine", "my agent", "user")]
    data = _analyze(_ctx(skill_registry=registry, workspace=ws), tools=tools, agents=agents,
                    messages=[HumanMessage(content="hello")])
    prompt_tokens = len("# Identity\nYou are TDDAgents.") + len("plain words")
    skill_tokens = rough_token_count_estimation("tdd-red Write a failing test")
    used = prompt_tokens + (2000 - skill_tokens) + 500 + len("mine my agent") + len("Rules.") + skill_tokens + 5
    reserved = 200_000 - (200_000 - 20_000 - 13_000)
    assert data["categories"] == [
        {"name": "System prompt", "tokens": prompt_tokens, "color": "promptBorder"},
        {"name": "System tools", "tokens": 2000 - skill_tokens, "color": "inactive"},
        {"name": "MCP tools", "tokens": 500, "color": "cyan_FOR_SUBAGENTS_ONLY"},
        {"name": "Custom agents", "tokens": len("mine my agent"), "color": "permission"},
        {"name": "Memory files", "tokens": len("Rules."), "color": "claude"},
        {"name": "Skills", "tokens": skill_tokens, "color": "warning"},
        {"name": "Messages", "tokens": 5, "color": "purple_FOR_SUBAGENTS_ONLY"},
        {"name": "Autocompact buffer", "tokens": reserved, "color": "inactive"},
        {"name": "Free space", "tokens": 200_000 - used - reserved, "color": "promptBorder"},
    ]
    assert data["totalTokens"] == used and data["percentage"] == ac._js_round(used / 2000)
    assert data["maxTokens"] == data["rawMaxTokens"] == 200_000
    assert data["autoCompactThreshold"] == 167_000 and data["isAutoCompactEnabled"] is True
    assert data["slashCommands"] == {"totalCommands": 1, "includedCommands": 1, "tokens": 1000}
    assert data["skills"]["skillFrontmatter"] == [{"name": "tdd-red", "source": "bundled", "tokens": skill_tokens}]
    assert data["skills"]["totalSkills"] == data["skills"]["includedSkills"] == 1
    assert data["model"] == "claude-sonnet-4-6" and data["apiUsage"] is None
    assert data["agents"] == [{"agentType": "mine", "source": "userSettings", "tokens": len("mine my agent")}]
    # The external build leaves the ant-only details out of the response.
    for key in ("systemTools", "systemPromptSections", "deferredBuiltinTools"):
        assert key not in data


def test_an_empty_session_has_only_the_buffer_and_free_space(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_DISABLE_AUTO_COMPACT", "1")
    data = _analyze(_ctx(agent_system_prompt=()))
    assert data["categories"] == [
        {"name": "Compact buffer", "tokens": 3000, "color": "inactive"},
        {"name": "Free space", "tokens": 197_000, "color": "promptBorder"}]
    assert data["isAutoCompactEnabled"] is False
    for key in ("autoCompactThreshold", "slashCommands", "skills"):
        assert key not in data
    assert data["messageBreakdown"] == {
        "toolCallTokens": 0, "toolResultTokens": 0, "attachmentTokens": 0, "assistantMessageTokens": 0,
        "userMessageTokens": 0, "toolCallsByType": [], "attachmentsByType": []}
    rows = data["gridRows"]
    assert len(rows) == 10 and all(len(r) == 10 for r in rows)
    flat = [s for r in rows for s in r]
    assert [s["categoryName"] for s in flat] == ["Free space"] * 98 + ["Compact buffer"] * 2
    # 3000 of 200k is 1.5 squares: two, the last one half full.
    assert flat[-1] == {"color": "inactive", "isFilled": True, "categoryName": "Compact buffer", "tokens": 3000,
                        "percentage": 2, "squareFullness": 0.5}
    assert flat[-2]["squareFullness"] == 1.0
    assert flat[0] == {"color": "promptBorder", "isFilled": True, "categoryName": "Free space", "tokens": 197_000,
                       "percentage": 99, "squareFullness": 1.0}


def test_the_api_usage_is_the_total_when_there_is_one():
    reply = AIMessage(content="x", usage_metadata={
        "input_tokens": 50_000, "output_tokens": 10, "total_tokens": 50_010,
        "input_token_details": {"cache_read": 40_000}})
    data = _analyze(messages=[HumanMessage(content="q"), reply])
    assert data["apiUsage"] == {"input_tokens": 10_000, "output_tokens": 10, "cache_creation_input_tokens": 0,
                                "cache_read_input_tokens": 40_000}
    assert data["totalTokens"] == 50_000 and data["percentage"] == 25
    # The original messages, when given, are where the usage is read.
    assert _analyze(messages=[reply], original_messages=[])["apiUsage"] is None


def test_the_grid_squares_and_their_fullness():
    ctx = _ctx(agent_system_prompt=("p" * 3000,))
    data = _analyze(ctx)
    flat = [s for r in data["gridRows"] for s in r]
    # 3000 of 200k is 1.5 squares: rounded to 2, the second half full.
    assert [s["categoryName"] for s in flat[:2]] == ["System prompt"] * 2
    assert [s["squareFullness"] for s in flat[:2]] == [1.0, 0.5]
    assert flat[0]["percentage"] == 2 and flat[2]["categoryName"] == "Free space"
    # A tiny category still takes one square.
    tiny = [s for r in _analyze(_ctx(agent_system_prompt=("p",)))["gridRows"] for s in r]
    assert tiny[0]["categoryName"] == "System prompt" and tiny[0]["squareFullness"] == pytest.approx(0.0005)


@pytest.mark.parametrize("model, width, terminal, shape", [
    ("claude-sonnet-4-6", None, None, (10, 10)),
    ("claude-sonnet-4-6", 79, 79, (5, 5)),
    ("claude-sonnet-4-6", 80, 80, (10, 10)),
    ("claude-sonnet-4-6", 0, 0, (10, 10)),
    ("claude-sonnet-4-6[1m]", None, None, (10, 20)),
    ("claude-sonnet-4-6[1m]", 60, 60, (10, 5)),
])
def test_the_grid_shape(model: str, width: Any, terminal: Any, shape: tuple[int, int]):
    data = _analyze(model=model, terminal_width=terminal)
    rows = data["gridRows"]
    assert (len(rows), len(rows[0])) == shape and all(len(r) == shape[1] for r in rows)
    assert data["maxTokens"] == (1_000_000 if "[1m]" in model else 200_000)


def test_the_default_system_prompt_and_the_system_context(monkeypatch):
    sections: list[Any] = []

    def resolve(ctx: Any, run_id: str, tools: Sequence[Any]) -> list[str]:
        sections.append((run_id, [t.name for t in tools]))
        return ["# Default\nbody"]

    monkeypatch.setattr(ac, "resolve_default_system_prompt", resolve)
    monkeypatch.setattr(ac, "get_session_context",
                        lambda run_id, ws: SessionContext(user_context={}, system_context={"gitStatus": "clean"}))
    data = _analyze(_ctx(agent_system_prompt=None), tools=[_tool("Bash")])
    assert sections == [("ctx-run", ["Bash"])]
    assert data["categories"][0]["tokens"] == len("# Default\nbody") + len("clean")
    custom = _analyze(_ctx(agent_system_prompt=None), custom_system_prompt="CUSTOM", append_system_prompt="APPEND")
    # A custom prompt keeps TDDAgents' TDD contract after it (user decision 2026-11-01).
    from app.context import tdd_contract_section

    assert custom["categories"][0]["tokens"] == (
        len("CUSTOM") + len(tdd_contract_section()) + len("APPEND") + len("clean"))
    assert len(sections) == 1


def test_the_real_default_system_prompt_is_counted():
    data = _analyze(_ctx(agent_system_prompt=None))
    assert data["categories"][0]["name"] == "System prompt" and data["categories"][0]["tokens"] > 1000


# ── /context ───────────────────────────────────────────────────────────────────

def test_collect_context_data_counts_from_the_last_boundary():
    reply = AIMessage(content="x", usage_metadata={"input_tokens": 70, "output_tokens": 1, "total_tokens": 71})
    messages = [HumanMessage(content="old " * 100), reply, create_compact_boundary_message("auto", 9),
                HumanMessage(content="summary")]
    data = run(collect_context_data(messages, main_loop_model="claude-sonnet-4-6", tools=[], agent_definitions={},
                                    tool_context=_ctx(agent_system_prompt=()), count_tokens=fake_count))
    assert data["categories"][0] == {"name": "Messages", "tokens": len("summary"),
                                     "color": "purple_FOR_SUBAGENTS_ONLY"}
    # The usage before the boundary is history: the estimate stands.
    assert data["apiUsage"] is None and data["totalTokens"] == len("summary")


def test_context_is_a_listed_local_command():
    assert parse_slash_command("/context") is not None
    assert "  /context                      show current context usage\n" in HELP_TEXT


def _data(**overrides: Any) -> Any:
    data: dict[str, Any] = {
        "categories": [
            {"name": "System prompt", "tokens": 3000, "color": "promptBorder"},
            {"name": "Messages", "tokens": 500, "color": "purple_FOR_SUBAGENTS_ONLY"},
            {"name": "Skills", "tokens": 0, "color": "warning"},
            {"name": "Autocompact buffer", "tokens": 33_000, "color": "inactive"},
            {"name": "Free space", "tokens": 163_500, "color": "promptBorder"},
        ],
        "totalTokens": 3500, "maxTokens": 200_000, "rawMaxTokens": 200_000, "percentage": 2, "gridRows": [],
        "model": "claude-sonnet-4-6", "memoryFiles": [], "mcpTools": [], "agents": [], "isAutoCompactEnabled": True,
        "messageBreakdown": {}, "apiUsage": None,
    }
    data.update(overrides)
    return data


def test_the_markdown_report():
    data = _data(
        mcpTools=[{"name": "mcp__srv__x", "serverName": "srv", "tokens": 1200, "isLoaded": False}],
        agents=[{"agentType": "mine", "source": "userSettings", "tokens": 12},
                {"agentType": "pol", "source": "policySettings", "tokens": 3},
                {"agentType": "odd", "source": "synthetic", "tokens": 1}],
        memoryFiles=[{"path": "/p/TDDAGENTS.md", "type": "Project", "tokens": 1500}],
        skills={"totalSkills": 2, "includedSkills": 2, "tokens": 30, "skillFrontmatter": [
            {"name": "tdd-red", "source": "bundled", "tokens": 20},
            {"name": "mine", "source": "projectSettings", "tokens": 10}]},
    )
    assert format_context_as_markdown_table(data) == (
        "## Context Usage\n\n"
        "**Model:** claude-sonnet-4-6  \n"
        "**Tokens:** 3.5k / 200k (2%)\n\n"
        "### Estimated usage by category\n\n"
        "| Category | Tokens | Percentage |\n"
        "|----------|--------|------------|\n"
        "| System prompt | 3k | 1.5% |\n"
        "| Messages | 500 | 0.3% |\n"
        "| Free space | 163.5k | 81.8% |\n"
        "| Autocompact buffer | 33k | 16.5% |\n\n"
        "### MCP Tools\n\n"
        "| Tool | Server | Tokens |\n"
        "|------|--------|--------|\n"
        "| mcp__srv__x | srv | 1.2k |\n\n"
        "### Custom Agents\n\n"
        "| Agent Type | Source | Tokens |\n"
        "|------------|--------|--------|\n"
        "| mine | User | 12 |\n"
        "| pol | Policy | 3 |\n"
        "| odd | synthetic | 1 |\n\n"
        "### Memory Files\n\n"
        "| Type | Path | Tokens |\n"
        "|------|------|--------|\n"
        "| Project | /p/TDDAGENTS.md | 1.5k |\n\n"
        "### Skills\n\n"
        "| Skill | Source | Tokens |\n"
        "|-------|--------|--------|\n"
        "| tdd-red | bundled | 20 |\n"
        "| mine | Project | 10 |\n\n")


def test_the_markdown_report_leaves_out_what_is_empty():
    data = _data(categories=[{"name": "Free space", "tokens": 167_000, "color": "promptBorder"},
                             {"name": "Autocompact buffer", "tokens": 33_000, "color": "inactive"}],
                 skills={"totalSkills": 0, "includedSkills": 0, "tokens": 0, "skillFrontmatter": []})
    assert format_context_as_markdown_table(data) == (
        "## Context Usage\n\n**Model:** claude-sonnet-4-6  \n**Tokens:** 3.5k / 200k (2%)\n\n")
    # A category that is present renders the Compact buffer row too; a zero free space is left out.
    data = _data(categories=[{"name": "Messages", "tokens": 1, "color": "x"},
                             {"name": "Compact buffer", "tokens": 3000, "color": "inactive"},
                             {"name": "Free space", "tokens": 0, "color": "promptBorder"}])
    table = format_context_as_markdown_table(data)
    assert "| Messages | 1 | 0.0% |\n| Compact buffer | 3k | 1.5% |\n\n" in table
    assert "Free space" not in table


def test_the_percentage_rounds_a_tie_up_as_to_fixed():
    data = _data(categories=[{"name": "Messages", "tokens": 1, "color": "x"}], rawMaxTokens=400)
    assert "| Messages | 1 | 0.3% |" in format_context_as_markdown_table(data)
