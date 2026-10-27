"""Hook event vocabulary (`events.py`) and the hook config schema (`schemas/hooks.py`)."""

from __future__ import annotations

import logging

import pytest

import app.utils.hooks.hook_events as ev
from app.schemas.hooks import (
    DEFAULT_HOOK_SHELL,
    SHELL_TYPES,
    AgentHook,
    CommandHook,
    HookMatcher,
    HookSchemaError,
    HttpHook,
    PromptHook,
    parse_hook_command,
    parse_hook_matcher,
    parse_hooks_settings,
)
from app.utils.hooks.hook_events import (
    HOOK_EVENTS,
    HookEvent,
    is_hook_event,
    match_query_for,
)

LOGGER = "TDDOrchestrator.Hooks"

UPSTREAM_EVENTS = (
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "Notification", "UserPromptSubmit",
    "SessionStart", "SessionEnd", "Stop", "StopFailure", "SubagentStart", "SubagentStop",
    "PreCompact", "PostCompact", "PermissionRequest", "PermissionDenied", "Setup",
    "TeammateIdle", "TaskCreated", "TaskCompleted", "Elicitation", "ElicitationResult",
    "ConfigChange", "WorktreeCreate", "WorktreeRemove", "InstructionsLoaded", "CwdChanged",
    "FileChanged",
)


# ── events ────────────────────────────────────────────────────────────────────

def test_all_27_events_in_upstream_order():
    assert HOOK_EVENTS == UPSTREAM_EVENTS
    assert len(HOOK_EVENTS) == 27
    assert [e.value for e in HookEvent] == list(UPSTREAM_EVENTS)


def test_timeouts():
    assert ev.TOOL_HOOK_EXECUTION_TIMEOUT_MS == 600_000
    assert ev.SESSION_END_HOOK_TIMEOUT_MS_DEFAULT == 1500


def test_tool_events():
    assert ev.TOOL_EVENTS == frozenset({"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"})


def test_is_hook_event():
    for name in UPSTREAM_EVENTS:
        assert is_hook_event(name) is True
    assert is_hook_event("pretooluse") is False
    assert is_hook_event("") is False


@pytest.mark.parametrize("event, field_name", [
    ("PreToolUse", "tool_name"),
    ("PostToolUse", "tool_name"),
    ("PostToolUseFailure", "tool_name"),
    ("PermissionRequest", "tool_name"),
    ("PermissionDenied", "tool_name"),
    ("SessionStart", "source"),
    ("Setup", "trigger"),
    ("PreCompact", "trigger"),
    ("PostCompact", "trigger"),
    ("Notification", "notification_type"),
    ("SessionEnd", "reason"),
    ("StopFailure", "error"),
    ("SubagentStart", "agent_type"),
    ("SubagentStop", "agent_type"),
    ("Elicitation", "mcp_server_name"),
    ("ElicitationResult", "mcp_server_name"),
    ("ConfigChange", "source"),
    ("InstructionsLoaded", "load_reason"),
])
def test_match_query_field_per_event(event, field_name):
    assert match_query_for({"hook_event_name": event, field_name: "VALUE"}) == "VALUE"
    assert match_query_for({"hook_event_name": event, field_name: 7}) == "7"
    assert match_query_for({"hook_event_name": event}) is None
    assert match_query_for({"hook_event_name": event, field_name: None}) is None
    # The query comes from that field only, not from a neighbour.
    other = "tool_name" if field_name != "tool_name" else "source"
    assert match_query_for({"hook_event_name": event, other: "X"}) is None


@pytest.mark.parametrize("event", [
    "Stop", "UserPromptSubmit", "TeammateIdle", "TaskCreated", "TaskCompleted",
    "WorktreeCreate", "WorktreeRemove", "CwdChanged", "Unknown",
])
def test_events_without_a_match_query(event):
    assert match_query_for({"hook_event_name": event, "tool_name": "X", "source": "s", "reason": "r"}) is None


def test_file_changed_matches_the_basename():
    assert match_query_for({"hook_event_name": "FileChanged", "file_path": "/a/b/c.py"}) == "c.py"
    assert match_query_for({"hook_event_name": "FileChanged", "file_path": "c.py"}) == "c.py"
    assert match_query_for({"hook_event_name": "FileChanged"}) is None


def test_missing_event_name_has_no_query():
    assert match_query_for({}) is None
    assert match_query_for({"tool_name": "Bash"}) is None


# ── schemas: hook types ───────────────────────────────────────────────────────

def test_shell_constants():
    assert SHELL_TYPES == ("bash", "powershell")
    assert DEFAULT_HOOK_SHELL == "bash"


def test_type_properties():
    assert CommandHook(command="x").type == "command"
    assert PromptHook(prompt="x").type == "prompt"
    assert AgentHook(prompt="x").type == "agent"
    assert HttpHook(url="https://x").type == "http"


def test_command_hook_all_fields():
    hook = parse_hook_command({
        "type": "command", "command": "echo hi", "if": "Bash(git *)", "shell": "powershell",
        "timeout": 5, "statusMessage": "checking", "once": True, "async": True, "asyncRewake": False,
        "unknown": "stripped",
    })
    assert hook == CommandHook(
        command="echo hi", if_condition="Bash(git *)", shell="powershell", timeout=5.0,
        status_message="checking", once=True, run_async=True, async_rewake=False,
    )
    assert isinstance(hook.timeout, float)


def test_command_hook_defaults():
    assert parse_hook_command({"type": "command", "command": "x"}) == CommandHook(command="x")
    hook = CommandHook(command="x")
    assert (hook.if_condition, hook.shell, hook.timeout, hook.status_message, hook.once, hook.run_async,
            hook.async_rewake) == (None, None, None, None, None, None, None)


def test_prompt_and_agent_hooks():
    raw = {"prompt": "check $ARGUMENTS", "if": "Edit", "timeout": 1.5, "model": "m",
           "statusMessage": "s", "once": False}
    assert parse_hook_command({"type": "prompt", **raw}) == PromptHook(
        prompt="check $ARGUMENTS", if_condition="Edit", timeout=1.5, model="m", status_message="s", once=False)
    assert parse_hook_command({"type": "agent", **raw}) == AgentHook(
        prompt="check $ARGUMENTS", if_condition="Edit", timeout=1.5, model="m", status_message="s", once=False)
    assert parse_hook_command({"type": "prompt", "prompt": "p"}) == PromptHook(prompt="p")
    assert parse_hook_command({"type": "agent", "prompt": "p"}) == AgentHook(prompt="p")


def test_http_hook_all_fields():
    hook = parse_hook_command({
        "type": "http", "url": "https://hooks.example/x", "if": "Bash", "timeout": 3,
        "headers": {"Authorization": "Bearer $T", "X": "y"}, "allowedEnvVars": ["T"],
        "statusMessage": "posting", "once": True,
    })
    assert hook == HttpHook(
        url="https://hooks.example/x", if_condition="Bash", timeout=3.0,
        headers=(("Authorization", "Bearer $T"), ("X", "y")), allowed_env_vars=("T",),
        status_message="posting", once=True,
    )
    assert parse_hook_command({"type": "http", "url": "http://h"}) == HttpHook(url="http://h")
    assert HttpHook(url="u").headers == () and HttpHook(url="u").allowed_env_vars == ()


@pytest.mark.parametrize("raw, message", [
    ("not a dict", "a hook must be an object"),
    ([], "a hook must be an object"),
    ({"type": "command"}, "'command' must be a string"),
    ({"type": "command", "command": 3}, "'command' must be a string"),
    ({"type": "command", "command": "x", "shell": "zsh"}, "'shell' must be one of bash, powershell"),
    ({"type": "command", "command": "x", "shell": 1}, "'shell' must be a string"),
    ({"type": "command", "command": "x", "async": "yes"}, "'async' must be a boolean"),
    ({"type": "command", "command": "x", "asyncRewake": 1}, "'asyncRewake' must be a boolean"),
    ({"type": "command", "command": "x", "once": "no"}, "'once' must be a boolean"),
    ({"type": "command", "command": "x", "if": 5}, "'if' must be a string"),
    ({"type": "command", "command": "x", "statusMessage": 5}, "'statusMessage' must be a string"),
    ({"type": "command", "command": "x", "timeout": 0}, "'timeout' must be a positive number"),
    ({"type": "command", "command": "x", "timeout": -1}, "'timeout' must be a positive number"),
    ({"type": "command", "command": "x", "timeout": "5"}, "'timeout' must be a positive number"),
    ({"type": "command", "command": "x", "timeout": True}, "'timeout' must be a positive number"),
    ({"type": "prompt"}, "'prompt' must be a string"),
    ({"type": "prompt", "prompt": "p", "model": 1}, "'model' must be a string"),
    ({"type": "agent", "prompt": None}, "'prompt' must be a string"),
    ({"type": "agent", "prompt": "p", "model": []}, "'model' must be a string"),
    ({"type": "http"}, "'url' must be a string"),
    ({"type": "http", "url": "not a url"}, "'url' must be a valid URL"),
    ({"type": "http", "url": "//host/path"}, "'url' must be a valid URL"),
    ({"type": "http", "url": "https://h", "headers": ["a"]}, "'headers' must map strings to strings"),
    ({"type": "http", "url": "https://h", "headers": {"a": 1}}, "'headers' must map strings to strings"),
    ({"type": "http", "url": "https://h", "headers": {1: "a"}}, "'headers' must map strings to strings"),
    ({"type": "http", "url": "https://h", "allowedEnvVars": "T"}, "'allowedEnvVars' must be a list of strings"),
    ({"type": "http", "url": "https://h", "allowedEnvVars": [1]}, "'allowedEnvVars' must be a list of strings"),
    ({"type": "callback"}, "unknown hook type: 'callback'"),
    ({}, "unknown hook type: None"),
])
def test_schema_rejections(raw, message):
    with pytest.raises(HookSchemaError) as exc:
        parse_hook_command(raw)
    assert str(exc.value) == message


def test_small_positive_timeout_and_url_forms_accepted():
    assert parse_hook_command({"type": "command", "command": "x", "timeout": 0.001}).timeout == 0.001
    assert parse_hook_command({"type": "http", "url": "mailto:someone"}) == HttpHook(url="mailto:someone")
    assert parse_hook_command({"type": "http", "url": "https://h", "headers": None}) == HttpHook(url="https://h")


def test_schema_error_is_a_value_error():
    assert issubclass(HookSchemaError, ValueError)


# ── schemas: matchers and settings ────────────────────────────────────────────

def test_matcher_parsing(caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        m = parse_hook_matcher(
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "a"}, {"type": "bogus"}]}, " in x"
        )
    assert m == HookMatcher(hooks=(CommandHook(command="a"),), matcher="Bash")
    assert [r.getMessage() for r in caplog.records] == ["Ignoring invalid hook in x: unknown hook type: 'bogus'"]
    assert parse_hook_matcher({"hooks": []}) == HookMatcher(hooks=(), matcher=None)
    assert HookMatcher(hooks=()).matcher is None


@pytest.mark.parametrize("raw, message", [
    ("x", "Ignoring malformed hook matcher in f: not an object"),
    ({"matcher": 1, "hooks": []}, "Ignoring hook matcher in f: 'matcher' must be a string"),
    ({"matcher": "a"}, "Ignoring hook matcher in f: 'hooks' must be a list"),
    ({"hooks": {}}, "Ignoring hook matcher in f: 'hooks' must be a list"),
])
def test_malformed_matchers(caplog, raw, message):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert parse_hook_matcher(raw, " in f") is None
    assert [r.getMessage() for r in caplog.records] == [message]


def test_matcher_default_where_is_empty(caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        parse_hook_matcher("x")
    assert caplog.records[0].getMessage() == "Ignoring malformed hook matcher: not an object"


def test_settings_parsing(caplog):
    raw = {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "a"}]}, "bad"],
        "Stop": [],
        "Nope": [],
        "PostToolUse": "x",
    }
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        parsed = parse_hooks_settings(raw, origin="settings.json")
    assert parsed == {
        "PreToolUse": (HookMatcher(hooks=(CommandHook(command="a"),), matcher="Bash"),),
        "Stop": (),
    }
    assert [r.getMessage() for r in caplog.records] == [
        "Ignoring malformed hook matcher in settings.json: not an object",
        "Ignoring hooks for unknown event 'Nope' in settings.json",
        "Ignoring hooks for PostToolUse in settings.json: not a list",
    ]


def test_settings_none_and_non_mapping(caplog):
    assert parse_hooks_settings(None) == {}
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert parse_hooks_settings([1]) == {}
        assert parse_hooks_settings("x", origin="o") == {}
        assert parse_hooks_settings({1: []}) == {}
    assert [r.getMessage() for r in caplog.records] == [
        "Ignoring 'hooks': not an object",
        "Ignoring 'hooks' in o: not an object",
        "Ignoring hooks for unknown event 1",
    ]
