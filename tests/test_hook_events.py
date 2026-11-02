"""Hook lifecycle events (`hookEvents.ts`): `system/hook_started` and `system/hook_response`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.cli.print import HeadlessOptions, run_headless
from app.schemas.hooks import HookMatcher
from app.utils.hooks.hook_events import MAX_PENDING_EVENTS, HookEventEmitter
from app.utils.hooks.hooks_config_snapshot import HooksConfigSnapshot
from tests.test_loop_hooks_execute import collect, dispatcher, script
from tests.test_stream_json import Feed, Model, lines_of, run_options

STOP = {"hook_event_name": "Stop", "stop_hook_active": False}


def test_what_the_emitter_emits() -> None:
    emitter = HookEventEmitter()
    assert emitter.should_emit("SessionStart") and emitter.should_emit("Setup")
    assert not emitter.should_emit("Stop")
    emitter.all_hook_events_enabled = True
    assert emitter.should_emit("Stop") and not emitter.should_emit("NotAnEvent")


def test_pending_events_are_bounded_and_flushed_to_the_handler() -> None:
    emitter = HookEventEmitter()
    for i in range(MAX_PENDING_EVENTS + 2):
        emitter.emit_started(f"h{i}", "SessionStart", "SessionStart")
    emitter.emit_started("ignored", "Stop", "Stop")
    seen: list[dict[str, Any]] = []
    emitter.register_handler(seen.append)
    assert len(seen) == MAX_PENDING_EVENTS and seen[0]["hookId"] == "h2" and emitter.pending == []
    emitter.emit_response(hook_id="r", hook_name="n", hook_event="Setup", output="o", stdout="o", stderr="",
                          exit_code=0, outcome="success")
    assert seen[-1] == {"type": "response", "hookId": "r", "hookName": "n", "hookEvent": "Setup", "output": "o",
                        "stdout": "o", "stderr": "", "exitCode": 0, "outcome": "success"}


def test_a_command_hook_is_started_then_answered(tmp_path: Path) -> None:
    ok = script(tmp_path, "ok.sh", "echo fine\n")
    failing = script(tmp_path, "bad.sh", "echo broke >&2\nexit 1\n")
    d = dispatcher(HooksConfigSnapshot(hooks={"Stop": (HookMatcher(hooks=(ok, failing)),)}), tmp_path)
    d.events.all_hook_events_enabled = True
    seen: list[dict[str, Any]] = []
    d.events.register_handler(seen.append)
    collect(d.execute_hooks(STOP, "tu", messages=[]))
    started = [e for e in seen if e["type"] == "started"]
    responses = {e["hookId"]: e for e in seen if e["type"] == "response"}
    assert len(started) == 2 and set(responses) == {e["hookId"] for e in started}
    outcomes = sorted((e["outcome"], e["exitCode"]) for e in responses.values())
    assert outcomes[0][0] == "error" and outcomes[1][0] == "success"
    assert any("broke" in e["stderr"] for e in responses.values())
    assert all(e["hookName"] == "Stop" and e["hookEvent"] == "Stop" for e in seen)


def test_session_start_hooks_reach_the_stream(python_workspace: Any, tmp_path: Path) -> None:
    settings = tmp_path / ".tddagents" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    hook = {"type": "command", "command": "echo started-up"}
    settings.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [hook]}]}}))
    feed = Feed()
    feed.close()
    written: list[str] = []
    options = HeadlessOptions(output_format="stream-json", input_format="stream-json", verbose=True)
    asyncio.run(run_headless("sj-hook-events", None, workspace=python_workspace, options=options,
                             input_blocks=feed.blocks(), write_stdout=written.append,
                             **run_options(tmp_path, Model([]))))
    events = [m for m in lines_of(written) if str(m.get("subtype", "")).startswith("hook_")]
    assert [e["subtype"] for e in events] == ["hook_started", "hook_response"]
    assert events[0]["hook_id"] == events[1]["hook_id"] and events[1]["hook_event"] == "SessionStart"
    assert events[1]["outcome"] == "success" and "started-up" in events[1]["stdout"]
