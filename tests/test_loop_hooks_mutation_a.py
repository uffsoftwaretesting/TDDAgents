"""
Mutation-driven pins for Phase 4's hook call sites (cluster A: the runner, the tool pipeline,
PermissionRequest, and the per-event entry points and runners in `app/utils/hooks/`).

Each test names what a surviving mutant got away with: an argument dropped on the way to a
hook (its payload, its tool-use id, the cancel signal), or a value the hook system hands on.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.query.config import build_run_config
from app.query.deps import LoopDeps
from app.query.state import CompactionResult, initial_loop_state
from app.query.stop_hooks import (
    StopHookResult,
    build_stop_hooks_runner,
    handle_stop_hooks,
)
from app.schemas.hooks import CommandHook, HookMatcher
from app.services.tools.tool_execution import run_tool_use
from app.services.tools.tool_hooks import (
    run_post_tool_use_failure_hooks,
    run_post_tool_use_hooks,
    run_pre_tool_use_hooks,
)
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tool import ToolResult, build_tool, tool_context_for
from app.types.hooks import AggregatedHookResult
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.hooks.hooks import (
    HookDispatcher,
    execute_session_start_hooks,
    execute_stop_hooks,
    execute_subagent_start_hooks,
    execute_user_prompt_submit_hooks,
    last_assistant_text,
)
from app.utils.hooks.hooks_config_snapshot import HooksConfigSnapshot
from app.utils.messages import HookBlockingError
from app.utils.permissions.permissions import (
    run_permission_request_hooks_for_headless_agent,
)
from app.utils.session_start import process_session_start_hooks
from tests.hook_doubles import ScriptedDispatcher

CONFIG = build_run_config("mutation-a", postgres_checkpointing=False)
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
COMPLETE = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)


class Recorder(ScriptedDispatcher):
    """A scripted dispatcher that also records the tool-use id and the signal it was handed."""

    def __init__(self, script: Mapping[str, Any] | None = None) -> None:
        super().__init__(script=script)
        self.calls: list[tuple[str, str, Any]] = []

    async def execute_hooks(
        self, hook_input: Mapping[str, Any], tool_use_id: str, **kwargs: Any
    ) -> AsyncIterator[AggregatedHookResult]:
        self.calls.append((str(hook_input["hook_event_name"]), tool_use_id, kwargs.get("signal")))
        async for item in super().execute_hooks(hook_input, tool_use_id, **kwargs):
            yield item


def marker_dispatcher(tmp_path: Path, event: str) -> tuple[HookDispatcher, Path]:
    """A real dispatcher whose only hook, for `event`, leaves a marker file when it runs."""
    marker = tmp_path / "ran"
    script = tmp_path / "hook.sh"
    script.write_text(f"#!/bin/sh\ncat > /dev/null\ntouch '{marker}'\n")
    script.chmod(0o755)
    snapshot = HooksConfigSnapshot(hooks={event: (HookMatcher(hooks=(CommandHook(command=str(script)),)),)})
    return HookDispatcher(snapshot=snapshot, session_id="s", cwd=str(tmp_path), project_dir=str(tmp_path)), marker


def cancelled_context(dispatcher: Any, **kw: Any) -> Any:
    context = tool_context_for(AppStateStore(), hook_dispatcher=dispatcher, **kw)
    context.cancel.cancel()
    return context


def worker_context(dispatcher: Any, mode: PermissionMode = PermissionMode.ACCEPT_EDITS) -> Any:
    perm = ToolPermissionContext(mode=mode, should_avoid_permission_prompts=True)
    return tool_context_for(
        AppStateStore(state=AppState(tool_permission_context=perm)), hook_dispatcher=dispatcher,
        agent_id="agent_1", agent_type="tester",
    )


def probe(call: Any = None, name: str = "Probe", is_mcp: bool = False) -> Any:
    async def ok(args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(content="real output")

    return build_tool(name=name, prompt="p", call=call or ok, input_schema={"type": "object"}, is_mcp=is_mcp)


def run(tool: Any, context: Any, call_id: str = "c1") -> Any:
    return asyncio.run(run_tool_use({"name": tool.name, "args": {}, "id": call_id}, AIMessage(content=""),
                                    context, [tool]))


# ── per-event entry points: payload, tool-use id, signal ────────────────────

def test_post_and_failure_hooks_carry_the_worker_identity_and_mode():
    d = ScriptedDispatcher()
    context = worker_context(d)
    asyncio.run(run_post_tool_use_hooks(context, "Probe", {"a": 1}, "out", "tu-1"))
    asyncio.run(run_post_tool_use_failure_hooks(context, "Probe", {"a": 1}, "boom", "tu-2"))
    for event, tool_use_id in (("PostToolUse", "tu-1"), ("PostToolUseFailure", "tu-2")):
        payload = d.payload(event)
        assert payload["agent_id"] == "agent_1"
        assert payload["agent_type"] == "tester"
        assert payload["permission_mode"] == "acceptEdits"
        assert payload["tool_name"] == "Probe"
        assert payload["tool_use_id"] == tool_use_id


def test_permission_request_hooks_carry_the_worker_identity():
    d = ScriptedDispatcher()
    asyncio.run(run_permission_request_hooks_for_headless_agent(
        probe(name="Bash"), {"command": "c"}, "tu", worker_context(d), PermissionMode.DEFAULT))
    payload = d.payload("PermissionRequest")
    assert (payload["agent_id"], payload["agent_type"]) == ("agent_1", "tester")


def test_post_hooks_are_handed_the_tool_use_id_and_the_cancel_signal():
    d = Recorder()
    context = worker_context(d)
    asyncio.run(run_post_tool_use_hooks(context, "Probe", {}, "out", "tu-1"))
    asyncio.run(run_post_tool_use_failure_hooks(context, "Probe", {}, "boom", "tu-2"))
    assert d.calls == [("PostToolUse", "tu-1", context.cancel), ("PostToolUseFailure", "tu-2", context.cancel)]


def test_lifecycle_hooks_get_a_fresh_uuid_as_their_tool_use_id():
    d = Recorder()
    context = worker_context(d)
    asyncio.run(_drain(execute_user_prompt_submit_hooks(context, "p", "default")))
    asyncio.run(_drain(execute_stop_hooks(context, "default", None, False, ())))
    asyncio.run(_drain(execute_session_start_hooks(d, "startup")))
    asyncio.run(_drain(execute_subagent_start_hooks(context, "agent_1", "tester")))
    assert [event for event, _, _ in d.calls] == ["UserPromptSubmit", "SubagentStop", "SessionStart", "SubagentStart"]
    assert all(UUID.match(tool_use_id) for _, tool_use_id, _ in d.calls)
    assert d.calls[0][2] is context.cancel


async def _await(awaitable: Any) -> Any:
    return await awaitable


async def _drain(stream: AsyncIterator[Any]) -> list[Any]:
    return [item async for item in stream]


def test_last_assistant_text_reads_a_text_block_without_text_as_empty():
    message = AIMessage(content=[{"type": "text"}, {"type": "text", "text": "hi"}])
    assert last_assistant_text([message]) == "hi"


# ── a cancelled run runs no hooks ───────────────────────────────────────────

def test_a_cancelled_context_runs_no_pre_tool_hook(tmp_path):
    d, marker = marker_dispatcher(tmp_path, "PreToolUse")
    outcome = asyncio.run(run_pre_tool_use_hooks(cancelled_context(d), "Probe", {}, "tu"))
    assert not marker.exists()
    assert outcome.stopped is False


def test_a_cancelled_context_runs_no_post_tool_hook(tmp_path):
    d, marker = marker_dispatcher(tmp_path, "PostToolUse")
    asyncio.run(run_post_tool_use_hooks(cancelled_context(d), "Probe", {}, "out", "tu"))
    assert not marker.exists()


def test_a_cancelled_context_runs_no_failure_hook(tmp_path):
    d, marker = marker_dispatcher(tmp_path, "PostToolUseFailure")
    asyncio.run(run_post_tool_use_failure_hooks(cancelled_context(d), "Probe", {}, "boom", "tu"))
    assert not marker.exists()


def test_a_cancelled_context_runs_no_permission_request_hook(tmp_path):
    d, marker = marker_dispatcher(tmp_path, "PermissionRequest")
    res = asyncio.run(run_permission_request_hooks_for_headless_agent(
        probe(name="Bash"), {}, "tu", cancelled_context(d), PermissionMode.DEFAULT))
    assert res is None
    assert not marker.exists()


def test_a_cancelled_context_runs_no_stop_hook(tmp_path):
    d, marker = marker_dispatcher(tmp_path, "Stop")
    state = initial_loop_state((HumanMessage(content="x"),), cancelled_context(d))
    result = asyncio.run(handle_stop_hooks(state, state.messages, False))
    assert not marker.exists()
    assert result == StopHookResult()


def test_post_hook_output_reaches_the_outcome():
    d = ScriptedDispatcher(script={"PostToolUse": [AggregatedHookResult(additional_contexts=("note",))]})
    outcome = asyncio.run(run_post_tool_use_hooks(worker_context(d), "Probe", {}, "out", "tu"))
    assert [a.type for a in outcome.attachments] == ["hook_additional_context"]


def test_permission_request_skips_results_without_a_decision():
    d = ScriptedDispatcher(script={"PermissionRequest": [
        AggregatedHookResult(additional_contexts=("noise",)),
        AggregatedHookResult(permission_request_result={"behavior": "deny", "message": "second"}),
    ]})
    res = asyncio.run(run_permission_request_hooks_for_headless_agent(
        probe(name="Bash"), {}, "tu", worker_context(d), PermissionMode.DEFAULT))
    assert res is not None and res.message == "second"


# ── stop hooks ──────────────────────────────────────────────────────────────

def test_configured_stop_hooks_see_the_turn_and_the_latch():
    d = ScriptedDispatcher()
    context = tool_context_for(AppStateStore(state=AppState(phase_ledger=COMPLETE)), hook_dispatcher=d)
    state = initial_loop_state((HumanMessage(content="x"), AIMessage(content="final words")), context)
    from dataclasses import replace

    pending = build_stop_hooks_runner()(replace(state, stop_hook_active=True), CONFIG)
    asyncio.run(_await(pending))
    payload = d.payload("Stop")
    assert payload["last_assistant_message"] == "final words"
    assert payload["stop_hook_active"] is True


# ── session start ───────────────────────────────────────────────────────────

def test_session_start_context_is_recorded_as_a_session_start_attachment():
    d = ScriptedDispatcher(script={"SessionStart": [AggregatedHookResult(additional_contexts=("ctx",))]})
    outcome = asyncio.run(process_session_start_hooks(d, "startup"))
    assert outcome.messages[0].additional_kwargs["attachment"]["hookEvent"] == "SessionStart"


def test_prompt_context_is_recorded_as_a_user_prompt_submit_attachment():
    from app.utils.session_start import process_user_prompt_submit_hooks

    d = ScriptedDispatcher(script={"UserPromptSubmit": [AggregatedHookResult(additional_contexts=("ctx",))]})
    outcome = asyncio.run(process_user_prompt_submit_hooks(worker_context(d), "p", "default"))
    assert outcome.messages[0].additional_kwargs["attachment"]["hookEvent"] == "UserPromptSubmit"


# ── the tool pipeline ───────────────────────────────────────────────────────

def test_a_pre_hook_failure_stops_the_call_with_no_extra_messages():
    out = run(probe(), worker_context(ScriptedDispatcher(raises=RuntimeError("hook crashed"))))
    assert out.extra_messages == ()
    assert out.message.status == "error"


def test_pipeline_hooks_are_handed_the_call_id():
    async def boom(args: dict[str, Any], ctx: Any) -> ToolResult:
        raise RuntimeError("disk on fire")

    d = ScriptedDispatcher()
    run(probe(), worker_context(d), call_id="ok-1")
    run(probe(call=boom), worker_context(d), call_id="bad-1")
    assert d.payload("PostToolUse")["tool_use_id"] == "ok-1"
    assert d.payload("PostToolUseFailure")["tool_use_id"] == "bad-1"


def test_the_permission_request_is_handed_the_call_id():
    """A default-mode write is an 'ask'; headless, PermissionRequest hooks get the call id."""
    from app.tools.file_write_tool.file_write_tool import build_write_file_tool

    d = Recorder()
    context = worker_context(d, mode=PermissionMode.DEFAULT)
    tool = build_write_file_tool()
    asyncio.run(run_tool_use({"name": tool.name, "args": {"file_path": "tests/t.py", "content": "x"}, "id": "w-1"},
                             AIMessage(content=""), context, [tool]))
    assert ("PermissionRequest", "w-1", context.cancel) in d.calls


def test_a_schema_error_names_the_tool():
    tool = build_tool(name="Strict", prompt="p", call=lambda a, c: ToolResult(content=""),
                      input_schema={"type": "object", "properties": {"n": {"type": "integer"}}})
    out = asyncio.run(run_tool_use({"name": "Strict", "args": {"n": "x"}, "id": "c"}, AIMessage(content=""),
                                   worker_context(None), [tool]))
    assert "Strict failed due to the following issue" in str(out.message.content)


def test_an_mcp_tool_output_is_replaced_by_a_post_hook_and_others_are_not():
    replacement = {"rewritten": True}
    d = ScriptedDispatcher(script={"PostToolUse": [AggregatedHookResult(updated_mcp_tool_output=replacement)]})
    mcp = run(probe(name="mcp__srv__x", is_mcp=True), worker_context(d))
    assert mcp.message.content == json.dumps(replacement)
    plain = run(probe(), worker_context(d))
    assert plain.message.content == "real output"
    text = ScriptedDispatcher(script={"PostToolUse": [AggregatedHookResult(updated_mcp_tool_output="text")]})
    assert run(probe(name="mcp__srv__y", is_mcp=True), worker_context(text)).message.content == "text"


def test_the_stopped_continuation_record_names_the_call():
    d = ScriptedDispatcher(script={"PreToolUse": [AggregatedHookResult(prevent_continuation=True)]})
    out = run(probe(), worker_context(d), call_id="c-9")
    assert out.extra_messages[-1].additional_kwargs["attachment"]["toolUseID"] == "c-9"


# ── the runner ──────────────────────────────────────────────────────────────

class Model:
    def __init__(self, turns: list[list[Any]]) -> None:
        self.turns = turns
        self.seen: list[Any] = []

    async def __call__(self, state: Any, config: Any) -> AsyncIterator[Any]:
        self.seen.append((state, config))
        for m in self.turns.pop(0):
            yield m


def loop_deps(model: Model) -> LoopDeps:
    async def no_tools(calls: Any, msgs: Any, state: Any, config: Any) -> AsyncIterator[Any]:
        for c in calls:
            yield ToolMessage(content="ran", tool_call_id=c["id"])

    async def no_compact(state: Any, config: Any) -> CompactionResult:
        return CompactionResult(compacted=False, messages=state.messages)

    async def allow_stop(state: Any, config: Any) -> StopHookResult:
        return StopHookResult()

    return LoopDeps(call_model=model, run_tools=no_tools, compact=no_compact, stop_hooks=allow_stop,
                    uuid=lambda: str(uuid.uuid4()), now=time.time, emit_event=lambda e: None)


def test_the_runner_builds_its_dispatcher_from_the_run(monkeypatch, tmp_path):
    import app.utils.hooks.hook_dispatcher as runtime
    from app.cli.print import run_settings_locations, tdd_loop_runner
    from tests.conftest import FakeWorkspace

    project, home, managed, ws_root = (tmp_path / n for n in ("proj", "home", "etc", "ws"))
    for p in (project, home, managed, ws_root):
        p.mkdir()
    (ws_root / "sub").mkdir()
    (project / ".tddagents").mkdir()
    # An additional directory inside the workspace is skipped only when the permission
    # context is anchored at the workspace — which is how the runner builds it.
    (project / ".tddagents" / "settings.json").write_text(
        json.dumps({"permissions": {"additionalDirectories": [str(ws_root / "sub")]}}))
    seen: dict[str, Any] = {}
    real_build = runtime.build_hook_dispatcher

    def capture(locations: Any, **kw: Any) -> Any:
        seen.update(kw, locations=locations)
        fakes: dict[str, Any] = {"prompt_model_caller": object(), "agent_runner": object()}
        return real_build(locations, **kw, **fakes)

    monkeypatch.setattr(runtime, "build_hook_dispatcher", capture)
    ws = FakeWorkspace()
    ws.root = str(ws_root)  # type: ignore[attr-defined]
    model = Model([[AIMessage(content="done")]])
    asyncio.run(tdd_loop_runner("spec", 0, {"session_id": "s"}, workspace=ws, deps=loop_deps(model),
                                project_root=project, home=home, managed_dir=managed))
    state, config = model.seen[0]
    assert seen["locations"] == run_settings_locations(config, project, home, managed)
    assert seen["session_id"] == config.run_id
    assert seen["cwd"] == str(ws_root)
    assert seen["project_dir"] == str(project)
    assert seen["model"] == config.model
    from app.utils.session_storage_portable import get_transcript_path

    assert seen["transcript_path"] == str(get_transcript_path(config.run_id, str(ws_root)))
    perm = state.tool_context.get_app_state().tool_permission_context
    assert perm.additional_working_directories == ()


def test_a_stopped_prompt_reports_its_plan_item(monkeypatch, tmp_path):
    from app.cli.print import tdd_loop_runner
    from tests.conftest import FakeWorkspace

    d = ScriptedDispatcher(script={"UserPromptSubmit": [
        AggregatedHookResult(blocking_error=HookBlockingError(blocking_error="no", command="c"))]})
    result = asyncio.run(tdd_loop_runner("the spec", 7, {"session_id": "s"}, workspace=FakeWorkspace(),
                                         deps=loop_deps(Model([])), permission_context=ToolPermissionContext(),
                                         hook_dispatcher=d))
    assert (result.index, result.sub_requirement) == (7, "the spec")
    assert d.payload("SessionStart")["model"] == build_run_config("x", postgres_checkpointing=False).model
