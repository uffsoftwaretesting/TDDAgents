"""
Tests for `app/query/stop_hooks.py`: `handleStopHooks` and barrier 3.

The barrier tests drive `run_loop` end to end with a scripted model: whatever a configured
Stop hook says, an incomplete Red->Green cycle never terminates `completed`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.query.config import build_run_config
from app.query.deps import LoopDeps
from app.query.query import drain, run_loop
from app.query.state import CompactionResult, initial_loop_state
from app.query.stop_hooks import (
    StopHookResult,
    build_stop_hooks_runner,
    handle_stop_hooks,
)
from app.query.transitions import Terminal
from app.schemas.hooks import CommandHook, HookMatcher
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tool import tool_context_for
from app.types.hooks import AggregatedHookResult
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.hooks.hooks import HookDispatcher
from app.utils.hooks.hooks_config_snapshot import HooksConfigSnapshot
from app.utils.messages import HookBlockingError
from tests.hook_doubles import ScriptedDispatcher

CONFIG = build_run_config("stop-test", postgres_checkpointing=False)
RED = PhaseLedger(phase=TddPhase.RED)
DONE = PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True, refactor_passed=True)
BLOCK = HookBlockingError(blocking_error="lint first", command="lint.sh")
TDD_RED_MESSAGE = (
    "TDD cycle incomplete: RED is not done (tests_written=False). Write robust tests for this "
    "sub-requirement, covering every relevant scenario and edge case, and run RunTests."
)


def make_state(dispatcher: Any = None, ledger: PhaseLedger = RED, *, stop_hook_active: bool | None = None,
               agent_id: str | None = None, agent_type: str | None = None, messages: tuple[Any, ...] = ()) -> Any:
    store = AppStateStore(AppState(phase_ledger=ledger,
                                   tool_permission_context=ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)))
    ctx = tool_context_for(store, hook_dispatcher=dispatcher, agent_id=agent_id, agent_type=agent_type)
    state = initial_loop_state(messages or (HumanMessage(content="task"),), ctx)
    return replace(state, stop_hook_active=stop_hook_active)


def stop(script: list[AggregatedHookResult], **kw: Any) -> ScriptedDispatcher:
    return ScriptedDispatcher(script={"Stop": script, "SubagentStop": script}, **kw)


# ── handle_stop_hooks ───────────────────────────────────────────────────────

def test_blocking_errors_become_meta_feedback_messages():
    state = make_state(stop([AggregatedHookResult(blocking_error=BLOCK), AggregatedHookResult()]))
    result = asyncio.run(handle_stop_hooks(state, state.messages, False))
    assert result.prevent_continuation is False
    (msg,) = result.blocking_errors
    assert msg.content == "Stop hook feedback:\nlint first"
    assert msg.additional_kwargs == {"is_meta": True}


def test_continue_false_wins_over_blocks():
    state = make_state(stop([AggregatedHookResult(blocking_error=BLOCK),
                             AggregatedHookResult(prevent_continuation=True, stop_reason="halt")]))
    result = asyncio.run(handle_stop_hooks(state, state.messages, False))
    assert result == StopHookResult(prevent_continuation=True)


def test_abort_during_hooks_prevents_continuation():
    state = make_state(stop([AggregatedHookResult(blocking_error=BLOCK)]))
    state.tool_context.cancel.cancel()
    assert asyncio.run(handle_stop_hooks(state, state.messages, False)) == StopHookResult(prevent_continuation=True)


def test_an_exception_is_an_empty_result(caplog):
    state = make_state(stop([], raises=RuntimeError("down")))
    with caplog.at_level(logging.ERROR, logger="TDDOrchestrator.Hooks"):
        assert asyncio.run(handle_stop_hooks(state, state.messages, False)) == StopHookResult()
    assert "Stop hooks failed" in [r.getMessage() for r in caplog.records]


def test_no_dispatcher_is_an_empty_result():
    state = make_state(None)
    assert asyncio.run(handle_stop_hooks(state, state.messages, False)) == StopHookResult()


def test_the_hook_input_carries_active_flag_mode_and_last_assistant_text():
    d = stop([])
    msgs = (HumanMessage(content="task"), AIMessage(content="  all done  "))
    state = make_state(d)
    asyncio.run(handle_stop_hooks(state, msgs, True))
    payload = d.payload("Stop")
    assert payload["stop_hook_active"] is True
    assert payload["last_assistant_message"] == "all done"
    assert payload["permission_mode"] == "acceptEdits"


def test_permission_mode_absent_without_app_state():
    d = stop([])
    state = make_state(d)

    def broken() -> Any:
        raise RuntimeError("no state")

    state = replace(state, tool_context=replace(state.tool_context, get_app_state=broken))
    asyncio.run(handle_stop_hooks(state, state.messages, False))
    assert "permission_mode" not in d.payload("Stop")


# ── build_stop_hooks_runner: barrier 3 ───────────────────────────────────────

def run_stop(state: Any) -> StopHookResult:
    async def go() -> StopHookResult:
        return await build_stop_hooks_runner()(state, CONFIG)

    return asyncio.run(go())


def test_tdd_hook_blocks_first_and_configured_blocks_are_added():
    d = stop([AggregatedHookResult(blocking_error=BLOCK)])
    result = run_stop(make_state(d))
    assert result.prevent_continuation is False
    assert [m.content for m in result.blocking_errors] == [TDD_RED_MESSAGE, "Stop hook feedback:\nlint first"]


def test_the_latch_prevents_without_running_configured_hooks():
    d = stop([AggregatedHookResult(blocking_error=BLOCK)])
    result = run_stop(make_state(d, stop_hook_active=True))
    assert result == StopHookResult(prevent_continuation=True)
    assert d.seen == []


def test_configured_continue_false_stops_rather_than_completes():
    d = stop([AggregatedHookResult(prevent_continuation=True)])
    assert run_stop(make_state(d)) == StopHookResult(prevent_continuation=True)


def test_a_complete_cycle_still_obeys_configured_blocks():
    d = stop([AggregatedHookResult(blocking_error=BLOCK)])
    result = run_stop(make_state(d, DONE))
    assert [m.content for m in result.blocking_errors] == ["Stop hook feedback:\nlint first"]
    assert run_stop(make_state(stop([]), DONE)) == StopHookResult()


def test_a_worker_skips_the_tdd_hook_and_fires_subagent_stop():
    d = stop([])
    result = run_stop(make_state(d, agent_id="agent_1", agent_type="tester"))
    assert result == StopHookResult()
    (event, payload), = d.seen
    assert event == "SubagentStop"
    assert payload["agent_id"] == "agent_1"
    assert payload["agent_type"] == "tester"
    assert payload["agent_transcript_path"] == ""


def test_a_worker_with_no_agent_type_reports_an_empty_one():
    d = stop([])
    run_stop(make_state(d, agent_id="agent_1"))
    assert d.payload("SubagentStop")["agent_type"] == ""


class Model:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, state: Any, config: Any) -> AsyncIterator[Any]:
        self.calls += 1
        yield AIMessage(content="I am done.")


def loop_deps(model: Model) -> LoopDeps:
    async def no_tools(*a: Any) -> AsyncIterator[Any]:
        return
        yield  # pragma: no cover

    async def no_compact(state: Any, config: Any) -> CompactionResult:
        return CompactionResult(compacted=False, messages=state.messages)

    return LoopDeps(call_model=model, run_tools=no_tools, compact=no_compact, stop_hooks=build_stop_hooks_runner(),
                    uuid=lambda: str(uuid.uuid4()), now=time.time, emit_event=lambda e: None)


@pytest.mark.parametrize("script, model_calls", [
    ([AggregatedHookResult(permission_behavior="allow")], 2),  # "approves": TDD blocks, then the latch
    ([], 2),  # says nothing: TDD blocks, then the latch
    ([AggregatedHookResult(prevent_continuation=True, stop_reason="ship it")], 1),  # continue: false stops at once
])
def test_no_configured_hook_completes_an_incomplete_cycle(script, model_calls):
    model = Model()
    state = make_state(stop(script))
    terminal = asyncio.run(drain(run_loop(state, CONFIG, loop_deps(model))))
    assert terminal.reason == Terminal.STOP_HOOK_PREVENTED
    assert model.calls == model_calls  # never `completed`


def test_a_complete_cycle_with_a_silent_hook_completes():
    model = Model()
    terminal = asyncio.run(drain(run_loop(make_state(stop([]), DONE), CONFIG, loop_deps(model))))
    assert terminal.reason == Terminal.COMPLETED
    assert model.calls == 1


def test_a_real_command_hook_that_approves_cannot_complete_red(tmp_path: Path):
    script = tmp_path / "approve.py"
    script.write_text(f"print({json.dumps(json.dumps({'decision': 'approve'}))})")
    hook = CommandHook(command=f"{sys.executable} {script}")
    snapshot = HooksConfigSnapshot(hooks={"Stop": (HookMatcher(hooks=(hook,)),)})
    dispatcher = HookDispatcher(snapshot=snapshot, session_id="s", cwd=str(tmp_path), project_dir=str(tmp_path))
    model = Model()
    terminal = asyncio.run(drain(run_loop(make_state(dispatcher), CONFIG, loop_deps(model))))
    assert terminal.reason == Terminal.STOP_HOOK_PREVENTED
    assert model.calls == 2


def test_a_real_command_hook_exit_2_blocks_a_complete_cycle_once(tmp_path: Path):
    script = tmp_path / "block.py"
    script.write_text("import sys; sys.stderr.write('format the code'); sys.exit(2)")
    hook = CommandHook(command=f"{sys.executable} {script}")
    snapshot = HooksConfigSnapshot(hooks={"Stop": (HookMatcher(hooks=(hook,)),)})
    dispatcher = HookDispatcher(snapshot=snapshot, session_id="s", cwd=str(tmp_path), project_dir=str(tmp_path))
    state = make_state(dispatcher, DONE)
    result = run_stop(state)
    (msg,) = result.blocking_errors
    assert msg.content == f"Stop hook feedback:\n[{hook.command}]: format the code"
