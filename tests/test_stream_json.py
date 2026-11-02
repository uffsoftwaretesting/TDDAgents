"""`-p` over the SDK protocol: the wire shapes, the runner's helpers, and `run_headless` end to end."""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import io
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage

from app.cli.print import (
    HeadlessOptions,
    NoMessagesReturned,
    build_mcp_server_statuses,
    can_batch_with,
    final_output,
    is_last_message_candidate,
    join_prompt_commands,
    parse_task_notification,
    prompt_from_content,
    run_headless,
    user_message_from_blocks,
)
from app.query_engine import ResultMessage
from app.services.compact.compact import create_compact_boundary_message
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.message_queue_manager import QueuedCommand
from app.utils.messages import ATTACHMENT_KEY, ToolUseSummaryMessage
from app.utils.sdk_messages import (
    StreamEventSynthesizer,
    SystemInitInputs,
    build_system_init_message,
    sdk_messages_for_event,
    sdk_status,
    sdk_tdd_message,
    sdk_usage,
    stream_event_message,
)

SID = "loop-1"


# ── the wire shapes ──────────────────────────────────────────────────────────

def test_assistant_messages_are_anthropic_blocks() -> None:
    message = AIMessage(
        id="msg_1", content=[{"type": "text", "text": "hi", "index": 0}],
        tool_calls=[{"name": "Bash", "id": "tu1", "args": {"command": "ls"}}],
        response_metadata={"model": "claude-x", "stop_reason": "tool_use"},
        usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7,
                        "input_token_details": {"cache_read": 2, "cache_creation": 1}},
    )
    (sdk,) = sdk_messages_for_event(message, SID)
    assert sdk["type"] == "assistant" and sdk["session_id"] == SID and sdk["parent_tool_use_id"] is None
    assert sdk["message"]["content"] == [{"type": "text", "text": "hi"},
                                         {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}]
    assert (sdk["message"]["id"], sdk["message"]["model"], sdk["message"]["stop_reason"]) == (
        "msg_1", "claude-x", "tool_use")
    assert sdk["message"]["usage"] == {"input_tokens": 3, "output_tokens": 4, "cache_creation_input_tokens": 1,
                                       "cache_read_input_tokens": 2}
    assert sdk_messages_for_event(AIMessage(content="  "), SID) == []
    error = sdk_messages_for_event(AIMessage(content="down", additional_kwargs={"is_api_error_message": True}), SID)
    assert error[0]["error"] == "unknown"


def test_user_messages_and_tool_results() -> None:
    (prompt,) = sdk_messages_for_event(HumanMessage(content="go", additional_kwargs={"is_meta": True}), SID)
    assert prompt["message"] == {"role": "user", "content": "go"} and prompt["isSynthetic"] is True
    result = ToolMessage(content="boom", tool_call_id="tu1", status="error",
                         additional_kwargs={"tool_use_result": {"stdout": ""}})
    (sdk,) = sdk_messages_for_event(result, SID)
    assert sdk["message"]["content"] == [{"type": "tool_result", "tool_use_id": "tu1", "content": "boom",
                                          "is_error": True}]
    assert sdk["tool_use_result"] == {"stdout": ""}


def test_attachments_boundary_summary_result_and_the_rest() -> None:
    queued = {"type": "queued_command", "prompt": "later", "source_uuid": "u9"}
    attachment = HumanMessage(content="", additional_kwargs={ATTACHMENT_KEY: queued})
    assert sdk_messages_for_event(attachment, SID) == []
    (replay,) = sdk_messages_for_event(attachment, SID, replay_user_messages=True)
    assert (replay["uuid"], replay["isReplay"], replay["message"]["content"]) == ("u9", True, "later")
    other = HumanMessage(content="", additional_kwargs={ATTACHMENT_KEY: {"type": "todo"}})
    assert sdk_messages_for_event(other, SID, replay_user_messages=True) == []
    (boundary,) = sdk_messages_for_event(create_compact_boundary_message("auto", 1234), SID)
    assert (boundary["subtype"], boundary["compact_metadata"]) == ("compact_boundary",
                                                                   {"trigger": "auto", "pre_tokens": 1234})
    summary = ToolUseSummaryMessage(summary="Read 2 files", preceding_tool_use_ids=("a", "b"), uuid="u", timestamp="t")
    assert sdk_messages_for_event(summary, SID) == [{"type": "tool_use_summary", "summary": "Read 2 files",
                                                     "preceding_tool_use_ids": ["a", "b"], "session_id": SID,
                                                     "uuid": "u"}]
    assert sdk_messages_for_event(SystemMessage(content="switched"), SID) == []
    assert sdk_messages_for_event(object(), SID) == []


def result_message(**overrides: Any) -> ResultMessage:
    fields: dict[str, Any] = dict(
        subtype="success", is_error=False, duration_ms=5, duration_api_ms=3, num_turns=2, stop_reason="end_turn",
        session_id=SID, total_cost_usd=0.5, usage={"input_tokens": 1}, model_usage={}, terminal_reason="completed",
        uuid="r1", result="ok", permission_denials=({"tool_name": "Agent", "tool_use_id": "t", "tool_input": {}},),
    )
    fields.update(overrides)
    return ResultMessage(**fields)


def test_the_result_message() -> None:
    (sdk,) = sdk_messages_for_event(result_message(), SID)
    assert sdk == {
        "type": "result", "subtype": "success", "is_error": False, "duration_ms": 5, "duration_api_ms": 3,
        "num_turns": 2, "stop_reason": "end_turn", "session_id": SID, "total_cost_usd": 0.5,
        "usage": {"input_tokens": 1}, "modelUsage": {},
        "permission_denials": [{"tool_name": "Task", "tool_use_id": "t", "tool_input": {}}],
        "uuid": "r1", "terminal_reason": "completed", "result": "ok",
    }
    (failed,) = sdk_messages_for_event(result_message(subtype="error_during_execution", is_error=True,
                                                      errors=("x",), result=None), SID)
    assert "result" not in failed and failed["errors"] == ["x"]


def test_the_init_message() -> None:
    inputs = SystemInitInputs(cwd="/p", tools=["Agent", "Bash"], mcp_servers=[("srv", "connected")], model="m",
                              permission_mode="plan", commands=["help"], agents=["tester"], skills=["s"])
    init = build_system_init_message(inputs, SID)
    assert init["tools"] == ["Task", "Bash"] and init["mcp_servers"] == [{"name": "srv", "status": "connected"}]
    assert (init["type"], init["subtype"], init["permissionMode"], init["output_style"]) == (
        "system", "init", "plan", "default")


def test_status_and_tdd_messages() -> None:
    status = sdk_status(SID, permission_mode="acceptEdits")
    assert (status["subtype"], status["status"], status["permissionMode"]) == ("status", None, "acceptEdits")
    assert "permissionMode" not in sdk_status(SID, status="compacting")
    ledger = sdk_tdd_message("tdd_ledger", {"phase": "red"}, SID)
    assert (ledger["type"], ledger["subtype"], ledger["phase"], ledger["session_id"]) == (
        "system", "tdd_ledger", "red", SID)


def test_partial_messages_synthesize_anthropic_events() -> None:
    synth = StreamEventSynthesizer(model="m", message_id="msg_x")
    events = synth.feed(AIMessageChunk(content="Hel"))
    events += synth.feed(AIMessageChunk(content="lo"))
    events += synth.feed(AIMessageChunk(content="", tool_call_chunks=[
        {"id": "tu1", "name": "Bash", "args": '{"co', "index": 1}]))
    events += synth.feed(AIMessageChunk(content="", tool_call_chunks=[
        {"id": None, "name": None, "args": 'mmand":"ls"}', "index": 1}],
        response_metadata={"stop_reason": "tool_use"},
        usage_metadata={"input_tokens": 1, "output_tokens": 9, "total_tokens": 10}))
    events += synth.finish()
    kinds = [e["type"] for e in events]
    assert kinds == ["message_start", "content_block_start", "content_block_delta", "content_block_delta",
                     "content_block_stop", "content_block_start", "content_block_delta", "content_block_delta",
                     "content_block_stop", "message_delta", "message_stop"]
    assert events[0]["message"]["id"] == "msg_x" and events[0]["message"]["model"] == "m"
    assert events[2]["delta"] == {"type": "text_delta", "text": "Hel"}
    assert events[5]["content_block"] == {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {}}
    assert events[7]["delta"] == {"type": "input_json_delta", "partial_json": 'mmand":"ls"}'}
    assert events[9] == {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                         "usage": {"output_tokens": 9}}
    assert StreamEventSynthesizer().finish() == []
    openai = StreamEventSynthesizer()
    openai.feed(AIMessageChunk(content="x", response_metadata={"finish_reason": "stop"}))
    assert openai.finish()[-2]["delta"]["stop_reason"] == "end_turn"
    wrapped = stream_event_message({"type": "message_stop"}, SID)
    assert (wrapped["type"], wrapped["event"], wrapped["parent_tool_use_id"]) == (
        "stream_event", {"type": "message_stop"}, None)
    assert sdk_usage(object()) == {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
                                   "cache_read_input_tokens": 0}


# ── the runner's helpers ─────────────────────────────────────────────────────

def test_prompt_batching() -> None:
    head = QueuedCommand("a", "prompt", uuid="1")
    assert can_batch_with(head, QueuedCommand("b", "prompt"))
    assert not can_batch_with(head, None)
    assert not can_batch_with(head, QueuedCommand("b", "task-notification"))
    assert not can_batch_with(head, QueuedCommand("b", "prompt", is_meta=True))
    assert join_prompt_commands([head]) is head
    image = {"type": "image", "source": {}}
    joined = join_prompt_commands([head, QueuedCommand("b", "prompt", uuid="2", content_blocks=(image,))])
    assert (joined.value, joined.uuid, joined.content_blocks) == ("a\nb", "2", (image,))


def test_prompt_from_content() -> None:
    assert prompt_from_content("hi") == ("hi", ())
    image = {"type": "image", "source": {"type": "base64"}}
    assert prompt_from_content([image, {"type": "text", "text": "a"}, {"type": "text", "text": "b"}, "junk"]) == (
        "b", (image, {"type": "text", "text": "a"}))
    assert prompt_from_content([image]) == ("", (image,))
    assert prompt_from_content(None) == ("", ())


def test_user_message_from_blocks_resizes_images() -> None:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    data = base64.b64encode(buffer.getvalue()).decode()
    url = {"type": "image", "source": {"type": "url", "url": "https://x"}}
    message = user_message_from_blocks("look", [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}, url], True)
    assert message.content == [{"type": "image", "base64": data, "mime_type": "image/png"}, url,
                               {"type": "text", "text": "look"}]
    assert message.additional_kwargs == {"is_meta": True}
    assert user_message_from_blocks("", [], False).content == []


def test_task_notifications_become_sdk_events() -> None:
    text = ("<task-notification><task-id>t1</task-id><tool-use-id>tu</tool-use-id><output-file>/o</output-file>"
            "<status>killed</status><summary>done</summary><usage><total_tokens>10</total_tokens>"
            "<tool_uses>2</tool_uses><duration_ms>5</duration_ms></usage></task-notification>")
    assert parse_task_notification(text) == {
        "type": "system", "subtype": "task_notification", "task_id": "t1", "status": "stopped",
        "output_file": "/o", "summary": "done", "tool_use_id": "tu",
        "usage": {"total_tokens": 10, "tool_uses": 2, "duration_ms": 5}}
    assert parse_task_notification("<task-id>t</task-id>") is None
    odd = parse_task_notification("<status>weird</status><usage><total_tokens>1</total_tokens>"
                                  "<tool_uses>1</tool_uses></usage>")
    assert odd is not None and odd["status"] == "completed" and odd["task_id"] == ""
    assert odd["usage"]["duration_ms"] == 0 and "tool_use_id" not in odd


def test_last_message_candidates_and_final_output() -> None:
    assert is_last_message_candidate({"type": "result"})
    assert is_last_message_candidate({"type": "system", "subtype": "init"})
    for skipped in ({"type": "stream_event"}, {"type": "control_response"},
                    {"type": "system", "subtype": "task_notification"}):
        assert not is_last_message_candidate(skipped)
    text, json_out, stream = HeadlessOptions(), HeadlessOptions(output_format="json"), HeadlessOptions(
        output_format="stream-json")
    success = {"type": "result", "subtype": "success", "result": "done"}
    assert final_output(stream, None, []) is None
    with pytest.raises(NoMessagesReturned, match="No messages returned"):
        final_output(text, {"type": "system"}, [])
    assert final_output(text, success, []) == "done\n"
    assert final_output(text, {**success, "result": "x\n"}, []) == "x\n"
    assert final_output(text, {"type": "result", "subtype": "error_during_execution"}, []) == "Execution error"
    assert final_output(text, {"type": "result", "subtype": "error_max_turns"}, [], max_turns=3) == (
        "Error: Reached max turns (3)")
    assert final_output(text, {"type": "result", "subtype": "error_max_budget_usd"}, [], max_budget_usd=1.5) == (
        "Error: Exceeded USD budget (1.5)")
    assert final_output(text, {"type": "result", "subtype": "error_max_structured_output_retries"}, []) == (
        "Error: Failed to provide valid structured output after maximum retries")
    assert final_output(text, {"type": "result", "subtype": "other"}, []) is None
    assert final_output(json_out, success, []) == json.dumps(success, separators=(",", ":")) + "\n"
    verbose = dataclasses.replace(json_out, verbose=True)
    assert json.loads(final_output(verbose, success, [{"type": "system"}, success]) or "") == [
        {"type": "system"}, success]


def test_mcp_server_statuses() -> None:
    from types import SimpleNamespace

    from mcp.types import Implementation

    from app.services.mcp.types import (
        ConnectedMCPServer,
        DisabledMCPServer,
        FailedMCPServer,
        McpOAuthConfig,
        McpServerConfig,
    )

    stdio = McpServerConfig(type="stdio", scope="project", command="x", args=("-v",))
    http = McpServerConfig(type="http", scope="user", url="https://h", headers={"A": "1"},
                           oauth=McpOAuthConfig(client_id="c"))
    clients = [
        ConnectedMCPServer(name="my srv", config=stdio, session=None, capabilities=None,
                           server_info=Implementation(name="srv", version="2")),
        FailedMCPServer(name="b", config=http, error="nope"),
        DisabledMCPServer(name="c", config=McpServerConfig(type="ws", scope="local", url="ws://w")),
        SimpleNamespace(name="d", type="weird", config=McpServerConfig(type=None, scope="dynamic", command="y")),
    ]
    tools = [SimpleNamespace(name="mcp__my_srv__raw", mcp_tool_name="read", is_read_only=lambda _: True,
                             is_destructive=lambda _: False),
             SimpleNamespace(name="mcp__my_srv__w", is_destructive=lambda _: True),
             SimpleNamespace(name="mcp__b__t")]
    assert build_mcp_server_statuses(clients, tools) == [
        {"name": "my srv", "status": "connected", "serverInfo": {"name": "srv", "version": "2"},
         "config": {"type": "stdio", "command": "x", "args": ["-v"]}, "scope": "project",
         "tools": [{"name": "read", "annotations": {"readOnly": True}},
                   {"name": "mcp__my_srv__w", "annotations": {"destructive": True}}]},
        {"name": "b", "status": "failed", "error": "nope", "scope": "user",
         "config": {"type": "http", "url": "https://h", "headers": {"A": "1"}, "oauth": {"client_id": "c"}}},
        {"name": "c", "status": "disabled", "scope": "local"},
        {"name": "d", "status": "pending", "config": {"type": "stdio", "command": "y", "args": []},
         "scope": "dynamic"},
    ]


# ── run_headless end to end ──────────────────────────────────────────────────

class Model:
    def __init__(self, turns: list[list[Any]]) -> None:
        self.turns = list(turns)
        self.seen: list[Any] = []

    async def __call__(self, state: Any, config: Any) -> AsyncIterator[Any]:
        self.seen.append(state)
        if not self.turns:
            raise AssertionError("model asked for more turns than scripted")
        for message in self.turns.pop(0):
            yield message


class Feed:
    def __init__(self, *lines: dict[str, Any]) -> None:
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        for line in lines:
            self.send(line)

    def send(self, message: dict[str, Any]) -> None:
        self.queue.put_nowait(json.dumps(message) + "\n")

    def close(self) -> None:
        self.queue.put_nowait(None)

    async def blocks(self) -> AsyncIterator[str]:
        while (item := await self.queue.get()) is not None:
            yield item


def run_options(tmp_path: Path, model: Model, *, prompts: bool = False) -> dict[str, Any]:
    from app.query.deps import get_production_deps

    perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, should_avoid_permission_prompts=not prompts)
    return {"deps": dataclasses.replace(get_production_deps(), call_model=model), "permission_context": perm,
            "project_root": tmp_path, "home": tmp_path / "home", "managed_dir": tmp_path / "etc"}


STREAM = HeadlessOptions(output_format="stream-json", input_format="stream-json", verbose=True)


def user(text: str, uuid: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"type": "user", "message": {"role": "user", "content": text},
                               "parent_tool_use_id": None}
    if uuid is not None:
        message["uuid"] = uuid
    return message


def control(request_id: str, subtype: str, **fields: Any) -> dict[str, Any]:
    return {"type": "control_request", "request_id": request_id, "request": {"subtype": subtype, **fields}}


def lines_of(written: list[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for chunk in written for line in chunk.splitlines() if line]


def responses(out: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {m["response"]["request_id"]: m["response"] for m in out if m["type"] == "control_response"}


def test_text_prompt_json_output(python_workspace: Any, tmp_path: Path) -> None:
    written: list[str] = []
    model = Model([[AIMessage(content="The plan is ready.")]])
    outcome = asyncio.run(run_headless("sj-text", "Build X", workspace=python_workspace,
                                       options=HeadlessOptions(output_format="json"), write_stdout=written.append,
                                       **run_options(tmp_path, model)))
    (result,) = lines_of(written)
    assert (result["type"], result["subtype"], result["result"], result["is_error"]) == (
        "result", "success", "The plan is ready.", False)
    assert outcome.result == result and outcome.result["session_id"] == "sj-text"
    assert "Build X" in [m.content for m in model.seen[0].messages if isinstance(m, HumanMessage)]


def test_text_output_writes_the_result_text(python_workspace: Any, tmp_path: Path) -> None:
    written: list[str] = []
    asyncio.run(run_headless("sj-plain", "Build Y", workspace=python_workspace, write_stdout=written.append,
                             **run_options(tmp_path, Model([[AIMessage(content="Hello")]]))))
    assert written == ["Hello\n"]


def test_stream_json_turn(python_workspace: Any, tmp_path: Path) -> None:
    feed = Feed(control("i1", "initialize"), user("hi there", "u1"))
    written: list[str] = []

    def write(text: str) -> None:
        written.append(text)
        if any(m.get("type") == "result" for m in lines_of([text])):
            feed.close()

    model = Model([[AIMessage(content="Hello back")]])
    options = dataclasses.replace(STREAM, replay_user_messages=True)
    outcome = asyncio.run(run_headless("sj-stream", None, workspace=python_workspace, options=options,
                                       input_blocks=feed.blocks(), write_stdout=write,
                                       **run_options(tmp_path, model)))
    out = lines_of(written)
    kinds = [(m["type"], m.get("subtype")) for m in out if not (m.get("subtype") or "").startswith("tdd_")]
    assert kinds[0] == ("control_response", None)
    init = responses(out)["i1"]["response"]
    assert init["output_style"] == "default" and isinstance(init["pid"], int)
    assert {"name": "help", "description": "Show the commands", "argumentHint": ""} in init["commands"]
    assert kinds[1:] == [("system", "init"), ("user", None), ("assistant", None), ("result", "success")]
    replay = next(m for m in out if m["type"] == "user")
    assert (replay["uuid"], replay["isReplay"], replay["message"]["content"]) == ("u1", True, "hi there")
    assert out[-1]["result"] == "Hello back" and outcome.result == out[-1]
    assert "hi there" in [m.content for m in model.seen[0].messages if isinstance(m, HumanMessage)]


def test_control_requests_without_a_turn(python_workspace: Any, tmp_path: Path) -> None:
    feed = Feed(
        control("a", "initialize", systemPrompt="HOST PROMPT", appendSystemPrompt="HOST TAIL"),
        control("b", "initialize"), control("c", "interrupt"),
        control("d", "no_such_thing"), control("e", "set_model", model="claude-other"),
        control("f", "cancel_async_message", message_uuid="nope"), control("g", "mcp_status"),
        control("h", "claude_authenticate"), control("i", "claude_oauth_callback"),
        control("j", "claude_oauth_wait_for_completion"), control("k", "channel_enable", serverName="s"),
        control("l", "remote_control", enabled=True), control("m", "remote_control", enabled=False),
        control("n", "set_permission_mode", mode="bypassPermissions"),
        control("o", "set_permission_mode", mode="acceptEdits"), control("p", "set_permission_mode", mode="bogus"),
        control("q", "set_max_thinking_tokens", max_thinking_tokens=0),
        control("r", "stop_task", task_id="t-missing"),
        control("s", "apply_flag_settings", settings={"model": "claude-flag", "x": 1, "y": None}),
        control("t", "get_settings"), control("u", "seed_read_state", path="missing.py", mtime=1),
        control("v", "set_proactive", enabled=True), control("ctx", "get_context_usage"),
        control("rw", "rewind_files", user_message_id="u-x"),
        control("rd", "rewind_files", user_message_id="u-x", dry_run=True),
        control("w", "end_session"), control("x", "interrupt"),
    )
    written: list[str] = []
    options = dataclasses.replace(STREAM)
    outcome = asyncio.run(run_headless("sj-control", None, workspace=python_workspace, options=options,
                                       input_blocks=feed.blocks(), write_stdout=written.append,
                                       **run_options(tmp_path, Model([]))))
    out = lines_of(written)
    got = responses(out)
    assert got["a"]["subtype"] == "success"
    assert (options.system_prompt, options.append_system_prompt) == ("HOST PROMPT", "HOST TAIL")
    assert (got["b"]["subtype"], got["b"]["error"], got["b"]["pending_permission_requests"]) == (
        "error", "Already initialized", [])
    assert got["c"] == {"subtype": "success", "request_id": "c"}
    assert got["d"]["error"] == "Unsupported control request subtype: no_such_thing"
    assert got["e"]["subtype"] == "success"
    assert got["f"]["response"] == {"cancelled": False}
    assert got["g"]["response"] == {"mcpServers": []}
    assert got["h"]["error"] == "claude.ai authentication is not available in this build"
    assert got["i"]["error"] == got["j"]["error"] == "No active claude_authenticate flow"
    assert got["k"]["error"] == "channels feature not available in this build"
    assert got["l"]["error"] == "Remote Control initialization failed"
    assert got["m"]["subtype"] == "success"
    assert got["n"]["error"] == ("Cannot set permission mode to bypassPermissions because the session was not "
                                 "launched with --dangerously-skip-permissions")
    assert got["o"]["response"] == {"mode": "acceptEdits"}
    assert got["p"]["error"] == "Invalid permission mode: bogus"
    assert got["q"]["subtype"] == "success" and options.thinking_config == {"type": "disabled"}
    assert got["r"]["error"] == "No task found with ID: t-missing"
    assert got["s"]["subtype"] == "success"
    settings = got["t"]["response"]
    assert settings["applied"]["model"] == "claude-flag"
    assert {"source": "flagSettings", "settings": {"model": "claude-flag", "x": 1}} in settings["sources"]
    assert got["u"]["subtype"] == "success"
    assert got["v"]["error"] == "Unsupported control request subtype: set_proactive"
    usage = got["ctx"]["response"]
    assert got["ctx"]["subtype"] == "success" and {"categories", "totalTokens", "model"} <= set(usage)
    # File history is off in a headless run without TDDAGENTS_ENABLE_SDK_FILE_CHECKPOINTING.
    assert got["rw"]["error"] == "File rewinding is not enabled."
    assert got["rd"] == {"subtype": "success", "request_id": "rd",
                         "response": {"canRewind": False, "error": "File rewinding is not enabled."}}
    assert got["w"]["subtype"] == "success" and "x" not in got
    assert outcome.result is None


def test_permission_mode_changes_are_streamed(python_workspace: Any, tmp_path: Path) -> None:
    # A session without a plan opens in plan mode: leaving it is one change, repeating it none.
    feed = Feed(control("o", "set_permission_mode", mode="default"),
                control("z", "set_permission_mode", mode="default"))
    feed.close()
    written: list[str] = []
    asyncio.run(run_headless("sj-mode", None, workspace=python_workspace, options=STREAM,
                             input_blocks=feed.blocks(), write_stdout=written.append,
                             **run_options(tmp_path, Model([]))))
    statuses = [m for m in lines_of(written) if m.get("subtype") == "status"]
    assert len(statuses) == 1 and statuses[0]["permissionMode"] == "default" and statuses[0]["status"] is None


def test_the_host_answers_a_question(python_workspace: Any, tmp_path: Path) -> None:
    from app.tdd.plan import TddPlan, plan_path, save_plan
    from app.cli.print import session_run_id

    save_plan(plan_path(session_run_id("sj-ask"), str(python_workspace.root)), TddPlan.from_texts(["x"]))
    question = {"question": "Size?", "header": "Size", "multiSelect": False,
                "options": [{"label": "Small", "description": "s"}, {"label": "Large", "description": "l"}]}
    asking = AIMessage(content="", tool_calls=[{"name": "AskUserQuestion", "id": "q1",
                                                "args": {"questions": [question]}}])
    feed = Feed(user("go", "u1"))
    written: list[str] = []
    asked: list[dict[str, Any]] = []

    def write(text: str) -> None:
        written.append(text)
        for message in lines_of([text]):
            request = message.get("request") or {}
            if message.get("type") == "control_request" and request.get("subtype") == "can_use_tool":
                asked.append(request)
                feed.send({"type": "control_response", "response": {
                    "subtype": "success", "request_id": message["request_id"],
                    "response": {"behavior": "allow",
                                 "updatedInput": {**request["input"], "answers": {"Size?": "Large"}}}}})
            if message.get("type") == "result":
                feed.close()

    model = Model([[asking], [AIMessage(content="Large it is.")]])
    asyncio.run(run_headless("sj-ask", None, workspace=python_workspace, options=STREAM,
                             input_blocks=feed.blocks(), write_stdout=write,
                             **run_options(tmp_path, model, prompts=True)))
    (request,) = asked
    assert (request["tool_name"], request["tool_use_id"]) == ("AskUserQuestion", "q1")
    (answer,) = [m for m in model.seen[1].messages if isinstance(m, ToolMessage)]
    assert "Large" in str(answer.content)
    assert lines_of(written)[-1]["result"] == "Large it is."


def test_initialize_registers_the_hosts_hook_callbacks(python_workspace: Any, tmp_path: Path) -> None:
    hooks = {"Stop": [{"matcher": None, "hookCallbackIds": ["cb-stop"], "timeout": 5}]}
    feed = Feed(control("i", "initialize", hooks=hooks), user("finish", "u1"))
    written: list[str] = []
    callbacks: list[dict[str, Any]] = []

    def write(text: str) -> None:
        written.append(text)
        for message in lines_of([text]):
            request = message.get("request") or {}
            if message.get("type") == "control_request" and request.get("subtype") == "hook_callback":
                callbacks.append(request)
                feed.send({"type": "control_response", "response": {
                    "subtype": "success", "request_id": message["request_id"], "response": {}}})
            if message.get("type") == "result":
                feed.close()

    asyncio.run(run_headless("sj-hooks", None, workspace=python_workspace, options=STREAM,
                             input_blocks=feed.blocks(), write_stdout=write,
                             **run_options(tmp_path, Model([[AIMessage(content="All done.")]]))))
    (callback,) = callbacks
    assert callback["callback_id"] == "cb-stop" and callback["input"]["hook_event_name"] == "Stop"
    assert lines_of(written)[-1]["result"] == "All done."
