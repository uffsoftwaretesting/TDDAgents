"""
Tests for the prompt-hook executor (`app/utils/hooks/exec_prompt_hook.py`), a port of
`execPromptHook.ts`. The model is always a fake.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import app.utils.hooks.exec_prompt_hook as ep
from app.schemas.hooks import PromptHook
from app.tool import CancelToken
from app.utils.hooks.exec_prompt_hook import (
    DEFAULT_PROMPT_HOOK_TIMEOUT_MS,
    HOOK_RESPONSE_JSON_SCHEMA,
    PROMPT_HOOK_SYSTEM_PROMPT,
    SMALL_FAST_MODEL_ENV,
    HookCancelled,
    add_arguments_to_prompt,
    exec_prompt_hook,
    get_small_fast_model,
    parse_arguments,
    parse_hook_response,
    run_with_signal,
    substitute_arguments,
)


class FakeCaller:
    def __init__(self, reply: Any = '{"ok": true}', delay: float = 0.0, error: Exception | None = None) -> None:
        self.reply = reply
        self.delay = delay
        self.error = error
        self.calls: list[tuple[list[Any], str, str, Any]] = []

    async def __call__(self, messages, system_prompt, model, output_schema):
        self.calls.append((list(messages), system_prompt, model, output_schema))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.reply


def run(hook: PromptHook, caller: FakeCaller, *, signal: Any = None, messages: Any = None,
        tool_use_id: str = "tu-1", default_model: str | None = "small") -> Any:
    return asyncio.run(
        exec_prompt_hook(
            hook, "Stop", "Stop", '{"a": 1}', signal, None, messages, tool_use_id,
            model_caller=caller, default_model=default_model,
        )
    )


# ── argument substitution ────────────────────────────────────────────────────


def test_arguments_placeholder_replaced_everywhere():
    assert substitute_arguments("x $ARGUMENTS y $ARGUMENTS", "A B") == "x A B y A B"


def test_indexed_arguments_and_shorthand():
    assert substitute_arguments("$ARGUMENTS[1]-$0-$2", 'a "b c"') == "b c-a-"


def test_shorthand_not_followed_by_word_char():
    assert substitute_arguments("$1x $1", "a b") == "$1x b"


def test_no_placeholder_appends_arguments():
    assert substitute_arguments("check it", '{"a":1}') == 'check it\n\nARGUMENTS: {"a":1}'


def test_no_placeholder_no_append_when_disabled_or_empty():
    assert substitute_arguments("check it", "x", append_if_no_placeholder=False) == "check it"
    assert substitute_arguments("check it", "") == "check it"
    assert substitute_arguments("check it", None) == "check it"


def test_add_arguments_to_prompt_uses_substitution():
    assert add_arguments_to_prompt("in: $ARGUMENTS", "{}") == "in: {}"


def test_parse_arguments_shell_and_fallback():
    assert parse_arguments("") == []
    assert parse_arguments("   ") == []
    assert parse_arguments("a 'b c' d") == ["a", "b c", "d"]
    assert parse_arguments('a "unterminated  b') == ["a", '"unterminated', "b"]


# ── schema ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"ok": True}, ({"ok": True}, None)),
        ({"ok": False, "reason": "r"}, ({"ok": False, "reason": "r"}, None)),
        ({"ok": True, "extra": 1}, ({"ok": True}, None)),
        ({"ok": "yes"}, (None, "ok: Expected boolean")),
        ({}, (None, "ok: Expected boolean")),
        ({"ok": True, "reason": 3}, (None, "reason: Expected string")),
        ([1], (None, "Expected object, received list")),
    ],
)
def test_parse_hook_response(data, expected):
    assert parse_hook_response(data) == expected


# ── model selection ──────────────────────────────────────────────────────────


def test_small_fast_model_env_wins(monkeypatch):
    monkeypatch.setenv(SMALL_FAST_MODEL_ENV, "envmodel")
    assert get_small_fast_model("d") == "envmodel"


def test_small_fast_model_of_a_claude_run_is_upstreams_default_haiku(monkeypatch):
    monkeypatch.delenv(SMALL_FAST_MODEL_ENV, raising=False)
    monkeypatch.delenv("TDDAGENTS_DEFAULT_HAIKU_MODEL", raising=False)
    assert get_small_fast_model("claude-sonnet-5") == "claude-haiku-4-5"
    assert get_small_fast_model("claude-haiku-5-5") == "claude-haiku-4-5"
    monkeypatch.setenv("TDDAGENTS_DEFAULT_HAIKU_MODEL", "claude-haiku-x")
    assert get_small_fast_model("claude-opus-4-6") == "claude-haiku-x"


def test_small_fast_model_of_another_provider_is_the_runs_own(monkeypatch):
    monkeypatch.delenv(SMALL_FAST_MODEL_ENV, raising=False)
    assert get_small_fast_model("gpt-5-mini") == "gpt-5-mini"
    assert get_small_fast_model("d") == "d"
    assert get_small_fast_model("") == ""


def test_small_fast_model_without_a_default_follows_config(monkeypatch):
    monkeypatch.delenv(SMALL_FAST_MODEL_ENV, raising=False)
    monkeypatch.delenv("TDDAGENTS_DEFAULT_HAIKU_MODEL", raising=False)
    from app.config.config import Config

    monkeypatch.setattr(Config, "MODEL", "claude-sonnet-5")
    assert get_small_fast_model() == "claude-haiku-4-5"
    monkeypatch.setattr(Config, "MODEL", "gpt-5-mini")
    assert get_small_fast_model() == "gpt-5-mini"


def test_hook_model_overrides_default(monkeypatch):
    monkeypatch.delenv(SMALL_FAST_MODEL_ENV, raising=False)
    caller = FakeCaller()
    run(PromptHook(prompt="p", model="big"), caller)
    assert caller.calls[0][2] == "big"
    caller2 = FakeCaller()
    run(PromptHook(prompt="p"), caller2)
    assert caller2.calls[0][2] == "small"


# ── outcomes ─────────────────────────────────────────────────────────────────


def test_success_request_shape_and_attachment():
    caller = FakeCaller('  {"ok": true}  ')
    hook = PromptHook(prompt="Check $ARGUMENTS")
    result = run(hook, caller)
    messages, system_prompt, _, schema = caller.calls[0]
    assert len(messages) == 1 and isinstance(messages[0], HumanMessage)
    assert messages[0].content == 'Check {"a": 1}'
    assert system_prompt == PROMPT_HOOK_SYSTEM_PROMPT
    assert "TDDAgents" in system_prompt
    assert schema == HOOK_RESPONSE_JSON_SCHEMA
    assert result.outcome == "success"
    assert result.hook is hook
    att = result.attachment
    assert (att.type, att.hook_name, att.tool_use_id, att.hook_event, att.content) == (
        "hook_success", "Stop", "tu-1", "Stop", "")
    assert result.blocking_error is None and result.prevent_continuation is False


def test_history_is_prepended():
    caller = FakeCaller()
    history = [HumanMessage(content="h1"), AIMessage(content="a1")]
    run(PromptHook(prompt="p"), caller, messages=history)
    sent = caller.calls[0][0]
    assert sent[:2] == history and sent[2].content.startswith("p")
    assert len(sent) == 3


def test_empty_history_sends_only_prompt():
    caller = FakeCaller()
    run(PromptHook(prompt="p"), caller, messages=[])
    assert len(caller.calls[0][0]) == 1


def test_generated_tool_use_id_when_missing():
    result = run(PromptHook(prompt="p"), FakeCaller(), tool_use_id="")
    assert result.attachment.tool_use_id.startswith("hook-")


def test_not_met_blocks_and_prevents_continuation():
    hook = PromptHook(prompt="the prompt")
    result = run(hook, FakeCaller('{"ok": false, "reason": "tests missing"}'))
    assert result.outcome == "blocking"
    assert result.blocking_error.blocking_error == "Prompt hook condition was not met: tests missing"
    assert result.blocking_error.command == "the prompt"
    assert result.prevent_continuation is True
    assert result.stop_reason == "tests missing"
    assert result.attachment is None


@pytest.mark.parametrize("reply", ["not json", "", "null", "false", "0", '""'])
def test_unparseable_reply_is_json_validation_failure(reply):
    result = run(PromptHook(prompt="p"), FakeCaller(reply))
    assert result.outcome == "non_blocking_error"
    att = result.attachment
    assert (att.type, att.stderr, att.stdout, att.exit_code) == (
        "hook_non_blocking_error", "JSON validation failed", reply.strip(), 1)
    assert (att.hook_name, att.hook_event, att.tool_use_id) == ("Stop", "Stop", "tu-1")


def test_schema_failure_is_non_blocking():
    result = run(PromptHook(prompt="p"), FakeCaller('{"ok": "x"}'))
    assert result.outcome == "non_blocking_error"
    assert result.attachment.stderr == "Schema validation failed: ok: Expected boolean"
    assert result.attachment.stdout == '{"ok": "x"}'


def test_empty_list_reply_is_schema_failure():
    result = run(PromptHook(prompt="p"), FakeCaller("[]"))
    assert result.attachment.stderr.startswith("Schema validation failed")


def test_caller_error_is_non_blocking():
    result = run(PromptHook(prompt="p"), FakeCaller(error=RuntimeError("boom")))
    assert result.outcome == "non_blocking_error"
    att = result.attachment
    assert att.stderr == "Error executing prompt hook: boom"
    assert att.stdout == "" and att.exit_code == 1


def test_timeout_is_cancelled():
    result = run(PromptHook(prompt="p", timeout=0.05), FakeCaller(delay=2))
    assert result.outcome == "cancelled"
    assert result.attachment is None


def test_parent_abort_is_cancelled():
    token = CancelToken()
    token.cancel()
    caller = FakeCaller()
    result = run(PromptHook(prompt="p"), caller, signal=token)
    assert result.outcome == "cancelled"
    assert caller.calls == []


def test_abort_mid_call_is_cancelled():
    token = CancelToken()

    async def go():
        async def later():
            await asyncio.sleep(0.1)
            token.cancel()
        asyncio.ensure_future(later())
        return await exec_prompt_hook(
            PromptHook(prompt="p"), "Stop", "Stop", "{}", token, None, None, "t",
            model_caller=FakeCaller(delay=3), default_model="m",
        )

    assert asyncio.run(go()).outcome == "cancelled"


def test_default_timeout_constant():
    assert DEFAULT_PROMPT_HOOK_TIMEOUT_MS == 30000


def test_default_timeout_used_when_hook_has_none(monkeypatch):
    seen: list[int] = []

    async def fake_run(op, signal, timeout_ms):
        seen.append(timeout_ms)
        return await op()

    monkeypatch.setattr(ep, "run_with_signal", fake_run)
    run(PromptHook(prompt="p"), FakeCaller())
    run(PromptHook(prompt="p", timeout=2.5), FakeCaller())
    assert seen == [30000, 2500]


# ── run_with_signal ──────────────────────────────────────────────────────────


def test_run_with_signal_returns_result():
    async def op():
        return 7
    assert asyncio.run(run_with_signal(op, None, 1000)) == 7


def test_run_with_signal_raises_on_pre_aborted_and_timeout():
    token = CancelToken()
    token.cancel()

    async def op():
        await asyncio.sleep(5)

    with pytest.raises(HookCancelled):
        asyncio.run(run_with_signal(op, token, 1000))
    with pytest.raises(HookCancelled):
        asyncio.run(run_with_signal(op, None, 20))


def test_run_with_signal_propagates_errors():
    async def op():
        raise ValueError("x")
    with pytest.raises(ValueError):
        asyncio.run(run_with_signal(op, None, 1000))


def test_text_of_extracts_text_blocks():
    assert ep._text_of("abc") == "abc"
    assert ep._text_of([{"type": "text", "text": "a"}, {"type": "tool_use"}, "b"]) == "ab"
    assert ep._text_of(None) == ""
