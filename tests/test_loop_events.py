"""Phase 6: `emit_event` writes every transition with the ledger snapshot and the last
`RunTests` exit code — the evidence Part L derives F1/F2 from."""

from __future__ import annotations

import dataclasses
import json

from app.query.transitions import Continue, Terminal, Terminated, Transition
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.event_log import EventLog, EventType
from app.tdd.events import TransitionEventRecorder, events_path
from app.tdd.ledger import PhaseLedger, TddPhase, TestRunRecord


def _recorder(tmp_path, store, **kw):
    return TransitionEventRecorder(store, tmp_path / "events.jsonl", session_id="s1", plan_index=3, **kw)


def test_a_transition_is_recorded_with_the_ledger_and_the_last_test_run(tmp_path):
    store = AppStateStore(AppState(
        phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True),
        last_test_run=TestRunRecord(test_path="tests/test_a.py", exit_code=1),
    ))
    rec = _recorder(tmp_path, store)
    rec(Transition(reason=Continue.NEXT_TURN))
    (entry,) = rec.log.all_events()
    assert entry.event_type == EventType.TRANSITION
    assert (entry.session_id, entry.turn_count, entry.phase) == ("s1", 1, "GREEN")
    assert entry.transition_reason == "next_turn"
    assert (entry.tests_written, entry.green_passed) == (True, False)
    assert entry.payload == {"plan_index": 3, "terminal": False, "test_exit_code": 1, "test_path": "tests/test_a.py"}


def test_without_a_test_run_the_exit_code_is_null(tmp_path):
    rec = _recorder(tmp_path, AppStateStore(AppState()))
    rec(Terminated(reason=Terminal.COMPLETED))
    (entry,) = rec.log.all_events()
    assert entry.payload == {"plan_index": 3, "terminal": True, "test_exit_code": None, "test_path": None}
    assert (entry.phase, entry.transition_reason) == ("RED", "completed")


def test_the_snapshot_is_taken_when_the_event_fires(tmp_path):
    store = AppStateStore(AppState())
    rec = _recorder(tmp_path, store)
    rec(Transition(reason=Continue.NEXT_TURN))
    store.update(lambda s: dataclasses.replace(
        s, phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True),
        last_test_run=TestRunRecord(test_path="t.py", exit_code=1)))
    rec(Transition(reason=Continue.STOP_HOOK_BLOCKING))
    first, second = rec.log.all_events()
    assert (first.tests_written, first.payload["test_exit_code"]) == (False, None)
    assert (second.tests_written, second.payload["test_exit_code"]) == (True, 1)


def test_turns_advance_only_on_next_turn(tmp_path):
    rec = _recorder(tmp_path, AppStateStore(AppState()))
    for reason in (Continue.NEXT_TURN, Continue.STOP_HOOK_BLOCKING, Continue.NEXT_TURN):
        rec(Transition(reason=reason))
    rec(Terminated(reason=Terminal.COMPLETED))
    assert [e.turn_count for e in rec.log.all_events()] == [1, 2, 2, 3]


def test_every_event_is_appended_to_the_file_as_it_happens(tmp_path):
    store = AppStateStore(AppState())
    rec = _recorder(tmp_path, store)
    rec(Transition(reason=Continue.NEXT_TURN))
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["transition_reason"] == "next_turn"
    rec(Terminated(reason=Terminal.COMPLETED))
    loaded = EventLog.load_jsonl(tmp_path / "events.jsonl")
    assert [e.transition_reason for e in loaded] == ["next_turn", "completed"]
    assert loaded.all_events() == rec.log.all_events()


def test_an_existing_file_is_appended_to_never_rewritten(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"keep": 1}\n')
    TransitionEventRecorder(AppStateStore(AppState()), path, session_id="s", plan_index=0)(
        Transition(reason=Continue.NEXT_TURN))
    assert path.read_text().splitlines()[0] == '{"keep": 1}'


def test_without_a_path_nothing_is_written(tmp_path):
    rec = TransitionEventRecorder(AppStateStore(AppState()), None, session_id="s", plan_index=0)
    rec(Transition(reason=Continue.NEXT_TURN))
    assert len(rec.log) == 1
    assert list(tmp_path.iterdir()) == []


def test_a_write_failure_does_not_break_the_loop(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    rec = TransitionEventRecorder(AppStateStore(AppState()), blocker / "events.jsonl", session_id="s", plan_index=0)
    rec(Transition(reason=Continue.NEXT_TURN))
    assert len(rec.log) == 1


def test_unknown_events_are_ignored(tmp_path):
    rec = _recorder(tmp_path, AppStateStore(AppState()))
    rec("not a transition")
    assert len(rec.log) == 0


def test_the_events_file_lives_in_the_session_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    from app.utils.tool_result_storage import sanitize_path

    assert events_path("s1", "/w/x") == tmp_path / "projects" / sanitize_path("/w/x") / "s1" / "events.jsonl"


def test_missing_parent_directories_are_created(tmp_path):
    path = tmp_path / "a" / "b" / "events.jsonl"
    TransitionEventRecorder(AppStateStore(AppState()), path, session_id="s", plan_index=0)(
        Transition(reason=Continue.NEXT_TURN))
    assert path.is_file()


def test_non_ascii_is_written_as_is(tmp_path):
    store = AppStateStore(AppState(last_test_run=TestRunRecord(test_path="tests/test_ação.py", exit_code=1)))
    rec = _recorder(tmp_path, store)
    rec(Transition(reason=Continue.NEXT_TURN))
    assert "test_ação.py" in (tmp_path / "events.jsonl").read_text(encoding="utf-8")


def test_the_plan_index_follows_the_plan_through_the_session(tmp_path):
    """One session spans the plan (Phase 11): each event names the item the work is on."""
    import dataclasses

    from app.tdd.plan import ItemStatus, TddPlan

    plan = TddPlan.from_texts(["a", "b", "c"])
    store = AppStateStore(AppState(tdd_plan=plan))
    rec = TransitionEventRecorder(store, None, session_id="s", plan_index=0)
    assert rec._plan_index(plan) == 0
    first_done = dataclasses.replace(plan, items=(dataclasses.replace(plan.items[0], status=ItemStatus.DONE),
                                                  *plan.items[1:]))
    assert rec._plan_index(first_done) == 1
    all_done = dataclasses.replace(plan, items=tuple(dataclasses.replace(i, status=ItemStatus.DONE)
                                                     for i in plan.items))
    assert rec._plan_index(all_done) == 3
    assert rec._plan_index(TddPlan()) == 0


def test_a_completed_item_is_logged_once_with_the_ledger_that_completed_it(tmp_path):
    """The advance restarts the ledger at once, so a `plan_item` event records the cycle."""
    import json

    from app.query.transitions import Continue, Transition
    from app.tdd.ledger import PhaseLedger, TddPhase
    from app.tdd.plan import TddPlan

    cycle = PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True, refactor_passed=True,
                        coverage=95.0)
    store = AppStateStore(AppState(tdd_plan=TddPlan.from_texts(["a", "b"])))
    path = tmp_path / "events.jsonl"
    rec = TransitionEventRecorder(store, path, session_id="s", plan_index=0)
    rec(Transition(reason=Continue.NEXT_TURN))
    store.update(lambda s: dataclasses.replace(s, tdd_plan=s.tdd_plan.complete_current(cycle)))
    rec(Transition(reason=Continue.NEXT_TURN))
    rec(Transition(reason=Continue.NEXT_TURN))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(r["event_type"], r["payload"]["plan_index"]) for r in rows] == [
        ("transition", 0), ("plan_item", 0), ("transition", 1), ("transition", 1)]
    done = rows[1]
    assert (done["transition_reason"], done["phase"], done["tests_written"], done["green_passed"],
            done["refactor_passed"], done["coverage"], done["turn_count"]) == (
        "completed", "REFACTOR", True, True, True, 95.0, 2)
    assert done["payload"] == {"plan_index": 0, "item_id": "1"}


def test_items_done_before_the_run_opened_are_not_logged_again(tmp_path):
    from app.query.transitions import Continue, Transition
    from app.tdd.ledger import PhaseLedger, TddPhase
    from app.tdd.plan import TddPlan

    cycle = PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True, refactor_passed=True)
    store = AppStateStore(AppState(tdd_plan=TddPlan.from_texts(["a", "b"]).complete_current(cycle)))
    rec = TransitionEventRecorder(store, None, session_id="s", plan_index=0)
    rec(Transition(reason=Continue.NEXT_TURN))
    assert [str(e.event_type) for e in rec.log] == ["transition"]
