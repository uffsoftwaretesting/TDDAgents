"""
Tests for Hook Schemas and Lossless Round-Trip Serialization (Part H2).
"""

from __future__ import annotations

import json

from app.hooks.schemas import (
    DEFAULT_HOOK_TIMEOUT,
    DEFAULT_PROMPT_TIMEOUT,
    AgentHook,
    CommandHook,
    HookCommand,
    HookMatcher,
    HookSettings,
    HttpHook,
    PromptHook,
    parse_hook,
    parse_matcher,
)


class TestHookSchemas:
    def test_command_hook_fields_and_defaults(self):
        hook = CommandHook(command="echo test")
        assert hook.type == "command"
        assert hook.command == "echo test"
        assert hook.shell == "bash"
        assert hook.timeout == DEFAULT_HOOK_TIMEOUT
        assert hook.label == "echo test"
        assert hook.if_condition is None
        assert hook.once is False

    def test_hook_command_is_backward_compatible_alias(self):
        hook = HookCommand(command="pytest", status_message="Running tests")
        assert isinstance(hook, CommandHook)
        assert hook.label == "Running tests"

    def test_prompt_hook_fields(self):
        hook = PromptHook(prompt="Verify $ARGUMENTS", model="claude-3-5-sonnet")
        assert hook.type == "prompt"
        assert hook.prompt == "Verify $ARGUMENTS"
        assert hook.model == "claude-3-5-sonnet"
        assert hook.timeout == DEFAULT_PROMPT_TIMEOUT
        assert hook.label == "Verify $ARGUMENTS"

    def test_agent_hook_fields(self):
        hook = AgentHook(prompt="Inspect workspace", timeout=45.0)
        assert hook.type == "agent"
        assert hook.prompt == "Inspect workspace"
        assert hook.timeout == 45.0
        assert hook.label == "Inspect workspace"

    def test_http_hook_fields(self):
        hook = HttpHook(
            url="https://hooks.slack.com/services/xyz",
            headers={"Authorization": "Bearer $TOKEN"},
            allowed_env_vars=("TOKEN",),
        )
        assert hook.type == "http"
        assert hook.url == "https://hooks.slack.com/services/xyz"
        assert hook.headers == {"Authorization": "Bearer $TOKEN"}
        assert hook.allowed_env_vars == ("TOKEN",)
        assert hook.label == "https://hooks.slack.com/services/xyz"


class TestLosslessRoundTripSerialization:
    """
    H2 Invariant:
    A schema used for round-tripping user configuration must NOT contain transforms
    producing non-serializable values, or saving the file silently deletes the user's settings.
    """

    def test_round_trip_all_hook_types(self):
        original_settings = HookSettings(
            events={
                "PreToolUse": (
                    HookMatcher(
                        matcher="Bash",
                        hooks=(
                            CommandHook(
                                command="git diff --check",
                                timeout=15.0,
                                if_condition="Bash(git commit*)",
                                status_message="Auditing git commit",
                                shell="bash",
                                once=True,
                                async_=False,
                            ),
                            PromptHook(
                                prompt="Ensure no secrets in $ARGUMENTS",
                                model="haiku",
                                timeout=20.0,
                            ),
                        ),
                    ),
                    HookMatcher(
                        matcher="Write",
                        hooks=(
                            AgentHook(
                                prompt="Verify changes against conventions: $ARGUMENTS",
                                timeout=90.0,
                            ),
                            HttpHook(
                                url="https://example.com/audit",
                                headers={"X-API-Key": "$KEY"},
                                allowed_env_vars=("KEY",),
                            ),
                        ),
                    ),
                ),
                "Stop": (
                    HookMatcher(
                        matcher=None,
                        hooks=(
                            CommandHook(command="python -m pytest"),
                        ),
                    ),
                ),
            },
            sources=("~/.tddagents/settings.json",),
        )

        # 1. Round-trip through dict
        data_dict = original_settings.to_dict()
        assert isinstance(data_dict, dict)
        recovered_from_dict = HookSettings.from_dict(data_dict)
        assert not recovered_from_dict.is_empty
        assert len(recovered_from_dict.events) == len(original_settings.events)

        # Verify no functions or lambdas exist in the dict (must be pure JSON primitives)
        json_str = json.dumps(data_dict)
        assert json_str is not None

        # 2. Round-trip through JSON
        recovered_from_json = HookSettings.from_json(original_settings.to_json())

        # Verify exact round-trip preservation
        for event, matchers in original_settings.events.items():
            rec_matchers = recovered_from_json.matchers_for(event)
            assert len(rec_matchers) == len(matchers)
            for orig_m, rec_m in zip(matchers, rec_matchers):
                assert orig_m.matcher == rec_m.matcher
                assert len(orig_m.hooks) == len(rec_m.hooks)
                for orig_h, rec_h in zip(orig_m.hooks, rec_m.hooks):
                    assert orig_h.type == rec_h.type
                    assert orig_h.timeout == rec_h.timeout
                    assert orig_h.if_condition == rec_h.if_condition
                    assert orig_h.status_message == rec_h.status_message
                    if isinstance(orig_h, CommandHook):
                        assert isinstance(rec_h, CommandHook)
                        assert orig_h.command == rec_h.command
                        assert orig_h.shell == rec_h.shell
                    elif isinstance(orig_h, PromptHook):
                        assert isinstance(rec_h, PromptHook)
                        assert orig_h.prompt == rec_h.prompt
                        assert orig_h.model == rec_h.model
                    elif isinstance(orig_h, AgentHook):
                        assert isinstance(rec_h, AgentHook)
                        assert orig_h.prompt == rec_h.prompt
                    elif isinstance(orig_h, HttpHook):
                        assert isinstance(rec_h, HttpHook)
                        assert orig_h.url == rec_h.url
                        assert orig_h.headers == rec_h.headers
                        assert orig_h.allowed_env_vars == rec_h.allowed_env_vars


class TestDefensiveParsing:
    def test_parse_hook_non_dict_returns_none(self):
        assert parse_hook("not a dict") is None
        assert parse_hook(123) is None

    def test_parse_hook_missing_required_fields(self):
        assert parse_hook({"type": "command"}) is None
        assert parse_hook({"type": "prompt"}) is None
        assert parse_hook({"type": "agent"}) is None
        assert parse_hook({"type": "http"}) is None

    def test_parse_hook_invalid_timeout_uses_default(self):
        hook = parse_hook({"type": "command", "command": "echo hi", "timeout": -10})
        assert hook is not None
        assert hook.timeout == DEFAULT_HOOK_TIMEOUT

    def test_parse_matcher_with_no_valid_hooks_returns_none(self):
        assert parse_matcher({"matcher": "Bash", "hooks": []}) is None
        assert parse_matcher({"matcher": "Bash", "hooks": [{"type": "unknown"}]}) is None
        assert parse_matcher("not a dict") is None
