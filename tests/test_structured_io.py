"""The SDK protocol's pieces: `StructuredIO`, `Stream`, NDJSON, the stdout guard, the host's permission answer."""

from __future__ import annotations

import asyncio
import io
import json
import os
from collections.abc import AsyncIterator
from typing import Any, NoReturn

import pytest

from app.cli.ndjson_safe_stringify import ndjson_safe_stringify
from app.cli.structured_io import (
    MAX_RESOLVED_TOOL_USE_IDS,
    STREAM_CLOSED_BEFORE_RESPONSE,
    ControlRequestError,
    RequestAbortedError,
    StreamClosedError,
    StructuredIO,
    build_requires_action_details,
    serialize_decision_reason,
)
from app.tool import CancelToken
from app.types.permissions import PermissionBehavior, PermissionResult
from app.utils.control_message_compat import normalize_control_message_keys
from app.utils.permissions.permission_prompt_tool_result_schema import (
    PermissionPromptOutputError,
    parse_permission_prompt_output,
    permission_prompt_tool_result_to_permission_decision,
)
from app.utils.stream import Stream
from app.utils.stream_json_stdout_guard import (
    STDOUT_GUARD_MARKER,
    GuardedStdout,
    install_stream_json_stdout_guard,
    is_json_line,
    uninstall_stream_json_stdout_guard,
)


class Exited(Exception):
    pass


def exit_with(message: str) -> NoReturn:
    raise Exited(message)


async def blocks(*parts: str) -> AsyncIterator[str]:
    for part in parts:
        yield part


def make_io(*parts: str, replay: bool = False) -> tuple[StructuredIO, list[str]]:
    written: list[str] = []
    return StructuredIO(blocks(*parts), replay, write_stdout=written.append, exit_with=exit_with), written


async def read_all(sio: StructuredIO) -> list[dict[str, Any]]:
    return [m async for m in sio.structured_input]


def drain(stream: Stream[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(stream._queue)


# ── small pieces ─────────────────────────────────────────────────────────────

def test_ndjson_escapes_js_line_terminators_and_keeps_unicode() -> None:
    assert ndjson_safe_stringify({"a": "x\u2028y\u2029z", "b": "ção"}) == '{"a":"x\\u2028y\\u2029z","b":"ção"}'


def test_control_message_keys_are_normalized() -> None:
    message = {"requestId": "1", "response": {"requestId": "2"}}
    assert normalize_control_message_keys(message) == {"request_id": "1", "response": {"request_id": "2"}}
    kept = {"requestId": "x", "request_id": "y", "response": {"requestId": "a", "request_id": "b"}}
    assert normalize_control_message_keys(kept) == kept
    assert normalize_control_message_keys("text") == "text"


def test_stream_delivers_in_order_then_ends() -> None:
    async def run() -> list[int]:
        stream: Stream[int] = Stream()
        stream.enqueue(1)

        async def later() -> None:
            await asyncio.sleep(0)
            stream.enqueue(2)
            stream.done()

        task = asyncio.ensure_future(later())
        got = [v async for v in stream]
        await task
        return got

    assert asyncio.run(run()) == [1, 2]


def test_stream_is_iterated_once_and_fails_on_error() -> None:
    returned: list[bool] = []
    stream: Stream[int] = Stream(lambda: returned.append(True))
    stream.__aiter__()
    with pytest.raises(RuntimeError, match="Stream can only be iterated once"):
        stream.__aiter__()

    async def run() -> None:
        failing: Stream[int] = Stream()
        failing.error(ValueError("boom"))
        async for _ in failing:
            pass

    with pytest.raises(ValueError, match="boom"):
        asyncio.run(run())


def test_cancel_token_listeners_fire_once_and_can_be_removed() -> None:
    token = CancelToken()
    calls: list[str] = []
    token.add_listener(lambda: calls.append("a"))
    remove = token.add_listener(lambda: calls.append("b"))
    remove()
    token.cancel("why")
    token.cancel("again")
    assert calls == ["a"] and token.reason == "why"
    token.add_listener(lambda: calls.append("late"))
    assert calls == ["a"]


def test_stdout_guard_diverts_non_json_lines(capsys: pytest.CaptureFixture[str]) -> None:
    out, err = io.StringIO(), io.StringIO()
    guard = GuardedStdout(out, err)
    guard.write('{"a":1}\nhello\n')
    guard.write("[1")
    guard.write("]\n\n")
    guard.write("tail")
    guard.drain()
    assert out.getvalue() == '{"a":1}\n[1]\n\n'
    assert err.getvalue() == f"{STDOUT_GUARD_MARKER} hello\n{STDOUT_GUARD_MARKER} tail\n"
    assert is_json_line("") and not is_json_line("{")


def test_stdout_guard_install_and_uninstall() -> None:
    import sys

    original = sys.stdout
    guard = install_stream_json_stdout_guard()
    try:
        assert sys.stdout is guard and install_stream_json_stdout_guard() is guard
    finally:
        uninstall_stream_json_stdout_guard()
    assert sys.stdout is original


# ── the permission answer ────────────────────────────────────────────────────

def test_parse_permission_prompt_output() -> None:
    assert parse_permission_prompt_output({"behavior": "allow", "updatedInput": {"a": 1}, "toolUseID": "t",
                                           "decisionClassification": "user_temporary"}) == {
        "behavior": "allow", "updatedInput": {"a": 1}, "toolUseID": "t", "decisionClassification": "user_temporary"}
    assert parse_permission_prompt_output({"behavior": "deny", "message": "no", "interrupt": True,
                                           "decisionClassification": "bogus"}) == {
        "behavior": "deny", "message": "no", "interrupt": True}
    # A malformed permission update is dropped, never a rejection of the answer.
    assert parse_permission_prompt_output({"behavior": "allow", "updatedInput": {},
                                           "updatedPermissions": [{"type": "nope"}]})["updatedPermissions"] is None
    for bad in ("x", {"behavior": "maybe"}, {"behavior": "allow"}, {"behavior": "deny"},
                {"behavior": "deny", "message": "m", "interrupt": "yes"},
                {"behavior": "deny", "message": "m", "toolUseID": 3}):
        with pytest.raises(PermissionPromptOutputError):
            parse_permission_prompt_output(bad)


class FakeTool:
    name = "Bash"

    def user_facing_name(self, input: Any) -> str:
        return "Bash"

    def get_activity_description(self, input: Any, context: Any = None) -> str | None:
        return None

    def get_tool_use_summary(self, input: Any, context: Any = None) -> str | None:
        return f"ls {input.get('path')}"


class FakeContext:
    def __init__(self) -> None:
        self.cancel = CancelToken()
        self.agent_id = None


def test_host_answer_becomes_a_decision() -> None:
    context = FakeContext()
    allow = permission_prompt_tool_result_to_permission_decision(
        {"behavior": "allow", "updatedInput": {}}, FakeTool(), {"cmd": "ls"}, context)
    assert allow.behavior == PermissionBehavior.ALLOW and allow.updated_input == {"cmd": "ls"}
    assert allow.decision_reason == {"type": "permissionPromptTool", "permissionPromptToolName": "Bash",
                                     "toolResult": {"behavior": "allow", "updatedInput": {}}}
    changed = permission_prompt_tool_result_to_permission_decision(
        {"behavior": "allow", "updatedInput": {"cmd": "pwd"}}, FakeTool(), {"cmd": "ls"}, context)
    assert changed.updated_input == {"cmd": "pwd"}
    deny = permission_prompt_tool_result_to_permission_decision(
        {"behavior": "deny", "message": "nope"}, FakeTool(), {}, context)
    assert (deny.behavior, deny.message, context.cancel.cancelled) == (PermissionBehavior.DENY, "nope", False)
    permission_prompt_tool_result_to_permission_decision(
        {"behavior": "deny", "message": "stop", "interrupt": True}, FakeTool(), {}, context)
    assert context.cancel.cancelled


def test_serialize_decision_reason_and_requires_action_details() -> None:
    assert serialize_decision_reason(None) is None
    assert serialize_decision_reason({"type": "rule", "reason": "r"}) is None
    for kind in ("hook", "classifier", "workingDir", "safetyCheck", "other", "asyncAgent", "sandboxOverride"):
        assert serialize_decision_reason({"type": kind, "reason": "why"}) == "why"
    assert serialize_decision_reason({"type": "hook"}) is None
    assert build_requires_action_details(FakeTool(), {"path": "/x"}, "t1", "r1") == {
        "tool_name": "Bash", "action_description": "ls /x", "tool_use_id": "t1", "request_id": "r1",
        "input": {"path": "/x"}}

    class Broken(FakeTool):
        def get_activity_description(self, input: Any, context: Any = None) -> str | None:
            raise ValueError("bad")

    assert build_requires_action_details(Broken(), {}, "t", "r")["action_description"] == "Bash"


# ── reading ──────────────────────────────────────────────────────────────────

def test_lines_are_split_across_blocks_and_skipped_types_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDK_TEST_VAR", raising=False)
    user = {"type": "user", "message": {"role": "user", "content": "hi"}}
    lines = "\n".join([
        json.dumps({"type": "keep_alive"}),
        json.dumps({"type": "update_environment_variables", "variables": {"SDK_TEST_VAR": "1"}}),
        json.dumps({"type": "mystery"}),
        "",
        json.dumps({"type": "control_request", "request_id": "r", "request": {"subtype": "interrupt"}}),
        json.dumps({"type": "assistant", "message": {}}),
        json.dumps({"type": "system", "subtype": "x"}),
    ]) + "\n" + json.dumps(user)
    half = len(lines) // 2
    sio, _ = make_io(lines[:half], lines[half:])
    got = asyncio.run(read_all(sio))
    assert [m["type"] for m in got] == ["control_request", "assistant", "system", "user"]
    assert os.environ["SDK_TEST_VAR"] == "1" and sio.input_closed
    monkeypatch.delenv("SDK_TEST_VAR")


def test_prepended_user_message_is_read_first() -> None:
    sio, _ = make_io(json.dumps({"type": "user", "message": {"role": "user", "content": "second"}}) + "\n")
    sio.prepend_user_message("first")
    got = asyncio.run(read_all(sio))
    assert [m["message"]["content"] for m in got] == ["first", "second"]


@pytest.mark.parametrize(("line", "error"), [
    ("not json", "Error parsing streaming input line: not json: "),
    ("[1]", "Error parsing streaming input line: [1]: not an object"),
    (json.dumps({"type": "control_request"}), "Error: Missing request on control_request"),
    (json.dumps({"type": "user", "message": {"role": "assistant"}}),
     "Error: Expected message role 'user', got 'assistant'"),
])
def test_fatal_lines_exit_with_upstreams_message(line: str, error: str) -> None:
    sio, _ = make_io(line + "\n")
    with pytest.raises(Exited) as exc:
        asyncio.run(read_all(sio))
    assert str(exc.value).startswith(error)


def control_response(request_id: str, payload: Any = None, *, error: str | None = None) -> str:
    if error is not None:
        return json.dumps({"type": "control_response",
                           "response": {"subtype": "error", "request_id": request_id, "error": error}})
    return json.dumps({"type": "control_response",
                       "response": {"subtype": "success", "requestId": request_id, "response": payload}})


class Feed:
    """An input the test writes lines into."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()

    def send(self, line: str) -> None:
        self.queue.put_nowait(line + "\n")

    def close(self) -> None:
        self.queue.put_nowait(None)

    async def __aiter__(self) -> AsyncIterator[str]:
        while (item := await self.queue.get()) is not None:
            yield item


def feed_io(replay: bool = False) -> tuple[StructuredIO, Feed]:
    feed = Feed()
    return StructuredIO(feed.__aiter__(), replay, write_stdout=lambda _: None, exit_with=exit_with), feed


def test_send_request_is_settled_by_its_response() -> None:
    async def run() -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
        sio, feed = feed_io(replay=True)
        reader = asyncio.ensure_future(read_all(sio))
        pending = asyncio.ensure_future(sio.send_request({"subtype": "x"}, lambda raw: raw["v"], request_id="r1"))
        await asyncio.sleep(0)
        sent = drain(sio.outbound)
        feed.send(control_response("r1", {"v": 7}))
        value = await pending
        feed.close()
        return value, sent, await reader

    value, sent, replayed = asyncio.run(run())
    assert value == 7
    assert sent == [{"type": "control_request", "request_id": "r1", "request": {"subtype": "x"}}]
    assert replayed[0]["response"]["request_id"] == "r1"


def test_error_responses_and_bad_payloads_fail_the_request() -> None:
    async def run() -> list[str]:
        sio, feed = feed_io()
        reader = asyncio.ensure_future(read_all(sio))
        failures = []
        for request_id, line in (("e", control_response("e", error="host failed")),
                                 ("p", control_response("p", {"wrong": 1}))):
            pending = asyncio.ensure_future(sio.send_request({"subtype": "x"}, lambda raw: raw["v"],
                                                             request_id=request_id))
            await asyncio.sleep(0)
            feed.send(line)
            try:
                await pending
            except (ControlRequestError, KeyError) as error:
                failures.append(type(error).__name__)
        no_schema = asyncio.ensure_future(sio.send_request({"subtype": "x"}, None, request_id="n"))
        await asyncio.sleep(0)
        feed.send(control_response("n", {"anything": 1}))
        failures.append(repr(await no_schema))
        feed.close()
        assert await reader == []
        return failures

    assert asyncio.run(run()) == ["ControlRequestError", "KeyError", "{}"]


def test_closing_the_input_fails_pending_and_later_requests() -> None:
    async def run() -> tuple[str, str]:
        sio, feed = feed_io()
        reader = asyncio.ensure_future(read_all(sio))
        pending = asyncio.ensure_future(sio.send_request({"subtype": "x"}, None))
        await asyncio.sleep(0)
        feed.close()
        await reader
        with pytest.raises(StreamClosedError) as first:
            await pending
        with pytest.raises(StreamClosedError) as later:
            await sio.send_request({"subtype": "x"}, None)
        return str(first.value), str(later.value)

    assert asyncio.run(run()) == (STREAM_CLOSED_BEFORE_RESPONSE, "Stream closed")


def test_an_aborted_request_is_cancelled_with_the_host() -> None:
    async def run() -> list[dict[str, Any]]:
        sio, _ = feed_io()
        token = CancelToken()
        pending = asyncio.ensure_future(sio.send_request({"subtype": "x"}, None, token, "r9"))
        await asyncio.sleep(0)
        token.cancel()
        with pytest.raises(RequestAbortedError):
            await pending
        cancelled = CancelToken()
        cancelled.cancel()
        with pytest.raises(RequestAbortedError, match="Request aborted"):
            await sio.send_request({"subtype": "x"}, None, cancelled)
        return drain(sio.outbound)

    assert asyncio.run(run())[1] == {"type": "control_cancel_request", "request_id": "r9"}


def test_orphan_and_duplicate_responses() -> None:
    async def run() -> list[str]:
        sio, feed = feed_io()
        orphans: list[str] = []

        async def orphan(message: dict[str, Any]) -> None:
            orphans.append(message["response"]["request_id"])

        sio.set_unexpected_response_callback(orphan)
        reader = asyncio.ensure_future(read_all(sio))
        request = {"subtype": "can_use_tool", "tool_use_id": "tu1"}
        pending = asyncio.ensure_future(sio.send_request(request, None, request_id="r1"))
        await asyncio.sleep(0)
        assert sio.get_pending_permission_requests()[0]["request_id"] == "r1"
        feed.send(control_response("r1", {}))
        await pending
        feed.send(control_response("late", {"toolUseID": "tu1"}))
        feed.send(control_response("unknown", {"toolUseID": "other"}))
        feed.close()
        await reader
        return orphans

    assert asyncio.run(run()) == ["unknown"]


def test_resolved_tool_use_ids_are_bounded() -> None:
    sio, _ = make_io()
    for i in range(MAX_RESOLVED_TOOL_USE_IDS + 1):
        sio._track_resolved_tool_use_id({"request": {"subtype": "can_use_tool", "tool_use_id": f"t{i}"}})
    sio._track_resolved_tool_use_id({"request": {"subtype": "other", "tool_use_id": "ignored"}})
    assert len(sio._resolved_tool_use_ids) == MAX_RESOLVED_TOOL_USE_IDS
    assert "t0" not in sio._resolved_tool_use_ids and "ignored" not in sio._resolved_tool_use_ids


def test_inject_control_response_settles_and_cancels_the_host_prompt() -> None:
    async def run() -> tuple[Any, list[dict[str, Any]]]:
        sio, _ = feed_io()
        pending = asyncio.ensure_future(sio.send_request({"subtype": "x"}, lambda raw: raw, request_id="r"))
        await asyncio.sleep(0)
        sio.inject_control_response({"response": {}})
        sio.inject_control_response({"response": {"request_id": "nobody"}})
        sio.inject_control_response({"response": {"subtype": "success", "request_id": "r", "response": {"a": 1}}})
        return await pending, drain(sio.outbound)

    value, out = asyncio.run(run())
    assert value == {"a": 1} and out[-1] == {"type": "control_cancel_request", "request_id": "r"}


def test_write_is_one_ndjson_line() -> None:
    sio, written = make_io()
    asyncio.run(sio.write({"type": "x", "t": "\u2028"}))
    assert written == ['{"type":"x","t":"\\u2028"}\n']


# ── what the run asks the host ───────────────────────────────────────────────

def ask_result(**kwargs: Any) -> PermissionResult:
    return PermissionResult(behavior=PermissionBehavior.ASK, message="ask", **kwargs)


def run_can_use_tool(answer: dict[str, Any] | None, monkeypatch: pytest.MonkeyPatch, *, hook: Any = None,
                     recheck: Any = None, result: PermissionResult | None = None,
                     input_args: dict[str, Any] | None = None) -> tuple[PermissionResult, list[dict[str, Any]], Any]:
    async def no_hook(*args: Any) -> Any:
        if hook == "wait":
            await asyncio.sleep(10)
        return hook

    async def fake_recheck(tool: Any, input_args: Any, context: Any) -> Any:
        return recheck

    monkeypatch.setattr("app.cli.structured_io.execute_permission_request_hooks_for_sdk", no_hook)
    monkeypatch.setattr("app.utils.permissions.permissions.check_rule_based_permissions", fake_recheck)
    prompts: list[Any] = []
    context = FakeContext()

    async def run() -> tuple[PermissionResult, list[dict[str, Any]]]:
        sio, feed = feed_io()
        states: list[Any] = []
        sio.on_permission_state = states.append
        can_use_tool = sio.create_can_use_tool(prompts.append)
        reader = asyncio.ensure_future(read_all(sio))
        decision = asyncio.ensure_future(can_use_tool(FakeTool(), input_args or {"path": "/x"},
                                                      result or ask_result(), context, "tu1"))
        await asyncio.sleep(0.01)
        sent = drain(sio.outbound)
        if answer is not None and sent:
            feed.send(control_response(sent[0]["request_id"], answer))
        elif answer is None:
            feed.close()
        value = await decision
        feed.close()
        await reader
        assert states[-1] is None
        return value, sent

    decision, sent = asyncio.run(run())
    return decision, sent, (prompts, context)


def test_allow_and_deny_pass_through_without_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    for behavior in (PermissionBehavior.ALLOW, PermissionBehavior.DENY):
        async def run(b: str = behavior) -> PermissionResult:
            sio, _ = feed_io()
            return await sio.create_can_use_tool()(FakeTool(), {}, PermissionResult(behavior=b), FakeContext(), "t")

        assert asyncio.run(run()).behavior == behavior


def test_the_host_answers_the_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    decision, sent, (prompts, _) = run_can_use_tool(
        {"behavior": "allow", "updatedInput": {}}, monkeypatch,
        result=ask_result(decision_reason={"type": "hook", "reason": "because"}))
    assert decision.behavior == PermissionBehavior.ALLOW and decision.updated_input == {"path": "/x"}
    request = sent[0]["request"]
    assert request == {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"path": "/x"},
                       "decision_reason": "because", "tool_use_id": "tu1"}
    assert prompts[0]["tool_use_id"] == "tu1" and prompts[0]["request_id"] == sent[0]["request_id"]


def test_a_changed_input_is_rechecked_against_the_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    denied = PermissionResult(behavior=PermissionBehavior.DENY, message="phase rule")
    decision, _, _ = run_can_use_tool({"behavior": "allow", "updatedInput": {"path": "/tests/x"}}, monkeypatch,
                                      recheck=denied)
    assert decision is denied
    allowed, _, _ = run_can_use_tool({"behavior": "allow", "updatedInput": {"path": "/y"}}, monkeypatch,
                                     recheck=PermissionResult(behavior=PermissionBehavior.ALLOW))
    assert allowed.behavior == PermissionBehavior.ALLOW and allowed.updated_input == {"path": "/y"}


def test_a_hook_decision_wins_and_cancels_the_host_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    hooked = PermissionResult(behavior=PermissionBehavior.DENY, message="hook says no")
    decision, sent, _ = run_can_use_tool({"behavior": "allow", "updatedInput": {}}, monkeypatch, hook=hooked)
    assert decision is hooked
    assert sent[-1]["type"] == "control_cancel_request"


def test_a_failed_request_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    decision, _, _ = run_can_use_tool(None, monkeypatch, hook="wait")
    assert decision.behavior == PermissionBehavior.DENY
    assert decision.message == f"Tool permission request failed: {STREAM_CLOSED_BEFORE_RESPONSE}"


def test_suggestions_and_blocked_path_reach_the_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.types.permissions import PermissionRuleSource
    from app.utils.permissions.permission_update import PermissionUpdate

    suggestion = PermissionUpdate("addRules", PermissionRuleSource.SESSION, rules=(("Bash", "ls:*"),),
                                  behavior=PermissionBehavior.ALLOW)
    _, sent, _ = run_can_use_tool({"behavior": "deny", "message": "no"}, monkeypatch, result=ask_result(
        suggestions=(suggestion,), decision_reason={"type": "workingDir", "reason": "out", "blockedPath": "/etc"}))
    request = sent[0]["request"]
    assert request["permission_suggestions"] == [{"type": "addRules", "destination": "session",
                                                  "rules": [{"toolName": "Bash", "ruleContent": "ls:*"}],
                                                  "behavior": "allow"}]
    assert request["blocked_path"] == "/etc" and request["decision_reason"] == "out"


def test_hook_callback_elicitation_sandbox_and_mcp_requests() -> None:
    async def run() -> list[Any]:
        sio, feed = feed_io()
        reader = asyncio.ensure_future(read_all(sio))
        results: list[Any] = []

        async def answer(call: Any, payload: Any) -> None:
            task = asyncio.ensure_future(call)
            await asyncio.sleep(0)
            sent = drain(sio.outbound)
            results.append(sent[-1]["request"])
            sio.outbound._queue.clear()
            feed.send(control_response(sent[-1]["request_id"], payload))
            results.append(await task)

        hook = sio.create_hook_callback("cb1", 5)
        assert (hook.type, hook.timeout) == ("callback", 5)
        await answer(hook.callback({"event": "x"}, "tu"), {"decision": "approve"})
        accepted = {"action": "accept", "content": {"a": 1}}
        await answer(sio.handle_elicitation("srv", "pick", {"type": "object"}), accepted)
        await answer(sio.handle_elicitation("srv", "pick"), {"action": "maybe"})
        allowed = {"behavior": "allow", "updatedInput": {}}
        await answer(sio.create_sandbox_ask_callback()({"host": "example.com"}), allowed)
        await answer(sio.send_mcp_message("srv", {"jsonrpc": "2.0"}), {"mcp_response": {"id": 1}})
        feed.close()
        await reader
        results.append(await sio.create_hook_callback("cb2").callback({}, None))
        results.append(await sio.create_sandbox_ask_callback()({"host": "h"}))
        return results

    results = asyncio.run(run())
    assert results[0] == {"subtype": "hook_callback", "callback_id": "cb1", "input": {"event": "x"},
                          "tool_use_id": "tu"}
    assert results[1] == {"decision": "approve"}
    assert results[2] == {"subtype": "elicitation", "mcp_server_name": "srv", "message": "pick",
                          "requested_schema": {"type": "object"}}
    assert results[3] == {"action": "accept", "content": {"a": 1}}
    assert results[5] == {"action": "cancel"}
    assert results[6]["tool_name"] == "SandboxNetworkAccess"
    assert results[6]["description"] == "Allow network connection to example.com?"
    assert results[7] is True
    assert results[8] == {"subtype": "mcp_message", "server_name": "srv", "message": {"jsonrpc": "2.0"}}
    assert results[9] == {"id": 1}
    assert results[10:] == [{}, False]
