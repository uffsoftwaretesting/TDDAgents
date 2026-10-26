"""
Integration tests for where Phase 4's hooks are called from: the tool pipeline, the tool
runners, the engine, the runner, compaction, workers and the request builder.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.loop.config import build_run_config
from app.loop.context.app_state import AppStateStore
from app.loop.context.tool_context import tool_context_for
from app.loop.deps import CompactionResult, LoopDeps, StopHookResult
from app.loop.engine import drain, run_loop
from app.loop.hooks.messages import HookBlockingError
from app.loop.hooks.output import AggregatedHookResult
from app.loop.permissions.types import ToolPermissionContext
from app.loop.state import initial_loop_state
from app.loop.tools.base import build_tool
from app.loop.tools.execution import CANCEL_MESSAGE, run_tool_use
from app.loop.tools.orchestration import run_tools
from app.loop.tools.types import ToolResult
from app.loop.transitions import Terminal
from tests.hook_doubles import ScriptedDispatcher

CONFIG = build_run_config("sites", postgres_checkpointing=False)

#: The first TDD-state announcement a fresh run attaches after the prompt (Phase 5).
INITIAL_TDD_STATE = (
    "<system-reminder>\nTDD phase ledger (written only by RunTests from observed test runs):\n"
    "Current Phase: RED\nRed Confirmed: False\nGreen Passed: False\n</system-reminder>"
)


def reminder(text: str) -> str:
    return f"<system-reminder>\n{text}\n</system-reminder>"


def probe(call: Any = None, name: str = "Probe") -> Any:
    async def ok(args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(content=f"ran {args.get('n', '')}".strip())

    return build_tool(name=name, prompt="p", call=call or ok, input_schema={"type": "object"},
                      is_concurrency_safe=lambda a: True)


def ctx(dispatcher: Any, tools: tuple[Any, ...] = (), **kw: Any) -> Any:
    return tool_context_for(AppStateStore(), hook_dispatcher=dispatcher, tools=tools,
                            permission_context=ToolPermissionContext(should_avoid_permission_prompts=True), **kw)


def execute(tool: Any, dispatcher: Any, call_id: str = "c1", c: Any = None) -> Any:
    context = c if c is not None else ctx(dispatcher)
    return asyncio.run(run_tool_use({"name": tool.name, "args": {}, "id": call_id}, AIMessage(content=""),
                                    context, [tool]))


# ── tools/execution.py ──────────────────────────────────────────────────────

def test_hook_messages_follow_the_result_pre_then_post_then_stop():
    d = ScriptedDispatcher(script={
        "PreToolUse": [AggregatedHookResult(additional_contexts=("pre",)),
                       AggregatedHookResult(prevent_continuation=True, stop_reason="halt")],
        "PostToolUse": [AggregatedHookResult(additional_contexts=("post",))],
    })
    out = execute(probe(), d)
    assert out.message.content == "ran"
    assert [m.content for m in out.extra_messages] == [
        reminder("PreToolUse:Probe hook additional context: pre"),
        reminder("PostToolUse:Probe hook additional context: post"),
        reminder("PreToolUse:Probe hook stopped continuation: halt"),
    ]
    assert out.extra_messages[2].additional_kwargs["attachment"]["hookEvent"] == "PreToolUse"


def test_a_stop_without_a_reason_has_the_default_record():
    d = ScriptedDispatcher(script={"PreToolUse": [AggregatedHookResult(prevent_continuation=True)]})
    out = execute(probe(), d)
    assert [m.content for m in out.extra_messages] == [
        reminder("PreToolUse:Probe hook stopped continuation: Execution stopped by hook")]


def test_a_raising_tool_runs_failure_hooks_not_post_hooks():
    async def boom(args: dict[str, Any], c: Any) -> ToolResult:
        raise RuntimeError("disk on fire")

    d = ScriptedDispatcher(script={
        "PreToolUse": [AggregatedHookResult(additional_contexts=("pre",))],
        "PostToolUseFailure": [AggregatedHookResult(additional_contexts=("failed",))],
    })
    out = execute(probe(boom), d)
    assert out.message.content == "<tool_use_error>Error calling tool (Probe): disk on fire</tool_use_error>"
    assert out.message.status == "error"
    assert [e for e, _ in d.seen] == ["PreToolUse", "PostToolUseFailure"]
    assert d.payload("PostToolUseFailure")["error"] == "disk on fire"
    assert d.payload("PostToolUseFailure")["is_interrupt"] is False
    assert [m.content for m in out.extra_messages] == [
        reminder("PreToolUse:Probe hook additional context: pre"),
        reminder("PostToolUseFailure:Probe hook additional context: failed"),
    ]


def test_a_failure_while_cancelled_is_an_interrupt():
    seen: dict[str, Any] = {}

    async def cancelled(args: dict[str, Any], c: Any) -> ToolResult:
        c.cancel.cancel()
        raise RuntimeError("aborted")

    d = ScriptedDispatcher()
    out = execute(probe(cancelled), d)
    seen.update(d.payload("PostToolUseFailure"))
    assert seen["is_interrupt"] is True
    assert out.extra_messages == ()


def test_the_deny_path_keeps_hook_messages():
    d = ScriptedDispatcher(script={"PreToolUse": [
        AggregatedHookResult(additional_contexts=("why",)),
        AggregatedHookResult(blocking_error=HookBlockingError(blocking_error="no", command="c")),
    ]})
    out = execute(probe(), d)
    assert out.message.content == "PreToolUse:Probe hook error: no"
    assert out.message.additional_kwargs["permission_behavior"] == "deny"
    assert [m.content for m in out.extra_messages] == [reminder("PreToolUse:Probe hook additional context: why")]
    assert [e for e, _ in d.seen] == ["PreToolUse"]


def test_a_deny_without_a_message_names_the_hook_stop(monkeypatch):
    import app.loop.tools.execution as execution_mod
    from app.loop.permissions.types import PermissionBehavior, PermissionResult

    async def silent_deny(*a: Any, **k: Any) -> Any:
        return SimpleNamespace(decision=PermissionResult(behavior=PermissionBehavior.DENY, message=""), input={})

    monkeypatch.setattr(execution_mod, "resolve_hook_permission_decision", silent_deny)
    d = ScriptedDispatcher(script={"PreToolUse": [AggregatedHookResult(prevent_continuation=True, stop_reason="x")]})
    assert execute(probe(), d).message.content == "Execution stopped by PreToolUse hook: x"
    d = ScriptedDispatcher(script={"PreToolUse": [AggregatedHookResult(prevent_continuation=True)]})
    assert execute(probe(), d).message.content == "Execution stopped by PreToolUse hook"
    d = ScriptedDispatcher()
    assert execute(probe(), d).message.content == "Permission denied for tool: Probe"


def test_a_stopped_pre_hook_answers_with_the_cancel_message():
    d = ScriptedDispatcher(raises=RuntimeError("hook crash"))
    out = execute(probe(), d)
    assert out.message.content == CANCEL_MESSAGE
    assert out.message.status == "error"
    assert out.message.additional_kwargs == {"is_error": True, "tool_use_result": "Error: None"}
    assert [e for e, _ in d.seen] == ["PreToolUse"]  # the tool never ran, no PostToolUse


# ── orchestration and the streaming executor ─────────────────────────────────

def _calls(*ids: str) -> tuple[Any, ...]:
    return tuple({"name": "Probe", "args": {"n": i}, "id": i} for i in ids)


def _post_dispatcher() -> ScriptedDispatcher:
    return ScriptedDispatcher(script={"PostToolUse": [AggregatedHookResult(additional_contexts=("note",))]})


def _assert_each_result_is_followed_by_its_note(out: list[Any], ids: tuple[str, ...]) -> None:
    assert len(out) == 2 * len(ids)
    for i in range(0, len(out), 2):
        assert isinstance(out[i], ToolMessage)
        assert out[i + 1].content == reminder("PostToolUse:Probe hook additional context: note")
    assert sorted(m.tool_call_id for m in out[::2]) == sorted(ids)


def test_serial_and_concurrent_batches_emit_extras_after_each_result():
    def ok(args: dict[str, Any], c: Any) -> ToolResult:
        return ToolResult(content="ok")

    for safe in (True, False):
        tool = build_tool(name="Probe", prompt="p", input_schema={"type": "object"}, call=ok,
                          is_concurrency_safe=(lambda a: True) if safe else (lambda a: False))
        d = _post_dispatcher()
        context = ctx(d, tools=(tool,))
        state = initial_loop_state((HumanMessage(content="t"),), context)

        async def collect() -> list[Any]:
            return [m async for m in run_tools(_calls("a", "b"), (AIMessage(content=""),), state, CONFIG)]  # noqa: B023

        _assert_each_result_is_followed_by_its_note(asyncio.run(collect()), ("a", "b"))


def test_the_streaming_executor_emits_extras_after_each_result():
    from app.loop.streaming.abort import AbortController
    from app.loop.streaming.executor import StreamingToolExecutor

    tool = probe()
    d = _post_dispatcher()
    context = ctx(d, tools=(tool,))

    async def go() -> list[Any]:
        ex = StreamingToolExecutor(tools=(tool,), tool_context=context, turn_abort_controller=AbortController())
        for call in _calls("a", "b"):
            ex.add_tool(call, AIMessage(content=""))
        return [m async for m in ex.get_remaining_results()]

    _assert_each_result_is_followed_by_its_note(asyncio.run(go()), ("a", "b"))


# ── engine ──────────────────────────────────────────────────────────────────

class Model:
    def __init__(self, turns: list[list[Any]]) -> None:
        self.turns = turns
        self.seen: list[Any] = []

    async def __call__(self, state: Any, config: Any) -> AsyncIterator[Any]:
        self.seen.append(state)
        for m in self.turns.pop(0):
            yield m


def deps(model: Model, tools: Any = None) -> LoopDeps:
    async def one_result(calls: Any, msgs: Any, state: Any, config: Any) -> AsyncIterator[Any]:
        for c in calls:
            yield ToolMessage(content="ran", tool_call_id=c["id"])

    async def no_compact(state: Any, config: Any) -> CompactionResult:
        return CompactionResult(compacted=False, messages=state.messages)

    async def allow_stop(state: Any, config: Any) -> StopHookResult:
        return StopHookResult()

    return LoopDeps(call_model=model, run_tools=tools or one_result, compact=no_compact, stop_hooks=allow_stop,
                    uuid=lambda: str(uuid.uuid4()), now=time.time, emit_event=lambda e: None)


def asks(call_id: str = "c1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "Probe", "args": {}, "id": call_id}])


class FakeCapture:
    def __init__(self, stdout: str, code: int = 0, stderr: str = "") -> None:
        self.status = "completed"
        self.stdout, self.stderr, self.exit_code = stdout, stderr, code

    async def result(self) -> int:
        return self.exit_code


def test_async_hook_responses_and_rewake_notes_follow_the_tool_results():
    d = ScriptedDispatcher()

    async def go() -> Any:
        d.registry.register_pending(
            process_id="p1", hook_id="h1", async_response={"async": True}, hook_name="PostToolUse:Probe",
            hook_event="PostToolUse", command="bg.sh",
            capture=FakeCapture('{"async":true}\n{"systemMessage":"bg done"}\n'),  # type: ignore[arg-type]
        )
        d.registry.register_rewake(hook_name="Stop", capture=FakeCapture("", 2, "wake up"))  # type: ignore[arg-type]
        await asyncio.sleep(0)
        model = Model([[asks()], [AIMessage(content="done")]])
        state = initial_loop_state((HumanMessage(content="t"),), ctx(d))
        events = [e async for e in run_loop(state, CONFIG, deps(model))]
        return model, events

    model, events = asyncio.run(go())
    second = list(model.seen[1].messages)
    last_result = max(i for i, m in enumerate(second) if isinstance(m, ToolMessage))
    after = second[last_result + 1:]
    by_type = {m.additional_kwargs["attachment"]["type"]: m for m in after}
    # The async response is an attachment of the turn, after its results (`getAttachmentMessages`).
    assert by_type["async_hook_response"].content == reminder("bg done")
    assert by_type["async_hook_response"].additional_kwargs["attachment"]["processId"] == "p1"
    # The rewake note is a `later` notification (`enqueuePendingNotification`): never drained
    # mid-turn, it waits in the run's queue for the runner to deliver as a turn of its own.
    assert "queued_command" not in by_type
    (note,) = d.registry.queue.snapshot()
    assert 'Stop hook blocking error from command "Stop": wake up' in note.value
    assert (note.mode, note.priority) == ("task-notification", "later")
    assert events[-1].reason == Terminal.COMPLETED


def test_no_dispatcher_delivers_nothing_extra():
    model = Model([[asks()], [AIMessage(content="done")]])
    state = initial_loop_state((HumanMessage(content="t"),), tool_context_for(AppStateStore()))
    asyncio.run(drain(run_loop(state, CONFIG, deps(model))))
    second = list(model.seen[1].messages)
    last_result = max(i for i, m in enumerate(second) if isinstance(m, ToolMessage))
    kinds = [m.additional_kwargs["attachment"]["type"] for m in second[last_result + 1:]]
    assert kinds == ["tdd_state"]  # no hook output, only the ledger's first announcement


def test_stop_failure_fires_on_an_api_error():
    d = ScriptedDispatcher()
    err = AIMessage(content="rate limited", additional_kwargs={
        "is_api_error_message": True, "api_error": "rate_limit", "error_details": "429 slow down"})
    state = initial_loop_state((HumanMessage(content="t"),), ctx(d))
    terminal = asyncio.run(drain(run_loop(state, CONFIG, deps(Model([[err]])))))
    assert terminal.reason == Terminal.MODEL_ERROR
    payload = d.payload("StopFailure")
    assert payload["error"] == "rate_limit"
    assert payload["error_details"] == "429 slow down"
    assert payload["last_assistant_message"] == "rate limited"
    assert "Stop" not in [e for e, _ in d.seen]


def test_stop_failure_defaults_to_unknown_error():
    d = ScriptedDispatcher()
    err = AIMessage(content="", additional_kwargs={"is_api_error_message": True})
    state = initial_loop_state((HumanMessage(content="t"),), ctx(d))
    asyncio.run(drain(run_loop(state, CONFIG, deps(Model([[err]])))))
    payload = d.payload("StopFailure")
    assert payload["error"] == "unknown"
    assert "error_details" not in payload and "last_assistant_message" not in payload


def test_stop_failure_fires_on_an_unrecovered_prompt_too_long():
    d = ScriptedDispatcher()
    ptl = AIMessage(content="too long", additional_kwargs={"api_error": "prompt_too_long"})
    state = initial_loop_state((HumanMessage(content="t"),), ctx(d))
    terminal = asyncio.run(drain(run_loop(state, CONFIG, deps(Model([[ptl]])))))
    assert terminal.reason == Terminal.PROMPT_TOO_LONG
    assert d.payload("StopFailure")["error"] == "prompt_too_long"


# ── runner ──────────────────────────────────────────────────────────────────

def without_skill_listing(messages: Any) -> list[Any]:
    """The first turn minus the bundled skills' listing (Phase 8), which these tests do not pin."""
    return [m for m in messages
            if (m.additional_kwargs.get("attachment") or {}).get("type") != "skill_listing"]


def run_runner(monkeypatch, tmp_path: Path, d: ScriptedDispatcher, model: Model) -> Any:
    from app.loop.runner import tdd_loop_runner
    from tests.conftest import FakeWorkspace

    finalized: list[bool] = []
    original = d.registry.finalize_pending

    async def spy() -> None:
        finalized.append(True)
        await original()

    monkeypatch.setattr(d.registry, "finalize_pending", spy)
    result = asyncio.run(tdd_loop_runner(
        "build it", 0, {"session_id": "s"}, workspace=FakeWorkspace(), deps=deps(model),
        permission_context=ToolPermissionContext(), hook_dispatcher=d))
    return result, finalized


def test_runner_places_hook_messages_around_the_prompt(monkeypatch, tmp_path):
    d = ScriptedDispatcher(script={
        "SessionStart": [AggregatedHookResult(additional_contexts=("session ctx",), initial_user_message="hello")],
        "UserPromptSubmit": [AggregatedHookResult(additional_contexts=("prompt ctx",))],
    })
    model = Model([[AIMessage(content="done")]])
    result, finalized = run_runner(monkeypatch, tmp_path, d, model)
    first = model.seen[0].messages
    assert [m.content for m in without_skill_listing(first)] == [
        reminder("SessionStart hook additional context: session ctx"),
        "hello",
        "build it",
        reminder("UserPromptSubmit hook additional context: prompt ctx"),
        INITIAL_TDD_STATE,
    ]
    assert model.seen[0].tool_context.messages == first
    assert d.payload("SessionStart")["source"] == "startup"
    assert d.payload("UserPromptSubmit")["prompt"] == "build it"
    assert d.payload("UserPromptSubmit")["permission_mode"] == "default"
    assert d.payload("SessionEnd")["reason"] == "other"
    assert finalized == [True]
    assert result.terminal_reason == "completed"


def test_runner_without_hook_output_starts_from_the_prompt(monkeypatch, tmp_path):
    model = Model([[AIMessage(content="done")]])
    run_runner(monkeypatch, tmp_path, ScriptedDispatcher(), model)
    assert [m.content for m in without_skill_listing(model.seen[0].messages)] == ["build it", INITIAL_TDD_STATE]


def test_a_blocked_prompt_never_reaches_the_model(monkeypatch, tmp_path):
    d = ScriptedDispatcher(script={"UserPromptSubmit": [
        AggregatedHookResult(blocking_error=HookBlockingError(blocking_error="nope", command="c"))]})
    model = Model([])
    result, finalized = run_runner(monkeypatch, tmp_path, d, model)
    assert model.seen == []
    assert (result.status, result.terminal_reason) == ("failed", "hook_stopped")
    assert result.error_message == (
        "UserPromptSubmit operation blocked by hook:\nnope\n\nOriginal prompt: build it")
    assert (result.red_confirmed, result.green_passed) == (False, False)
    assert d.payload("SessionEnd")["reason"] == "other"
    assert finalized == [True]


def test_a_stopped_prompt_never_reaches_the_model(monkeypatch, tmp_path):
    d = ScriptedDispatcher(script={"UserPromptSubmit": [
        AggregatedHookResult(prevent_continuation=True, stop_reason="later")]})
    model = Model([])
    result, _ = run_runner(monkeypatch, tmp_path, d, model)
    assert model.seen == []
    assert result.terminal_reason == "hook_stopped"
    assert result.error_message == "Operation stopped by hook: later"


def test_run_settings_locations(tmp_path):
    from app.loop.permissions.settings import MANAGED_DIR, SettingsLocations
    from app.loop.runner import run_settings_locations

    flagged = replace(CONFIG, settings_path=str(tmp_path / "flag.json"))
    assert run_settings_locations(flagged, tmp_path, tmp_path / "home", tmp_path / "etc") == SettingsLocations(
        project_root=tmp_path, home=tmp_path / "home", flag_settings_path=tmp_path / "flag.json",
        managed_dir=tmp_path / "etc")
    assert run_settings_locations(CONFIG, tmp_path) == SettingsLocations(
        project_root=tmp_path, home=None, flag_settings_path=None, managed_dir=Path(MANAGED_DIR))


# ── workers and the request builder ─────────────────────────────────────────

def test_the_request_builder_reorders_hook_messages_after_tool_results(monkeypatch):
    import app.loop.model as model_mod

    note = HumanMessage(content="n", additional_kwargs={"is_meta": True, "attachment": {"type": "x"}})
    call = AIMessage(content="", tool_calls=[{"name": "Probe", "args": {}, "id": "a"},
                                             {"name": "Probe", "args": {}, "id": "b"}])
    history = (HumanMessage(content="t"), call, ToolMessage(content="1", tool_call_id="a"), note,
               ToolMessage(content="2", tool_call_id="b"))
    monkeypatch.setattr(model_mod, "get_session_context", lambda run_id, ws: SimpleNamespace(
        section_cache=None, system_context={}, user_context={}))
    monkeypatch.setattr(model_mod, "resolve_system_prompt_sections", lambda sections, cache: [])
    monkeypatch.setattr(model_mod, "get_system_prompt", lambda ws, m, mcp: [])
    monkeypatch.setattr(model_mod, "prepend_user_context", lambda msgs, ctx: list(msgs))
    state = initial_loop_state(history, tool_context_for(AppStateStore()))
    request = model_mod.build_request_messages(state, CONFIG)
    assert isinstance(request[0], SystemMessage)
    body = [m for m in request[1:] if not (getattr(m, "additional_kwargs", {}) or {}).get("tdd_state")]
    kinds = [getattr(m, "tool_call_id", None) or m.content for m in body[:5]]
    assert kinds == ["t", "", "a", "b", "n"]


# ── Phase 4 divergences resolved toward upstream ─────────────────────────────

def test_an_exception_in_the_permission_phase_becomes_a_tool_use_error_and_the_run_goes_on():
    """
    `runToolUse`'s outer `catch`: the `AbortError` `hasPermissionsToUseTool` throws when a
    headless auto-mode run hits the denial limit is reported as the call's error result. It
    does not cancel the run.
    """
    import asyncio as _asyncio
    import dataclasses

    from langchain_core.messages import AIMessage as _AI

    from app.loop.context.app_state import AppState
    from app.loop.context.app_state import AppStateStore as _Store
    from app.loop.context.tool_context import tool_context_for as _ctx
    from app.loop.permissions.denial_tracking import DenialTrackingState
    from app.loop.permissions.types import (
        PermissionBehavior,
        PermissionMode,
        PermissionResult,
        ToolPermissionContext,
    )
    from app.loop.permissions.yolo_classifier import YoloClassifierResult
    from app.loop.tools.base import build_tool as _build
    from app.loop.tools.execution import run_tool_use as _run
    from app.loop.tools.types import ToolResult as _Result

    async def block(*args, **kwargs):
        return YoloClassifierResult(should_block=True, reason="risky", model="m")

    tool = _build(
        name="Bash", prompt="p", call=lambda a, c: _Result(content="ran"),
        check_permissions=lambda a, c: PermissionResult(behavior=PermissionBehavior.ASK, message="ask?"),
    )
    perm = ToolPermissionContext(mode=PermissionMode.AUTO, should_avoid_permission_prompts=True)
    store = _Store(AppState(tool_permission_context=perm, denial_tracking=DenialTrackingState(2, 2)))
    ctx = dataclasses.replace(_ctx(store), auto_mode_classifier=block)
    call = {"id": "c1", "name": "Bash", "args": {"command": "x"}}
    outcome = _asyncio.run(_run(call, _AI(content=""), ctx, [tool]))  # type: ignore[arg-type]
    assert outcome.message.content == (
        "<tool_use_error>Error calling tool (Bash): "
        "Agent aborted: too many classifier denials in headless mode</tool_use_error>"
    )
    assert outcome.message.additional_kwargs == {
        "is_error": True,
        "tool_use_result": "Error calling tool (Bash): Agent aborted: too many classifier denials in headless mode",
    }
    assert ctx.cancel.cancelled is False


def test_stop_failure_hooks_are_fire_and_forget_but_finish_before_the_run_ends():
    """`void executeStopFailureHooks(...)`: the turn ends without waiting; the dispatcher's
    background work is drained when the run finalizes."""
    import asyncio as _asyncio

    from langchain_core.messages import AIMessage as _AI
    from langchain_core.messages import HumanMessage as _Human

    from app.loop.config import build_run_config
    from app.loop.context.app_state import AppStateStore as _Store
    from app.loop.context.tool_context import tool_context_for as _ctx
    from app.loop.engine import drain, run_loop
    from app.loop.state import initial_loop_state
    from app.loop.transitions import Terminal
    from tests.hook_doubles import ScriptedDispatcher

    class SlowOutside(ScriptedDispatcher):
        def __init__(self) -> None:
            super().__init__()
            self.finished = False

        async def execute_hooks_outside_repl(self, hook_input, **kwargs):
            await _asyncio.sleep(0.2)
            self.finished = True
            return []

    async def model(state, config):
        yield _AI(content="", additional_kwargs={"is_api_error_message": True, "api_error": "overloaded"})

    async def go():
        dispatcher = SlowOutside()
        ctx = _ctx(_Store(), hook_dispatcher=dispatcher)
        state = initial_loop_state((_Human(content="t"),), ctx)
        import dataclasses

        from app.loop.factory import get_production_deps

        deps = dataclasses.replace(get_production_deps(), call_model=model)
        terminal = await drain(run_loop(state, build_run_config("r", postgres_checkpointing=False), deps))
        assert terminal.reason == Terminal.MODEL_ERROR
        assert dispatcher.finished is False  # the turn did not wait for it
        await dispatcher.drain_background()
        assert dispatcher.finished is True

    _asyncio.run(go())
