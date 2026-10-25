"""Phase 7: agent definitions, tool resolution and the Agent tool prompt, ported from
`loadAgentsDir.ts`, `agentToolUtils.ts`, `constants/tools.ts`, `prompt.ts` and
`permissions.ts` (`filterDeniedAgents` / `getDenyRuleForAgent`)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from app.loop.agents.definition import AgentDefinition, AgentFrontmatterError
from app.loop.agents.loader import (
    get_agent_definitions_with_overrides,
    load_agent_definition,
)
from app.loop.agents.prompt import format_agent_line, get_prompt, get_tools_description
from app.loop.agents.resolution import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    filter_denied_agents,
    filter_tools_for_agent,
    get_deny_rule_for_agent,
    resolve_agent_tools,
)
from app.loop.ledger import TddPhase
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)
from app.loop.tools.base import build_tool

PROMPTS = Path(__file__).parent.parent / "app" / "prompts" / "agents"


def _md(**fm: Any) -> str:
    import yaml

    return f"---\n{yaml.safe_dump(fm)}---\nBody text."


def _load(content: str, **kw: Any) -> AgentDefinition:
    """A definition the test expects to exist."""
    defn = load_agent_definition(content, **kw)
    assert defn is not None
    return defn


def _tool(name: str, aliases: tuple[str, ...] = ()) -> Any:
    async def call(args: Any, ctx: Any) -> Any:
        return None

    return build_tool(name=name, prompt="", call=call, aliases=aliases)


class TestParseAgentFromMarkdown:
    def test_name_and_description_are_required(self, caplog):
        assert load_agent_definition(_md(description="d")) is None
        with caplog.at_level(logging.DEBUG):
            assert load_agent_definition(_md(name="a")) is None
        assert "missing required 'description'" in caplog.text
        assert load_agent_definition(_md(name=3, description="d")) is None

    def test_description_unescapes_newlines(self):
        defn = _load(_md(name="a", description="one\\ntwo"))
        assert defn is not None and defn.description == "one\ntwo"

    def test_only_upstream_permission_modes_parse(self, caplog):
        for mode in ("default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"):
            defn = _load(_md(name="a", description="d", permissionMode=mode))
            assert defn is not None and defn.permission_mode == mode
        with caplog.at_level(logging.DEBUG):
            for bad in ("read_only", "workspace_write", "ACCEPTEDITS", "bubble"):
                defn = _load(_md(name="a", description="d", permissionMode=bad))
                assert defn is not None and defn.permission_mode is None
        assert "invalid permissionMode 'read_only'" in caplog.text

    def test_model_is_trimmed_and_inherit_is_a_sentinel(self):
        assert _load(_md(name="a", description="d", model=" INHERIT ")).model == "inherit"
        assert _load(_md(name="a", description="d", model=" m-1 ")).model == "m-1"
        assert _load(_md(name="a", description="d", model="   ")).model is None
        assert _load(_md(name="a", description="d", model=5)).model is None

    def test_background_is_true_or_unset(self):
        for value in (True, "true"):
            assert _load(_md(name="a", description="d", background=value)).background is True
        for value in (False, "false", "maybe"):
            assert _load(_md(name="a", description="d", background=value)).background is None

    def test_max_turns_is_a_positive_int_or_unset(self):
        assert _load(_md(name="a", description="d", maxTurns=7)).max_turns == 7
        assert _load(_md(name="a", description="d", maxTurns="12")).max_turns == 12
        for bad in (0, -1, "x", 1.5):
            assert _load(_md(name="a", description="d", maxTurns=bad)).max_turns is None
        assert _load(_md(name="a", description="d")).max_turns is None

    def test_tools_follow_parse_agent_tools_from_frontmatter(self):
        def tools(value: Any) -> Any:
            return _load(_md(name="a", description="d", tools=value)).tools

        assert _load(_md(name="a", description="d")).tools is None
        assert tools(["ReadFile", "Agent(tester, developer)"]) == ("ReadFile", "Agent(tester, developer)")
        assert tools("ReadFile, Grep") == ("ReadFile", "Grep")
        assert tools(["*"]) is None
        assert tools("") == ()
        assert tools(None) is None
        defn = _load(_md(name="a", description="d", disallowedTools="WriteFile, Edit"))
        assert defn.disallowed_tools == ("WriteFile", "Edit")
        assert _load(_md(name="a", description="d")).disallowed_tools is None

    def test_memory_scopes(self):
        for scope in ("user", "project", "local", "run", "session", "none"):
            assert _load(_md(name="a", description="d", memory=scope)).memory == scope
        assert _load(_md(name="a", description="d", memory="cloud")).memory is None

    def test_phase_still_fails_loudly(self):
        assert _load(_md(name="a", description="d", phase="red")).phase == TddPhase.RED
        with pytest.raises(AgentFrontmatterError):
            _load(_md(name="a", description="d", phase="BLUE"))

    def test_hooks_are_kept_raw(self):
        hooks = {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}
        assert _load(_md(name="a", description="d", hooks=hooks)).hooks == hooks
        assert _load(_md(name="a", description="d", hooks="nope")).hooks is None

    def test_omit_claude_md_only_for_built_in_agents(self):
        content = _md(name="a", description="d", omitClaudeMd=True)
        assert _load(content, source="built-in").omit_claude_md is True
        assert _load(content, source="project").omit_claude_md is False
        assert _load(_md(name="a", description="d"), source="built-in").omit_claude_md is False

    def test_auto_memory_adds_the_file_tools(self, monkeypatch):
        monkeypatch.setenv("TDDAGENTS_ENABLE_AUTO_MEMORY", "1")
        defn = _load(_md(name="a", description="d", memory="run", tools=["Grep", "ReadFile"]))
        assert defn.tools == ("Grep", "ReadFile", "WriteFile", "Edit")
        monkeypatch.delenv("TDDAGENTS_ENABLE_AUTO_MEMORY")
        defn = _load(_md(name="a", description="d", memory="run", tools=["Grep"]))
        assert defn.tools == ("Grep",)


class TestDefinitionsWithOverrides:
    def test_the_roster_comes_from_app_prompts_agents(self, tmp_path):
        defs = get_agent_definitions_with_overrides(project_dir=tmp_path, user_home=tmp_path)
        assert set(defs) == {"tester", "developer", "refactorer", "verification", "Explore", "Plan"}
        assert all(d.source == "built-in" for d in defs.values())

    def test_project_overrides_user_overrides_built_in(self, tmp_path):
        for scope in ("home", "proj"):
            d = tmp_path / scope / ".tddagents" / "agents"
            d.mkdir(parents=True)
            (d / "tester.md").write_text(_md(name="tester", description=scope))
        defs = get_agent_definitions_with_overrides(project_dir=tmp_path / "proj", user_home=tmp_path / "home")
        assert (defs["tester"].description, defs["tester"].source) == ("proj", "project")
        (tmp_path / "proj" / ".tddagents" / "agents" / "tester.md").unlink()
        defs = get_agent_definitions_with_overrides(project_dir=tmp_path / "proj", user_home=tmp_path / "home")
        assert (defs["tester"].description, defs["tester"].source) == ("home", "user")

    def test_a_file_without_a_name_is_skipped(self, tmp_path):
        d = tmp_path / ".tddagents" / "agents"
        d.mkdir(parents=True)
        (d / "notes.md").write_text("# just notes")
        (d / "half.md").write_text(_md(name="half"))
        defs = get_agent_definitions_with_overrides(project_dir=tmp_path, user_home=tmp_path)
        assert "half" not in defs and "unnamed_agent" not in defs

    def test_no_prompt_registry_is_consulted(self):
        import inspect

        from app.loop.agents import loader

        assert "global_prompt_registry" not in inspect.getsource(loader)


class TestTheShippedRoster:
    @pytest.mark.parametrize(("directory", "name", "phase"), [
        ("tester", "tester", TddPhase.RED),
        ("developer", "developer", TddPhase.GREEN),
        ("refactorer", "refactorer", TddPhase.REFACTOR),
        ("verification", "verification", None),
        ("explore", "Explore", None),
        ("plan", "Plan", None),
    ])
    def test_each_agent(self, directory, name, phase):
        from app.loop.agents.loader import load_agent_definition_from_path

        defn = load_agent_definition_from_path(PROMPTS / directory / "AGENT.md")
        assert defn is not None
        assert (defn.name, defn.phase) == (name, phase)
        # Upstream's worker default (`permissionMode ?? 'acceptEdits'`) is applied at spawn.
        assert defn.permission_mode is None
        assert "permissionMode" not in defn.raw_frontmatter
        assert "revertOnRed" not in defn.raw_frontmatter
        if phase is None:
            assert defn.disallowed_tools == ("Agent", "WriteFile", "Edit")
        else:
            assert defn.disallowed_tools is None
        assert defn.omit_claude_md is (name in ("Explore", "Plan"))


class TestFilterToolsForAgent:
    def test_agent_is_disallowed_for_every_subagent_in_the_external_build(self):
        assert "Agent" in ALL_AGENT_DISALLOWED_TOOLS
        tools = [_tool("Agent", ("Task",)), _tool("ReadFile")]
        assert [t.name for t in filter_tools_for_agent(tools, is_built_in=True)] == ["ReadFile"]
        assert [t.name for t in filter_tools_for_agent(tools, is_built_in=False)] == ["ReadFile"]

    def test_mcp_tools_always_pass(self):
        mcp = _tool("mcp__s__x")
        assert filter_tools_for_agent([mcp], is_built_in=False, is_async=True) == [mcp]

    def test_exit_plan_mode_passes_in_plan_mode_only(self):
        exit_plan = _tool("ExitPlanMode")
        assert filter_tools_for_agent([exit_plan], is_built_in=True, permission_mode="plan") == [exit_plan]
        assert filter_tools_for_agent([exit_plan], is_built_in=True, permission_mode="default") == []

    def test_async_agents_keep_only_the_async_set(self):
        assert {"ReadFile", "Grep", "Glob", "Bash", "WriteFile", "Edit", "TodoWrite", "RunTests"} <= (
            ASYNC_AGENT_ALLOWED_TOOLS)
        run_tests, other = _tool("RunTests"), _tool("Other")
        assert filter_tools_for_agent([run_tests, other], is_built_in=True, is_async=True) == [run_tests]
        assert filter_tools_for_agent([run_tests, other], is_built_in=True) == [run_tests, other]


class TestResolveAgentTools:
    def _defn(self, **kw: Any) -> AgentDefinition:
        return AgentDefinition(name="w", description="d", prompt="p", **kw)

    def test_wildcard_resolves_everything_allowed(self):
        tools = [_tool("ReadFile"), _tool("WriteFile"), _tool("Agent")]
        res = resolve_agent_tools(self._defn(tools=None, disallowed_tools=("WriteFile",)), tools)
        assert res.has_wildcard is True
        assert [t.name for t in res.resolved_tools] == ["ReadFile"]
        res = resolve_agent_tools(self._defn(tools=("*",)), tools)
        assert [t.name for t in res.resolved_tools] == ["ReadFile", "WriteFile"]

    def test_explicit_list_is_exact_and_deduplicated(self):
        tools = [_tool("ReadFile"), _tool("Grep")]
        res = resolve_agent_tools(self._defn(tools=("ReadFile", "readfile", "Ghost", "ReadFile")), tools)
        assert [t.name for t in res.resolved_tools] == ["ReadFile"]
        assert res.valid_tools == ("ReadFile", "ReadFile")
        assert res.invalid_tools == ("readfile", "Ghost")

    def test_a_disallowed_spec_is_parsed_like_a_permission_rule(self):
        tools = [_tool("Bash"), _tool("ReadFile")]
        res = resolve_agent_tools(self._defn(disallowed_tools=("Bash(rm:*)",)), tools)
        assert [t.name for t in res.resolved_tools] == ["ReadFile"]

    def test_agent_spec_carries_allowed_types_but_no_tool_for_subagents(self):
        tools = [_tool("Agent"), _tool("ReadFile")]
        res = resolve_agent_tools(self._defn(tools=("Agent(tester, developer)", "ReadFile")), tools)
        assert res.allowed_agent_types == ("tester", "developer")
        assert res.valid_tools == ("Agent(tester, developer)", "ReadFile")
        assert [t.name for t in res.resolved_tools] == ["ReadFile"]

    def test_the_main_thread_skips_the_subagent_filter(self):
        tools = [_tool("Agent"), _tool("ReadFile")]
        res = resolve_agent_tools(self._defn(tools=("Agent(tester)",)), tools, is_main_thread=True)
        assert [t.name for t in res.resolved_tools] == ["Agent"]
        assert res.allowed_agent_types == ("tester",)
        res = resolve_agent_tools(self._defn(tools=("Agent",)), tools, is_main_thread=True)
        assert res.allowed_agent_types is None

    def test_custom_agents_and_async_flow_through(self):
        tools = [_tool("ReadFile"), _tool("Other")]
        res = resolve_agent_tools(self._defn(source="project"), tools, is_async=True)
        assert [t.name for t in res.resolved_tools] == ["ReadFile"]


class TestDeniedAgents:
    def _context(self, *contents: str | None) -> ToolPermissionContext:
        rules = tuple(
            PermissionRule(tool_name="Agent", rule_behavior=PermissionBehavior.DENY, rule_content=c,
                           source=PermissionRuleSource.PROJECT_SETTINGS)
            for c in contents
        )
        return ToolPermissionContext(always_deny_rules=rules)

    def test_filter_denied_agents(self):
        agents = [AgentDefinition(name=n, description="d", prompt="p") for n in ("tester", "developer")]
        kept = filter_denied_agents(agents, self._context("tester", None), "Agent")
        assert [a.name for a in kept] == ["developer"]

    def test_get_deny_rule_for_agent(self):
        context = self._context(None, "developer")
        rule = get_deny_rule_for_agent(context, "Agent", "developer")
        assert rule is not None and rule.rule_content == "developer"
        assert get_deny_rule_for_agent(context, "Agent", "tester") is None


class TestAgentToolPrompt:
    def test_tools_description(self):
        def d(**kw: Any) -> AgentDefinition:
            return AgentDefinition(name="a", description="w", prompt="p", **kw)

        assert get_tools_description(d(tools=("A", "B"), disallowed_tools=("B",))) == "A"
        assert get_tools_description(d(tools=("A",), disallowed_tools=("A",))) == "None"
        assert get_tools_description(d(tools=("A", "B"))) == "A, B"
        assert get_tools_description(d(disallowed_tools=("X", "Y"))) == "All tools except X, Y"
        assert get_tools_description(d()) == "All tools"
        assert get_tools_description(d(tools=())) == "All tools"

    def test_agent_line(self):
        defn = AgentDefinition(name="tester", description="Writes tests", prompt="p", tools=("ReadFile",))
        assert format_agent_line(defn) == "- tester: Writes tests (Tools: ReadFile)"

    def _agents(self) -> list[AgentDefinition]:
        return [
            AgentDefinition(name="tester", description="Writes tests", prompt="p", tools=("ReadFile",)),
            AgentDefinition(name="Explore", description="Searches", prompt="p", disallowed_tools=("Edit",)),
        ]

    def test_coordinator_gets_the_shared_core_only(self):
        text = get_prompt(self._agents(), is_coordinator=True)
        assert text.startswith("Launch a new agent to handle complex, multi-step tasks autonomously.\n\n"
                               "The Agent tool launches specialized agents")
        listing = "Available agent types and the tools they have access to:\n- tester: Writes tests (Tools: ReadFile)\n"
        assert listing in text
        assert "- Explore: Searches (Tools: All tools except Edit)" in text
        assert text.endswith("specify a subagent_type parameter to select which agent type to use. "
                             "If omitted, the general-purpose agent is used.")
        assert "Usage notes" not in text

    def test_a_required_subagent_type_drops_the_general_purpose_default(self):
        text = get_prompt(self._agents(), is_coordinator=True, default_agent_type=None)
        assert text.endswith("specify a subagent_type parameter to select which agent type to use.")

    def test_allowed_agent_types_restrict_the_listing(self):
        text = get_prompt(self._agents(), is_coordinator=True, allowed_agent_types=["Explore"])
        assert "- tester" not in text and "- Explore" in text

    def test_the_full_prompt(self):
        text = get_prompt(self._agents())
        assert "When NOT to use the Agent tool:\n- If you want to read a specific file path, use the ReadFile tool "\
               "or the Glob tool instead of the Agent tool" in text
        assert "- Launch multiple agents concurrently whenever possible" in text
        assert "run_in_background" not in text  # background tasks arrive in Phase 8
        assert "Each Agent invocation starts fresh — provide a complete task description." in text
        assert "since it is not aware of the user's intent" in text
        assert "## Writing the prompt\n\nBrief the agent like a smart colleague" in text
        assert "Terse command-style prompts produce shallow, generic work." in text
        assert '"test-runner": use this agent after you are done writing code to run tests' in text
        assert "use the WriteFile tool to write the following code" in text
        assert "## When to fork" not in text
        assert 'isolation: "worktree"' not in text

    def test_background_notes_when_enabled(self):
        text = get_prompt(self._agents(), background_tasks_enabled=True)
        assert "- You can optionally run agents in the background using the run_in_background parameter." in text


#: The whole non-coordinator prompt, written from upstream's `prompt.ts` with this build's tool
#: names, fork off, the background notes on, and no worktree line (no `isolation` parameter).
#: The text is the contract the model reads, so it is pinned verbatim.
FULL_PROMPT = (
    "Launch a new agent to handle complex, multi-step tasks autonomously.\n"
    "\n"
    "The Agent tool launches specialized agents (subprocesses) that autonomously handle complex tasks. "
    "Each agent type has specific capabilities and tools available to it.\n"
    "\n"
    "Available agent types and the tools they have access to:\n"
    "- tester: Writes tests (Tools: ReadFile)\n"
    "- Explore: Searches (Tools: All tools except Edit)\n"
    "\n"
    "When using the Agent tool, specify a subagent_type parameter to select which agent type to use. "
    "If omitted, the general-purpose agent is used.\n"
    "\n"
    "When NOT to use the Agent tool:\n"
    "- If you want to read a specific file path, use the ReadFile tool or the Glob tool instead of the Agent "
    "tool, to find the match more quickly\n"
    "- If you are searching for a specific class definition like \"class Foo\", use the Glob tool instead, "
    "to find the match more quickly\n"
    "- If you are searching for code within a specific file or set of 2-3 files, use the ReadFile tool instead "
    "of the Agent tool, to find the match more quickly\n"
    "- Other tasks that are not related to the agent descriptions above\n"
    "\n"
    "\n"
    "Usage notes:\n"
    "- Always include a short description (3-5 words) summarizing what the agent will do\n"
    "- Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a single "
    "message with multiple tool uses\n"
    "- When the agent is done, it will return a single message back to you. The result returned by the agent is "
    "not visible to the user. To show the user the result, you should send a text message back to the user with "
    "a concise summary of the result.\n"
    "- You can optionally run agents in the background using the run_in_background parameter. When an agent runs "
    "in the background, you will be automatically notified when it completes — do NOT sleep, poll, or proactively "
    "check on its progress. Continue with other work or respond to the user instead.\n"
    "- **Foreground vs background**: Use foreground (default) when you need the agent's results before you can "
    "proceed — e.g., research agents whose findings inform your next steps. Use background when you have "
    "genuinely independent work to do in parallel.\n"
    "- To continue a previously spawned agent, use SendMessage with the agent's ID or name as the `to` field. "
    "The agent resumes with its full context preserved. Each Agent invocation starts fresh — provide a complete "
    "task description.\n"
    "- The agent's outputs should generally be trusted\n"
    "- Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, "
    "web fetches, etc.), since it is not aware of the user's intent\n"
    "- If the agent description mentions that it should be used proactively, then you should try your best to use "
    "it without the user having to ask for it first. Use your judgement.\n"
    "- If the user specifies that they want you to run agents \"in parallel\", you MUST send a single message with "
    "multiple Agent tool use content blocks. For example, if you need to launch both a build-validator agent and a "
    "test-runner agent in parallel, send a single message with both tool calls.\n"
    "\n"
    "## Writing the prompt\n"
    "\n"
    "Brief the agent like a smart colleague who just walked into the room — it hasn't seen this conversation, "
    "doesn't know what you've tried, doesn't understand why this task matters.\n"
    "- Explain what you're trying to accomplish and why.\n"
    "- Describe what you've already learned or ruled out.\n"
    "- Give enough context about the surrounding problem that the agent can make judgment calls rather than just "
    "following a narrow instruction.\n"
    "- If you need a short response, say so (\"report in under 200 words\").\n"
    "- Lookups: hand over the exact command. Investigations: hand over the question — prescribed steps become dead "
    "weight when the premise is wrong.\n"
    "\n"
    "Terse command-style prompts produce shallow, generic work.\n"
    "\n"
    "**Never delegate understanding.** Don't write \"based on your findings, fix the bug\" or \"based on the "
    "research, implement it.\" Those phrases push synthesis onto the agent instead of doing it yourself. Write "
    "prompts that prove you understood: include file paths, line numbers, what specifically to change.\n"
    "\n"
    "\n"
    "Example usage:\n"
    "\n"
    "<example_agent_descriptions>\n"
    "\"test-runner\": use this agent after you are done writing code to run tests\n"
    "\"greeting-responder\": use this agent to respond to user greetings with a friendly joke\n"
    "</example_agent_descriptions>\n"
    "\n"
    "<example>\n"
    "user: \"Please write a function that checks if a number is prime\"\n"
    "assistant: I'm going to use the WriteFile tool to write the following code:\n"
    "<code>\n"
    "function isPrime(n) {\n"
    "  if (n <= 1) return false\n"
    "  for (let i = 2; i * i <= n; i++) {\n"
    "    if (n % i === 0) return false\n"
    "  }\n"
    "  return true\n"
    "}\n"
    "</code>\n"
    "<commentary>\n"
    "Since a significant piece of code was written and the task was completed, now use the test-runner agent to "
    "run the tests\n"
    "</commentary>\n"
    "assistant: Uses the Agent tool to launch the test-runner agent\n"
    "</example>\n"
    "\n"
    "<example>\n"
    "user: \"Hello\"\n"
    "<commentary>\n"
    "Since the user is greeting, use the greeting-responder agent to respond with a friendly joke\n"
    "</commentary>\n"
    "assistant: \"I'm going to use the Agent tool to launch the greeting-responder agent\"\n"
    "</example>\n"
)


def test_the_full_prompt_is_upstreams_text_verbatim():
    agents = TestAgentToolPrompt()._agents()
    assert get_prompt(agents, background_tasks_enabled=True) == FULL_PROMPT
    # Defaults: not a coordinator, no background notes.
    without_background = get_prompt(agents)
    assert without_background.startswith(FULL_PROMPT.split("\n- You can optionally run")[0] + "\n- To continue")
    assert get_prompt(agents, False, None) == without_background


def test_the_tools_description_separator():
    defn = AgentDefinition(name="a", description="w", prompt="p", tools=("A", "B", "C"), disallowed_tools=("B",))
    assert get_tools_description(defn) == "A, C"


class TestTriageResolution:
    """Mutation triage (Phase 7 gate): every `continue` keeps filtering the rest of the pool."""

    def test_each_skipped_or_kept_tool_does_not_end_the_filter(self):
        later = _tool("ReadFile")
        for first, mode in ((_tool("mcp__s__x"), None), (_tool("ExitPlanMode"), "plan"), (_tool("Agent"), None)):
            kept = filter_tools_for_agent([first, later], is_built_in=False, permission_mode=mode)
            assert later in kept
        kept = filter_tools_for_agent([_tool("Other"), later], is_built_in=True, is_async=True)
        assert [t.name for t in kept] == ["ReadFile"]

    def test_the_definitions_mode_reaches_the_filter(self):
        defn = AgentDefinition(name="w", description="d", prompt="p", permission_mode="plan")
        res = resolve_agent_tools(defn, [_tool("ExitPlanMode"), _tool("ReadFile")])
        assert [t.name for t in res.resolved_tools] == ["ExitPlanMode", "ReadFile"]

    def test_the_wildcard_result_has_empty_lists_and_an_explicit_one_is_not_a_wildcard(self):
        tools = [_tool("ReadFile")]
        wildcard = resolve_agent_tools(AgentDefinition(name="w", description="d", prompt="p"), tools)
        assert (wildcard.valid_tools, wildcard.invalid_tools) == ((), ())
        explicit = resolve_agent_tools(
            AgentDefinition(name="w", description="d", prompt="p", tools=("ReadFile",)), tools)
        assert explicit.has_wildcard is False


class TestTriageLoader:
    def test_every_placeholder_form_is_rendered(self):
        from app.loop.agents.loader import render_prompt

        assert render_prompt("{{A}} ${B} {C}", {"A": "1", "B": "2", "C": "3"}) == "1 2 3"

    @pytest.mark.parametrize(("raw", "expected"), [(True, None), (False, None), (3.0, 3), (3.5, None), (1, 1),
                                                   ("2 turns", 2), (0, None)])
    def test_max_turns_follows_parse_positive_int(self, raw, expected):
        assert _load(_md(name="a", description="d", maxTurns=raw)).max_turns == expected

    def test_a_non_string_description_is_not_an_agent(self):
        assert load_agent_definition(_md(name="a", description=123)) is None

    def test_only_markdown_files_and_agent_dirs_are_scanned(self, tmp_path):
        from app.loop.agents.loader import _scan_agent_dir

        (tmp_path / "README.md").write_text(_md(name="readme", description="d"))
        (tmp_path / "notes.txt").write_text(_md(name="notes", description="d"))
        (tmp_path / "real.md").write_text(_md(name="real", description="d"))
        assert list(_scan_agent_dir(tmp_path, "project")) == ["real"]

    def test_html_comment_frontmatter_is_not_frontmatter(self):
        """Upstream's `FRONTMATTER_REGEX` accepts `---` fences only."""
        from app.loop.agents.loader import parse_markdown_frontmatter

        assert parse_markdown_frontmatter("<!--\nname: a\n-->\nbody") == ({}, "<!--\nname: a\n-->\nbody")
        assert parse_markdown_frontmatter("---\nname: a\n---\n body ") == ({"name": "a"}, "body")
        assert parse_markdown_frontmatter("---\n[unclosed\n---\nbody") == ({}, "body")
        assert parse_markdown_frontmatter("---\n- a list\n---\nbody") == ({}, "body")


class TestTriageFork:
    def test_messages_without_tool_calls_or_with_text_blocks_yield_no_ids(self):
        from langchain_core.messages import HumanMessage

        from app.loop.agents.fork import (
            _get_tool_result_ids_from_message,
            _get_tool_use_ids_from_assistant,
        )

        assert _get_tool_use_ids_from_assistant(HumanMessage(content="x")) == []
        assert _get_tool_result_ids_from_message(HumanMessage(content=["plain", {"type": "text", "text": "t"}])) == []


# ── Phase 8: preloaded skills ───────────────────────────────────────────────

def test_skills_parse_as_upstreams_tool_lists():
    """`parseAgentFromMarkdown`: `skills` goes through `parseSlashCommandToolsFromFrontmatter`."""
    assert _load(_md(name="a", description="d", skills="tdd-test-design, tdd-refactor-clean")).skills == (
        "tdd-test-design", "tdd-refactor-clean")
    assert _load(_md(name="a", description="d", skills=["x", "y"])).skills == ("x", "y")
    assert _load(_md(name="a", description="d")).skills == ()
