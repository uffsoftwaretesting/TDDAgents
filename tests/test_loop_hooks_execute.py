"""Tests for `app/loop/hooks/execute.py`: `HookDispatcher` and the per-event entry points."""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.loop.context import CancelToken
from app.loop.hooks import execute as ex
from app.loop.hooks.config import HooksConfigSnapshot
from app.loop.hooks.events import (
    SESSION_END_HOOK_TIMEOUT_MS_DEFAULT,
    TOOL_HOOK_EXECUTION_TIMEOUT_MS,
)
from app.loop.hooks.execute import (
    HookDispatcher,
    HookOutsideReplResult,
    PreCompactHookResult,
    dispatcher_of,
    execute_permission_request_hooks,
    execute_post_compact_hooks,
    execute_post_tool_hooks,
    execute_post_tool_use_failure_hooks,
    execute_pre_compact_hooks,
    execute_pre_tool_hooks,
    execute_session_end_hooks,
    execute_session_start_hooks,
    execute_stop_failure_hooks,
    execute_stop_hooks,
    execute_subagent_start_hooks,
    execute_user_prompt_submit_hooks,
    last_assistant_text,
)
from app.loop.hooks.messages import HookAttachment, HookBlockingError
from app.loop.hooks.output import AggregatedHookResult, HookResult
from app.loop.hooks.schemas import (
    AgentHook,
    CommandHook,
    HookCommand,
    HookMatcher,
    HttpHook,
    PromptHook,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def snapshot(event: str, *hooks: HookCommand, matcher: str | None = None, **kwargs: Any) -> HooksConfigSnapshot:
    return HooksConfigSnapshot(hooks={event: (HookMatcher(hooks=tuple(hooks), matcher=matcher),)}, **kwargs)


def dispatcher(snap: HooksConfigSnapshot | None = None, tmp_path: Path | None = None, **kwargs: Any) -> HookDispatcher:
    where = str(tmp_path) if tmp_path is not None else "."
    return HookDispatcher(
        snapshot=snap or HooksConfigSnapshot(), session_id="sess", cwd=where, project_dir=where,
        transcript_path="/t.jsonl", **kwargs,
    )


def script(tmp_path: Path, name: str, body: str) -> CommandHook:
    path = tmp_path / name
    path.write_text(body)
    return CommandHook(command=f"sh {path}")


def collect(stream: Any) -> list[AggregatedHookResult]:
    async def go() -> list[AggregatedHookResult]:
        return [item async for item in stream]

    return asyncio.run(go())


class ScriptedPrompts:
    """A prompt executor that answers per prompt, after a per-prompt delay."""

    def __init__(self, answers: Mapping[str, Any], delays: Mapping[str, float] | None = None) -> None:
        self.answers = answers
        self.delays = delays or {}
        self.calls: list[tuple[Any, ...]] = []

    async def __call__(self, hook: PromptHook, *args: Any) -> HookResult:
        self.calls.append((hook, *args))
        await asyncio.sleep(self.delays.get(hook.prompt, 0))
        answer = self.answers[hook.prompt]
        if isinstance(answer, Exception):
            raise answer
        result: HookResult = answer(hook) if callable(answer) else answer
        return result


PRE = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}


def run_prompts(answers: Mapping[str, Any], delays: Mapping[str, float] | None = None,
                hook_input: Mapping[str, Any] = PRE, **kwargs: Any) -> list[AggregatedHookResult]:
    prompts = [PromptHook(prompt=p) for p in answers]
    d = dispatcher(snapshot(str(hook_input["hook_event_name"]), *prompts),
                   prompt_executor=ScriptedPrompts(answers, delays))
    return collect(d.execute_hooks(hook_input, "tu", match_query="Bash", **kwargs))


# ── identity ─────────────────────────────────────────────────────────────────

def test_base_hook_input_carries_only_the_set_fields():
    d = dispatcher()
    assert d.create_base_hook_input() == {"session_id": "sess", "transcript_path": "/t.jsonl", "cwd": "."}
    assert d.create_base_hook_input("acceptEdits", "a1", "tester") == {
        "session_id": "sess", "transcript_path": "/t.jsonl", "cwd": ".",
        "permission_mode": "acceptEdits", "agent_id": "a1", "agent_type": "tester",
    }


def test_has_hook_for_event_reads_snapshot_and_session_matchers():
    d = dispatcher(snapshot("Stop", CommandHook(command="x")))
    assert d.has_hook_for_event("Stop") is True
    assert d.has_hook_for_event("PreToolUse") is False
    d.session_matchers["PreToolUse"] = [HookMatcher(hooks=())]
    assert d.has_hook_for_event("PreToolUse") is True
    d.session_matchers["SessionEnd"] = []
    assert d.has_hook_for_event("SessionEnd") is False


def test_matching_includes_session_matchers():
    hook = CommandHook(command="session-hook")
    d = dispatcher()
    d.session_matchers["Stop"] = [HookMatcher(hooks=(hook,))]
    assert [m.hook for m in d.matching("Stop", {"hook_event_name": "Stop"}, None)] == [hook]


def test_dispatcher_of_accepts_only_a_dispatcher():
    d = dispatcher()
    assert dispatcher_of(SimpleNamespace(hook_dispatcher=d)) is d
    assert dispatcher_of(SimpleNamespace(hook_dispatcher=object())) is None
    assert dispatcher_of(SimpleNamespace()) is None
    assert dispatcher_of(None) is None


# ── execute_hooks: gates ─────────────────────────────────────────────────────

def ok(hook: Any) -> HookResult:
    return HookResult(hook=hook, outcome="success")


def test_disable_all_runs_nothing():
    prompts = ScriptedPrompts({"p": ok})
    d = dispatcher(snapshot("PreToolUse", PromptHook(prompt="p"), disable_all=True), prompt_executor=prompts)
    assert collect(d.execute_hooks(PRE, "tu")) == []
    assert asyncio.run(d.execute_hooks_outside_repl(PRE)) == []
    assert prompts.calls == []


def test_no_matching_hook_runs_nothing():
    prompts = ScriptedPrompts({"p": ok})
    d = dispatcher(snapshot("PreToolUse", PromptHook(prompt="p"), matcher="Write"), prompt_executor=prompts)
    assert collect(d.execute_hooks(PRE, "tu")) == []
    assert asyncio.run(d.execute_hooks_outside_repl(PRE)) == []
    assert prompts.calls == []


def test_an_aborted_signal_runs_nothing():
    token = CancelToken()
    token.cancel()
    prompts = ScriptedPrompts({"p": ok})
    d = dispatcher(snapshot("PreToolUse", PromptHook(prompt="p")), prompt_executor=prompts)
    assert collect(d.execute_hooks(PRE, "tu", signal=token)) == []
    assert asyncio.run(d.execute_hooks_outside_repl(PRE, signal=token)) == []
    assert prompts.calls == []


def test_the_prompt_executor_is_handed_everything():
    prompts = ScriptedPrompts({"p": ok})
    d = dispatcher(snapshot("PreToolUse", PromptHook(prompt="p")), prompt_executor=prompts)
    token = CancelToken()
    ctx = SimpleNamespace(tools=())
    messages = [HumanMessage(content="m")]
    collect(d.execute_hooks(PRE, "tu-9", match_query="Bash", signal=token, tool_context=ctx, messages=messages))
    [(_hook, hook_name, event, json_input, signal, context, msgs, tool_use_id)] = prompts.calls
    assert (hook_name, event, tool_use_id) == ("PreToolUse:Bash", "PreToolUse", "tu-9")
    assert json.loads(json_input) == PRE
    assert signal is token and context is ctx and msgs is messages


def test_hook_name_without_a_match_query_is_the_event():
    prompts = ScriptedPrompts({"p": ok})
    d = dispatcher(snapshot("Stop", PromptHook(prompt="p")), prompt_executor=prompts)
    collect(d.execute_hooks({"hook_event_name": "Stop"}, "tu"))
    assert prompts.calls[0][1] == "Stop"


# ── execute_hooks: aggregation ───────────────────────────────────────────────

def test_every_item_kind_in_upstreams_order():
    blocking = HookBlockingError(blocking_error="no", command="c")
    attachment = HookAttachment(type="hook_blocking_error", hook_name="PreToolUse:Bash", tool_use_id="tu",
                                hook_event="PreToolUse", blocking_error=blocking)

    def full(hook: Any) -> HookResult:
        return HookResult(
            hook=hook, outcome="success", attachment=attachment, system_message="sys",
            blocking_error=blocking, prevent_continuation=True, stop_reason="halt",
            permission_behavior="ask", hook_permission_decision_reason="why",
            additional_context="ctx", initial_user_message="first", updated_input={"command": "pwd"},
            updated_mcp_tool_output={"x": 1}, permission_request_result={"behavior": "allow"},
            watch_paths=("/w",), retry=True,
        )

    items = run_prompts({"p": full})
    assert items == [
        AggregatedHookResult(prevent_continuation=True, stop_reason="halt"),
        AggregatedHookResult(blocking_error=blocking),
        AggregatedHookResult(attachment=attachment),
        AggregatedHookResult(attachment=HookAttachment(
            type="hook_system_message", hook_name="PreToolUse:Bash", tool_use_id="tu",
            hook_event="PreToolUse", content="sys")),
        AggregatedHookResult(additional_contexts=("ctx",)),
        AggregatedHookResult(initial_user_message="first"),
        AggregatedHookResult(watch_paths=("/w",)),
        AggregatedHookResult(updated_mcp_tool_output={"x": 1}),
        AggregatedHookResult(permission_behavior="ask", hook_permission_decision_reason="why",
                             hook_source="settings", updated_input={"command": "pwd"}),
        AggregatedHookResult(permission_request_result={"behavior": "allow"}),
        AggregatedHookResult(retry=True),
    ]


def test_a_system_message_without_an_attachment_has_empty_names():
    items = run_prompts({"p": lambda h: HookResult(hook=h, outcome="success", system_message="s")})
    assert items == [AggregatedHookResult(attachment=HookAttachment(
        type="hook_system_message", hook_name="", tool_use_id="", hook_event="", content="s"))]


def test_a_silent_success_yields_nothing():
    assert run_prompts({"p": ok}) == []


def test_a_rewrite_without_a_decision_is_a_passthrough_item():
    items = run_prompts({"p": lambda h: HookResult(hook=h, outcome="success", updated_input={"a": 1})})
    assert items == [AggregatedHookResult(updated_input={"a": 1})]


def test_updated_input_rides_with_allow_and_ask_but_not_deny():
    cases: tuple[tuple[Any, Any], ...] = (("allow", {"a": 1}), ("ask", {"a": 1}), ("deny", None))
    for behavior, expected in cases:
        items = run_prompts({"p": lambda h: HookResult(
            hook=h, outcome="success", permission_behavior=behavior, updated_input={"a": 1})})  # noqa: B023
        assert items == [AggregatedHookResult(permission_behavior=behavior, hook_source="settings",
                                              updated_input=expected)]


def test_passthrough_sets_no_permission():
    items = run_prompts({"p": lambda h: HookResult(hook=h, outcome="success", permission_behavior="passthrough")})
    assert items == []


def _decide(behavior: str | None, reason: str | None = None) -> Any:
    return lambda h: HookResult(hook=h, outcome="success", permission_behavior=behavior,  # type: ignore[arg-type]
                                hook_permission_decision_reason=reason)


@pytest.mark.parametrize("order, expected", [
    (("allow", "deny"), ["allow", "deny"]),
    (("deny", "allow"), ["deny", "deny"]),
    (("ask", "allow"), ["ask", "ask"]),
    (("allow", "ask"), ["allow", "ask"]),
    (("deny", "ask"), ["deny", "deny"]),
    (("ask", "deny"), ["ask", "deny"]),
    (("allow", "allow"), ["allow", "allow"]),
    (("passthrough", "allow"), ["allow"]),
    (("allow", "passthrough"), ["allow", "allow"]),
])
def test_permission_precedence_is_deny_then_ask_then_allow(order, expected):
    answers = {f"p{i}": _decide(b, f"r{i}") for i, b in enumerate(order)}
    delays = {f"p{i}": 0.02 * i for i in range(len(order))}
    items = run_prompts(answers, delays)
    assert [i.permission_behavior for i in items] == expected


def test_the_aggregate_is_re_yielded_after_a_later_silent_hook():
    items = run_prompts({"a": _decide("deny", "first"), "b": _decide(None, None)}, {"a": 0, "b": 0.05})
    assert items == [
        AggregatedHookResult(permission_behavior="deny", hook_permission_decision_reason="first",
                             hook_source="settings"),
        AggregatedHookResult(permission_behavior="deny", hook_permission_decision_reason=None,
                             hook_source="settings"),
    ]


def test_results_are_yielded_in_completion_order():
    def tagged(tag: str) -> Any:
        return lambda h: HookResult(hook=h, outcome="success", additional_context=tag)

    items = run_prompts({"slow": tagged("slow"), "fast": tagged("fast")}, {"slow": 0.1, "fast": 0})
    assert [i.additional_contexts for i in items] == [("fast",), ("slow",)]


def test_hooks_run_in_parallel():
    import time

    started = time.monotonic()
    run_prompts({f"p{i}": ok for i in range(4)}, {f"p{i}": 0.2 for i in range(4)})
    assert time.monotonic() - started < 0.5


def test_closing_the_stream_early_cancels_the_rest():
    prompts = ScriptedPrompts({"fast": lambda h: HookResult(hook=h, outcome="success", additional_context="x"),
                               "slow": ok}, {"slow": 5})
    d = dispatcher(snapshot("PreToolUse", PromptHook(prompt="fast"), PromptHook(prompt="slow")),
                   prompt_executor=prompts)

    async def go() -> AggregatedHookResult:
        stream: Any = d.execute_hooks(PRE, "tu")
        first: AggregatedHookResult = await stream.__anext__()
        await stream.aclose()
        return first

    assert asyncio.run(asyncio.wait_for(go(), timeout=2)).additional_contexts == ("x",)


# ── _run_one: per hook type ──────────────────────────────────────────────────

def one(d: HookDispatcher, hook: HookCommand, messages: Sequence[Any] | None = None,
        hook_input: Mapping[str, Any] = PRE) -> HookResult:
    return asyncio.run(d._run_one(hook, 0, str(hook_input["hook_event_name"]), "PreToolUse:Bash",
                                  json.dumps(hook_input), "tu", None, 1000, None, messages, False,
                                  hook_input.get("agent_type")))


@pytest.fixture
def clock(monkeypatch):
    ticks = itertools.count(100.0, 0.25)
    monkeypatch.setattr(ex, "time", SimpleNamespace(monotonic=lambda: next(ticks)))


@pytest.mark.parametrize("hook, message", [
    (PromptHook(prompt="p"), "Failed to run: No prompt hook executor is configured for this run."),
    (AgentHook(prompt="p"), "Failed to run: No agent hook executor is configured for this run."),
    (HttpHook(url="https://x.test/h"), "Failed to run: No HTTP hook executor is configured for this run."),
])
def test_a_missing_executor_is_a_non_blocking_error(hook, message, clock):
    result = one(dispatcher(), hook, messages=[])
    assert result == HookResult(hook=hook, outcome="non_blocking_error", attachment=HookAttachment(
        type="hook_non_blocking_error", hook_name="PreToolUse:Bash", tool_use_id="tu", hook_event="PreToolUse",
        stderr=message, stdout="", exit_code=1,
        command=hook.url if isinstance(hook, HttpHook) else "p", duration_ms=250))


def test_an_agent_hook_without_messages_is_a_bug_report():
    async def agent(*args: Any) -> HookResult:
        raise AssertionError("must not run")

    result = one(dispatcher(agent_executor=agent), AgentHook(prompt="verify"))
    assert result.attachment is not None
    assert result.attachment.stderr == "Failed to run: Messages are required for agent hooks. This is a bug."


def test_the_agent_executor_is_handed_everything_including_agent_type(clock):
    seen: list[Any] = []

    async def agent(*args: Any) -> HookResult:
        seen.append(args)
        return HookResult(hook=args[0], outcome="success", attachment=HookAttachment(
            type="hook_success", hook_name="n", tool_use_id="tu", hook_event="SubagentStop", content=""))

    messages = [HumanMessage(content="m")]
    hook = AgentHook(prompt="verify", status_message="Verifying")
    hook_input = {"hook_event_name": "SubagentStop", "agent_type": "tester"}
    result = one(dispatcher(agent_executor=agent), hook, messages, hook_input)
    [(h, name, event, json_input, _signal, _ctx, tool_use_id, msgs, agent_type)] = seen
    assert (h, name, event, tool_use_id, msgs, agent_type) == (
        hook, "PreToolUse:Bash", "SubagentStop", "tu", messages, "tester")
    assert json.loads(json_input) == hook_input
    assert result.attachment is not None
    assert (result.attachment.command, result.attachment.duration_ms) == ("Verifying", 250)


def test_agent_type_is_none_when_the_input_has_none():
    seen: list[Any] = []

    async def agent(*args: Any) -> HookResult:
        seen.append(args[-1])
        return HookResult(hook=args[0], outcome="success")

    d = dispatcher(snapshot("Stop", AgentHook(prompt="v")), agent_executor=agent)
    collect(d.execute_hooks({"hook_event_name": "Stop"}, "tu", messages=[]))
    assert seen == [None]
    d = dispatcher(snapshot("Stop", AgentHook(prompt="v")), agent_executor=agent)
    collect(d.execute_hooks({"hook_event_name": "Stop", "agent_type": "dev"}, "tu", messages=[]))
    assert seen == [None, "dev"]


@pytest.mark.parametrize("kind, timed", [
    ("hook_success", True), ("hook_non_blocking_error", True),
    ("hook_blocking_error", False), ("hook_cancelled", False),
])
def test_timing_is_injected_only_into_success_and_non_blocking(kind, timed, clock):
    attachment = HookAttachment(type=kind, hook_name="n", tool_use_id="tu", hook_event="PreToolUse")
    prompts = ScriptedPrompts({"p": lambda h: HookResult(hook=h, outcome="success", attachment=attachment)})
    result = one(dispatcher(prompt_executor=prompts), PromptHook(prompt="p"))
    assert result.attachment is not None
    expected = ("p", 250) if timed else (None, None)
    assert (result.attachment.command, result.attachment.duration_ms) == expected


def test_a_prompt_result_without_an_attachment_is_returned_as_is():
    prompts = ScriptedPrompts({"p": ok})
    hook = PromptHook(prompt="p")
    assert one(dispatcher(prompt_executor=prompts), hook) == HookResult(hook=hook, outcome="success")


def test_an_executor_exception_becomes_a_non_blocking_error():
    prompts = ScriptedPrompts({"p": ValueError("model down")})
    result = one(dispatcher(prompt_executor=prompts), PromptHook(prompt="p"))
    assert result.outcome == "non_blocking_error"
    assert result.attachment is not None and result.attachment.stderr == "Failed to run: model down"


# ── _run_command ─────────────────────────────────────────────────────────────

def cmd(tmp_path: Path, body: str, **kwargs: Any) -> tuple[CommandHook, HookResult]:
    hook = script(tmp_path, "h.sh", body)
    if kwargs:
        hook = CommandHook(command=hook.command, **kwargs)
    return hook, one(dispatcher(tmp_path=tmp_path), hook)


def test_plain_exit_zero_is_a_success_with_trimmed_content(tmp_path, clock):
    hook, result = cmd(tmp_path, "echo '  hello  '; echo note >&2")
    assert result == HookResult(hook=hook, outcome="success", attachment=HookAttachment(
        type="hook_success", hook_name="PreToolUse:Bash", tool_use_id="tu", hook_event="PreToolUse",
        content="hello", stdout="  hello  \n", stderr="note\n", exit_code=0, command=hook.command,
        duration_ms=250))


def test_exit_two_is_a_blocking_error_naming_the_command(tmp_path):
    hook, result = cmd(tmp_path, "echo refuse >&2; exit 2")
    assert result == HookResult(hook=hook, outcome="blocking", blocking_error=HookBlockingError(
        blocking_error=f"[{hook.command}]: refuse\n", command=hook.command))


def test_exit_two_without_stderr_says_so(tmp_path):
    hook, result = cmd(tmp_path, "echo out; exit 2")
    assert result.blocking_error is not None
    assert result.blocking_error.blocking_error == f"[{hook.command}]: No stderr output"


def test_another_exit_code_is_non_blocking(tmp_path, clock):
    hook, result = cmd(tmp_path, "echo out; echo '  oops ' >&2; exit 5")
    assert result == HookResult(hook=hook, outcome="non_blocking_error", attachment=HookAttachment(
        type="hook_non_blocking_error", hook_name="PreToolUse:Bash", tool_use_id="tu",
        hook_event="PreToolUse", stderr="Failed with non-blocking status code: oops", stdout="out\n",
        exit_code=5, command=hook.command, duration_ms=250))


def test_another_exit_code_without_stderr_says_so(tmp_path):
    _, result = cmd(tmp_path, "exit 1")
    assert result.attachment is not None
    assert result.attachment.stderr == "Failed with non-blocking status code: No stderr output"


def test_a_status_message_is_the_reported_command(tmp_path):
    _, result = cmd(tmp_path, "exit 1", status_message="Linting")
    assert result.attachment is not None and result.attachment.command == "Linting"


def test_a_timed_out_command_is_cancelled(tmp_path, clock):
    hook, result = cmd(tmp_path, "sleep 5", timeout=0.2)
    assert result == HookResult(hook=hook, outcome="cancelled", attachment=HookAttachment(
        type="hook_cancelled", hook_name="PreToolUse:Bash", tool_use_id="tu", hook_event="PreToolUse",
        command=hook.command, duration_ms=250))


def test_the_dispatch_timeout_applies_when_the_hook_has_none(tmp_path):
    hook = script(tmp_path, "h.sh", "sleep 5")
    result = asyncio.run(dispatcher(tmp_path=tmp_path)._run_one(
        hook, 0, "Stop", "Stop", "{}", "tu", None, 200, None, None, False, None))
    assert result.outcome == "cancelled"


def test_a_backgrounded_command_is_a_bare_success(tmp_path):
    d = dispatcher(tmp_path=tmp_path)
    hook = CommandHook(command=script(tmp_path, "h.sh", "sleep 0.1").command, run_async=True)

    async def go() -> tuple[HookResult, int]:
        result = await d._run_one(hook, 0, "PreToolUse", "PreToolUse:Bash", "{}", "tu", None, 1000, None, None,
                                  False, None)
        pending = len(d.registry.get_pending())
        await d.registry.finalize_pending()
        return result, pending

    result, pending = asyncio.run(go())
    assert result == HookResult(hook=hook, outcome="success")
    assert pending == 1


def test_force_sync_reaches_the_command(tmp_path):
    d = dispatcher(snapshot("Stop", CommandHook(command=script(tmp_path, "h.sh", "echo x").command,
                                                run_async=True)), tmp_path=tmp_path)
    collect(d.execute_hooks({"hook_event_name": "Stop"}, "tu", force_sync=True))
    assert d.registry.get_pending() == []


def test_invalid_json_output_is_a_validation_failure(tmp_path, clock):
    hook, result = cmd(tmp_path, """echo '{"continue": "yes"}'""")
    assert result.outcome == "non_blocking_error"
    assert result.attachment is not None
    assert str(result.attachment.stderr).startswith(
        "JSON validation failed: Hook JSON output validation failed:\n  - continue: Expected boolean")
    assert (result.attachment.stdout, result.attachment.exit_code) == ('{"continue": "yes"}\n', 1)
    assert (result.attachment.command, result.attachment.duration_ms) == (hook.command, 250)


def test_an_async_json_reply_at_exit_is_a_bare_success(tmp_path):
    hook, result = cmd(tmp_path, """echo '{"async": true}'""")
    assert result == HookResult(hook=hook, outcome="success")


def test_a_json_reply_is_processed_with_its_exit_status(tmp_path, clock):
    body = """echo '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}'; """
    hook, result = cmd(tmp_path, body + "echo e >&2; exit 3")
    assert result.outcome == "success"
    assert result.permission_behavior == "allow"
    assert result.attachment == HookAttachment(
        type="hook_success", hook_name="PreToolUse:Bash", tool_use_id="tu", hook_event="PreToolUse",
        content="", stdout=result.attachment.stdout if result.attachment else None, stderr="e\n", exit_code=3,
        command=hook.command, duration_ms=250)


def test_a_json_reply_for_the_wrong_event_is_a_failure(tmp_path):
    _, result = cmd(tmp_path, """echo '{"hookSpecificOutput": {"hookEventName": "PostToolUse"}}'""")
    assert result.outcome == "non_blocking_error"
    assert result.attachment is not None
    assert str(result.attachment.stderr).startswith(
        "Failed to run: Hook returned incorrect event name: expected 'PreToolUse' but got 'PostToolUse'")


# ── _run_http ────────────────────────────────────────────────────────────────

def http_result(**kwargs: Any) -> Any:
    fields = {"ok": True, "status_code": 200, "body": "", "error": None, "aborted": False}
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def http_one(answer: Any, hook_input: Mapping[str, Any] = PRE) -> tuple[HttpHook, HookResult, list[Any]]:
    seen: list[Any] = []

    async def http(*args: Any) -> Any:
        seen.append(args)
        return answer

    hook = HttpHook(url="https://hooks.test/pre")
    return hook, one(dispatcher(http_executor=http), hook, hook_input=hook_input), seen


def test_http_is_handed_the_hook_event_and_input():
    token = CancelToken()
    seen: list[Any] = []

    async def http(*args: Any) -> Any:
        seen.append(args)
        return http_result(body="{}")

    hook = HttpHook(url="https://hooks.test/pre")
    asyncio.run(dispatcher(http_executor=http)._run_one(
        hook, 0, "PreToolUse", "PreToolUse:Bash", '{"j": 1}', "tu", token, 1000, None, None, False, None))
    assert seen == [(hook, "PreToolUse", '{"j": 1}', token)]


def test_http_aborted_is_cancelled():
    hook, result, _ = http_one(http_result(aborted=True))
    assert result == HookResult(hook=hook, outcome="cancelled", attachment=HookAttachment(
        type="hook_cancelled", hook_name="PreToolUse:Bash", tool_use_id="tu", hook_event="PreToolUse"))


def test_http_transport_error_is_non_blocking():
    hook, result, _ = http_one(http_result(ok=False, status_code=None, error="refused"))
    assert result == HookResult(hook=hook, outcome="non_blocking_error", attachment=HookAttachment(
        type="hook_non_blocking_error", hook_name="PreToolUse:Bash", tool_use_id="tu", hook_event="PreToolUse",
        stderr="refused", stdout="", exit_code=0))


def test_http_non_2xx_names_status_and_url():
    _, result, _ = http_one(http_result(ok=False, status_code=503))
    assert result.attachment is not None
    assert (result.attachment.stderr, result.attachment.exit_code) == ("HTTP 503 from https://hooks.test/pre", 503)


def test_http_invalid_json_is_a_validation_failure():
    _, result, _ = http_one(http_result(body="plain", status_code=201))
    assert result.attachment is not None
    assert result.attachment.stderr == (
        "JSON validation failed: HTTP hook must return JSON, but got non-JSON response body: plain")
    assert (result.attachment.stdout, result.attachment.exit_code) == ("plain", 201)


def test_http_invalid_json_without_a_status_reports_zero():
    _, result, _ = http_one(http_result(body="plain", status_code=None))
    assert result.attachment is not None and result.attachment.exit_code == 0


def test_http_async_reply_is_a_bare_success():
    hook, result, _ = http_one(http_result(body='{"async": true}'))
    assert result == HookResult(hook=hook, outcome="success")


def test_http_reply_is_processed_with_the_url_as_command():
    body = '{"decision": "block", "reason": "nope"}'
    _hook, result, _ = http_one(http_result(body=body, status_code=200))
    assert result.outcome == "success"
    assert result.blocking_error == HookBlockingError(blocking_error="nope", command="https://hooks.test/pre")
    assert result.attachment is not None and result.attachment.type == "hook_blocking_error"


def test_http_empty_body_is_an_empty_json_reply():
    _hook, result, _ = http_one(http_result(body="", status_code=204))
    assert result.outcome == "success"
    assert result.attachment is not None
    assert (result.attachment.type, result.attachment.stdout, result.attachment.exit_code,
            result.attachment.stderr, result.attachment.command) == ("hook_success", "", 204, "",
                                                                     "https://hooks.test/pre")


# ── execute_hooks_outside_repl ───────────────────────────────────────────────

COMPACT = {"hook_event_name": "PreCompact", "trigger": "auto"}


def outside(d: HookDispatcher, hook_input: Mapping[str, Any] = COMPACT, **kwargs: Any) -> list[HookOutsideReplResult]:
    return asyncio.run(d.execute_hooks_outside_repl(hook_input, **kwargs))


def test_outside_prompt_and_agent_hooks_are_unsupported():
    d = dispatcher(snapshot("PreCompact", PromptHook(prompt="p"), AgentHook(prompt="a")))
    assert outside(d) == [
        HookOutsideReplResult(command="p", succeeded=False,
                              output="Prompt stop hooks are not yet supported outside REPL", blocked=False),
        HookOutsideReplResult(command="a", succeeded=False,
                              output="Agent stop hooks are not yet supported outside REPL", blocked=False),
    ]


def test_outside_command_success_and_order(tmp_path):
    first = script(tmp_path, "a.sh", "sleep 0.1; echo first")
    second = script(tmp_path, "b.sh", "echo second")
    d = dispatcher(snapshot("PreCompact", first, second), tmp_path=tmp_path)
    assert outside(d) == [
        HookOutsideReplResult(command=first.command, succeeded=True, output="first\n", blocked=False),
        HookOutsideReplResult(command=second.command, succeeded=True, output="second\n", blocked=False),
    ]


def test_outside_command_failure_reports_stderr_and_exit_two_blocks(tmp_path):
    two = script(tmp_path, "a.sh", "echo out; echo err >&2; exit 2")
    one_ = script(tmp_path, "b.sh", "echo out; exit 1")
    d = dispatcher(snapshot("PreCompact", two, one_), tmp_path=tmp_path)
    assert outside(d) == [
        HookOutsideReplResult(command=two.command, succeeded=False, output="err\n", blocked=True),
        HookOutsideReplResult(command=one_.command, succeeded=False, output="", blocked=False),
    ]


def test_outside_command_json_block_watch_paths_and_system_message(tmp_path):
    doc = {"decision": "block", "systemMessage": "heads up",
           "hookSpecificOutput": {"hookEventName": "CwdChanged", "watchPaths": ["/a", "/b"]}}
    hook = script(tmp_path, "a.sh", f"echo '{json.dumps(doc)}'")
    [result] = outside(dispatcher(snapshot("PreCompact", hook), tmp_path=tmp_path))
    assert result == HookOutsideReplResult(
        command=hook.command, succeeded=True, output=json.dumps(doc) + "\n", blocked=True,
        watch_paths=("/a", "/b"), system_message="heads up")


def test_outside_command_json_without_specific_output(tmp_path):
    hook = script(tmp_path, "a.sh", """echo '{"decision": "approve"}'""")
    [result] = outside(dispatcher(snapshot("PreCompact", hook), tmp_path=tmp_path))
    assert (result.blocked, result.watch_paths, result.system_message) == (False, (), None)


def test_outside_command_async_json_is_ignored(tmp_path):
    hook = script(tmp_path, "a.sh", """echo '{"async": true}'""")
    [result] = outside(dispatcher(snapshot("PreCompact", hook), tmp_path=tmp_path))
    assert (result.succeeded, result.blocked, result.system_message) == (True, False, None)


def test_outside_command_invalid_json_fails_with_the_validation_text(tmp_path):
    hook = script(tmp_path, "a.sh", """echo '{"continue": 1}'""")
    [result] = outside(dispatcher(snapshot("PreCompact", hook), tmp_path=tmp_path))
    assert (result.succeeded, result.blocked) == (False, False)
    assert result.output.startswith("Hook JSON output validation failed:\n  - continue: Expected boolean")


def test_outside_command_timeout_is_cancelled(tmp_path):
    hook = CommandHook(command=script(tmp_path, "a.sh", "sleep 5").command, timeout=0.2)
    assert outside(dispatcher(snapshot("PreCompact", hook), tmp_path=tmp_path)) == [
        HookOutsideReplResult(command=hook.command, succeeded=False, output="Hook cancelled", blocked=False)]


def test_outside_dispatch_timeout_applies_when_the_hook_has_none(tmp_path):
    hook = script(tmp_path, "a.sh", "sleep 5")
    [result] = outside(dispatcher(snapshot("PreCompact", hook), tmp_path=tmp_path), timeout_ms=200)
    assert result.output == "Hook cancelled"


def test_outside_command_crash_is_a_failure(tmp_path, monkeypatch):
    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("spawn exploded")

    monkeypatch.setattr(ex, "exec_command_hook", boom)
    hook = CommandHook(command="x")
    assert outside(dispatcher(snapshot("PreCompact", hook))) == [
        HookOutsideReplResult(command="x", succeeded=False, output="spawn exploded", blocked=False)]


def test_outside_command_gets_cwd_project_and_registry(tmp_path, monkeypatch):
    seen: list[Any] = []

    async def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append((args, kwargs))
        return SimpleNamespace(aborted=False, stdout="", stderr="", status=0)

    monkeypatch.setattr(ex, "exec_command_hook", spy)
    d = dispatcher(snapshot("PreCompact", CommandHook(command="x")), tmp_path=tmp_path)
    token = CancelToken()
    outside(d, match_query="auto", signal=token)
    [(args, kwargs)] = seen
    assert args == (CommandHook(command="x"), "PreCompact", "PreCompact:auto", json.dumps(COMPACT), token)
    assert kwargs == {"cwd": str(tmp_path), "project_dir": str(tmp_path), "registry": d.registry,
                      "timeout_ms": TOOL_HOOK_EXECUTION_TIMEOUT_MS}


def outside_http(answer: Any, event: str = "PreCompact") -> HookOutsideReplResult:
    async def http(*args: Any) -> Any:
        if isinstance(answer, Exception):
            raise answer
        return answer

    d = dispatcher(snapshot(event, HttpHook(url="https://h.test/x")), http_executor=http)
    [result] = outside(d, {"hook_event_name": event})
    return result


def test_outside_http_paths():
    url = "https://h.test/x"
    assert outside_http(http_result(aborted=True)) == HookOutsideReplResult(url, False, "Hook cancelled", False)
    assert outside_http(http_result(ok=False, error="refused")) == HookOutsideReplResult(url, False, "refused", False)
    assert outside_http(http_result(ok=False, status_code=500)) == HookOutsideReplResult(
        url, False, "HTTP 500 from https://h.test/x", False)
    assert outside_http(http_result(body="not json")) == HookOutsideReplResult(
        url, False, "HTTP hook must return JSON, but got non-JSON response body: not json", False)
    assert outside_http(http_result(body='{"decision": "block"}')) == HookOutsideReplResult(
        url, True, '{"decision": "block"}', True)
    async_reply = '{"async": true}'
    assert outside_http(http_result(body=async_reply)) == HookOutsideReplResult(url, True, async_reply, False)
    assert outside_http(RuntimeError("down")) == HookOutsideReplResult(url, False, "down", False)


def test_outside_http_without_an_executor_fails():
    d = dispatcher(snapshot("PreCompact", HttpHook(url="https://h.test/x")))
    assert outside(d) == [HookOutsideReplResult(
        "https://h.test/x", False, "No HTTP hook executor is configured for this run.", False)]


def test_outside_http_worktree_create_answers_the_path():
    body = json.dumps({"hookSpecificOutput": {"hookEventName": "WorktreeCreate", "worktreePath": "/wt"}})
    assert outside_http(http_result(body=body), "WorktreeCreate").output == "/wt"
    assert outside_http(http_result(body="{}"), "WorktreeCreate").output == ""


# ── per-event entry points ───────────────────────────────────────────────────

class Recorder(HookDispatcher):
    """A real dispatcher (real `has_hook_for_event`) that records what it was asked to run."""

    def __init__(self, snap: HooksConfigSnapshot, outside_results: Sequence[HookOutsideReplResult] = (),
                 items: Sequence[AggregatedHookResult] = ()) -> None:
        super().__init__(snapshot=snap, session_id="sess", cwd="/ws", project_dir="/proj", transcript_path="/t")
        self.calls: list[tuple[dict[str, Any], str | None, dict[str, Any]]] = []
        self.outside_results = list(outside_results)
        self.items = list(items)

    async def execute_hooks(self, hook_input: Mapping[str, Any], tool_use_id: str,
                            **kwargs: Any) -> Any:
        self.calls.append((dict(hook_input), tool_use_id, kwargs))
        for item in self.items:
            yield item

    async def execute_hooks_outside_repl(self, hook_input: Mapping[str, Any],
                                         **kwargs: Any) -> list[HookOutsideReplResult]:
        self.calls.append((dict(hook_input), None, kwargs))
        return self.outside_results


def recorder(*events: str, **kwargs: Any) -> Recorder:
    return Recorder(HooksConfigSnapshot(hooks={e: (HookMatcher(hooks=()),) for e in events}), **kwargs)


BASE = {"session_id": "sess", "transcript_path": "/t", "cwd": "/ws"}


def ctx(d: Any, agent_id: str | None = None, agent_type: str | None = None) -> Any:
    return SimpleNamespace(hook_dispatcher=d, agent_id=agent_id, agent_type=agent_type, cancel=CancelToken())


def test_pre_tool_hooks_input_and_routing():
    d = recorder("PreToolUse")
    context = ctx(d, "a1", "tester")
    collect(execute_pre_tool_hooks(context, "Bash", "tu", {"command": "ls"}, "default", "sig"))
    [(hook_input, tool_use_id, kwargs)] = d.calls
    assert hook_input == {**BASE, "permission_mode": "default", "agent_id": "a1", "agent_type": "tester",
                          "hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "tool_input": {"command": "ls"}, "tool_use_id": "tu"}
    assert tool_use_id == "tu"
    assert kwargs == {"match_query": "Bash", "signal": "sig", "tool_context": context}


def test_tool_entry_points_short_circuit_without_a_dispatcher_or_hook():
    no_hooks = recorder()
    for context in (ctx(None), ctx(no_hooks)):
        assert collect(execute_pre_tool_hooks(context, "Bash", "tu", {}, None)) == []
        assert collect(execute_post_tool_hooks(context, "Bash", "tu", {}, "out", None)) == []
        assert collect(execute_post_tool_use_failure_hooks(context, "Bash", "tu", {}, "e", None, None)) == []
        assert collect(execute_stop_hooks(context, None, None, False, [])) == []
        assert collect(execute_user_prompt_submit_hooks(context, "p", "default")) == []
        asyncio.run(execute_stop_failure_hooks(context, "e", None, []))
    assert no_hooks.calls == []
    assert collect(execute_permission_request_hooks(ctx(None), "Bash", "tu", {}, None)) == []
    assert collect(execute_subagent_start_hooks(ctx(None), "a", "t")) == []
    assert collect(execute_session_start_hooks(None, "startup")) == []
    assert asyncio.run(execute_pre_compact_hooks(None, "auto", None)) == PreCompactHookResult()
    assert asyncio.run(execute_post_compact_hooks(None, "auto", "s")) is None
    assert asyncio.run(execute_session_end_hooks(None, "other")) == []


def test_post_tool_hooks_input():
    d = recorder("PostToolUse")
    context = ctx(d)
    collect(execute_post_tool_hooks(context, "Edit", "tu2", {"file_path": "a"}, {"ok": 1}, "plan"))
    [(hook_input, _tool_use_id, kwargs)] = d.calls
    assert hook_input == {**BASE, "permission_mode": "plan", "hook_event_name": "PostToolUse",
                          "tool_name": "Edit", "tool_input": {"file_path": "a"}, "tool_response": {"ok": 1},
                          "tool_use_id": "tu2"}
    assert kwargs == {"match_query": "Edit", "signal": None, "tool_context": context}


def test_post_tool_use_failure_input_with_and_without_interrupt():
    d = recorder("PostToolUseFailure")
    context = ctx(d)
    collect(execute_post_tool_use_failure_hooks(context, "Bash", "tu", {"command": "x"}, "boom", True, None))
    collect(execute_post_tool_use_failure_hooks(context, "Bash", "tu", {"command": "x"}, "boom", None, None))
    first, second = d.calls
    assert first[0] == {**BASE, "hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                        "tool_input": {"command": "x"}, "tool_use_id": "tu", "error": "boom", "is_interrupt": True}
    assert "is_interrupt" not in second[0]
    assert first[2] == {"match_query": "Bash", "signal": None, "tool_context": context}


def test_permission_request_input_runs_even_without_a_configured_hook():
    d = recorder()
    context = ctx(d)
    collect(execute_permission_request_hooks(context, "Bash", "tu", {"command": "x"}, "default", ["s"], "sig"))
    collect(execute_permission_request_hooks(context, "Bash", "tu", {"command": "x"}, "default"))
    first, second = d.calls
    assert first[0] == {**BASE, "permission_mode": "default", "hook_event_name": "PermissionRequest",
                        "tool_name": "Bash", "tool_input": {"command": "x"}, "permission_suggestions": ["s"]}
    assert "permission_suggestions" not in second[0]
    assert first[1] == "tu"
    assert first[2] == {"match_query": "Bash", "signal": "sig", "tool_context": context}


def test_last_assistant_text():
    assert last_assistant_text(None) is None
    assert last_assistant_text([HumanMessage(content="u")]) is None
    assert last_assistant_text([AIMessage(content="  a  "), HumanMessage(content="u")]) == "a"
    assert last_assistant_text([AIMessage(content="old"), AIMessage(content="   ")]) is None
    blocks = AIMessage(content=[{"type": "text", "text": "one"}, {"type": "tool_use", "id": "x"},
                                {"type": "text", "text": "two "}, "raw"])
    assert last_assistant_text([blocks]) == "one\ntwo \nraw".strip()


def test_stop_hooks_on_the_main_thread():
    d = recorder("Stop")
    context = ctx(d)
    messages = [HumanMessage(content="task"), AIMessage(content="all done")]
    collect(execute_stop_hooks(context, "default", "sig", True, messages))
    [(hook_input, tool_use_id, kwargs)] = d.calls
    assert hook_input == {**BASE, "permission_mode": "default", "hook_event_name": "Stop",
                          "stop_hook_active": True, "last_assistant_message": "all done"}
    assert tool_use_id
    assert kwargs == {"signal": "sig", "timeout_ms": TOOL_HOOK_EXECUTION_TIMEOUT_MS, "tool_context": context,
                      "messages": messages}


def test_stop_hooks_in_a_worker_fire_subagent_stop():
    d = recorder("SubagentStop")
    context = ctx(d, "agent-1", "tester")
    collect(execute_stop_hooks(context, None, None, False, [HumanMessage(content="x")], timeout_ms=5))
    [(hook_input, _, kwargs)] = d.calls
    assert hook_input == {**BASE, "hook_event_name": "SubagentStop", "stop_hook_active": False,
                          "agent_id": "agent-1", "agent_transcript_path": "", "agent_type": "tester"}
    assert kwargs["timeout_ms"] == 5
    d2 = recorder("SubagentStop")
    collect(execute_stop_hooks(ctx(d2, "agent-2"), None, None, False, []))
    assert d2.calls[0][0]["agent_type"] == ""
    # A worker does not fire `Stop`, and the main thread does not fire `SubagentStop`.
    only_stop = recorder("Stop")
    assert collect(execute_stop_hooks(ctx(only_stop, "agent-1"), None, None, False, [])) == []
    only_sub = recorder("SubagentStop")
    assert collect(execute_stop_hooks(ctx(only_sub), None, None, False, [])) == []
    assert only_stop.calls == [] and only_sub.calls == []


def test_user_prompt_submit_input():
    d = recorder("UserPromptSubmit")
    context = ctx(d)
    collect(execute_user_prompt_submit_hooks(context, "build a calculator", "acceptEdits"))
    [(hook_input, tool_use_id, kwargs)] = d.calls
    assert hook_input == {**BASE, "permission_mode": "acceptEdits", "hook_event_name": "UserPromptSubmit",
                          "prompt": "build a calculator"}
    assert tool_use_id
    assert kwargs == {"signal": context.cancel, "tool_context": context}


def test_session_start_input():
    d = recorder()
    collect(execute_session_start_hooks(d, "startup", "tester", "o4-mini", "sig", True))
    collect(execute_session_start_hooks(d, "compact"))
    first, second = d.calls
    assert first[0] == {**BASE, "hook_event_name": "SessionStart", "source": "startup",
                        "agent_type": "tester", "model": "o4-mini"}
    assert first[2] == {"match_query": "startup", "signal": "sig", "force_sync": True}
    assert second[0] == {**BASE, "hook_event_name": "SessionStart", "source": "compact"}
    assert second[2] == {"match_query": "compact", "signal": None, "force_sync": False}


def test_subagent_start_input():
    d = recorder()
    collect(execute_subagent_start_hooks(ctx(d), "a1", "developer", "sig"))
    [(hook_input, _, kwargs)] = d.calls
    assert hook_input == {**BASE, "hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "developer"}
    assert kwargs == {"match_query": "developer", "signal": "sig"}


def test_pre_compact_merges_successful_outputs_and_describes_all():
    results = [
        HookOutsideReplResult("a", True, "  keep tests  ", False),
        HookOutsideReplResult("b", True, "   ", False),
        HookOutsideReplResult("c", False, " broke ", False),
        HookOutsideReplResult("d", False, "", False),
        HookOutsideReplResult("e", True, "and ledger", False),
    ]
    d = recorder(outside_results=results)
    result = asyncio.run(execute_pre_compact_hooks(d, "auto", "custom", "sig", 7))
    assert result == PreCompactHookResult(
        new_custom_instructions="keep tests\n\nand ledger",
        user_display_message="\n".join([  # noqa: FLY002 - one line per display line
            "PreCompact [a] completed successfully: keep tests",
            "PreCompact [b] completed successfully",
            "PreCompact [c] failed: broke",
            "PreCompact [d] failed",
            "PreCompact [e] completed successfully: and ledger",
        ]),
    )
    [(hook_input, _, kwargs)] = d.calls
    assert hook_input == {**BASE, "hook_event_name": "PreCompact", "trigger": "auto", "custom_instructions": "custom"}
    assert kwargs == {"match_query": "auto", "signal": "sig", "timeout_ms": 7}


def test_pre_compact_without_hooks_or_outputs():
    assert asyncio.run(execute_pre_compact_hooks(recorder(), "auto", None)) == PreCompactHookResult()
    d = recorder(outside_results=[HookOutsideReplResult("a", False, "x", False)])
    result = asyncio.run(execute_pre_compact_hooks(d, "manual", None))
    assert result == PreCompactHookResult(None, "PreCompact [a] failed: x")
    assert d.calls[0][0]["custom_instructions"] is None
    assert d.calls[0][2]["timeout_ms"] == TOOL_HOOK_EXECUTION_TIMEOUT_MS


def test_post_compact_input_and_display():
    d = recorder(outside_results=[HookOutsideReplResult("a", True, "ok", False),
                                  HookOutsideReplResult("b", False, "", False)])
    message = asyncio.run(execute_post_compact_hooks(d, "auto", "the summary", "sig", 9))
    assert message == "PostCompact [a] completed successfully: ok\nPostCompact [b] failed"
    [(hook_input, _, kwargs)] = d.calls
    assert hook_input == {**BASE, "hook_event_name": "PostCompact", "trigger": "auto",
                          "compact_summary": "the summary"}
    assert kwargs == {"match_query": "auto", "signal": "sig", "timeout_ms": 9}
    assert asyncio.run(execute_post_compact_hooks(recorder(), "auto", "s")) is None
    d2 = recorder()
    asyncio.run(execute_post_compact_hooks(d2, "auto", "s"))
    assert d2.calls[0][2]["timeout_ms"] == TOOL_HOOK_EXECUTION_TIMEOUT_MS


def test_session_end_logs_failures_and_uses_the_short_timeout(caplog):
    results = [HookOutsideReplResult("a", False, "disk full", False),
               HookOutsideReplResult("b", False, "", False),
               HookOutsideReplResult("c", True, "fine", False)]
    d = recorder(outside_results=results)
    with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Hooks"):
        assert asyncio.run(execute_session_end_hooks(d, "other", "sig")) == results
    assert [r.getMessage() for r in caplog.records] == ["SessionEnd hook [a] failed: disk full"]
    [(hook_input, _, kwargs)] = d.calls
    assert hook_input == {**BASE, "hook_event_name": "SessionEnd", "reason": "other"}
    assert kwargs == {"match_query": "other", "signal": "sig", "timeout_ms": SESSION_END_HOOK_TIMEOUT_MS_DEFAULT}
    assert SESSION_END_HOOK_TIMEOUT_MS_DEFAULT == 1500


def test_stop_failure_input_and_unknown_default():
    d = recorder("StopFailure")
    context = ctx(d, "a1", "dev")
    asyncio.run(execute_stop_failure_hooks(context, "rate_limit", "429 too many", [AIMessage(content="partial")], 3))
    asyncio.run(execute_stop_failure_hooks(context, None, None, []))
    first, second = d.calls
    assert first[0] == {**BASE, "agent_id": "a1", "agent_type": "dev", "hook_event_name": "StopFailure",
                        "error": "rate_limit", "error_details": "429 too many", "last_assistant_message": "partial"}
    assert first[2] == {"match_query": "rate_limit", "timeout_ms": 3}
    assert second[0] == {**BASE, "agent_id": "a1", "agent_type": "dev", "hook_event_name": "StopFailure",
                         "error": "unknown"}
    assert second[2] == {"match_query": "unknown", "timeout_ms": TOOL_HOOK_EXECUTION_TIMEOUT_MS}


# ── end to end through a real command hook ───────────────────────────────────

def test_a_real_command_hook_receives_the_input_and_steers_the_call(tmp_path):
    seen = tmp_path / "seen.json"
    body = (f"cat > {seen}\n"
            """echo '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", """
            """"permissionDecisionReason": "not today"}}'\n""")
    hook = script(tmp_path, "h.sh", body)
    d = dispatcher(snapshot("PreToolUse", hook, matcher="Bash"), tmp_path=tmp_path)
    context = SimpleNamespace(hook_dispatcher=d, agent_id=None, agent_type=None, tools=())
    items = collect(execute_pre_tool_hooks(context, "Bash", "tu", {"command": "rm -rf x"}, "default"))
    assert json.loads(seen.read_text()) == {
        "session_id": "sess", "transcript_path": "/t.jsonl", "cwd": str(tmp_path), "permission_mode": "default",
        "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "rm -rf x"},
        "tool_use_id": "tu"}
    blocking = HookBlockingError(blocking_error="not today", command=hook.command)
    assert items[0] == AggregatedHookResult(blocking_error=blocking)
    assert items[1].attachment is not None and items[1].attachment.type == "hook_blocking_error"
    assert items[2] == AggregatedHookResult(permission_behavior="deny", hook_permission_decision_reason="not today",
                                            hook_source="settings")
