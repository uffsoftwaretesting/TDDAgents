"""
The loop's Bash tool (port of claude-code's `BashTool`).

Injection detection is a deterministic permission check (`check_permissions`), never a
step inside `call`, and never an LLM: these tests run fully offline.
"""

import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolCall

from app.loop.context import AppStateStore, tool_context_for
from app.loop.permissions.gate import has_permissions_to_use_tool
from app.loop.permissions.types import PermissionMode, ToolPermissionContext
from app.loop.tools import bash as bash_mod
from app.loop.tools.bash import (
    DEFAULT_TIMEOUT_MS,
    MAX_TIMEOUT_MS,
    build_bash_tool,
    check_bash_permissions,
    get_default_bash_timeout_ms,
    get_max_bash_timeout_ms,
    validate_bash_input,
)
from app.loop.tools.execution import run_tool_use
from app.workspace.base import CommandResult
from app.workspace.local import LocalWorkspace
from tests.conftest import FakeWorkspace


def run(coro):
    return asyncio.run(coro)


def ctx_for(ws=None, permission_context=None):
    return tool_context_for(AppStateStore(), workspace=ws, permission_context=permission_context)


class RecordingWorkspace(FakeWorkspace):
    def __init__(self):
        super().__init__()
        self.timeouts = []

    def execute(self, cmd, timeout=None, env=None):
        self.timeouts.append(timeout)
        return super().execute(cmd, timeout, env)


# ── call ─────────────────────────────────────────────────────────────────────

def test_simple_execution_on_local_workspace(tmp_path):
    res = run(build_bash_tool().call({"command": "echo 'hello world'"}, ctx_for(LocalWorkspace(str(tmp_path)))))
    assert res.is_error is False
    assert res.content == "hello world"
    assert res.exit_code == 0


def test_non_zero_exit_is_an_error_result_carrying_the_code(tmp_path):
    res = run(build_bash_tool().call({"command": "echo out; echo err >&2; exit 3"},
                                     ctx_for(LocalWorkspace(str(tmp_path)))))
    assert res.is_error is True
    assert res.exit_code == 3
    assert res.content == "out\n\nSTDERR:\nerr"


def test_no_output_message():
    ws = FakeWorkspace()
    res = run(build_bash_tool().call({"command": "true"}, ctx_for(ws)))
    assert res.content == "Command executed successfully (no output)."
    assert res.is_error is False
    assert res.exit_code == 0
    assert ws.command_log == ["true"]


def test_stderr_only_output():
    ws = FakeWorkspace()
    ws.command_results["c"] = CommandResult(stdout="", stderr="warn", exit_code=0, duration=0.0, workspace="sandbox")
    res = run(build_bash_tool().call({"command": "c"}, ctx_for(ws)))
    assert res.content == "STDERR:\nwarn"
    assert res.is_error is False


def test_missing_command_runs_empty_string():
    ws = FakeWorkspace()
    run(build_bash_tool().call({}, ctx_for(ws)))
    assert ws.command_log == [""]


def test_no_workspace_is_an_error():
    res = run(build_bash_tool().call({"command": "ls"}, ctx_for(None)))
    assert res.is_error is True
    assert res.content == "No workspace available"


def test_execution_exception_is_an_error_result():
    class Boom(FakeWorkspace):
        def execute(self, cmd, timeout=None, env=None):
            raise RuntimeError("sandbox gone")

    res = run(build_bash_tool().call({"command": "ls"}, ctx_for(Boom())))
    assert res.is_error is True
    assert res.content == "Execution error: sandbox gone"


def test_call_does_not_run_security_checks():
    """Security lives in check_permissions; call() just executes."""
    ws = FakeWorkspace()
    run(build_bash_tool().call({"command": "echo `id`"}, ctx_for(ws)))
    assert ws.command_log == ["echo `id`"]


# ── timeouts (src/utils/timeouts.ts) ─────────────────────────────────────────

def test_timeout_constants():
    assert DEFAULT_TIMEOUT_MS == 120_000
    assert MAX_TIMEOUT_MS == 600_000


def test_explicit_timeout_is_passed_in_seconds():
    ws = RecordingWorkspace()
    run(build_bash_tool().call({"command": "x", "timeout": 5000}, ctx_for(ws)))
    assert ws.timeouts == [5.0]


def test_default_timeout_when_omitted_or_zero(monkeypatch):
    monkeypatch.delenv("BASH_DEFAULT_TIMEOUT_MS", raising=False)
    ws = RecordingWorkspace()
    run(build_bash_tool().call({"command": "x"}, ctx_for(ws)))
    run(build_bash_tool().call({"command": "x", "timeout": 0}, ctx_for(ws)))
    assert ws.timeouts == [120.0, 120.0]


def test_default_timeout_env_override(monkeypatch):
    monkeypatch.setenv("BASH_DEFAULT_TIMEOUT_MS", "3000")
    ws = RecordingWorkspace()
    run(build_bash_tool().call({"command": "x"}, ctx_for(ws)))
    assert ws.timeouts == [3.0]


@pytest.mark.parametrize("value, expected", [
    (None, 120_000), ("", 120_000), ("abc", 120_000), ("0", 120_000), ("-5", 120_000),
    ("4000", 4000), (" 4000ms", 4000), ("+7", 7), ("12.9", 12), ("1", 1),
])
def test_get_default_bash_timeout_ms(value, expected):
    env = {} if value is None else {"BASH_DEFAULT_TIMEOUT_MS": value}
    assert get_default_bash_timeout_ms(env) == expected


@pytest.mark.parametrize("env, expected", [
    ({}, 600_000),
    ({"BASH_MAX_TIMEOUT_MS": "900000"}, 900_000),
    ({"BASH_MAX_TIMEOUT_MS": "1000"}, 120_000),
    ({"BASH_MAX_TIMEOUT_MS": "1000", "BASH_DEFAULT_TIMEOUT_MS": "5000"}, 5000),
    ({"BASH_DEFAULT_TIMEOUT_MS": "700000"}, 700_000),
    ({"BASH_MAX_TIMEOUT_MS": "junk"}, 600_000),
])
def test_get_max_bash_timeout_ms(env, expected):
    assert get_max_bash_timeout_ms(env) == expected


def test_timeout_helpers_read_process_env_by_default(monkeypatch):
    monkeypatch.setenv("BASH_DEFAULT_TIMEOUT_MS", "4321")
    monkeypatch.setenv("BASH_MAX_TIMEOUT_MS", "999999")
    assert get_default_bash_timeout_ms() == 4321
    assert get_max_bash_timeout_ms() == 999999


# ── validate_input (schema) ──────────────────────────────────────────────────

@pytest.mark.parametrize("args, valid, message", [
    ({"command": "ls"}, True, ""),
    ({"command": "ls", "timeout": 10}, True, ""),
    ({"command": "ls", "timeout": 1.5}, True, ""),
    ({"command": "ls", "description": "List"}, True, ""),
    ({}, False, "command must be a string."),
    ({"command": 3}, False, "command must be a string."),
    ({"command": "ls", "timeout": "10"}, False, "timeout must be a number of milliseconds."),
    ({"command": "ls", "timeout": True}, False, "timeout must be a number of milliseconds."),
    ({"command": "ls", "description": 1}, False, "description must be a string."),
])
def test_validate_bash_input(args, valid, message):
    res = validate_bash_input(args, ctx_for())
    assert res.valid is valid
    assert res.message == message


def test_tool_wires_validate_input():
    assert build_bash_tool().validate_input({"command": 1}, ctx_for()).valid is False


# ── metadata ─────────────────────────────────────────────────────────────────

def test_read_only_and_concurrency_safety_are_per_input():
    tool = build_bash_tool()
    assert tool.is_read_only({"command": "ls -la"}) is True
    assert tool.is_concurrency_safe({"command": "ls -la"}) is True
    assert tool.is_read_only({"command": "pip install requests"}) is False
    assert tool.is_concurrency_safe({"command": "pip install requests"}) is False
    assert tool.is_read_only({"command": "echo x > f"}) is False


def test_metadata(monkeypatch):
    tool = build_bash_tool()
    assert tool.name == "Bash"
    assert tool.description({"command": "ls"}) == "Run shell command"
    assert tool.description({"command": "ls", "description": "List files"}) == "List files"
    schema = tool.input_schema
    assert schema["required"] == ["command"]
    assert set(schema["properties"]) == {"command", "timeout", "description"}
    assert schema["properties"]["command"]["type"] == "string"
    assert schema["properties"]["timeout"]["type"] == "number"
    assert schema["properties"]["description"]["type"] == "string"
    assert schema["properties"]["timeout"]["description"].endswith(f"(max {get_max_bash_timeout_ms()})")
    assert not tool.prompt.startswith("---")
    assert "name: Bash" not in tool.prompt
    assert "injection" in tool.prompt


def test_prompt_fallback_when_file_missing(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setattr(bash_mod, "PROMPT_PATH", tmp_path / "missing.md")
    src = bash_mod.PROMPT_PATH
    assert not src.exists()
    reloaded = importlib.reload(bash_mod)
    try:
        assert reloaded.BASH_PROMPT.startswith("Runs a command")
    finally:
        importlib.reload(bash_mod)


# ── check_permissions → the security gates ───────────────────────────────────

@pytest.mark.parametrize("command", ["ls -la", "git status", ""])
def test_read_only_commands_are_allowed(command):
    res = run(check_bash_permissions({"command": command}, ctx_for()))
    assert res.behavior == "allow"
    if command:
        assert res.decision_reason is not None and res.decision_reason["type"] == "subcommandResults"
        inner = res.decision_reason["reasons"][command]
        assert inner.decision_reason == {"type": "other", "reason": "Read-only command is allowed"}


@pytest.mark.parametrize("command", ["pytest -q tests/", "python -m pytest 2>&1", "pip install requests"])
def test_clean_non_read_only_commands_pass_through_without_a_rule(command):
    """Upstream: nothing resolved it, so `passthrough`; the runtime gate makes that an ask."""
    res = run(check_bash_permissions({"command": command}, ctx_for()))
    assert res.behavior == "passthrough"
    assert res.message == "This command requires approval"


def test_missing_command_is_checked_as_empty():
    assert run(check_bash_permissions({}, ctx_for())).behavior == "allow"


@pytest.mark.parametrize("command, reason", [
    ("git diff $(rm -rf /)", "Command contains $() command substitution"),
    ("echo `id`", "Command contains backticks (`) for command substitution"),
    ("zmodload zsh/system", "Command uses Zsh-specific 'zmodload' which can bypass security checks"),
])
def test_flagged_commands_ask_with_validator_reason(command, reason):
    res = run(check_bash_permissions({"command": command}, ctx_for()))
    assert res.behavior == "ask"
    assert res.message == reason
    assert res.decision_reason == {"type": "other", "reason": reason}


def test_output_redirection_inside_cwd_needs_approval_in_default_mode(tmp_path):
    """Upstream `isPathAllowed`: a create in the working dir needs acceptEdits or a rule."""
    res = run(check_bash_permissions({"command": "echo hi > out.txt"}, ctx_for(LocalWorkspace(str(tmp_path)))))
    assert res.behavior == "ask"
    assert res.message == (
        f"Output redirection to '{tmp_path / 'out.txt'}' was blocked. For security, TDDAgents may only write "
        f"to files in the allowed working directories for this session: '{tmp_path}'."
    )


def test_allow_rule_resolves_and_headless_gate_denies_the_rest():
    from app.loop.permissions.types import PermissionBehavior, PermissionRule, PermissionRuleSource

    rule = PermissionRule(tool_name="Bash", rule_behavior=PermissionBehavior.ALLOW, rule_content="pytest:*",
                          source=PermissionRuleSource.PROJECT_SETTINGS)
    perm = ToolPermissionContext(always_allow_rules=(rule,), should_avoid_permission_prompts=True)
    tool = build_bash_tool()
    allowed = run(has_permissions_to_use_tool(tool, {"command": "pytest -q"}, ctx_for(permission_context=perm)))
    assert allowed.behavior == "allow"
    assert allowed.decision_reason is not None
    assert allowed.decision_reason["reasons"]["pytest -q"].decision_reason == {"type": "rule", "rule": rule}
    denied = run(has_permissions_to_use_tool(tool, {"command": "pip install x"}, ctx_for(permission_context=perm)))
    assert denied.behavior == "deny"
    assert denied.message.startswith("Permission to use Bash has been denied. IMPORTANT:")
    assert denied.decision_reason == {"type": "asyncAgent",
                                      "reason": "Permission prompts are not available in this context"}


def test_tool_check_permissions_is_the_security_check():
    tool = build_bash_tool()
    assert run(tool.check_permissions({"command": "echo $(id)"}, ctx_for())).behavior == "ask"
    assert run(tool.check_permissions({"command": "ls"}, ctx_for())).behavior == "allow"


def test_runtime_gate_refuses_flagged_command_in_default_mode():
    tool = build_bash_tool()
    res = run(has_permissions_to_use_tool(tool, {"command": "echo $(id)"}, ctx_for()))
    assert res.behavior == "ask"
    assert res.message == "Command contains $() command substitution"


def test_runtime_gate_bypass_mode_overrides_security_ask_like_upstream():
    """A decisionReason of type 'other' is not bypass-immune upstream either."""
    tool = build_bash_tool()
    perm = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
    res = run(has_permissions_to_use_tool(tool, {"command": "echo $(id)"}, ctx_for(permission_context=perm)))
    assert res.behavior == "allow"


def test_run_tool_use_refuses_flagged_command_without_executing():
    ws = FakeWorkspace()
    tool = build_bash_tool()
    ctx = tool_context_for(AppStateStore(), workspace=ws, tools=(tool,))
    call = ToolCall(name="Bash", args={"command": "cat /proc/self/environ"}, id="c1")
    outcome = run(run_tool_use(call, AIMessage(content=""), ctx, (tool,)))
    assert ws.command_log == []
    assert outcome.message.status == "error"
    assert "environ" in outcome.message.content


def test_run_tool_use_executes_clean_command():
    ws = FakeWorkspace()
    tool = build_bash_tool()
    ctx = tool_context_for(AppStateStore(), workspace=ws, tools=(tool,))
    call = ToolCall(name="Bash", args={"command": "ls"}, id="c2")
    outcome = run(run_tool_use(call, AIMessage(content=""), ctx, (tool,)))
    assert ws.command_log == ["ls"]
    assert outcome.message.status == "success"


def test_input_schema_is_exact():
    assert build_bash_tool().input_schema == {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to execute"},
            "timeout": {
                "type": "number",
                "description": f"Optional timeout in milliseconds (max {get_max_bash_timeout_ms()})",
            },
            "description": {
                "type": "string",
                "description": "Clear, concise description of what this command does in active voice.",
            },
        },
        "required": ["command"],
    }
