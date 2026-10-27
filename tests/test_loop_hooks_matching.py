"""Which configured hooks run for one event (`app/utils/hooks/hooks.py`)."""

from __future__ import annotations

import logging
from typing import Any

import app.utils.hooks.hooks as matching_mod
from app.schemas.hooks import (
    AgentHook,
    CommandHook,
    HookMatcher,
    HttpHook,
    PromptHook,
)
from app.tool import ToolResult, build_tool
from app.utils.hooks.hooks import (
    MatchedHook,
    get_hooks_config,
    get_legacy_tool_names,
    get_matching_hooks,
    hook_display_text,
    matches_pattern,
    prepare_if_condition_matcher,
)
from app.utils.hooks.hooks_config_snapshot import HooksConfigSnapshot

# ── matchesPattern ────────────────────────────────────────────────────────────


def test_empty_and_star_match_everything():
    assert matches_pattern("Bash", None) is True
    assert matches_pattern("Bash", "") is True
    assert matches_pattern("Bash", "*") is True


def test_exact_and_pipe():
    assert matches_pattern("Bash", "Bash") is True
    assert matches_pattern("Bash", "Bas") is False
    assert matches_pattern("BashX", "Bash") is False
    assert matches_pattern("Edit", "Write|Edit") is True
    assert matches_pattern("Edit", "Write | Edit") is False  # spaces make it a regex: "Write | Edit"
    assert matches_pattern("Read", "Write|Edit") is False
    assert matches_pattern("Writ", "Write|Edit") is False


def test_legacy_names_in_simple_matchers():
    assert get_legacy_tool_names("Agent") == ["Task"]
    assert get_legacy_tool_names("TaskOutput") == ["AgentOutputTool", "BashOutputTool"]
    assert get_legacy_tool_names("Bash") == []
    assert matches_pattern("Agent", "Task") is True
    assert matches_pattern("Agent", "Task|Bash") is True
    assert matches_pattern("Task", "Task") is False  # the query is already normalised by the caller


def test_regex_matchers():
    assert matches_pattern("WriteFile", "^Write.*") is True
    assert matches_pattern("Edit", "^(Write|Edit)$") is True
    assert matches_pattern("ReadFile", "^Write.*") is False
    assert matches_pattern("mcp__srv__tool", "mcp__.*") is True
    assert matches_pattern("xEdit", "Ed.t") is True  # search, not fullmatch
    # A regex also tries the query's legacy names.
    assert matches_pattern("Agent", "^Task$") is True
    assert matches_pattern("Bash", "^Task$") is False


def test_invalid_regex_matches_nothing(caplog):
    with caplog.at_level(logging.DEBUG, logger="TDDOrchestrator.Hooks"):
        assert matches_pattern("Bash", "([") is False
    assert "Invalid regex pattern in hook matcher: ([" in [r.getMessage() for r in caplog.records]


# ── prepareIfConditionMatcher ─────────────────────────────────────────────────

def _tool(name: str = "Bash", matcher: Any = None, aliases: tuple[str, ...] = ()) -> Any:
    async def call(args: dict[str, Any], ctx: Any) -> ToolResult:  # pragma: no cover - never called
        return ToolResult(content="")

    return build_tool(
        name=name, prompt="p", call=call, aliases=aliases,
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
        prepare_permission_matcher=matcher,
    )


def _git_matcher(args: dict[str, Any]) -> Any:
    command = args["command"]
    return lambda content: content == command


def _input(event: str = "PreToolUse", tool: str = "Bash", **tool_input: Any) -> dict[str, Any]:
    return {"hook_event_name": event, "tool_name": tool, "tool_input": tool_input}


def test_non_tool_events_have_no_if_matcher():
    for event in ("Stop", "SessionStart", "UserPromptSubmit", "PermissionDenied", "SubagentStop"):
        assert prepare_if_condition_matcher({"hook_event_name": event, "tool_name": "Bash"}, []) is None


def test_tool_events_have_an_if_matcher():
    for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"):
        m = prepare_if_condition_matcher(_input(event, command="ls"), [_tool(matcher=_git_matcher)])
        assert m is not None and m("Bash(ls)") is True


def test_if_matcher_tool_name_and_content():
    m = prepare_if_condition_matcher(_input(command="git push"), [_tool(matcher=_git_matcher)])
    assert m is not None
    assert m("Bash") is True
    assert m("Bash(git push)") is True
    assert m("Bash(git pull)") is False
    assert m("Edit") is False
    assert m("Edit(git push)") is False


def test_if_matcher_with_legacy_tool_names():
    m = prepare_if_condition_matcher(_input(tool="Agent", command="x"), [_tool("Agent", _git_matcher)])
    assert m is not None
    assert m("Task") is True
    assert m("Agent") is True
    agent = _tool("Agent", _git_matcher, aliases=("Task",))
    legacy_query = prepare_if_condition_matcher(_input(tool="Task", command="x"), [agent])
    assert legacy_query is not None and legacy_query("Agent(x)") is True


def test_if_matcher_without_a_content_matcher():
    # The tool has none, the input fails its schema, the tool is unknown, or the input is not a mapping:
    # a bare `Tool` condition still matches, a content condition never does.
    cases = [
        prepare_if_condition_matcher(_input(command="ls"), [_tool(matcher=None)]),
        prepare_if_condition_matcher(_input(command=5), [_tool(matcher=_git_matcher)]),
        prepare_if_condition_matcher(_input(command="ls"), [_tool("Other", _git_matcher)]),
        prepare_if_condition_matcher(_input(command="ls"), None),
        prepare_if_condition_matcher({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": "x"},
                                     [_tool(matcher=_git_matcher)]),
    ]
    for m in cases:
        assert m is not None
        assert m("Bash") is True
        assert m("Bash(ls)") is False


def test_if_matcher_missing_tool_name():
    m = prepare_if_condition_matcher({"hook_event_name": "PreToolUse"}, [])
    assert m is not None
    assert m("Bash") is False


def test_find_tool_prefers_primary_name():
    primary = _tool("Bash", _git_matcher)
    aliased = _tool("Shell", lambda args: (lambda c: True), aliases=("Bash",))
    m = prepare_if_condition_matcher(_input(command="git"), [aliased, primary])
    assert m is not None
    assert m("Bash(other)") is False  # the primary-name tool's matcher decided


# ── getMatchingHooks ──────────────────────────────────────────────────────────

def _names(hooks: list[MatchedHook]) -> list[str]:
    return [hook_display_text(m.hook) for m in hooks]


def _snap(event: str, *matchers: HookMatcher, managed_only: bool = False) -> HooksConfigSnapshot:
    return HooksConfigSnapshot(hooks={event: tuple(matchers)}, managed_only=managed_only)


def test_get_hooks_config_with_session_matchers():
    base = HookMatcher(hooks=(CommandHook(command="a"),))
    extra = HookMatcher(hooks=(CommandHook(command="b"),))
    snap = _snap("Stop", base)
    assert get_hooks_config(snap, "Stop") == [base]
    assert get_hooks_config(snap, "Stop", {"Stop": [extra]}) == [base, extra]
    assert get_hooks_config(snap, "Stop", {"PreToolUse": [extra]}) == [base]
    managed = _snap("Stop", base, managed_only=True)
    assert get_hooks_config(managed, "Stop", {"Stop": [extra]}) == [base]
    assert get_hooks_config(snap, "PreToolUse") == []


def test_matcher_filter_by_query():
    bash = HookMatcher(matcher="Bash", hooks=(CommandHook(command="bash-hook"),))
    edit = HookMatcher(matcher="Edit", hooks=(CommandHook(command="edit-hook"),))
    every = HookMatcher(hooks=(CommandHook(command="all"),))
    snap = _snap("PreToolUse", bash, edit, every)
    hooks = get_matching_hooks(snap, "PreToolUse", _input(command="ls"))
    assert _names(hooks) == ["bash-hook", "all"]
    assert all(m.hook_source == "settings" for m in hooks)


def test_no_query_means_every_matcher():
    snap = _snap("Stop", HookMatcher(matcher="Bash", hooks=(CommandHook(command="a"),)),
                 HookMatcher(matcher="nope", hooks=(CommandHook(command="b"),)))
    hooks = get_matching_hooks(snap, "Stop", {"hook_event_name": "Stop"})
    assert _names(hooks) == ["a", "b"]


def test_empty_query_skips_matcher_filtering():
    snap = _snap("SessionStart", HookMatcher(matcher="startup", hooks=(CommandHook(command="a"),)))
    hooks = get_matching_hooks(snap, "SessionStart", {"hook_event_name": "SessionStart", "source": ""})
    assert _names(hooks) == ["a"]
    other = get_matching_hooks(snap, "SessionStart", {"hook_event_name": "SessionStart", "source": "resume"})
    assert other == []


def test_dedup_by_type_order_and_last_wins():
    first = CommandHook(command="dup", status_message="first")
    last = CommandHook(command="dup", status_message="last")
    ps = CommandHook(command="dup", shell="powershell")
    bash_explicit = CommandHook(command="dup", shell="bash", status_message="explicit")
    gated = CommandHook(command="dup", if_condition="Bash")
    prompt_a = PromptHook(prompt="p", model="a")
    prompt_b = PromptHook(prompt="p", model="b")
    agent = AgentHook(prompt="p")
    http_a = HttpHook(url="https://h", timeout=1)
    http_b = HttpHook(url="https://h", timeout=2)
    other = CommandHook(command="other")
    snap = _snap("Stop", HookMatcher(hooks=(http_a, first, prompt_a, agent, ps, other)),
                 HookMatcher(hooks=(last, prompt_b, http_b, gated, bash_explicit)))
    hooks = [m.hook for m in get_matching_hooks(snap, "Stop", {"hook_event_name": "Stop"})]
    # Commands first (dup keeps its first position, takes the last value; shell defaults to bash),
    # then prompts, agents, HTTP. The `if` hook is dropped: Stop is not a tool event.
    assert hooks == [bash_explicit, ps, other, prompt_b, agent, http_b]


def test_agent_and_prompt_with_same_prompt_do_not_collide():
    snap = _snap("Stop", HookMatcher(hooks=(AgentHook(prompt="p"), PromptHook(prompt="p"))))
    kinds = [m.hook.type for m in get_matching_hooks(snap, "Stop", {"hook_event_name": "Stop"})]
    assert kinds == ["prompt", "agent"]


def test_if_conditions_filter_tool_hooks(caplog):
    yes = CommandHook(command="yes", if_condition="Bash(git push)")
    no = CommandHook(command="no", if_condition="Bash(git pull)")
    plain = CommandHook(command="plain")
    snap = _snap("PreToolUse", HookMatcher(hooks=(yes, no, plain)))
    with caplog.at_level(logging.DEBUG, logger="TDDOrchestrator.Hooks"):
        hooks = get_matching_hooks(snap, "PreToolUse", _input(command="git push"), [_tool(matcher=_git_matcher)])
    assert [m.hook for m in hooks] == [yes, plain]


def test_if_conditions_on_a_non_tool_event_are_skipped(caplog):
    gated = CommandHook(command="g", if_condition="Bash")
    snap = _snap("Stop", HookMatcher(hooks=(gated, CommandHook(command="p"))))
    with caplog.at_level(logging.DEBUG, logger="TDDOrchestrator.Hooks"):
        hooks = get_matching_hooks(snap, "Stop", {"hook_event_name": "Stop"})
    assert _names(hooks) == ["p"]
    assert "Hook if condition 'Bash' cannot be evaluated for non-tool event Stop" in [
        r.getMessage() for r in caplog.records]


def test_no_if_condition_never_prepares_a_matcher(monkeypatch):
    def boom(*a: Any) -> Any:
        raise AssertionError("should not be called")

    monkeypatch.setattr(matching_mod, "prepare_if_condition_matcher", boom)
    snap = _snap("PreToolUse", HookMatcher(hooks=(CommandHook(command="a"),)))
    assert _names(get_matching_hooks(snap, "PreToolUse", _input(command="x"))) == ["a"]


def test_http_hooks_never_run_for_session_start_or_setup():
    http = HttpHook(url="https://h")
    cmd = CommandHook(command="c")
    for event, field_name in (("SessionStart", "source"), ("Setup", "trigger")):
        snap = _snap(event, HookMatcher(hooks=(http, cmd)))
        assert [m.hook for m in get_matching_hooks(snap, event, {"hook_event_name": event, field_name: "x"})] == [cmd]
    snap = _snap("Stop", HookMatcher(hooks=(http, cmd)))
    assert [m.hook for m in get_matching_hooks(snap, "Stop", {"hook_event_name": "Stop"})] == [cmd, http]


def test_session_matchers_follow_the_snapshot_unless_managed_only():
    snap = _snap("Stop", HookMatcher(hooks=(CommandHook(command="a"),)))
    session = {"Stop": [HookMatcher(hooks=(CommandHook(command="s"),))]}
    stop = {"hook_event_name": "Stop"}
    names = _names(get_matching_hooks(snap, "Stop", stop, None, session))
    assert names == ["a", "s"]
    managed = HooksConfigSnapshot(hooks=dict(snap.hooks), managed_only=True)
    names = _names(get_matching_hooks(managed, "Stop", stop, None, session))
    assert names == ["a"]


def test_a_failure_means_no_hooks(monkeypatch, caplog):
    def boom(*a: Any) -> Any:
        raise RuntimeError("x")

    monkeypatch.setattr(matching_mod, "get_hooks_config", boom)
    with caplog.at_level(logging.ERROR, logger="TDDOrchestrator.Hooks"):
        assert get_matching_hooks(HooksConfigSnapshot(), "Stop", {"hook_event_name": "Stop"}) == []
    assert caplog.records[0].getMessage() == "Failed to collect matching hooks for Stop"


def test_matched_hook_default_source():
    assert MatchedHook(hook=CommandHook(command="c")).hook_source == "settings"


def test_hook_display_text():
    assert hook_display_text(CommandHook(command="c")) == "c"
    assert hook_display_text(PromptHook(prompt="p")) == "p"
    assert hook_display_text(AgentHook(prompt="a")) == "a"
    assert hook_display_text(HttpHook(url="https://u")) == "https://u"
    assert hook_display_text(CommandHook(command="c", status_message="Checking")) == "Checking"
    assert hook_display_text(HttpHook(url="https://u", status_message="")) == "https://u"
