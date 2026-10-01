"""
Tests for Hook Backends: Command, Prompt, Agent, and HTTP (Parts H3, H4).
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from app.hooks.backends import (
    BLOCKING_EXIT_CODE,
    AgentHookBackend,
    CommandHookBackend,
    HttpHookBackend,
    PromptHookBackend,
    interpolate_arguments,
    interpolate_headers,
)
from app.hooks.schemas import AgentHook, CommandHook, HttpHook, PromptHook


class TestArgumentAndHeaderInterpolation:
    def test_interpolate_arguments(self):
        template = "Check this: $ARGUMENTS please"
        payload = {"tool": "Bash", "command": "ls"}
        result = interpolate_arguments(template, payload)
        assert '"tool": "Bash"' in result
        assert "$ARGUMENTS" not in result

    def test_interpolate_headers_resolves_only_allowed_vars(self):
        with patch.dict(os.environ, {"SECRET_KEY": "12345", "OTHER_KEY": "67890"}):
            headers = {
                "Authorization": "Bearer $SECRET_KEY",
                "X-Other": "${OTHER_KEY}",
                "X-Static": "static-value",
            }
            # Only SECRET_KEY is allowed
            resolved = interpolate_headers(headers, allowed_env_vars=("SECRET_KEY",))
            assert resolved["Authorization"] == "Bearer 12345"
            assert resolved["X-Other"] == ""  # Disallowed env var is blanked
            assert resolved["X-Static"] == "static-value"


class TestCommandHookBackend:
    def test_exit_zero_proceeds(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        hook = CommandHook(command="exit 0")
        outcome = backend.execute(hook, {"test": 1}, "PreToolUse")
        assert outcome.exit_code == 0
        assert outcome.denied is False

    def test_exit_two_blocks_pre_tool_use(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        hook = CommandHook(command="echo 'disallowed' >&2; exit 2")
        outcome = backend.execute(hook, {}, "PreToolUse")
        assert outcome.exit_code == BLOCKING_EXIT_CODE
        assert outcome.denied is True
        assert "disallowed" in outcome.reason

    def test_exit_two_post_tool_use(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        hook = CommandHook(command="echo 'post warning' >&2; exit 2")
        outcome = backend.execute(hook, {}, "PostToolUse")
        assert outcome.exit_code == BLOCKING_EXIT_CODE
        assert outcome.denied is False  # Already executed
        assert outcome.stop_continuation is True
        assert "post warning" in outcome.additional_context

    def test_exit_two_stop_hook(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        hook = CommandHook(command="echo 'tests missing' >&2; exit 2")
        outcome = backend.execute(hook, {}, "Stop")
        assert outcome.exit_code == BLOCKING_EXIT_CODE
        assert "tests missing" in outcome.blocking_errors[0]

    def test_non_zero_non_two_exit_code_is_ignored(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        hook = CommandHook(command="exit 42")
        outcome = backend.execute(hook, {}, "PreToolUse")
        assert outcome.exit_code == 42
        assert outcome.denied is False
        assert outcome.stop_continuation is False

    def test_stdout_json_permission_decision_and_updated_input(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        json_doc = json.dumps({
            "permissionDecision": "deny",
            "permissionDecisionReason": "policy violation",
            "updatedInput": {"command": "ls -l"},
            "additionalContext": "note this",
            "continue": False,
        })
        hook = CommandHook(command=f"echo '{json_doc}'")
        outcome = backend.execute(hook, {}, "PreToolUse")
        assert outcome.denied is True
        assert outcome.reason == "policy violation"
        assert outcome.updated_input == {"command": "ls -l"}
        assert outcome.additional_context == "note this"

    def test_stdout_json_ok_false(self, tmp_path):
        backend = CommandHookBackend(tmp_path)
        json_doc = json.dumps({"ok": False, "reason": "lint failed"})
        hook = CommandHook(command=f"echo '{json_doc}'")
        outcome = backend.execute(hook, {}, "Stop")
        assert outcome.exit_code == BLOCKING_EXIT_CODE
        assert "lint failed" in outcome.blocking_errors[0]


class TestPromptHookBackend:
    @pytest.mark.anyio
    async def test_deterministic_default_when_no_model_caller(self):
        backend = PromptHookBackend()
        hook = PromptHook(prompt="Verify $ARGUMENTS")
        outcome = await backend.execute(hook, {"tool": "Bash"}, "PreToolUse")
        assert outcome.exit_code == 0
        assert outcome.denied is False

    @pytest.mark.anyio
    async def test_prompt_hook_ok_true(self):
        backend = PromptHookBackend(model_caller=lambda sys, prompt: '{"ok": true}')
        hook = PromptHook(prompt="Check secrets in $ARGUMENTS")
        outcome = await backend.execute(hook, {"cmd": "echo hi"}, "PreToolUse")
        assert outcome.exit_code == 0
        assert outcome.denied is False

    @pytest.mark.anyio
    async def test_prompt_hook_ok_false_blocks(self):
        backend = PromptHookBackend(
            model_caller=lambda sys, prompt: '{"ok": false, "reason": "Secret detected"}'
        )
        hook = PromptHook(prompt="Check secrets in $ARGUMENTS")
        outcome = await backend.execute(hook, {"cmd": "api_key=123"}, "PreToolUse")
        assert outcome.exit_code == BLOCKING_EXIT_CODE
        assert outcome.denied is True
        assert "Secret detected" in outcome.reason

    @pytest.mark.anyio
    async def test_prompt_hook_malformed_json_fails_open(self):
        backend = PromptHookBackend(model_caller=lambda sys, prompt: "I am an AI and I think ok")
        hook = PromptHook(prompt="Check secrets")
        outcome = await backend.execute(hook, {}, "PreToolUse")
        assert outcome.exit_code == 1
        assert outcome.denied is False  # Non-blocking error, fails open!


class TestAgentHookBackend:
    @pytest.mark.anyio
    async def test_agent_hook_ok_true(self):
        backend = AgentHookBackend(agent_runner=lambda prompt, payload: {"ok": True})
        hook = AgentHook(prompt="Verify tests passed")
        outcome = await backend.execute(hook, {}, "Stop")
        assert outcome.exit_code == 0
        assert not outcome.blocking_errors

    @pytest.mark.anyio
    async def test_agent_hook_ok_false_blocks(self):
        backend = AgentHookBackend(
            agent_runner=lambda prompt, payload: {"ok": False, "reason": "Unit test failed in test_foo.py"}
        )
        hook = AgentHook(prompt="Verify tests passed")
        outcome = await backend.execute(hook, {}, "Stop")
        assert outcome.exit_code == BLOCKING_EXIT_CODE
        assert "Unit test failed" in outcome.blocking_errors[0]


class TestHttpHookBackend:
    def test_http_hook_200_proceeds(self):
        def client(url, payload, headers):
            return (200, '{"status": "ok"}')

        backend = HttpHookBackend(http_client=client)
        hook = HttpHook(url="https://api.example.com/audit")
        outcome = backend.execute(hook, {"action": "run"}, "PreToolUse")
        assert outcome.exit_code == 0
        assert outcome.denied is False

    def test_http_hook_200_with_ok_false_blocks(self):
        def client(url, payload, headers):
            return (200, '{"ok": false, "reason": "Audit rejected"}')

        backend = HttpHookBackend(http_client=client)
        hook = HttpHook(url="https://api.example.com/audit")
        outcome = backend.execute(hook, {"action": "delete"}, "PreToolUse")
        assert outcome.exit_code == BLOCKING_EXIT_CODE
        assert outcome.denied is True
        assert "Audit rejected" in outcome.reason

    def test_http_hook_403_blocks(self):
        def client(url, payload, headers):
            return (403, "Access forbidden by security policy")

        backend = HttpHookBackend(http_client=client)
        hook = HttpHook(url="https://api.example.com/audit")
        outcome = backend.execute(hook, {}, "PreToolUse")
        assert outcome.exit_code == BLOCKING_EXIT_CODE
        assert outcome.denied is True
        assert "Access forbidden" in outcome.reason
