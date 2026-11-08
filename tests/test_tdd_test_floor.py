"""
The session's test floor (gap (c), user decision 2026-11-08): a run that records fewer tests than an earlier
one is not a pass in GREEN, is accepted in REFACTOR only with changed test files, and is stated in RED. The
floor carries across sub-requirements, RunTests is its only writer, and nothing restores it.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest

from app.state.app_state_store import AppState, AppStateStore
from app.tdd.collection_gate import Collection
from app.tdd.junit_report import JUnitReport
from app.tdd.ledger import PhaseLedger, TddPhase, TestRunRecord
from app.tdd.plan import TddPlan, update_tdd_plan
from app.tdd.run_judgement import FLOOR_ACCEPTED, Outcome, floor_fact, judge
from app.tdd.run_tests_tool.run_tests_tool import (
    PHASE_RULES,
    SuiteExecutionResult,
    build_run_tests_tool,
    next_test_floor,
)
from app.tdd.suite_fingerprint import fingerprint_test_files
from app.tool import tool_context_for
from tests.conftest import FakeWorkspace
from tests.plan_helpers import CURRENT_ITEM_PLAN, TEST_PROJECT_CONFIG

GREEN, RED, REFACTOR = TddPhase.GREEN, TddPhase.RED, TddPhase.REFACTOR
FILES = {"tests/test_a.py": "def test_a():\n    pass\n"}
RAN = Collection(frozenset(FILES), ())
FLOOR = TestRunRecord(command="t", exit_code=0, tests=3, test_fingerprint="before")


def two(**kwargs: Any) -> JUnitReport:
    return JUnitReport(tests=2, files=frozenset(FILES), **kwargs)


def test_the_floor_fact_reads_exactly_this():
    assert floor_fact(2, FLOOR) == "the run records 2 tests, fewer than the 3 an earlier run recorded"
    assert FLOOR_ACCEPTED == "and the test files changed since that run"


def test_green_with_fewer_tests_than_the_floor_is_not_a_pass():
    judgement = judge(0, two(), RAN, GREEN, FLOOR, "after")
    assert (judgement.outcome, judgement.unmet, judgement.meets_phase, judgement.stated) == (
        Outcome.PASSED, (floor_fact(2, FLOOR),), False, ())


@pytest.mark.parametrize("fingerprint", ["before", None])
def test_refactor_with_fewer_tests_and_the_same_test_files_is_not_a_pass(fingerprint: str | None):
    judgement = judge(0, two(), RAN, REFACTOR, FLOOR, fingerprint)
    assert (judgement.unmet, judgement.meets_phase) == ((floor_fact(2, FLOOR),), False)


def test_refactor_accepts_fewer_tests_when_the_test_files_changed():
    judgement = judge(0, two(), RAN, REFACTOR, FLOOR, "after")
    assert (judgement.unmet, judgement.meets_phase, judgement.passed) == ((), True, True)
    assert judgement.stated == (f"{floor_fact(2, FLOOR)}, {FLOOR_ACCEPTED}",)


@pytest.mark.parametrize("code", [0, 1])
def test_red_states_fewer_tests_and_is_not_held_to_it(code: int):
    judgement = judge(code, two(), RAN, RED, FLOOR, "before")
    assert (judgement.meets_phase, judgement.stated) == (True, (floor_fact(2, FLOOR),))
    assert floor_fact(2, FLOOR) not in judgement.unmet


@pytest.mark.parametrize("floor", [None, TestRunRecord(command="t", exit_code=1),
                                   dataclasses.replace(FLOOR, tests=2), dataclasses.replace(FLOOR, tests=1)])
@pytest.mark.parametrize("phase", list(TddPhase))
def test_as_many_tests_as_the_floor_or_no_floor_is_judged_as_before(floor: TestRunRecord | None, phase: TddPhase):
    judgement = judge(0, two(), RAN, phase, floor, "before")
    assert (judgement.unmet, judgement.stated, judgement.passed) == ((), (), True)


def test_a_run_without_a_report_is_not_held_to_the_floor():
    judgement = judge(1, None, None, GREEN, FLOOR, "before")
    assert judgement.unmet == ("the test command exited 1",)


# ── how the floor moves ──────────────────────────────────────────────────────


def _record(tests: int | None, phase: TddPhase = GREEN, fingerprint: str = "now") -> TestRunRecord:
    return TestRunRecord(command="t", exit_code=0, phase=phase, tests=tests, test_fingerprint=fingerprint)


MET = judge(0, two(), RAN, REFACTOR)
NOT_MET = judge(0, two(), RAN, REFACTOR, FLOOR, "before")


@pytest.mark.parametrize(("floor", "record", "judgement", "expected"), [
    (None, _record(2), MET, "record"),                      # the first run with a report sets it
    (None, _record(None), MET, "floor"),                    # no report: nothing to record
    (None, _record(0), MET, "floor"),                       # no tests: nothing to record
    (FLOOR, _record(4), MET, "record"),                     # it rises
    (FLOOR, _record(3), MET, "record"),                     # as many: the latest floor run, its fingerprint
    (FLOOR, _record(2), MET, "floor"),                      # GREEN never lowers it
    (FLOOR, _record(2, RED), MET, "floor"),                 # nor does RED
    (FLOOR, _record(2, REFACTOR), NOT_MET, "floor"),        # a refused decrease
    (FLOOR, _record(2, REFACTOR), MET, "record"),           # an accepted decrease lowers it
    (TestRunRecord(command="t", exit_code=1), _record(1), MET, "record"),
])
def test_the_floor_rises_and_drops_only_on_an_accepted_decrease(
        floor: TestRunRecord | None, record: TestRunRecord, judgement: Any, expected: str):
    assert next_test_floor(floor, record, judgement) is (record if expected == "record" else floor)


# ── RunTests writes it ───────────────────────────────────────────────────────


def _store(phase: TddPhase, floor: TestRunRecord | None = None) -> AppStateStore:
    ledger = PhaseLedger(phase=phase, tests_written=phase != RED, green_passed=phase == REFACTOR)
    return AppStateStore(AppState(tdd_plan=CURRENT_ITEM_PLAN, project_config=TEST_PROJECT_CONFIG,
                                  phase_ledger=ledger, test_floor=floor))


def _run(store: AppStateStore, report: JUnitReport, files: dict[str, str] | None = None, *,
         shared: bool = True) -> Any:
    ctx = tool_context_for(store, workspace=FakeWorkspace(files=dict(FILES if files is None else files)))
    ctx = dataclasses.replace(ctx, shares_app_state=shared)
    return asyncio.run(build_run_tests_tool(lambda c: SuiteExecutionResult(exit_code=0, report=report)).call({}, ctx))


def _fingerprint(files: dict[str, str]) -> str | None:
    return fingerprint_test_files(FakeWorkspace(files=dict(files)), TEST_PROJECT_CONFIG)


def test_run_tests_records_the_floor_and_raises_it():
    store = _store(GREEN)
    _run(store, two())
    floor = store.get().test_floor
    assert floor is not None and (floor.tests, floor.test_fingerprint) == (2, _fingerprint(FILES))
    assert store.get().last_test_run is floor
    _run(store, JUnitReport(tests=5, files=frozenset(FILES)))
    raised = store.get().test_floor
    assert raised is not None and raised.tests == 5


def test_green_with_fewer_tests_than_an_earlier_run_stays_green():
    floor = _record(3, fingerprint=str(_fingerprint(FILES)))
    store = _store(GREEN, floor)
    res = _run(store, two())
    assert res.content.split("\n\n", 1)[0].splitlines()[-1] == (
        f"TDD ledger: still GREEN. {PHASE_RULES[GREEN]}: the run records 2 tests, fewer than the 3 an earlier run "
        "recorded.")
    assert store.get().phase_ledger.phase == GREEN and store.get().test_floor is floor


def test_refactor_with_fewer_tests_and_changed_test_files_lowers_the_floor():
    store = _store(REFACTOR, _record(3, fingerprint=str(_fingerprint(FILES))))
    changed = {"tests/test_a.py": "def test_a():\n    assert True\n"}
    res = _run(store, two(), changed)
    assert res.content.split("\n\n", 1)[0].splitlines()[-1] == (
        f"TDD ledger: REFACTOR passed, so this sub-requirement's cycle is complete. {PHASE_RULES[REFACTOR]}: this "
        "run met it. Also observed: the run records 2 tests, fewer than the 3 an earlier run recorded, and the test "
        "files changed since that run.")
    floor = store.get().test_floor
    assert floor is not None and (floor.tests, floor.test_fingerprint) == (2, _fingerprint(changed))
    assert store.get().phase_ledger.refactor_passed


def test_refactor_with_fewer_tests_and_the_same_test_files_is_refused():
    floor = _record(3, fingerprint=str(_fingerprint(FILES)))
    store = _store(REFACTOR, floor)
    res = _run(store, two())
    assert "still REFACTOR" in res.content and store.get().test_floor is floor
    assert not store.get().phase_ledger.refactor_passed


def test_red_states_fewer_tests_and_still_counts_the_changed_test_files():
    floor = _record(3)
    store = _store(RED, floor)
    res = _run(store, two())
    assert res.content.split("\n\n", 1)[0].splitlines()[-1] == (
        f"TDD ledger: RED → GREEN. {PHASE_RULES[RED]}: they changed, and this run counts. Also observed: the run "
        "records 2 tests, fewer than the 3 an earlier run recorded.")
    assert store.get().phase_ledger.phase == GREEN and store.get().test_floor is floor


def test_a_background_run_writes_no_floor():
    store = _store(GREEN)
    _run(store, two(), shared=False)
    assert store.get().test_floor is None and store.get().last_test_run is None


def test_the_floor_carries_across_sub_requirements():
    store = AppStateStore(AppState(tdd_plan=TddPlan.from_texts(["a", "b"]), project_config=TEST_PROJECT_CONFIG,
                                   phase_ledger=PhaseLedger(phase=GREEN, tests_written=True)))
    _run(store, JUnitReport(tests=4, files=frozenset(FILES)))
    floor = store.get().test_floor
    ctx = tool_context_for(store, workspace=FakeWorkspace(files=dict(FILES)))
    update_tdd_plan(ctx, lambda plan: plan.replace_pending(["c"]))
    assert store.get().tdd_plan.current != CURRENT_ITEM_PLAN.current
    assert store.get().phase_ledger == PhaseLedger() and store.get().last_test_run is None
    assert store.get().test_floor is floor


def test_a_new_session_starts_with_no_floor():
    assert AppState().test_floor is None
