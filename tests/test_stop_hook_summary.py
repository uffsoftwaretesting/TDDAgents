"""
The stop-hook summary ("Ran N stop hooks"): `executeHooks`' progress items, `handleStopHooks`'
bookkeeping (`reference/claude-code/src/query/stopHooks.ts`), the loop yielding it, and the REPL's
rendering (`components/messages/SystemTextMessage.tsx` -> `StopHookSummaryMessage`).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.components.messages.collapsed_read_search_content import CollapsedReadSearchRenderer
from app.components.messages.system_text_message import render_stop_hook_summary
from app.query.query import run_loop
from app.query.stop_hooks import StopHookResult, handle_stop_hooks
from app.query.transitions import Terminal
from app.schemas.hooks import CommandHook, HookMatcher, PromptHook
from app.types.hooks import AggregatedHookResult, HookProgress
from app.utils.hooks.hooks import HookDispatcher
from app.utils.hooks.hooks_config_snapshot import HooksConfigSnapshot
from app.utils.messages import (
    HookAttachment,
    HookBlockingError,
    StopHookInfo,
    StopHookSummaryMessage,
    create_stop_hook_summary_message,
)
from tests.hook_doubles import ScriptedDispatcher
from tests.test_loop_hooks_stop_hooks import CONFIG, DONE, Model, loop_deps, make_state


def _dispatcher(tmp_path: Path, *hooks: Any) -> HookDispatcher:
    snapshot = HooksConfigSnapshot(hooks={"Stop": (HookMatcher(hooks=hooks),)})
    return HookDispatcher(snapshot=snapshot, session_id="s", cwd=str(tmp_path), project_dir=str(tmp_path))


def _script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(body)
    return f"{sys.executable} {path}"


def _collect(stream: Any) -> list[Any]:
    async def run() -> list[Any]:
        return [item async for item in stream]

    return asyncio.run(run())


# ── executeHooks' progress ───────────────────────────────────────────────────

def test_progress_comes_first_for_each_matched_hook_only_when_asked(tmp_path: Path) -> None:
    ok = CommandHook(command=_script(tmp_path, "ok.py", "print('fine')"), status_message="Checking")
    prompt = PromptHook(prompt="is it done?")
    dispatcher = _dispatcher(tmp_path, ok)
    dispatcher.snapshot = HooksConfigSnapshot(hooks={"Stop": (HookMatcher(hooks=(ok, prompt)),)})

    async def judge(*_args: Any) -> Any:
        from app.types.hooks import HookResult

        return HookResult(hook=prompt, outcome="success")

    dispatcher.prompt_executor = judge
    items = _collect(dispatcher.execute_hooks({"hook_event_name": "Stop"}, "t", with_progress=True))
    assert [item.progress for item in items[:2]] == [
        HookProgress(hook_event="Stop", hook_name="Stop", command="Checking", status_message="Checking"),
        HookProgress(hook_event="Stop", hook_name="Stop", command="is it done?", prompt_text="is it done?"),
    ]
    assert all(item.progress is None for item in items[2:])
    assert all(item.progress is None for item in _collect(dispatcher.execute_hooks({"hook_event_name": "Stop"}, "t")))


# ── handleStopHooks' summary ─────────────────────────────────────────────────

def test_a_failing_hook_is_summarized_with_its_error(tmp_path: Path) -> None:
    failing = CommandHook(command=_script(tmp_path, "fail.py", "import sys; sys.stderr.write('boom'); sys.exit(1)"))
    quiet = CommandHook(command=_script(tmp_path, "quiet.py", "pass"))
    state = make_state(_dispatcher(tmp_path, failing, quiet))
    result = asyncio.run(handle_stop_hooks(state, state.messages, False))
    (summary,) = result.messages
    assert isinstance(summary, StopHookSummaryMessage) and summary.subtype == "stop_hook_summary"
    assert summary.hook_count == 2 and summary.level == "suggestion"
    assert summary.hook_errors == ("Failed with non-blocking status code: boom",)
    assert summary.has_output and not summary.prevented_continuation
    assert [info.command for info in summary.hook_infos] == [failing.command, quiet.command]
    assert all(info.duration_ms is not None for info in summary.hook_infos)
    assert summary.tool_use_id


def test_a_blocking_hook_and_a_stopping_hook_are_summarized(tmp_path: Path) -> None:
    blocking = CommandHook(command=_script(tmp_path, "block.py",
                                           "import sys; sys.stderr.write('lint first'); sys.exit(2)"))
    state = make_state(_dispatcher(tmp_path, blocking), DONE)
    result = asyncio.run(handle_stop_hooks(state, state.messages, False))
    (summary,) = result.messages
    assert summary.hook_errors == (f"[{blocking.command}]: lint first",) and summary.has_output
    assert len(result.blocking_errors) == 1

    stopping = CommandHook(command=_script(tmp_path, "stop.py",
                                           "print('{\"continue\": false, \"stopReason\": \"ship it\"}')"))
    state = make_state(_dispatcher(tmp_path, stopping))
    result = asyncio.run(handle_stop_hooks(state, state.messages, False))
    assert result.prevent_continuation and result.blocking_errors == ()
    (summary,) = result.messages
    assert summary.prevented_continuation and summary.stop_reason == "ship it"


def test_the_bookkeeping_follows_upstream() -> None:
    progress = [AggregatedHookResult(progress=HookProgress("Stop", "Stop", "a")),
                AggregatedHookResult(progress=HookProgress("Stop", "Stop", "a")),
                AggregatedHookResult(progress=HookProgress("Stop", "Stop", ""))]
    attachments = [
        AggregatedHookResult(attachment=HookAttachment(type="hook_success", hook_name="Stop", tool_use_id="t1",
                                                       hook_event="Stop", stdout="  ", command="a", duration_ms=5)),
        AggregatedHookResult(attachment=HookAttachment(type="hook_non_blocking_error", hook_name="Stop",
                                                       tool_use_id="t2", hook_event="Stop", exit_code=3,
                                                       command="a", duration_ms=7)),
        AggregatedHookResult(attachment=HookAttachment(type="hook_error_during_execution", hook_name="Stop",
                                                       tool_use_id="t3", hook_event="Stop", content="crashed")),
        AggregatedHookResult(attachment=HookAttachment(type="hook_non_blocking_error", hook_name="Pre",
                                                       tool_use_id="t4", hook_event="PreToolUse", stderr="ignored")),
        AggregatedHookResult(blocking_error=HookBlockingError(blocking_error="fix it", command="b")),
        AggregatedHookResult(prevent_continuation=True),
    ]
    state = make_state(ScriptedDispatcher(script={"Stop": [*progress, *attachments]}))
    result = asyncio.run(handle_stop_hooks(state, state.messages, False))
    (summary,) = result.messages
    assert summary.hook_count == 3
    # A progress item without a command is counted but not listed; durations go to the first unassigned entry.
    assert summary.hook_infos == (StopHookInfo("a", duration_ms=5), StopHookInfo("a", duration_ms=7))
    assert summary.hook_errors == ("Exit code 3", "crashed", "fix it")
    assert summary.prevented_continuation and summary.stop_reason == "Stop hook prevented continuation"
    assert summary.tool_use_id == "t1"
    # A successful hook with blank output has none.
    state = make_state(ScriptedDispatcher(script={"Stop": [progress[0], attachments[0]]}))
    (quiet,) = asyncio.run(handle_stop_hooks(state, state.messages, False)).messages
    assert not quiet.has_output and quiet.hook_errors == () and not quiet.prevented_continuation


def test_no_hook_ran_no_summary() -> None:
    state = make_state(ScriptedDispatcher(script={"Stop": [AggregatedHookResult()]}))
    assert asyncio.run(handle_stop_hooks(state, state.messages, False)).messages == ()
    blocked = StopHookResult(blocking_errors=(HumanMessage(content="x"),), prevent_continuation=True, messages=(1,))
    assert blocked.blocking_errors == () and blocked.messages == (1,)


def test_the_loop_yields_the_summary_before_its_verdict(tmp_path: Path) -> None:
    failing = CommandHook(command=_script(tmp_path, "fail.py", "import sys; sys.exit(1)"))
    state = make_state(_dispatcher(tmp_path, failing), DONE)

    async def run() -> list[Any]:
        return [event async for event in run_loop(state, CONFIG, loop_deps(Model()))]

    events = asyncio.run(run())
    summaries = [i for i, e in enumerate(events) if isinstance(e, StopHookSummaryMessage)]
    assert len(summaries) == 1 and events[-1].reason == Terminal.COMPLETED
    assert summaries[0] == len(events) - 2 and isinstance(events[0], AIMessage)
    assert events[summaries[0]].hook_errors == ("Failed with non-blocking status code: No stderr output",)


# ── the REPL's rendering ─────────────────────────────────────────────────────

def _summary(**kw: Any) -> StopHookSummaryMessage:
    fields: dict[str, Any] = {"hook_count": 2, "hook_infos": [StopHookInfo("lint.sh"), StopHookInfo("prompt", "ok?")],
                              "hook_errors": [], "prevented_continuation": False, "stop_reason": None,
                              "has_output": False, "level": "suggestion"}
    fields.update(kw)
    return create_stop_hook_summary_message(**fields)


def test_only_an_error_or_a_stop_shows_the_summary() -> None:
    assert render_stop_hook_summary(_summary()) == []
    assert render_stop_hook_summary(_summary(hook_errors=["boom"])) == [
        "● Ran 2 stop hooks", "  ⎿  Stop hook error: boom"]
    assert render_stop_hook_summary(_summary(hook_count=1, prevented_continuation=True, stop_reason="ship it"),
                                    verbose=True) == [
        "● Ran 1 stop hook", "  ⎿  lint.sh", "  ⎿  prompt: ok?", "  ⎿  ship it"]
    assert render_stop_hook_summary(_summary(prevented_continuation=True)) == ["● Ran 2 stop hooks"]
    assert render_stop_hook_summary(_summary(hook_label="PreToolUse", hook_count=1)) == [
        "  ⎿  Ran 1 PreToolUse hook"]


def test_the_renderer_closes_the_group_and_prints_the_summary() -> None:
    renderer = CollapsedReadSearchRenderer(lambda: (), lambda: None, lambda _e: None)
    assert renderer.feed(_summary(hook_errors=["boom"])) == ["● Ran 2 stop hooks", "  ⎿  Stop hook error: boom"]
    assert renderer.feed(_summary()) == []
