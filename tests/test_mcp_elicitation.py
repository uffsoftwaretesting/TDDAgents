"""
MCP elicitation: the Elicitation / ElicitationResult / Notification hook executors
(`app/utils/hooks/hooks.py`), `app/services/mcp/elicitation_handler.py`, and the connection's
`elicitation_callback` / `on_elicitation_complete` against a real in-process MCP server.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import (
    ElicitCompleteNotification,
    ElicitCompleteNotificationParams,
    ElicitRequestFormParams,
    ElicitRequestURLParams,
    ElicitResult,
)
from pydantic import BaseModel

import app.services.mcp.client as mc
import app.services.mcp.elicitation_handler as eh
import app.services.mcp.mcp_connection_manager as mm
from app.schemas.hooks import CommandHook, HookMatcher
from app.services.mcp.types import ConnectedMCPServer, McpServerConfig
from app.utils.hooks.hooks import (
    ElicitationHookResult,
    ElicitationResponse,
    ElicitationResultHookResult,
    HookDispatcher,
    HookOutsideReplResult,
    execute_elicitation_hooks,
    execute_elicitation_result_hooks,
    execute_notification_hooks,
    parse_elicitation_hook_output,
)
from app.utils.hooks.hook_events import TOOL_HOOK_EXECUTION_TIMEOUT_MS
from app.utils.hooks.hooks_config_snapshot import HooksConfigSnapshot
from app.utils.messages import HookBlockingError
from tests.test_mcp_client import factory

CONFIG = McpServerConfig(type="stdio", scope="project", command="unused")
BASE = {"session_id": "sess", "transcript_path": "/t", "cwd": "/ws"}


class Recorder(HookDispatcher):
    """A dispatcher recording `execute_hooks_outside_repl` calls and answering canned results."""

    def __init__(self, results: list[HookOutsideReplResult] | None = None, error: Exception | None = None) -> None:
        super().__init__(snapshot=HooksConfigSnapshot(), session_id="sess", cwd="/ws", project_dir="/p",
                         transcript_path="/t")
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.results = results or []
        self.error = error

    async def execute_hooks_outside_repl(self, hook_input: Mapping[str, Any],
                                         **kwargs: Any) -> list[HookOutsideReplResult]:
        self.calls.append((dict(hook_input), kwargs))
        if self.error is not None and hook_input["hook_event_name"] != "Notification":
            raise self.error
        return self.results if hook_input["hook_event_name"] != "Notification" else []


def ok(output: Any, command: str = "c") -> HookOutsideReplResult:
    text = output if isinstance(output, str) else json.dumps(output)
    return HookOutsideReplResult(command=command, succeeded=True, output=text, blocked=False)


def specific(event: str, **fields: Any) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event, **fields}}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ── parseElicitationHookOutput ──


def test_exit_code_two_blocks_with_the_output_or_a_default():
    blocked = HookOutsideReplResult(command="h", succeeded=False, output="no way", blocked=True)
    assert parse_elicitation_hook_output(blocked, "Elicitation") == (
        None, HookBlockingError(blocking_error="no way", command="h"))
    silent = HookOutsideReplResult(command="h", succeeded=False, output="", blocked=True)
    assert parse_elicitation_hook_output(silent, "Elicitation")[1] == HookBlockingError(
        blocking_error="Elicitation blocked by hook", command="h")


@pytest.mark.parametrize("output", [
    "", "   ", "plain text", "{not json", json.dumps({"decision": "maybe"}), json.dumps({"async": True}),
    json.dumps({"systemMessage": "hi"}), json.dumps(specific("ElicitationResult", action="accept")),
    json.dumps(specific("Elicitation")), json.dumps({"hookSpecificOutput": None}),
])
def test_outputs_that_say_nothing(output):
    assert parse_elicitation_hook_output(ok(output), "Elicitation") == (None, None)


def test_a_json_block_decision_blocks_with_its_reason():
    assert parse_elicitation_hook_output(ok({"decision": "block", "reason": "nope"}, "h"), "Elicitation") == (
        None, HookBlockingError(blocking_error="nope", command="h"))
    assert parse_elicitation_hook_output(ok({"decision": "block"}, "h"), "Elicitation")[1] == HookBlockingError(
        blocking_error="Elicitation blocked by hook", command="h")
    # A succeeded result flagged blocked (a callback's `decision: block`) also blocks a JSON answer.
    flagged = HookOutsideReplResult(command="h", succeeded=True, output=json.dumps(
        specific("Elicitation", action="accept")), blocked=True)
    assert parse_elicitation_hook_output(flagged, "Elicitation")[1] == HookBlockingError(
        blocking_error="Elicitation blocked by hook", command="h")


def test_an_action_answers_and_a_decline_also_blocks():
    accept = ok(specific("Elicitation", action="accept", content={"v": "x"}))
    assert parse_elicitation_hook_output(accept, "Elicitation") == (
        ElicitationResponse(action="accept", content={"v": "x"}), None)
    assert parse_elicitation_hook_output(ok(specific("Elicitation", action="cancel")), "Elicitation") == (
        ElicitationResponse(action="cancel"), None)
    declined = parse_elicitation_hook_output(ok(specific("Elicitation", action="decline"), "h"), "Elicitation")
    assert declined == (ElicitationResponse(action="decline"),
                        HookBlockingError(blocking_error="Elicitation denied by hook", command="h"))
    result_declined = parse_elicitation_hook_output(
        ok(specific("ElicitationResult", action="decline"), "h"), "ElicitationResult")
    assert result_declined[1] == HookBlockingError(blocking_error="Elicitation result blocked by hook", command="h")
    reasoned = parse_elicitation_hook_output(
        ok({"reason": "why", **specific("Elicitation", action="decline")}, "h"), "Elicitation")
    assert reasoned[1] == HookBlockingError(blocking_error="why", command="h")


# ── the executors ──


def test_execute_elicitation_hooks_input_and_fold():
    d = Recorder([ok(specific("Elicitation", action="accept", content={"a": 1})),
                  ok(specific("Elicitation", action="cancel")),
                  HookOutsideReplResult(command="b", succeeded=False, output="stop", blocked=True)])
    result = run(execute_elicitation_hooks(
        d, "srv", "pick", requested_schema={"type": "object"}, permission_mode="default", signal="sig",
        timeout_ms=7, mode="form"))
    # The last response and the last blocking error win, as upstream's loop.
    assert result == ElicitationHookResult(elicitation_response=ElicitationResponse(action="cancel"),
                                           blocking_error=HookBlockingError(blocking_error="stop", command="b"))
    [(hook_input, kwargs)] = d.calls
    assert hook_input == {**BASE, "permission_mode": "default", "hook_event_name": "Elicitation",
                          "mcp_server_name": "srv", "message": "pick", "mode": "form",
                          "requested_schema": {"type": "object"}}
    assert kwargs == {"match_query": "srv", "signal": "sig", "timeout_ms": 7}


def test_execute_elicitation_hooks_url_mode_and_defaults():
    d = Recorder()
    assert run(execute_elicitation_hooks(d, "srv", "go", mode="url", url="https://x", elicitation_id="e1")) == (
        ElicitationHookResult())
    [(hook_input, kwargs)] = d.calls
    assert hook_input == {**BASE, "hook_event_name": "Elicitation", "mcp_server_name": "srv", "message": "go",
                          "mode": "url", "url": "https://x", "elicitation_id": "e1"}
    assert kwargs == {"match_query": "srv", "signal": None, "timeout_ms": TOOL_HOOK_EXECUTION_TIMEOUT_MS}
    assert run(execute_elicitation_hooks(None, "srv", "go")) == ElicitationHookResult()


def test_execute_elicitation_result_hooks_input_and_fold():
    d = Recorder([ok(specific("ElicitationResult", action="accept", content={"b": 2}))])
    result = run(execute_elicitation_result_hooks(
        d, "srv", "accept", content={"a": 1}, permission_mode="plan", signal="sig", timeout_ms=5, mode="url",
        elicitation_id="e2"))
    assert result == ElicitationResultHookResult(
        elicitation_result_response=ElicitationResponse(action="accept", content={"b": 2}))
    [(hook_input, kwargs)] = d.calls
    assert hook_input == {**BASE, "permission_mode": "plan", "hook_event_name": "ElicitationResult",
                          "mcp_server_name": "srv", "elicitation_id": "e2", "mode": "url", "action": "accept",
                          "content": {"a": 1}}
    assert kwargs == {"match_query": "srv", "signal": "sig", "timeout_ms": 5}
    d2 = Recorder([HookOutsideReplResult(command="b", succeeded=False, output="", blocked=True)])
    blocked = run(execute_elicitation_result_hooks(d2, "srv", "cancel"))
    assert blocked.blocking_error == HookBlockingError(blocking_error="Elicitation blocked by hook", command="b")
    assert d2.calls[0][0] == {**BASE, "hook_event_name": "ElicitationResult", "mcp_server_name": "srv",
                              "action": "cancel"}
    assert d2.calls[0][1]["timeout_ms"] == TOOL_HOOK_EXECUTION_TIMEOUT_MS
    assert run(execute_elicitation_result_hooks(None, "srv", "accept")) == ElicitationResultHookResult()


def test_execute_notification_hooks_input():
    d = Recorder()
    run(execute_notification_hooks(d, "hello", "elicitation_complete", title="T", timeout_ms=3))
    run(execute_notification_hooks(d, "bye", "elicitation_response"))
    run(execute_notification_hooks(None, "nobody", "x"))
    first, second = d.calls
    assert first == ({**BASE, "hook_event_name": "Notification", "message": "hello",
                      "notification_type": "elicitation_complete", "title": "T"},
                     {"match_query": "elicitation_complete", "timeout_ms": 3})
    assert second == ({**BASE, "hook_event_name": "Notification", "message": "bye",
                       "notification_type": "elicitation_response"},
                      {"match_query": "elicitation_response", "timeout_ms": TOOL_HOOK_EXECUTION_TIMEOUT_MS})


# ── runElicitationHooks / runElicitationResultHooks ──

FORM = ElicitRequestFormParams(message="name?", requested_schema={"type": "object"})
URL = ElicitRequestURLParams(message="open", url="https://auth", elicitation_id="e9")


def notifications(d: Recorder) -> list[str]:
    return [c[0]["message"] for c in d.calls if c[0]["hook_event_name"] == "Notification"]


def test_get_elicitation_mode():
    assert eh.get_elicitation_mode(FORM) == "form" and eh.get_elicitation_mode(URL) == "url"


def test_run_elicitation_hooks_outcomes():
    assert run(eh.run_elicitation_hooks(None, "srv", FORM)) is None
    answered = Recorder([ok(specific("Elicitation", action="accept", content={"name": "x"}))])
    assert run(eh.run_elicitation_hooks(answered, "srv", FORM)) == ElicitResult(action="accept",
                                                                                content={"name": "x"})
    assert answered.calls[0][0]["requested_schema"] == {"type": "object"}
    assert answered.calls[0][0]["mode"] == "form" and "url" not in answered.calls[0][0]
    declined = Recorder([ok(specific("Elicitation", action="decline", content={"name": "x"}))])
    assert run(eh.run_elicitation_hooks(declined, "srv", URL, "sig")) == ElicitResult(action="decline")
    assert declined.calls[0][0]["url"] == "https://auth" and declined.calls[0][0]["elicitation_id"] == "e9"
    assert declined.calls[0][1]["signal"] == "sig"
    assert run(eh.run_elicitation_hooks(Recorder([ok("nothing")]), "srv", FORM)) is None


def test_a_failing_elicitation_hook_answers_nothing(caplog):
    with caplog.at_level(logging.ERROR, logger=eh.__name__):
        assert run(eh.run_elicitation_hooks(Recorder(error=RuntimeError("boom")), "srv", FORM)) is None
    assert caplog.messages == ["MCP server srv: Elicitation hook error: boom"]


def drained(d: Recorder, coro: Any) -> Any:
    async def go() -> Any:
        value = await coro
        await d.drain_background()
        return value

    return run(go())


def test_run_elicitation_result_hooks_keeps_or_replaces_the_answer():
    answer = ElicitResult(action="accept", content={"name": "user"})
    unchanged = Recorder()
    assert drained(unchanged, eh.run_elicitation_result_hooks(unchanged, "srv", answer, "sig", "url", "e1")) is answer
    assert unchanged.calls[0][0]["content"] == {"name": "user"} and unchanged.calls[0][0]["elicitation_id"] == "e1"
    assert unchanged.calls[0][1]["signal"] == "sig"
    assert notifications(unchanged) == ['Elicitation response for server "srv": accept']
    replaced = Recorder([ok(specific("ElicitationResult", action="accept", content={"name": "hook"}))])
    assert drained(replaced, eh.run_elicitation_result_hooks(replaced, "srv", answer)) == ElicitResult(
        action="accept", content={"name": "hook"})
    # A replacement without content keeps the user's.
    kept = Recorder([ok(specific("ElicitationResult", action="cancel"))])
    assert drained(kept, eh.run_elicitation_result_hooks(kept, "srv", answer)) == ElicitResult(
        action="cancel", content={"name": "user"})
    assert notifications(kept) == ['Elicitation response for server "srv": cancel']


def test_a_blocking_result_hook_declines():
    d = Recorder([HookOutsideReplResult(command="b", succeeded=False, output="no", blocked=True)])
    answer = ElicitResult(action="accept", content={"name": "user"})
    assert drained(d, eh.run_elicitation_result_hooks(d, "srv", answer)) == ElicitResult(action="decline")
    assert notifications(d) == ['Elicitation response for server "srv": decline']


def test_a_failing_result_hook_keeps_the_answer(caplog):
    d = Recorder(error=RuntimeError("bad"))
    answer = ElicitResult(action="decline")
    with caplog.at_level(logging.ERROR, logger=eh.__name__):
        assert drained(d, eh.run_elicitation_result_hooks(d, "srv", answer)) is answer
    assert caplog.messages == ["MCP server srv: ElicitationResult hook error: bad"]
    assert notifications(d) == ['Elicitation response for server "srv": decline']
    assert run(eh.run_elicitation_result_hooks(None, "srv", answer)) is answer


# ── build_elicitation_callback ──


class Host:
    """`ask_host`: records the question and answers a canned dict."""

    def __init__(self, answer: Mapping[str, Any]) -> None:
        self.answer = answer
        self.asked: list[tuple[Any, ...]] = []

    async def __call__(self, *args: Any) -> Mapping[str, Any]:
        self.asked.append(args)
        return self.answer


def test_the_callback_asks_the_host_when_no_hook_answers():
    d = Recorder()
    host = Host({"action": "accept", "content": {"name": "host"}})
    callback = eh.build_elicitation_callback(d, host)
    assert drained(d, callback("srv", FORM)) == ElicitResult(action="accept", content={"name": "host"})
    assert host.asked == [("srv", "name?", {"type": "object"}, "form", None, None)]
    url_host = Host({"action": "accept"})
    drained(d, eh.build_elicitation_callback(d, url_host)("srv", URL))
    assert url_host.asked == [("srv", "open", None, "url", "https://auth", "e9")]
    result_inputs = [c[0] for c in d.calls if c[0]["hook_event_name"] == "ElicitationResult"]
    assert [(i["action"], i.get("mode"), i.get("elicitation_id")) for i in result_inputs] == [
        ("accept", "form", None), ("accept", "url", "e9")]


def test_the_callback_skips_the_host_when_a_hook_answers():
    d = Recorder([ok(specific("Elicitation", action="accept", content={"name": "hook"}))])
    host = Host({"action": "cancel"})
    assert run(eh.build_elicitation_callback(d, host)("srv", FORM)) == ElicitResult(
        action="accept", content={"name": "hook"})
    assert host.asked == [] and [c[0]["hook_event_name"] for c in d.calls] == ["Elicitation"]


def test_elicitation_complete_handler():
    d = Recorder()
    emitted: list[tuple[str, str]] = []
    drained(d, eh.build_elicitation_complete_handler(d, lambda s, e: emitted.append((s, e)))("srv", "e7"))
    assert emitted == [("srv", "e7")]
    assert notifications(d) == ['MCP server "srv" confirmed elicitation e7 complete']
    assert d.calls[0][1]["match_query"] == "elicitation_complete"
    run(eh.build_elicitation_complete_handler(None)("srv", "e8"))


# ── against a real MCP server ──


class Answer(BaseModel):
    """The probe server's elicitation schema."""

    name: str


def probe_server() -> MCPServer:
    srv = MCPServer("probe")

    @srv.tool()
    async def ask(ctx: Context) -> str:
        result = await ctx.elicit("Your name?", Answer)
        return f"{result.action}:{result.data.name}" if result.action == "accept" else result.action

    @srv.tool()
    async def visit(ctx: Context) -> str:
        result = await ctx.elicit_url("Sign in", "https://auth.example/x", "eid-1")
        await ctx.request_context.session.send_elicit_complete("eid-1")
        return result.action

    return srv


def script_dispatcher(tmp_path: Path, event: str, body: str, matcher: str = "probe") -> HookDispatcher:
    path = tmp_path / f"{event}.sh"
    path.write_text(body)
    hooks = {event: (HookMatcher(hooks=(CommandHook(command=f"sh {path}"),), matcher=matcher),)}
    return HookDispatcher(snapshot=HooksConfigSnapshot(hooks=hooks), session_id="s", cwd=str(tmp_path),
                          project_dir=str(tmp_path))


def call_probe(tool: str, until: asyncio.Event | None = None, **kw: Any) -> Any:
    async def scenario() -> Any:
        conn = await mc.connect_to_server("probe", CONFIG, cwd="/w", transport_factory=factory(probe_server()), **kw)
        assert isinstance(conn, ConnectedMCPServer) and conn.cleanup is not None
        try:
            result = (await mc.call_mcp_tool(conn, tool, {}, run_id="r", cwd="/w"))["structuredContent"]["result"]
            if until is not None:
                # The SDK runs a notification's handler in a task of its own.
                await asyncio.wait_for(until.wait(), 5)
            return result
        finally:
            await conn.cleanup()

    return run(scenario())


def test_without_a_callback_every_elicitation_is_cancelled():
    assert call_probe("ask") == "cancel"


def test_a_hook_answers_the_server(tmp_path):
    answer = json.dumps(specific("Elicitation", action="accept", content={"name": "hooked"}))
    d = script_dispatcher(tmp_path, "Elicitation", f"cat > {tmp_path}/input.json\necho '{answer}'\n")
    host = Host({"action": "cancel"})
    assert call_probe("ask", elicitation_callback=eh.build_elicitation_callback(d, host)) == "accept:hooked"
    assert host.asked == []
    seen = json.loads((tmp_path / "input.json").read_text())
    assert (seen["mcp_server_name"], seen["message"], seen["mode"]) == ("probe", "Your name?", "form")
    assert seen["requested_schema"]["properties"]["name"]["type"] == "string"


def test_the_host_answers_the_server():
    host = Host({"action": "accept", "content": {"name": "host"}})
    assert call_probe("ask", elicitation_callback=eh.build_elicitation_callback(None, host)) == "accept:host"
    assert host.asked[0][:2] == ("probe", "Your name?")


def test_a_blocking_hook_declines_for_the_server(tmp_path):
    d = script_dispatcher(tmp_path, "Elicitation", "echo nope >&2\nexit 2\n")
    host = Host({"action": "accept", "content": {"name": "host"}})
    assert call_probe("ask", elicitation_callback=eh.build_elicitation_callback(d, host)) == "decline"
    assert host.asked == []


def test_a_hook_for_another_server_does_not_answer(tmp_path):
    d = script_dispatcher(tmp_path, "Elicitation", "exit 2\n", matcher="other")
    host = Host({"action": "accept", "content": {"name": "host"}})
    assert call_probe("ask", elicitation_callback=eh.build_elicitation_callback(d, host)) == "accept:host"


def test_url_mode_and_the_completion_notification():
    host = Host({"action": "accept"})
    completed: list[tuple[str, str]] = []
    done = asyncio.Event()

    async def on_complete(server_name: str, elicitation_id: str) -> None:
        completed.append((server_name, elicitation_id))
        done.set()

    result = call_probe("visit", done, elicitation_callback=eh.build_elicitation_callback(None, host),
                        on_elicitation_complete=on_complete)
    assert result == "accept"
    assert host.asked == [("probe", "Sign in", None, "url", "https://auth.example/x", "eid-1")]
    assert completed == [("probe", "eid-1")]


def test_the_manager_passes_the_callbacks_to_every_connection():
    host = Host({"action": "accept", "content": {"name": "managed"}})
    callback = eh.build_elicitation_callback(None, host)

    async def on_complete(server_name: str, elicitation_id: str) -> None:
        return None

    async def scenario() -> tuple[Any, Any]:
        manager = mm.McpConnectionManager(cwd="/w", transport_factory=factory(probe_server()),
                                          elicitation_callback=callback, on_elicitation_complete=on_complete)
        await manager.connect({"probe": CONFIG})
        [connected] = manager.clients
        assert isinstance(connected, ConnectedMCPServer)
        first = (await mc.call_mcp_tool(connected, "ask", {}, run_id="r", cwd="/w"))
        again = await manager.reconnect("probe", CONFIG)
        assert isinstance(again.client, ConnectedMCPServer) and again.client.cleanup is not None
        try:
            second = await mc.call_mcp_tool(again.client, "ask", {}, run_id="r", cwd="/w")
        finally:
            await again.client.cleanup()
            await manager.aclose()
        return first["structuredContent"]["result"], second["structuredContent"]["result"]

    assert run(scenario()) == ("accept:managed", "accept:managed")
    assert len(host.asked) == 2


def test_the_session_callbacks_default_to_cancel_and_no_tee():
    assert mc._elicitation_callback("s", None) is mc._cancel_elicitation
    assert mc._message_handler("s", None) is None
    seen: list[tuple[str, str]] = []

    async def on_complete(server_name: str, elicitation_id: str) -> None:
        seen.append((server_name, elicitation_id))

    handler = mc._message_handler("s", on_complete)
    assert handler is not None
    run(handler(RuntimeError("transport fault")))
    run(handler(ElicitCompleteNotification(params=ElicitCompleteNotificationParams(elicitation_id="e"))))
    assert seen == [("s", "e")]
