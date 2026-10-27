"""
The run's `emit_event` sink (Phase 6): every transition, with the evidence beside it.

Upstream has no equivalent — `query.ts` keeps its `transition` on the loop state for tests
and never persists it. TDDAgents needs it persisted because the paper's F1/F2 flows are
*shapes of transition history* (Part L): a flow is F1 when the ledger confirmed RED before
GREEN passed. So each `Transition` and the final `Terminated` is written with a snapshot of
the authoritative ledger and the last `RunTests` exit code, read from `AppState` at the
moment the event fires. Nothing here is taken from a model's message.

The file is `<project dir>/<session id>/events.jsonl` — inside the session directory, next
to `subagents/` and `tool-results/`, so a directory listing of `*.jsonl` session files never
mistakes it for a transcript. Each event is appended as it happens, never rewritten, so a
killed run still leaves its history. Sidechains (workers) do not get this sink: their
transitions are not the sub-requirement's flow.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.query.transitions import Continue, Terminated, Transition
from app.state.app_state_store import AppStateStore
from app.tdd.event_log import EventLog, EventType
from app.utils.session_storage_portable import get_project_dir

logger = logging.getLogger(__name__)

EVENTS_FILE_NAME = "events.jsonl"


def events_path(session_id: str, cwd: str | Path) -> Path:
    """`<project dir>/<session id>/events.jsonl`."""
    return get_project_dir(cwd) / session_id / EVENTS_FILE_NAME


class TransitionEventRecorder:
    """`LoopDeps.emit_event` for one sub-requirement's main loop."""

    def __init__(self, store: AppStateStore, path: Path | None, *, session_id: str, plan_index: int) -> None:
        self.store = store
        self.path = path
        self.plan_index = plan_index
        self.log = EventLog(session_id=session_id)
        self._turn = 1

    def __call__(self, event: Any) -> None:
        if not isinstance(event, (Transition, Terminated)):
            return
        app_state = self.store.get()
        ledger = app_state.phase_ledger
        run = app_state.last_test_run
        entry = self.log.record_transition(
            turn_count=self._turn,
            phase=str(ledger.phase),
            reason=str(event.reason),
            red_confirmed=ledger.red_confirmed,
            green_passed=ledger.green_passed,
            payload={
                "plan_index": self.plan_index,
                "terminal": isinstance(event, Terminated),
                "test_exit_code": run.exit_code if run is not None else None,
                "test_path": run.test_path if run is not None else None,
            },
        )
        if event.reason == Continue.NEXT_TURN:
            self._turn += 1
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("Failed to append event to %s: %s", self.path, e)


__all__ = ["EVENTS_FILE_NAME", "EventType", "TransitionEventRecorder", "events_path"]
