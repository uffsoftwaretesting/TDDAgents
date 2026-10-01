"""
Targeted tests to kill surviving mutants in app/hooks/ (Part H Quality Gate).
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import os
import urllib.error
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from app.hooks.backends import (
    BLOCKING_EXIT_CODE,
    AgentHookBackend,
    CommandHookBackend,
    HookExecutionOutcome,
    HttpHookBackend,
    PromptHookBackend,
    interpolate_arguments,
    interpolate_headers,
)
from app.hooks.config import (
    LOCAL_SETTINGS_FILENAME,
    SETTINGS_DIR,
    SETTINGS_FILENAME,
    HookMatcher,
    HookSettings,
    _load_one,
    settings_paths,
)
from app.hooks.dispatcher import HookDispatcher, HookOutcome
from app.hooks.events import HookEvent
from app.hooks.matching import deduplicate_hooks, eval_if_condition
from app.hooks.schemas import (
    DEFAULT_HOOK_TIMEOUT,
    DEFAULT_PROMPT_TIMEOUT,
    AgentHook,
    CommandHook,
    HookDefinition,
    HttpHook,
    PromptHook,
    parse_hook,
    parse_matcher,
)
from app.hooks.stop_hooks import build_stop_hooks_runner
from app.loop.config import build_run_config
from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.deps import StopHookResult
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.state import initial_loop_state


class TestInterpolateArgumentsFormatting:
    def test_exact_indent_and_formatting(self):
        template = "$ARGUMENTS"
        payload = {"b": 2, "a": 1}
        result = interpolate_arguments(template, payload)
        # Verify 2-space indentation in output
        assert result == json.dumps(payload, indent=2)

    def test_interpolate_headers_various_patterns(self):
        with patch.dict(os.environ, {"A": "val_a", "B": "val_b"}):
            headers = {"H1": "$A", "H2": "${B}", "H3": "$C", "H4": "plain"}
            res = interpolate_headers(headers, allowed_env_vars=("A",))
            assert res["H1"] == "val_a"
            assert res["H2"] == ""  # B not in allowed_env_vars
            assert res["H3"] == ""  # C not in allowed_env_vars
            assert res["H4"] == "plain"


class TestEvalIfConditionDeep:
    def test_eval_if_condition_with_path_fallback(self):
        # file_path is None, but path is provided
        assert eval_if_condition("Read(*.py)", "Read", tool_input={"path": "main.py"}) is True
        assert eval_if_condition("Read(*.py)", "Read", tool_input={"path": "main.js"}) is False

    def test_eval_if_condition_with_non_string_tool_input(self):
        assert eval_if_condition("Bash(pip *)", "Bash", tool_input={"command": 123}) is False
        assert eval_if_condition("Read(*.py)", "Read", tool_input={"file_path": 123, "path": 456}) is False

    def test_eval_if_condition_with_empty_inputs(self):
        assert eval_if_condition("Bash(pip *)", "Bash", command=None, tool_input=None) is False
        assert eval_if_condition("Bash(pip *)", "") is False


class TestDeduplicateHooksDeep:
    def test_custom_hook_type_fallback_key(self):
        class CustomHook(HookDefinition):
            pass

        c1 = CustomHook(type="custom", source="user")
        c2 = CustomHook(type="custom", source="user")
        deduped = deduplicate_hooks([c1, c2])
        # Two distinct objects have different id(), so both preserved
        assert len(deduped) == 2

    def test_command_hook_different_shells(self):
        h1 = CommandHook(command="echo hi", shell="bash", source="user")
        h2 = CommandHook(command="echo hi", shell="zsh", source="user")
        deduped = deduplicate_hooks([h1, h2])
        assert len(deduped) == 2


class TestCommandHookBackendDeep:
    def test_non_bash_shell(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        hook = CommandHook(command="echo custom", shell="/bin/sh")
        outcome = backend.execute(hook, {}, "PreToolUse")
        assert outcome.exit_code == 0
        assert outcome.stdout.strip() == "custom"

    def test_timeout_returns_code_124(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        hook = CommandHook(command="sleep 5", timeout=0.1)
        outcome = backend.execute(hook, {}, "PreToolUse")
        assert outcome.exit_code == 124
        assert "timed out" in outcome.stderr

    def test_oserror_returns_code_127(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        hook = CommandHook(command="echo hi", shell="/nonexistent/shell/xyz")
        outcome = backend.execute(hook, {}, "PreToolUse")
        assert outcome.exit_code == 127
        assert outcome.stderr != ""

    def test_json_stdout_permission_allow_clears_denied(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        hook = CommandHook(command='echo \'{"permissionDecision": "allow"}\'')
        outcome = backend.execute(hook, {}, "PreToolUse")
        assert outcome.denied is False
        assert outcome.reason == ""

    def test_json_stdout_unsupported_permission_decision(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        hook = CommandHook(command='echo \'{"permissionDecision": "ask"}\'')
        outcome = backend.execute(hook, {}, "PreToolUse")
        assert outcome.denied is False

    def test_json_stdout_additional_context_appending(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        hook = CommandHook(command='echo "some line"; echo \'{"additionalContext": "more info"}\'')
        outcome = backend.execute(hook, {}, "PostToolUse")
        assert outcome.additional_context == "more info"

    def test_json_stdout_prevent_continuation(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        hook = CommandHook(command='echo \'{"preventContinuation": true}\'')
        outcome = backend.execute(hook, {}, "Stop")
        assert outcome.prevent_continuation is True

    def test_json_stdout_ok_true_does_not_block(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        hook = CommandHook(command='echo \'{"ok": true}\'')
        outcome = backend.execute(hook, {}, "Stop")
        assert outcome.exit_code == 0
        assert not outcome.blocking_errors

    def test_generic_event_blocking_exit_code(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        hook = CommandHook(command='echo "generic veto" >&2; exit 2')
        outcome = backend.execute(hook, {}, "CustomEvent")
        assert outcome.denied is True
        assert "generic veto" in outcome.reason


class TestPromptAndAgentHookAsyncExecution:
    @pytest.mark.anyio
    async def test_prompt_hook_with_async_model_caller(self):
        async def async_caller(sys, prompt):
            return '{"ok": true}'

        backend = PromptHookBackend(model_caller=async_caller)
        hook = PromptHook(prompt="Verify $ARGUMENTS")
        outcome = await backend.execute(hook, {}, "Stop")
        assert outcome.exit_code == 0

    @pytest.mark.anyio
    async def test_prompt_hook_model_caller_exception(self):
        def buggy_caller(sys, prompt):
            raise RuntimeError("API down")

        backend = PromptHookBackend(model_caller=buggy_caller)
        hook = PromptHook(prompt="Verify $ARGUMENTS")
        outcome = await backend.execute(hook, {}, "Stop")
        assert outcome.exit_code == 1
        assert "API down" in outcome.stderr

    @pytest.mark.anyio
    async def test_prompt_hook_schema_without_ok(self):
        backend = PromptHookBackend(model_caller=lambda sys, p: '{"valid_json": "but no ok"}')
        hook = PromptHook(prompt="test")
        outcome = await backend.execute(hook, {}, "Stop")
        assert outcome.exit_code == 1
        assert "Schema validation failed" in outcome.stderr

    @pytest.mark.anyio
    async def test_agent_hook_with_async_runner(self):
        async def async_runner(prompt, payload):
            return {"ok": False, "reason": "failed async check"}

        backend = AgentHookBackend(agent_runner=async_runner)
        hook = AgentHook(prompt="Verify")
        outcome = await backend.execute(hook, {}, "Stop")
        assert outcome.exit_code == BLOCKING_EXIT_CODE
        assert "failed async check" in outcome.blocking_errors[0]

    @pytest.mark.anyio
    async def test_agent_hook_runner_exception(self):
        def buggy_runner(prompt, payload):
            raise ValueError("bad agent")

        backend = AgentHookBackend(agent_runner=buggy_runner)
        hook = AgentHook(prompt="Verify")
        outcome = await backend.execute(hook, {}, "Stop")
        assert outcome.exit_code == 1
        assert "bad agent" in outcome.stderr

    @pytest.mark.anyio
    async def test_agent_hook_non_dict_return(self):
        backend = AgentHookBackend(agent_runner=cast(Any, lambda p, pl: "not a dict"))
        hook = AgentHook(prompt="Verify")
        outcome = await backend.execute(hook, {}, "Stop")
        assert outcome.exit_code == 1
        assert "Invalid agent output format" in outcome.stderr


class TestHttpHookBackendDefaultPost:
    def test_default_http_post_mocked_success(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            backend = HttpHookBackend()
            hook = HttpHook(url="https://api.example.com/test")
            outcome = backend.execute(hook, {"test": 1}, "PreToolUse")
            assert outcome.exit_code == 0

    def test_default_http_post_mocked_http_error(self):
        err = urllib.error.HTTPError(
            url="https://api.example.com/test",
            code=403,
            msg="Forbidden",
            hdrs=cast(Any, {}),
            fp=io.BytesIO(b"Not allowed"),
        )
        with patch("urllib.request.urlopen", side_effect=err):
            backend = HttpHookBackend()
            hook = HttpHook(url="https://api.example.com/test")
            outcome = backend.execute(hook, {}, "PreToolUse")
            assert outcome.exit_code == BLOCKING_EXIT_CODE
            assert outcome.denied is True
            assert "Not allowed" in outcome.reason

    def test_default_http_post_mocked_network_exception(self):
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            backend = HttpHookBackend()
            hook = HttpHook(url="https://api.example.com/test")
            outcome = backend.execute(hook, {}, "PreToolUse")
            assert outcome.exit_code == 1
            assert "connection refused" in outcome.stderr


class TestHookDispatcherMethods:
    def test_from_project_and_run_event(self, tmp_path):
        dot_tdd = tmp_path / ".tddagents"
        dot_tdd.mkdir()
        (dot_tdd / "settings.json").write_text(
            json.dumps({
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "echo 'stop veto' >&2; exit 2"}]}]
                }
            }),
            encoding="utf-8",
        )
        home = tmp_path / "home"
        home.mkdir()

        dispatcher = HookDispatcher.from_project(tmp_path, home)
        outcome = dispatcher.run_event(HookEvent.STOP, {"turn": 1})
        assert "stop veto" in outcome.blocking_errors[0]

    def test_run_single_hook_prompt_in_running_loop(self):
        async def _test():
            backend = HookDispatcher(
                HookSettings(),
                model_caller=lambda sys, p: '{"ok": true}',
            )
            hook = PromptHook(prompt="Check $ARGUMENTS")
            outcome = backend._run_single_hook(hook, {}, "PreToolUse")
            assert outcome.exit_code == 0

        asyncio.run(_test())

    def test_run_single_hook_agent_in_running_loop(self):
        async def _test():
            backend = HookDispatcher(
                HookSettings(),
                agent_runner=lambda p, pl: {"ok": True},
            )
            hook = AgentHook(prompt="Verify $ARGUMENTS")
            outcome = backend._run_single_hook(hook, {}, "Stop")
            assert outcome.exit_code == 0

        asyncio.run(_test())

    def test_run_single_hook_unknown_type(self):
        class UnknownHook(HookDefinition):
            pass

        dispatcher = HookDispatcher(HookSettings())
        outcome = dispatcher._run_single_hook(UnknownHook(type="unknown"), {}, "Stop")
        assert outcome.exit_code == 0


class TestHookSchemasCoverage:
    def test_command_hook_all_options_to_dict(self):
        hook = CommandHook(
            command="echo hi",
            shell="zsh",
            async_=True,
            async_rewake=True,
            timeout=10.0,
            if_condition="Bash(git *)",
            status_message="Checking git",
            once=True,
        )
        d = hook.to_dict()
        assert d["command"] == "echo hi"
        assert d["shell"] == "zsh"
        assert d["async"] is True
        assert d["asyncRewake"] is True
        assert d["timeout"] == 10.0
        assert d["if"] == "Bash(git *)"
        assert d["statusMessage"] == "Checking git"
        assert d["once"] is True

    def test_prompt_hook_with_model_to_dict(self):
        hook = PromptHook(prompt="Inspect", model="claude-3-haiku")
        d = hook.to_dict()
        assert d["prompt"] == "Inspect"
        assert d["model"] == "claude-3-haiku"

    def test_agent_hook_with_model_to_dict(self):
        hook = AgentHook(prompt="Inspect agent", model="claude-3-5-sonnet")
        d = hook.to_dict()
        assert d["prompt"] == "Inspect agent"
        assert d["model"] == "claude-3-5-sonnet"

    def test_http_hook_with_headers_and_env_to_dict(self):
        hook = HttpHook(
            url="https://example.com/api",
            headers={"X-Token": "xyz"},
            allowed_env_vars=("ENV_VAR",),
        )
        d = hook.to_dict()
        assert d["url"] == "https://example.com/api"
        assert d["headers"] == {"X-Token": "xyz"}
        assert d["allowedEnvVars"] == ["ENV_VAR"]

    def test_hook_settings_describe_empty(self):
        settings = HookSettings()
        assert settings.describe() == "no hook settings found"

    def test_hook_settings_from_dict_edge_cases(self):
        assert HookSettings.from_dict(cast(Any, "not a dict")).is_empty
        assert HookSettings.from_dict({"hooks": cast(Any, "not a dict")}).is_empty
        assert HookSettings.from_dict({"hooks": {"UnknownEvent": []}}).is_empty
        assert HookSettings.from_dict({"hooks": {"PreToolUse": cast(Any, "not a list")}}).is_empty
        assert HookSettings.from_json("invalid json").is_empty


class TestBuildStopHooksRunnerDeep:
    @pytest.mark.anyio
    async def test_build_stop_hooks_runner_with_settings(self, tmp_path):
        settings = HookSettings(
            events={
                "Stop": (
                    HookMatcher(
                        matcher=None,
                        hooks=(CommandHook(command="exit 0"),),
                    ),
                )
            }
        )
        runner = build_stop_hooks_runner(settings=settings, project_root=tmp_path)
        ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
        store = AppStateStore(state=AppState(phase_ledger=ledger))
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("run-1", postgres_checkpointing=False)

        res = await runner(state, config)
        assert res.prevent_continuation is False
        assert not res.blocking_errors

    @pytest.mark.anyio
    async def test_build_stop_hooks_runner_with_extra_hooks(self):
        async def extra_blocking_hook(state, config):
            return StopHookResult(blocking_errors=(HumanMessage(content="extra blocked"),))

        runner = build_stop_hooks_runner(extra_stop_hooks=[extra_blocking_hook])
        ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
        store = AppStateStore(state=AppState(phase_ledger=ledger))
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("run-1", postgres_checkpointing=False)

        res = await runner(state, config)
        assert len(res.blocking_errors) == 1
        assert "extra blocked" in res.blocking_errors[0].content

    @pytest.mark.anyio
    async def test_build_stop_hooks_runner_with_extra_prevent_continuation(self):
        async def extra_prevent_hook(state, config):
            return StopHookResult(prevent_continuation=True)

        runner = build_stop_hooks_runner(extra_stop_hooks=[extra_prevent_hook])
        ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
        store = AppStateStore(state=AppState(phase_ledger=ledger))
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("run-1", postgres_checkpointing=False)

        res = await runner(state, config)
        assert res.prevent_continuation is True
        assert not res.blocking_errors


class TestSchemasDeepExtended:
    def test_parse_hook_non_dict_and_non_str_type(self):
        assert parse_hook(123) is None
        assert parse_hook("string") is None
        assert parse_hook([]) is None
        assert parse_hook({"type": 123}) is None

    def test_parse_hook_if_and_status_message(self):
        h1 = parse_hook({"command": "echo", "if": "   "})
        assert h1 is not None and h1.if_condition is None
        h2 = parse_hook({"command": "echo", "if": "Bash(*)"})
        assert h2 is not None and h2.if_condition == "Bash(*)"

        h3 = parse_hook({"command": "echo", "statusMessage": 123})
        assert h3 is not None and h3.status_message == ""
        h4 = parse_hook({"command": "echo", "statusMessage": "running"})
        assert h4 is not None and h4.status_message == "running"

    def test_parse_hook_once_flag(self):
        h0 = parse_hook({"command": "echo"})
        assert h0 is not None and h0.once is False
        h_f = parse_hook({"command": "echo", "once": False})
        assert h_f is not None and h_f.once is False
        h_t = parse_hook({"command": "echo", "once": True})
        assert h_t is not None and h_t.once is True
        h_1 = parse_hook({"command": "echo", "once": 1})
        assert h_1 is not None and h_1.once is True
        h_zero = parse_hook({"command": "echo", "once": 0})
        assert h_zero is not None and h_zero.once is False

    def test_parse_command_hook_edge_cases(self):
        assert parse_hook({"type": "command"}) is None
        assert parse_hook({"type": "command", "command": ""}) is None
        assert parse_hook({"type": "command", "command": "   "}) is None

        h = parse_hook({"command": "echo", "timeout": 0})
        assert h is not None and h.timeout == DEFAULT_HOOK_TIMEOUT
        h = parse_hook({"command": "echo", "timeout": -5})
        assert h is not None and h.timeout == DEFAULT_HOOK_TIMEOUT
        h = parse_hook({"command": "echo", "timeout": True})
        assert h is not None and h.timeout == DEFAULT_HOOK_TIMEOUT
        h = parse_hook({"command": "echo", "timeout": "not a number"})
        assert h is not None and h.timeout == DEFAULT_HOOK_TIMEOUT
        h = parse_hook({"command": "echo", "timeout": 45.5})
        assert h is not None and h.timeout == 45.5

        h_cmd = parse_hook({"command": "echo", "shell": "zsh", "async": True, "asyncRewake": True})
        assert isinstance(h_cmd, CommandHook)
        assert h_cmd.shell == "zsh"
        assert h_cmd.async_ is True
        assert h_cmd.async_rewake is True

        h_default = parse_hook({"command": "echo", "shell": 123})
        assert isinstance(h_default, CommandHook)
        assert h_default.shell == "bash"
        assert h_default.async_ is False
        assert h_default.async_rewake is False

    def test_parse_prompt_hook_edge_cases(self):
        assert parse_hook({"type": "prompt"}) is None
        assert parse_hook({"type": "prompt", "prompt": ""}) is None
        assert parse_hook({"type": "prompt", "prompt": "  "}) is None

        h = parse_hook({"type": "prompt", "prompt": "check", "model": "claude-3-opus", "timeout": 20.0})
        assert isinstance(h, PromptHook)
        assert h.prompt == "check"
        assert h.model == "claude-3-opus"
        assert h.timeout == 20.0

        h2 = parse_hook({"type": "prompt", "prompt": "check", "model": 123, "timeout": -1})
        assert isinstance(h2, PromptHook)
        assert h2.model is None
        assert h2.timeout == DEFAULT_PROMPT_TIMEOUT

    def test_parse_agent_hook_edge_cases(self):
        assert parse_hook({"type": "agent"}) is None
        assert parse_hook({"type": "agent", "prompt": ""}) is None
        assert parse_hook({"type": "agent", "prompt": "  "}) is None

        h = parse_hook({"type": "agent", "prompt": "verify", "model": "claude-3-sonnet", "timeout": 35.0})
        assert isinstance(h, AgentHook)
        assert h.prompt == "verify"
        assert h.model == "claude-3-sonnet"
        assert h.timeout == 35.0

        h2 = parse_hook({"type": "agent", "prompt": "verify", "model": None, "timeout": 0})
        assert isinstance(h2, AgentHook)
        assert h2.model is None
        assert h2.timeout == DEFAULT_HOOK_TIMEOUT

    def test_parse_http_hook_edge_cases(self):
        assert parse_hook({"type": "http"}) is None
        assert parse_hook({"type": "http", "url": ""}) is None
        assert parse_hook({"type": "http", "url": "   "}) is None

        h = parse_hook({
            "type": "http",
            "url": "https://example.com/webhook",
            "headers": {"X-Test": "1"},
            "allowedEnvVars": ["TOKEN", 123],
            "timeout": 15.0,
        })
        assert isinstance(h, HttpHook)
        assert h.url == "https://example.com/webhook"
        assert h.headers == {"X-Test": "1"}
        assert h.allowed_env_vars == ("TOKEN",)
        assert h.timeout == 15.0

        h2 = parse_hook({
            "type": "http",
            "url": "https://example.com",
            "headers": "not a dict",
            "allowedEnvVars": "not a list",
            "timeout": "bad",
        })
        assert isinstance(h2, HttpHook)
        assert h2.headers == {}
        assert h2.allowed_env_vars == ()
        assert h2.timeout == DEFAULT_HOOK_TIMEOUT

    def test_parse_unknown_hook_type(self):
        assert parse_hook({"type": "quantum_hook"}) is None

    def test_parse_matcher_edge_cases(self):
        assert parse_matcher(123) is None
        assert parse_matcher({"matcher": "Bash", "hooks": "not a list"}) is None
        assert parse_matcher({"matcher": "Bash", "hooks": []}) is None
        assert parse_matcher({"matcher": "Bash", "hooks": [{"type": "unknown"}]}) is None

        m = parse_matcher({"matcher": "   ", "hooks": [{"command": "echo 1"}]})
        assert m is not None
        assert m.matcher is None
        assert len(m.hooks) == 1

        m2 = parse_matcher({"matcher": "Read(*)", "hooks": [{"command": "echo 1"}]})
        assert m2 is not None
        assert m2.matcher == "Read(*)"
        assert len(m2.hooks) == 1

    def test_hook_settings_methods(self):
        s = HookSettings()
        assert s.is_empty is True
        assert s.describe() == "no hook settings found"
        assert s.matchers_for("PreToolUse") == ()

        m = HookMatcher(matcher="Bash", hooks=(CommandHook(command="exit 0"),))
        s2 = HookSettings(events={"PreToolUse": (m,)}, sources=("user",))
        assert s2.is_empty is False
        assert s2.matchers_for("PreToolUse") == (m,)
        assert "PreToolUse=1" in s2.describe()
        assert "user" in s2.describe()

        assert HookSettings.from_dict(cast(Any, "not dict")) == HookSettings()
        assert HookSettings.from_dict({"hooks": cast(Any, "not dict")}) == HookSettings()
        assert HookSettings.from_dict({"hooks": {"UnknownEvent": []}}) == HookSettings()
        assert HookSettings.from_dict({"hooks": {"PreToolUse": cast(Any, "not list")}}) == HookSettings()

        assert HookSettings.from_json("invalid json{{{") == HookSettings()


class TestMatchingDeduplicateDeep:
    def test_deduplicate_empty(self):
        assert deduplicate_hooks(()) == ()

    def test_deduplicate_command_hooks_dimensions(self):
        h1 = CommandHook(command="ls", shell="bash", source="proj", if_condition="c1")
        h1_dup = CommandHook(command="ls", shell="bash", source="proj", if_condition="c1", status_message="updated")
        h_diff_shell = CommandHook(command="ls", shell="zsh", source="proj", if_condition="c1")
        h_diff_cmd = CommandHook(command="pwd", shell="bash", source="proj", if_condition="c1")
        h_diff_source = CommandHook(command="ls", shell="bash", source="user", if_condition="c1")
        h_diff_if = CommandHook(command="ls", shell="bash", source="proj", if_condition="c2")

        res = deduplicate_hooks([h1, h1_dup, h_diff_shell, h_diff_cmd, h_diff_source, h_diff_if])
        assert len(res) == 5
        assert res[0].status_message == "updated"

    def test_deduplicate_prompt_hooks_dimensions(self):
        p1 = PromptHook(prompt="check", source="user", if_condition="c1")
        p1_dup = PromptHook(prompt="check", source="user", if_condition="c1", status_message="updated")
        p_diff_prompt = PromptHook(prompt="verify", source="user", if_condition="c1")
        p_diff_source = PromptHook(prompt="check", source="proj", if_condition="c1")
        p_diff_if = PromptHook(prompt="check", source="user", if_condition="c2")

        res = deduplicate_hooks([p1, p1_dup, p_diff_prompt, p_diff_source, p_diff_if])
        assert len(res) == 4
        assert res[0].status_message == "updated"

    def test_deduplicate_agent_hooks_dimensions(self):
        a1 = AgentHook(prompt="check", source="user", if_condition="c1")
        a1_dup = AgentHook(prompt="check", source="user", if_condition="c1", status_message="updated")
        a_diff_prompt = AgentHook(prompt="verify", source="user", if_condition="c1")
        a_diff_source = AgentHook(prompt="check", source="proj", if_condition="c1")
        a_diff_if = AgentHook(prompt="check", source="user", if_condition="c2")

        res = deduplicate_hooks([a1, a1_dup, a_diff_prompt, a_diff_source, a_diff_if])
        assert len(res) == 4
        assert res[0].status_message == "updated"

    def test_deduplicate_http_hooks_dimensions(self):
        h1 = HttpHook(url="http://a", source="user", if_condition="c1")
        h1_dup = HttpHook(url="http://a", source="user", if_condition="c1", status_message="updated")
        h_diff_url = HttpHook(url="http://b", source="user", if_condition="c1")
        h_diff_source = HttpHook(url="http://a", source="proj", if_condition="c1")
        h_diff_if = HttpHook(url="http://a", source="user", if_condition="c2")

        res = deduplicate_hooks([h1, h1_dup, h_diff_url, h_diff_source, h_diff_if])
        assert len(res) == 4
        assert res[0].status_message == "updated"

    def test_deduplicate_fallback_identity(self):
        c1 = HookDefinition(type="custom", source="s")
        c2 = HookDefinition(type="custom", source="s")
        res = deduplicate_hooks([c1, c2])
        assert len(res) == 2


class TestPromptAndAgentBackendsDeep:
    @pytest.mark.anyio
    async def test_prompt_backend_execution_branches(self):
        backend_no_model = PromptHookBackend(model_caller=None)
        res = await backend_no_model.execute(PromptHook(prompt="p"), {}, "PreToolUse")
        assert res.exit_code == 0
        assert res.stdout == '{"ok": true}'

        def failing_model(s, p):
            raise ValueError("model explosion")

        backend_fail = PromptHookBackend(model_caller=failing_model)
        res = await backend_fail.execute(PromptHook(prompt="p"), {}, "PreToolUse")
        assert res.exit_code == 1
        assert "model explosion" in res.stderr

    def test_prompt_backend_interpret_event_branches(self):
        backend = PromptHookBackend()
        hook = PromptHook(prompt="p")

        assert backend.interpret(hook, "not json", "PreToolUse").exit_code == 1
        assert backend.interpret(hook, '["array"]', "PreToolUse").exit_code == 1
        assert backend.interpret(hook, '{"no_ok": 1}', "PreToolUse").exit_code == 1

        res_ok = backend.interpret(hook, '{"ok": true}', "PreToolUse")
        assert res_ok.exit_code == 0
        assert not res_ok.denied

        res_pre = backend.interpret(hook, '{"ok": false, "reason": "pre blocked"}', "PreToolUse")
        assert res_pre.exit_code == BLOCKING_EXIT_CODE
        assert res_pre.denied is True
        assert res_pre.reason == "pre blocked"

        res_post = backend.interpret(hook, '{"ok": false, "reason": "post stop"}', "PostToolUse")
        assert res_post.exit_code == BLOCKING_EXIT_CODE
        assert res_post.additional_context == "post stop"
        assert res_post.stop_continuation is True

        res_stop = backend.interpret(hook, '{"ok": false, "reason": "stop retry"}', "Stop")
        assert res_stop.exit_code == BLOCKING_EXIT_CODE
        assert res_stop.blocking_errors == ("stop retry",)

        res_other = backend.interpret(hook, '{"ok": false}', "SessionStart")
        assert res_other.exit_code == BLOCKING_EXIT_CODE
        assert res_other.denied is True
        assert "Condition not met" in res_other.reason

    @pytest.mark.anyio
    async def test_agent_backend_execution_branches(self):
        backend_no_agent = AgentHookBackend(agent_runner=None)
        res = await backend_no_agent.execute(AgentHook(prompt="p"), {}, "PreToolUse")
        assert res.exit_code == 0
        assert res.stdout == '{"ok": true}'

        def failing_agent(p, d):
            raise RuntimeError("agent crashed")

        backend_fail = AgentHookBackend(agent_runner=failing_agent)
        res = await backend_fail.execute(AgentHook(prompt="p"), {}, "PreToolUse")
        assert res.exit_code == 1
        assert "agent crashed" in res.stderr

        backend_bad_type = AgentHookBackend(agent_runner=cast(Any, lambda p, d: "not a dict"))
        res = await backend_bad_type.execute(AgentHook(prompt="p"), {}, "PreToolUse")
        assert res.exit_code == 1
        assert "Invalid agent output format" in res.stderr

        backend_ok = AgentHookBackend(agent_runner=lambda p, d: {"ok": True})
        res = await backend_ok.execute(AgentHook(prompt="p"), {}, "PreToolUse")
        assert res.exit_code == 0

        backend_pre = AgentHookBackend(agent_runner=lambda p, d: {"ok": False, "reason": "agent denied"})
        res = await backend_pre.execute(AgentHook(prompt="p"), {}, "PreToolUse")
        assert res.exit_code == BLOCKING_EXIT_CODE
        assert res.denied is True
        assert res.reason == "agent denied"

        backend_post = AgentHookBackend(agent_runner=lambda p, d: {"ok": False, "reason": "agent post"})
        res = await backend_post.execute(AgentHook(prompt="p"), {}, "PostToolUse")
        assert res.exit_code == BLOCKING_EXIT_CODE
        assert res.additional_context == "agent post"
        assert res.stop_continuation is True

        backend_stop = AgentHookBackend(agent_runner=lambda p, d: {"ok": False, "reason": "agent retry"})
        res = await backend_stop.execute(AgentHook(prompt="p"), {}, "Stop")
        assert res.exit_code == BLOCKING_EXIT_CODE
        assert res.blocking_errors == ("agent retry",)

        backend_other = AgentHookBackend(agent_runner=lambda p, d: {"ok": False})
        res = await backend_other.execute(AgentHook(prompt="p"), {}, "Notification")
        assert res.exit_code == BLOCKING_EXIT_CODE
        assert res.denied is True
        assert "Agent verification failed" in res.reason


class TestHttpBackendDeepExtended:
    def test_http_backend_execute_branches(self):
        hook = HttpHook(url="https://api.test/webhook")

        client_200_deny = lambda u, p, h: (200, '{"ok": false, "reason": "http veto"}')  # noqa: E731
        backend = HttpHookBackend(http_client=client_200_deny)
        res = backend.execute(hook, {}, "PreToolUse")
        assert res.exit_code == BLOCKING_EXIT_CODE
        assert res.denied is True
        assert res.reason == "http veto"

        client_200_stop = lambda u, p, h: (200, '{"ok": false, "reason": "http stop error"}')  # noqa: E731
        backend = HttpHookBackend(http_client=client_200_stop)
        res = backend.execute(hook, {}, "Stop")
        assert res.exit_code == BLOCKING_EXIT_CODE
        assert res.blocking_errors == ("http stop error",)

        client_200_no_reason = lambda u, p, h: (200, '{"ok": false}')  # noqa: E731
        backend = HttpHookBackend(http_client=client_200_no_reason)
        res = backend.execute(hook, {}, "PreToolUse")
        assert "HTTP hook rejected" in res.reason

        client_200_invalid_json = lambda u, p, h: (200, "plain text response")  # noqa: E731
        backend = HttpHookBackend(http_client=client_200_invalid_json)
        res = backend.execute(hook, {}, "PreToolUse")
        assert res.exit_code == 0

        client_403_stop = lambda u, p, h: (403, "custom forbidden message")  # noqa: E731
        backend = HttpHookBackend(http_client=client_403_stop)
        res = backend.execute(hook, {}, "Stop")
        assert res.exit_code == BLOCKING_EXIT_CODE
        assert res.blocking_errors == ("custom forbidden message",)

        client_422_empty = lambda u, p, h: (422, "   ")  # noqa: E731
        backend = HttpHookBackend(http_client=client_422_empty)
        res = backend.execute(hook, {}, "PreToolUse")
        assert "HTTP hook blocked by" in res.reason

        client_500 = lambda u, p, h: (500, "internal error")  # noqa: E731
        backend = HttpHookBackend(http_client=client_500)
        res = backend.execute(hook, {}, "PreToolUse")
        assert res.exit_code == 1
        assert "HTTP 500" in res.stderr

    def test_default_http_post_mocked(self):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"ok": true}'
        mock_response.__enter__.return_value = mock_response

        with patch("urllib.request.urlopen", return_value=mock_response):
            code, body = HttpHookBackend._default_http_post("http://test", {}, {}, 5.0)
            assert code == 200
            assert body == '{"ok": true}'

        fp = io.BytesIO(b"rejected")
        err = urllib.error.HTTPError("http://test", 403, "Forbidden", cast(Any, {}), fp)
        with patch("urllib.request.urlopen", side_effect=err):
            code, body = HttpHookBackend._default_http_post("http://test", {}, {}, 5.0)
            assert code == 403
            assert body == "rejected"

        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            code, body = HttpHookBackend._default_http_post("http://test", {}, {}, 5.0)
            assert code == 500
            assert "network down" in body


class TestCommandBackendDeepExtended:
    def test_command_backend_init_default(self):
        backend = CommandHookBackend()
        assert backend.project_root == Path(".")

    def test_command_backend_interpret_event_branches(self):
        backend = CommandHookBackend()
        hook = CommandHook(command="exit 2")

        res_post = backend.interpret(hook, BLOCKING_EXIT_CODE, "", "post failed", "PostToolUse")
        assert res_post.additional_context == "post failed"
        assert res_post.stop_continuation is True

        res_stop = backend.interpret(hook, BLOCKING_EXIT_CODE, "", "stop failed", "Stop")
        assert res_stop.blocking_errors == ("stop failed",)

        res_empty = backend.interpret(hook, BLOCKING_EXIT_CODE, "", "", "PreToolUse")
        assert res_empty.denied is True
        assert f"Blocked by hook '{hook.label}'." == res_empty.reason

    def test_command_backend_apply_json_branches(self):
        backend = CommandHookBackend()
        hook = CommandHook(command="echo")

        outcome = HookExecutionOutcome(denied=True, reason="previous")
        backend._apply_json(outcome, '{"permissionDecision": "allow"}', "PreToolUse", hook)
        assert outcome.denied is False
        assert outcome.reason == ""

        outcome2 = HookExecutionOutcome()
        backend._apply_json(
            outcome2,
            '{"permissionDecision": "deny", "permissionDecisionReason": "denied!"}',
            "PreToolUse",
            hook,
        )
        assert outcome2.denied is True
        assert outcome2.reason == "denied!"

        outcome3 = HookExecutionOutcome()
        backend._apply_json(outcome3, '{"permissionDecision": "deny"}', "PreToolUse", hook)
        assert outcome3.denied is True
        assert f"Denied by hook '{hook.label}'." == outcome3.reason

        outcome4 = HookExecutionOutcome()
        backend._apply_json(outcome4, '{"permissionDecision": "maybe"}', "PreToolUse", hook)
        assert outcome4.denied is False

        outcome5 = HookExecutionOutcome()
        backend._apply_json(outcome5, '{"ok": false, "reason": "stop rejected"}', "Stop", hook)
        assert outcome5.exit_code == BLOCKING_EXIT_CODE
        assert outcome5.blocking_errors == ("stop rejected",)

        outcome6 = HookExecutionOutcome()
        backend._apply_json(outcome6, '{"ok": false}', "PostToolUse", hook)
        assert outcome6.exit_code == BLOCKING_EXIT_CODE
        assert outcome6.additional_context == f"Hook '{hook.label}' condition not met."
        assert outcome6.stop_continuation is True

        outcome7 = HookExecutionOutcome()
        backend._apply_json(outcome7, '{"ok": true}', "PreToolUse", hook)
        assert outcome7.exit_code == 0
        assert not outcome7.denied


class TestDispatcherDeepExtended:
    def test_dispatcher_init_fields(self, tmp_path):
        settings = HookSettings()
        dispatcher = HookDispatcher(settings, project_root=tmp_path)
        assert dispatcher.project_root == tmp_path
        assert dispatcher.command_backend.project_root == tmp_path
        assert dispatcher.prompt_backend.model_caller is None
        assert dispatcher.agent_backend.agent_runner is None
        assert dispatcher.http_backend.http_client is None

    @pytest.mark.anyio
    async def test_run_single_hook_prompt_and_agent_in_active_loop(self):
        dispatcher = HookDispatcher(HookSettings())
        prompt_hook = PromptHook(prompt="check")
        agent_hook = AgentHook(prompt="verify")

        out_prompt = dispatcher._run_single_hook(prompt_hook, {}, "PreToolUse")
        assert out_prompt.exit_code == 0

        out_agent = dispatcher._run_single_hook(agent_hook, {}, "PreToolUse")
        assert out_agent.exit_code == 0

    def test_run_single_hook_prompt_and_agent_outside_loop(self):
        dispatcher = HookDispatcher(HookSettings())
        prompt_hook = PromptHook(prompt="check")
        agent_hook = AgentHook(prompt="verify")

        out_prompt = dispatcher._run_single_hook(prompt_hook, {}, "PreToolUse")
        assert out_prompt.exit_code == 0

        out_agent = dispatcher._run_single_hook(agent_hook, {}, "PreToolUse")
        assert out_agent.exit_code == 0

    def test_run_single_hook_unknown_type(self):
        dispatcher = HookDispatcher(HookSettings())
        out = dispatcher._run_single_hook(HookDefinition(type="custom"), {}, "PreToolUse")
        assert out.exit_code == 0
        assert out.denied is False

    def test_run_event_pre_tool_use_short_circuit(self):
        hook_deny = CommandHook(command='echo \'{"permissionDecision": "deny"}\'')
        hook_second = CommandHook(command='echo "second"')
        m = HookMatcher(matcher="Bash", hooks=(hook_deny, hook_second))
        settings = HookSettings(events={"PreToolUse": (m,)})
        dispatcher = HookDispatcher(settings)

        outcome = dispatcher.run(
            "PreToolUse",
            tool_name="Bash",
            tool_input={"command": "ls"},
        )
        assert outcome.denied is True
        assert outcome.additional_context == ""


class TestConfigDeepExtended:
    def test_load_one_edge_cases(self, tmp_path):
        assert _load_one(tmp_path / "non_existent.json") == {}

        bad_json = tmp_path / "bad.json"
        bad_json.write_text("{invalid")
        assert _load_one(bad_json) == {}

        arr_json = tmp_path / "arr.json"
        arr_json.write_text("[1, 2, 3]")
        assert _load_one(arr_json) == {}

        no_hooks = tmp_path / "nohooks.json"
        no_hooks.write_text('{"hooks": "invalid"}')
        assert _load_one(no_hooks) == {}

        mixed = tmp_path / "mixed.json"
        mixed.write_text(json.dumps({
            "hooks": {
                "UnknownEvent": [{"hooks": [{"command": "ls"}]}],
                "PreToolUse": "not a list",
            }
        }))
        assert _load_one(mixed) == {}

    def test_settings_paths_resolution(self, tmp_path):
        home_path = tmp_path / "home"
        proj_path = tmp_path / "proj"
        paths = settings_paths(proj_path, home=home_path)
        assert len(paths) == 3
        assert paths[0] == home_path / SETTINGS_DIR / SETTINGS_FILENAME
        assert paths[1] == proj_path / SETTINGS_DIR / SETTINGS_FILENAME
        assert paths[2] == proj_path / SETTINGS_DIR / LOCAL_SETTINGS_FILENAME


class TestStopHooksRunnerDeepExtended:
    @pytest.mark.anyio
    async def test_stop_payload_keys_verification(self, tmp_path):
        received_payload = {}

        class SpyingDispatcher:
            def run_event(self, event, payload):
                nonlocal received_payload
                received_payload = payload
                return HookOutcome()

        runner = build_stop_hooks_runner(dispatcher=cast(Any, SpyingDispatcher()))
        ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
        store = AppStateStore(state=AppState(phase_ledger=ledger))
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("run-1", postgres_checkpointing=False)

        await runner(state, config)

        assert received_payload["hook_event_name"] == HookEvent.STOP.value
        assert received_payload["stop_hook_active"] is False
        assert received_payload["turn_count"] == 1
        assert received_payload["phase"] == str(TddPhase.GREEN)
        assert received_payload["red_confirmed"] is True
        assert received_payload["green_passed"] is True
        assert received_payload["messages_count"] == 1

    @pytest.mark.anyio
    async def test_stop_hook_configured_blocking_errors_and_prevent_continuation(self):
        class BlockingDispatcher:
            def run_event(self, event, payload):
                return HookOutcome(blocking_errors=("err1", "err2"))

        runner = build_stop_hooks_runner(dispatcher=cast(Any, BlockingDispatcher()))
        ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
        store = AppStateStore(state=AppState(phase_ledger=ledger))
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("run-1", postgres_checkpointing=False)

        res = await runner(state, config)
        assert len(res.blocking_errors) == 2
        assert res.blocking_errors[0].content == "err1"
        assert res.blocking_errors[1].content == "err2"

        class PreventDispatcher:
            def run_event(self, event, payload):
                return HookOutcome(prevent_continuation=True, blocking_errors=("ignored",))

        runner2 = build_stop_hooks_runner(dispatcher=cast(Any, PreventDispatcher()))
        res2 = await runner2(state, config)
        assert res2.prevent_continuation is True
        assert not res2.blocking_errors

    @pytest.mark.anyio
    async def test_stop_hooks_runner_with_stop_hook_active_true(self):
        received_payload = {}

        class SpyingDispatcher:
            def run_event(self, event, payload):
                nonlocal received_payload
                received_payload = payload
                return HookOutcome()

        runner = build_stop_hooks_runner(dispatcher=cast(Any, SpyingDispatcher()))
        ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
        store = AppStateStore(state=AppState(phase_ledger=ledger))
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        state = dataclasses.replace(state, stop_hook_active=True)
        config = build_run_config("run-1", postgres_checkpointing=False)

        await runner(state, config)
        assert received_payload["stop_hook_active"] is True

    @pytest.mark.anyio
    async def test_stop_hooks_runner_tdd_incomplete_blocks(self):
        ledger = PhaseLedger(phase=TddPhase.RED, red_confirmed=False)
        store = AppStateStore(state=AppState(phase_ledger=ledger))
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("run-1", postgres_checkpointing=False)

        runner = build_stop_hooks_runner()
        res = await runner(state, config)
        assert len(res.blocking_errors) > 0
        assert res.prevent_continuation is False

    @pytest.mark.anyio
    async def test_extra_hooks_arguments_verified(self):
        received_args = []

        async def extra_hook(s, c):
            received_args.append((s, c))
            return StopHookResult()

        ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
        store = AppStateStore(state=AppState(phase_ledger=ledger))
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("run-1", postgres_checkpointing=False)

        runner = build_stop_hooks_runner(extra_stop_hooks=[extra_hook])
        await runner(state, config)
        assert len(received_args) == 1
        assert received_args[0][0] is state
        assert received_args[0][1] is config


class TestSchemasPassThroughFields:
    def test_pass_through_all_hook_types(self):
        # PromptHook
        hp = parse_hook(
            {"type": "prompt", "prompt": "p", "if": "cond", "statusMessage": "stat", "once": True},
            source="src",
        )
        assert isinstance(hp, PromptHook)
        assert hp.if_condition == "cond"
        assert hp.status_message == "stat"
        assert hp.source == "src"
        assert hp.once is True

        # AgentHook
        ha = parse_hook(
            {"type": "agent", "prompt": "a", "if": "cond", "statusMessage": "stat", "once": True},
            source="src",
        )
        assert isinstance(ha, AgentHook)
        assert ha.if_condition == "cond"
        assert ha.status_message == "stat"
        assert ha.source == "src"
        assert ha.once is True

        # HttpHook
        hh = parse_hook(
            {"type": "http", "url": "https://test", "if": "cond", "statusMessage": "stat", "once": True},
            source="src",
        )
        assert isinstance(hh, HttpHook)
        assert hh.if_condition == "cond"
        assert hh.status_message == "stat"
        assert hh.source == "src"
        assert hh.once is True

        # CommandHook
        hc = parse_hook(
            {"type": "command", "command": "c", "if": "cond", "statusMessage": "stat", "once": True},
            source="src",
        )
        assert isinstance(hc, CommandHook)
        assert hc.if_condition == "cond"
        assert hc.status_message == "stat"
        assert hc.source == "src"
        assert hc.once is True


class TestMatchingDeduplicateDefaults:
    def test_deduplicate_missing_attributes_on_duck_types(self):
        class DuckHook:
            def __init__(self, **kwargs: Any) -> None:
                for k, v in kwargs.items():
                    setattr(self, k, v)

        # missing type -> defaults to command
        d1 = DuckHook(command="c1")
        d2 = DuckHook(command="c1")
        res = deduplicate_hooks(cast(Any, [d1, d2]))
        assert len(res) == 1

        # missing source -> defaults to ""
        d3 = DuckHook(type="command", command="c1")
        res2 = deduplicate_hooks(cast(Any, [d1, d3]))
        assert len(res2) == 1

        # missing shell -> defaults to "bash"
        d4 = DuckHook(type="command", command="c1", shell="bash")
        res3 = deduplicate_hooks(cast(Any, [d1, d4]))
        assert len(res3) == 1

        # missing if_condition -> defaults to ""
        d5 = DuckHook(type="command", command="c1", if_condition="")
        res4 = deduplicate_hooks(cast(Any, [d1, d5]))
        assert len(res4) == 1


class TestLoggingOutputsWithCaplog:
    def test_parse_hook_warnings_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
            parse_hook(123, source="file.json")
            assert "Ignoring a non-object hook entry in file.json." in caplog.text

            parse_hook({"type": "command"}, source="file.json")
            assert "Ignoring a command hook with no command in file.json." in caplog.text

            parse_hook({"type": "prompt"}, source="file.json")
            assert "Ignoring a prompt hook with no prompt in file.json." in caplog.text

            parse_hook({"type": "agent"}, source="file.json")
            assert "Ignoring an agent hook with no prompt in file.json." in caplog.text

            parse_hook({"type": "http"}, source="file.json")
            assert "Ignoring an http hook with no url in file.json." in caplog.text

            parse_hook({"type": "unknown_type"}, source="file.json")
            assert "Ignoring unsupported hook type 'unknown_type' in file.json." in caplog.text

            parse_matcher(123, source="file.json")
            assert "Ignoring a non-object matcher in file.json." in caplog.text

            HookSettings.from_dict({"hooks": {"UnknownEvt": []}}, source="file.json")
            assert "Ignoring unsupported hook event 'UnknownEvt' in file.json." in caplog.text

            HookSettings.from_dict({"hooks": {"PreToolUse": "not list"}}, source="file.json")
            assert "Ignoring a non-list hook event in file.json." in caplog.text

    def test_command_backend_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
            b = CommandHookBackend()
            h = CommandHook(command="exit 1")
            b.interpret(h, 1, "out", "err", "PreToolUse")
            assert "exited 1 (non-blocking)" in caplog.text

    def test_command_backend_unsupported_decision_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
            b = CommandHookBackend()
            h = CommandHook(command="echo")
            out = HookExecutionOutcome()
            b._apply_json(out, '{"permissionDecision": "maybe"}', "PreToolUse", h)
            assert "unsupported permissionDecision 'maybe'" in caplog.text

    def test_prompt_backend_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
            b = PromptHookBackend()
            h = PromptHook(prompt="p")
            b.interpret(h, "{invalid json", "PreToolUse")
            assert "returned invalid JSON" in caplog.text

    @pytest.mark.anyio
    async def test_agent_backend_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
            async def _fail(p: str, d: dict[str, Any]) -> dict[str, Any]:
                raise RuntimeError("boom")
            b = AgentHookBackend(agent_runner=_fail)
            h = AgentHook(prompt="p")
            await b.execute(h, {}, "PreToolUse")
            assert "execution failed" in caplog.text

    def test_http_backend_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
            b = HttpHookBackend(http_client=lambda u, p, h: (500, "err"))
            h = HttpHook(url="https://test")
            b.execute(h, {}, "PreToolUse")
            assert "returned status 500; ignored." in caplog.text

    def test_config_logging(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
            p = tmp_path / "bad.json"
            p.write_text("{bad json")
            _load_one(p)
            assert "Ignoring malformed hook settings" in caplog.text

            p2 = tmp_path / "top_level.json"
            p2.write_text("123")
            _load_one(p2)
            assert "the top level is not an object." in caplog.text

            p3 = tmp_path / "evt.json"
            p3.write_text('{"hooks": {"UnknownEvt": []}}')
            _load_one(p3)
            assert "Ignoring unsupported hook event 'UnknownEvt'" in caplog.text

            p4 = tmp_path / "non_list.json"
            p4.write_text('{"hooks": {"PreToolUse": "not a list"}}')
            _load_one(p4)
            assert "Ignoring a non-list hook event" in caplog.text


class TestPrecisionMutantKillers:
    def test_command_backend_execute_logger_and_shell_and_timeouts(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        b = CommandHookBackend()

        # subprocess.run arguments verification
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")) as mock_run:
            b.execute(CommandHook(command="echo 1", shell="bash"), {}, "PreToolUse")
            assert mock_run.call_args[0][0] == ["/bin/bash", "-c", "echo 1"]
            assert mock_run.call_args[1]["text"] is True
            assert mock_run.call_args[1]["encoding"] == "utf-8"

            mock_run.reset_mock()
            b.execute(CommandHook(command="echo 1", shell="zsh"), {}, "PreToolUse")
            assert mock_run.call_args[0][0] == ["zsh", "-c", "echo 1"]
            assert mock_run.call_args[1]["text"] is True
            assert mock_run.call_args[1]["encoding"] == "utf-8"

        # status_message logging
        with caplog.at_level(logging.INFO, logger="TDDOrchestrator.Hooks"):
            h_stat = CommandHook(command="echo ok", status_message="deploying hook")
            b.execute(h_stat, {}, "PreToolUse")
            assert f"🪝 {h_stat.status_message}" in caplog.text

        # timeout logging and stderr message
        with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
            h_time = CommandHook(command="sleep 10", timeout=0.01)
            out_time = b.execute(h_time, {}, "PreToolUse")
            assert out_time.exit_code == 124
            assert out_time.stderr == "Hook timed out"
            assert f"Hook '{h_time.label}' exceeded {h_time.timeout}s and was skipped." in caplog.text

        # start failure (missing executable) logging and outcome
        with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
            with patch("subprocess.run", side_effect=FileNotFoundError("missing binary")):
                h_err = CommandHook(command="true")
                out_err = b.execute(h_err, {}, "PreToolUse")
                assert out_err.exit_code == 127
                assert out_err.stderr == "missing binary"
                assert f"Hook '{h_err.label}' could not be started: missing binary" in caplog.text

    def test_command_backend_apply_json_precision(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        b = CommandHookBackend()
        h = CommandHook(command="echo")

        # ok is False on PreToolUse
        out_pre = HookExecutionOutcome()
        b._apply_json(out_pre, '{"ok": false, "reason": "vetoed by test"}', "PreToolUse", h)
        assert out_pre.exit_code == BLOCKING_EXIT_CODE
        assert out_pre.denied is True
        assert out_pre.reason == "vetoed by test"

        # non-JSON debug logging
        with caplog.at_level(logging.DEBUG, logger="TDDOrchestrator.Hooks"):
            out_log = HookExecutionOutcome()
            b._apply_json(out_log, "just plain text line", "PreToolUse", h)
            assert "produced non-JSON stdout; treated as a log line." in caplog.text

        # reverse lines with invalid line followed by valid line
        out_multiline = HookExecutionOutcome()
        b._apply_json(out_multiline, 'line 1\n{not json\n{"ok": true}', "PreToolUse", h)
        assert out_multiline.exit_code == 0

    def test_dispatcher_run_event_if_condition_and_payload_keys(self) -> None:
        hook = CommandHook(command='echo \'{"additionalContext": "HOOK_RAN"}\'', if_condition="Bash(pip *)")
        m = HookMatcher(matcher="Bash", hooks=(hook,))
        settings = HookSettings(events={"PreToolUse": (m,)})
        dispatcher = HookDispatcher(settings)

        # payload has tool_name and command and tool_input matching condition
        matching_payload = {
            "tool_name": "Bash",
            "command": "pip install testpkg",
            "tool_input": {"command": "pip install testpkg"},
        }
        res_match = dispatcher.run_event("PreToolUse", matching_payload)
        assert res_match.additional_context == "HOOK_RAN"

        # payload has different command that does NOT match condition
        non_matching_payload = {
            "tool_name": "Bash",
            "command": "npm install testpkg",
            "tool_input": {"command": "npm install testpkg"},
        }
        res_non = dispatcher.run_event("PreToolUse", non_matching_payload)
        assert res_non.additional_context == ""

    def test_dispatcher_run_single_hook_all_types(self) -> None:
        dispatcher = HookDispatcher(HookSettings())

        # HttpHook through _run_single_hook
        hook_http = HttpHook(url="https://test.local")
        received_http_payload = []

        def client(u: str, p: dict[str, Any], h: dict[str, str]) -> tuple[int, str]:
            received_http_payload.append(p)
            return (200, '{"ok": true}')

        dispatcher.http_backend = HttpHookBackend(http_client=client)
        out_http = dispatcher._run_single_hook(hook_http, {"payload_k": "payload_v"}, "PreToolUse")
        assert out_http.exit_code == 0
        assert received_http_payload == [{"payload_k": "payload_v"}]

        # PromptHook through _run_single_hook with custom caller
        received_prompt = []

        def caller(sys: str, usr: str) -> str:
            received_prompt.append((sys, usr))
            return '{"ok": true}'

        hook_prompt = PromptHook(prompt="Verify $ARGUMENTS")
        dispatcher.prompt_backend = PromptHookBackend(model_caller=caller)
        out_prompt = dispatcher._run_single_hook(hook_prompt, {"arg": "val"}, "PreToolUse")
        assert out_prompt.exit_code == 0
        assert len(received_prompt) == 1
        assert "arg" in received_prompt[0][1]

        # AgentHook through _run_single_hook with custom runner
        received_agent = []

        def runner(pr: str, pl: dict[str, Any]) -> dict[str, Any]:
            received_agent.append((pr, pl))
            return {"ok": True}

        hook_agent = AgentHook(prompt="Agent $ARGUMENTS")
        dispatcher.agent_backend = AgentHookBackend(agent_runner=runner)
        out_agent = dispatcher._run_single_hook(hook_agent, {"arg": "val"}, "PreToolUse")
        assert out_agent.exit_code == 0
        assert len(received_agent) == 1
        assert "arg" in received_agent[0][1]

        # PostToolUse stop_continuation propagation
        h_prompt_post = PromptHook(prompt="Verify")
        dispatcher.prompt_backend = PromptHookBackend(model_caller=lambda s, p: '{"ok": false, "reason": "stop!"}')
        out_p_post = dispatcher._run_single_hook(h_prompt_post, {}, "PostToolUse")
        assert out_p_post.stop_continuation is True
        assert out_p_post.additional_context == "stop!"

        h_agent_post = AgentHook(prompt="Agent")
        dispatcher.agent_backend = AgentHookBackend(agent_runner=lambda pr, pl: {"ok": False, "reason": "agent stop!"})
        out_a_post = dispatcher._run_single_hook(h_agent_post, {}, "PostToolUse")
        assert out_a_post.stop_continuation is True
        assert out_a_post.additional_context == "agent stop!"

    @pytest.mark.anyio
    async def test_dispatcher_run_single_hook_pool_workers(self) -> None:
        import concurrent.futures
        dispatcher = HookDispatcher(HookSettings())
        with patch("concurrent.futures.ThreadPoolExecutor", wraps=concurrent.futures.ThreadPoolExecutor) as mock_pool:
            out_p = dispatcher._run_single_hook(PromptHook(prompt="p"), {}, "PreToolUse")
            assert out_p.exit_code == 0
            mock_pool.assert_called_with(max_workers=1)

        with patch("concurrent.futures.ThreadPoolExecutor", wraps=concurrent.futures.ThreadPoolExecutor) as mock_pool2:
            out_a = dispatcher._run_single_hook(AgentHook(prompt="a"), {}, "PreToolUse")
            assert out_a.exit_code == 0
            mock_pool2.assert_called_with(max_workers=1)

    def test_dispatcher_init_backends_and_defaults(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        dummy_model = lambda s, p: '{"ok": true}'  # noqa: E731
        dummy_agent = lambda p, pl: {"ok": True}  # noqa: E731
        dummy_http = lambda u, p, h: (200, "ok")  # noqa: E731
        settings = HookSettings()

        with caplog.at_level(logging.INFO, logger="TDDOrchestrator.Hooks"):
            d = HookDispatcher(
                settings,
                model_caller=dummy_model,
                agent_runner=dummy_agent,
                http_client=dummy_http,
            )
            assert d.project_root == Path(".")
            assert d.prompt_backend.model_caller is dummy_model
            assert d.agent_backend.agent_runner is dummy_agent
            assert d.http_backend.http_client is dummy_http
            assert f"🪝 Hook configuration: {settings.describe()}" in caplog.text

    def test_dispatcher_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        with caplog.at_level(logging.INFO, logger="TDDOrchestrator.Hooks"):
            HookDispatcher(HookSettings())
            assert "🪝 Hook configuration:" in caplog.text

        with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
            dispatcher = HookDispatcher(HookSettings())
            dispatcher._run_single_hook(HookDefinition(type="unknown_duck"), {}, "PreToolUse")
            assert "Unknown hook definition type:" in caplog.text

    def test_interpret_command_backend_exact_fields(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        b = CommandHookBackend()
        h = CommandHook(command="exit 1")

        out = b.interpret(h, 0, "out", "err_custom", "PreToolUse")
        assert out.stderr == "err_custom"

        with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
            b.interpret(h, 1, "custom_stdout", "", "PreToolUse")
            assert f"Hook '{h.label}' exited 1 (non-blocking); output: custom_stdout" in caplog.text

    @pytest.mark.anyio
    async def test_agent_backend_execute_exact_fields(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        hook = AgentHook(prompt="Inspect $ARGUMENTS")
        backend = AgentHookBackend(agent_runner=lambda pr, pl: {"ok": True, "seen": pr})
        res = await backend.execute(hook, {"tool": "Bash"}, "PreToolUse")
        assert res.exit_code == 0
        expected_json = json.dumps({"ok": True, "seen": 'Inspect {\n  "tool": "Bash"\n}'})
        assert res.stdout == expected_json

        b_no_ok = AgentHookBackend(agent_runner=lambda pr, pl: {"arbitrary": "value"})
        res_no_ok = await b_no_ok.execute(hook, {}, "PreToolUse")
        assert res_no_ok.exit_code == 0

        b_bad = AgentHookBackend(agent_runner=cast(Any, lambda pr, pl: "not a dict"))
        res_bad = await b_bad.execute(hook, {}, "PreToolUse")
        assert res_bad.stderr == "Invalid agent output format"

        with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
            async def _explode(pr: str, pl: dict[str, Any]) -> dict[str, Any]:
                raise RuntimeError("agent explosion")
            b_err = AgentHookBackend(agent_runner=_explode)
            res_err = await b_err.execute(hook, {}, "PreToolUse")
            assert res_err.exit_code == 1
            assert f"Agent hook '{hook.label}' execution failed: agent explosion" in caplog.text

    def test_http_backend_execute_exact_fields(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        hook = HttpHook(url="https://api.test/webhook", timeout=12.5)

        received_args: dict[str, Any] = {}

        def client(u: str, p: dict[str, Any], h: dict[str, str]) -> tuple[int, str]:
            received_args["url"] = u
            received_args["payload"] = p
            received_args["headers"] = h
            return (200, "custom body response")

        backend = HttpHookBackend(http_client=client)
        res = backend.execute(hook, {"k": "v"}, "PreToolUse")
        assert res.exit_code == 0
        assert res.stdout == "custom body response"
        assert received_args["url"] == "https://api.test/webhook"
        assert received_args["payload"] == {"k": "v"}
        assert received_args["headers"]["Content-Type"] == "application/json"

        b2 = HttpHookBackend()
        with patch.object(b2, "_default_http_post", return_value=(200, "ok")) as mock_post:
            b2.execute(hook, {"data": 1}, "PreToolUse")
            mock_post.assert_called_once_with(
                "https://api.test/webhook",
                {"data": 1},
                {"Content-Type": "application/json"},
                12.5,
            )

        with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
            b3 = HttpHookBackend(http_client=lambda u, p, h: (500, "server err"))
            b3.execute(hook, {}, "PreToolUse")
            assert f"HTTP hook '{hook.label}' returned status 500; ignored." in caplog.text

    def test_prompt_backend_interpret_exact_fields(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        backend = PromptHookBackend()
        hook = PromptHook(prompt="p")

        with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
            res_bad = backend.interpret(hook, "{bad json", "PreToolUse")
            assert res_bad.exit_code == 1
            assert res_bad.stdout == "{bad json"
            assert res_bad.stderr == "JSON validation failed"
            assert f"Prompt hook '{hook.label}' returned invalid JSON: {{bad json" in caplog.text

        res_no_ok = backend.interpret(hook, '{"no_ok": 1}', "PreToolUse")
        assert res_no_ok.exit_code == 1
        assert res_no_ok.stderr == "Schema validation failed"
