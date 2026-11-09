"""
The session's test floor (gap (c), user decisions 2026-11-08): a test count per test file, from the files the
reports name. A run with fewer tests than its total is not a pass in GREEN, is accepted in REFACTOR only with
changed test files, and is stated in RED, where a run that counts lowers the floor only for the files the
sub-requirement changed. The floor carries across sub-requirements, RunTests is its only writer, and nothing
restores it.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest

from app.state.app_state_store import AppState, AppStateStore
from app.tdd.collection_gate import Collection
from app.tdd.junit_report import JUnitReport
from app.tdd.ledger import PhaseLedger, TddPhase, TestFloor, TestRunRecord
from app.tdd.plan import TddPlan, update_tdd_plan
from app.tdd.run_judgement import FLOOR_ACCEPTED, Outcome, floor_fact, judge
from app.tdd.run_tests_tool.run_tests_tool import (
    PHASE_RULES,
    SuiteExecutionResult,
    build_run_tests_tool,
    next_test_floor,
)
from app.tdd.suite_fingerprint import fingerprint_test_files, hash_test_files
from app.tool import tool_context_for
from tests.conftest import FakeWorkspace
from tests.plan_helpers import CURRENT_ITEM_PLAN, TEST_PROJECT_CONFIG

GREEN, RED, REFACTOR = TddPhase.GREEN, TddPhase.RED, TddPhase.REFACTOR
A, B = "tests/test_a.py", "tests/test_b.py"
FILES = {A: "def test_a():\n    pass\n", B: "def test_b():\n    pass\n"}
#: Two tests in `A`, one in `B`.
FLOOR = TestFloor(((A, 2), (B, 1)), "before")


def report(**per_file: int) -> JUnitReport:
    """A report that names its files, `per_file` given as `a=2, b=1`."""
    counts = tuple(sorted((f"tests/test_{name}.py", n) for name, n in per_file.items()))
    return JUnitReport(tests=sum(n for _, n in counts), files=frozenset(path for path, _ in counts),
                       named=frozenset(path for path, _ in counts), named_counts=counts)


def collection(rep: JUnitReport) -> Collection:
    return Collection(rep.files, (), counts=rep.named_counts)


# ── the floor itself ─────────────────────────────────────────────────────────


def test_a_floor_is_a_count_per_file_and_its_total_is_the_sum():
    assert (FLOOR.total, FLOOR.count(A), FLOOR.count(B), FLOOR.count("x")) == (3, 2, 1, 0)
    assert TestFloor.of({B: 1, A: 2, "gone": 0}, "fp") == TestFloor(((A, 2), (B, 1)), "fp")
    assert TestFloor.of({A: 0}, "fp") is None and TestFloor.of({}, None) is None


def test_the_floor_fact_reads_exactly_this():
    assert floor_fact(2, FLOOR) == "the run records 2 tests, fewer than the 3 earlier runs recorded"
    assert floor_fact(2, FLOOR, ((A, 1), (B, 1))) == (
        "the run records 2 tests, fewer than the 3 earlier runs recorded (tests/test_a.py: 1 of 2)")
    assert floor_fact(1, FLOOR, ((A, 1),)) == (
        "the run records 1 tests, fewer than the 3 earlier runs recorded (tests/test_a.py: 1 of 2, "
        "tests/test_b.py: 0 of 1)")
    # Fewer in total, though no file fell short of its own floor (a file the floor never saw).
    assert floor_fact(2, FLOOR, ((A, 2),)).endswith("(tests/test_b.py: 0 of 1)")
    assert FLOOR_ACCEPTED == "and the test files changed since the floor was recorded"


# ── judge ────────────────────────────────────────────────────────────────────


def test_green_with_fewer_tests_than_the_floor_is_not_a_pass_and_names_the_files_short():
    rep = report(a=1, b=1)
    judgement = judge(0, rep, collection(rep), GREEN, FLOOR, "after")
    assert (judgement.outcome, judgement.unmet, judgement.meets_phase, judgement.stated) == (
        Outcome.PASSED, (floor_fact(2, FLOOR, rep.named_counts),), False, ())


def test_green_states_only_the_total_when_the_report_names_no_files():
    rep = JUnitReport(tests=2)
    judgement = judge(0, rep, None, GREEN, FLOOR, "after")
    assert judgement.unmet == (floor_fact(2, FLOOR),)


@pytest.mark.parametrize("fingerprint", ["before", None])
def test_refactor_with_fewer_tests_and_the_same_test_files_is_not_a_pass(fingerprint: str | None):
    rep = report(a=1, b=1)
    judgement = judge(0, rep, collection(rep), REFACTOR, FLOOR, fingerprint)
    assert (judgement.unmet, judgement.meets_phase) == ((floor_fact(2, FLOOR, rep.named_counts),), False)


def test_refactor_accepts_fewer_tests_when_the_test_files_changed():
    rep = report(a=1, b=1)
    judgement = judge(0, rep, collection(rep), REFACTOR, FLOOR, "after")
    assert (judgement.unmet, judgement.meets_phase, judgement.passed) == ((), True, True)
    assert judgement.stated == (f"{floor_fact(2, FLOOR, rep.named_counts)}, {FLOOR_ACCEPTED}",)


@pytest.mark.parametrize("code", [0, 1])
def test_red_states_fewer_tests_and_is_not_held_to_it(code: int):
    rep = report(a=1, b=1)
    judgement = judge(code, rep, collection(rep), RED, FLOOR, "before")
    assert (judgement.meets_phase, judgement.stated) == (True, (floor_fact(2, FLOOR, rep.named_counts),))


@pytest.mark.parametrize("floor", [None, TestFloor(((A, 3),)), TestFloor(((A, 1), (B, 1)))])
@pytest.mark.parametrize("phase", list(TddPhase))
def test_as_many_tests_as_the_floor_or_no_floor_is_judged_as_before(floor: TestFloor | None, phase: TddPhase):
    rep = report(a=2, b=1)
    judgement = judge(0, rep, collection(rep), phase, floor, "before")
    assert (judgement.unmet, judgement.stated, judgement.passed) == ((), (), True)


def test_a_run_without_a_report_is_not_held_to_the_floor():
    assert judge(1, None, None, GREEN, FLOOR, "before").unmet == ("the test command exited 1",)


# ── how the floor moves ──────────────────────────────────────────────────────


def _record(phase: TddPhase = GREEN, fingerprint: str = "now", **per_file: int) -> TestRunRecord:
    rep = report(**per_file)
    return TestRunRecord(command="t", exit_code=0, phase=phase, tests=rep.tests, test_fingerprint=fingerprint,
                         file_tests=rep.named_counts)


def _met(phase: TddPhase) -> Any:
    rep = report(a=1)
    return judge(0, rep, collection(rep), phase)


NOT_MET = judge(0, report(a=1), collection(report(a=1)), REFACTOR, FLOOR, "before")
SAME = {A: "h-a", B: "h-b"}


@pytest.mark.parametrize(("floor", "record", "judgement", "expected"), [
    # The first run that names its files sets it.
    (None, _record(a=2), _met(GREEN), TestFloor(((A, 2),), "now")),
    # It rises, replaced by the run's counts.
    (FLOOR, _record(a=3, b=1), _met(GREEN), TestFloor(((A, 3), (B, 1)), "now")),
    # As many tests: the latest counts and fingerprint (tests moved from one file to another).
    (FLOOR, _record(b=3), _met(GREEN), TestFloor(((B, 3),), "now")),
    # GREEN never lowers it.
    (FLOOR, _record(a=1, b=1), _met(GREEN), FLOOR),
    # A refused REFACTOR decrease leaves it; an accepted one lowers it.
    (FLOOR, _record(REFACTOR, a=1, b=1), NOT_MET, FLOOR),
    (FLOOR, _record(REFACTOR, a=1, b=1), _met(REFACTOR), TestFloor(((A, 1), (B, 1)), "now")),
])
def test_the_floor_rises_and_drops_only_on_an_accepted_decrease(
        floor: TestFloor | None, record: TestRunRecord, judgement: Any, expected: TestFloor | None):
    assert next_test_floor(floor, record, judgement, False, SAME, SAME) == expected


def test_a_run_whose_report_names_no_files_never_moves_it():
    unnamed = TestRunRecord(command="t", exit_code=0, phase=RED, tests=9, file_tests=None)
    assert next_test_floor(FLOOR, unnamed, _met(RED), True, SAME, {A: "x"}) is FLOOR
    assert next_test_floor(None, unnamed, _met(GREEN), False, SAME, SAME) is None
    empty = TestRunRecord(command="t", exit_code=0, phase=GREEN, tests=0, file_tests=())
    assert next_test_floor(FLOOR, empty, _met(GREEN), False, SAME, SAME) is FLOOR


def test_a_red_run_that_counts_lowers_the_floor_only_for_the_files_this_item_changed():
    # `A` was edited this item (one test removed), `B` was not, though it ran one test fewer.
    record = _record(RED, a=1)
    changed = {A: "h-a2", B: "h-b"}
    floor = next_test_floor(FLOOR, record, _met(RED), True, SAME, changed)
    assert floor == TestFloor(((A, 1), (B, 1)), "now")
    # An unchanged file keeps its floor, raised to the run's count when that is higher.
    raised = next_test_floor(FLOOR, _record(RED, a=1, b=4), _met(RED), True, SAME, changed)
    assert raised == TestFloor(((A, 1), (B, 4)), "now")


def test_a_file_deleted_this_item_loses_its_floor_and_a_new_one_takes_the_run_count():
    record = _record(RED, a=2, c=5)
    current = {A: "h-a", "tests/test_c.py": "h-c"}  # `B` deleted, `C` created
    floor = next_test_floor(FLOOR, record, _met(RED), True, SAME, current)
    assert floor == TestFloor(((A, 2), ("tests/test_c.py", 5)), "now")


def test_without_hashes_on_either_side_every_file_counts_as_changed():
    record = _record(RED, a=1)
    assert next_test_floor(FLOOR, record, _met(RED), True, None, SAME) == TestFloor(((A, 1),), "now")
    assert next_test_floor(FLOOR, record, _met(RED), True, SAME, None) == TestFloor(((A, 1),), "now")


def test_a_red_run_that_does_not_count_lowers_nothing():
    assert next_test_floor(FLOOR, _record(RED, a=1), _met(RED), False, SAME, {A: "x"}) is FLOOR
    # With no floor yet, or more tests, it is any other run.
    assert next_test_floor(None, _record(RED, a=1), _met(RED), False, SAME, SAME) == TestFloor(((A, 1),), "now")


# ── RunTests writes it ───────────────────────────────────────────────────────


def _hashes(files: dict[str, str]) -> dict[str, str] | None:
    return hash_test_files(FakeWorkspace(files=dict(files)), TEST_PROJECT_CONFIG)


def _fingerprint(files: dict[str, str]) -> str | None:
    return fingerprint_test_files(FakeWorkspace(files=dict(files)), TEST_PROJECT_CONFIG)


def _store(phase: TddPhase, floor: TestFloor | None = None, start: dict[str, str] | None = None) -> AppStateStore:
    """The item started over `start` (default: `FILES`), its baseline and per-file hashes taken then."""
    ledger = PhaseLedger(phase=phase, tests_written=phase != RED, green_passed=phase == REFACTOR)
    begun = FILES if start is None else start
    return AppStateStore(AppState(tdd_plan=CURRENT_ITEM_PLAN, project_config=TEST_PROJECT_CONFIG,
                                  phase_ledger=ledger, test_floor=floor,
                                  tdd_item_test_baseline=_fingerprint(begun), tdd_item_test_hashes=_hashes(begun)))


def _run(store: AppStateStore, rep: JUnitReport, files: dict[str, str] | None = None, *,
         shared: bool = True) -> Any:
    ctx = tool_context_for(store, workspace=FakeWorkspace(files=dict(FILES if files is None else files)))
    ctx = dataclasses.replace(ctx, shares_app_state=shared)
    return asyncio.run(build_run_tests_tool(lambda c: SuiteExecutionResult(exit_code=0, report=rep)).call({}, ctx))


def _ledger_line(res: Any) -> str:
    return str(res.content.split("\n\n", 1)[0].splitlines()[-1])


def test_run_tests_records_the_floor_per_file_and_raises_it():
    store = _store(GREEN)
    _run(store, report(a=2, b=1))
    assert store.get().test_floor == TestFloor(((A, 2), (B, 1)), _fingerprint(FILES))
    record = store.get().last_test_run
    assert record is not None and (record.tests, record.file_tests) == (3, ((A, 2), (B, 1)))
    _run(store, report(a=4, b=1))
    assert store.get().test_floor == TestFloor(((A, 4), (B, 1)), _fingerprint(FILES))


def test_green_with_fewer_tests_than_the_floor_stays_green_and_names_the_file():
    floor = TestFloor(((A, 2), (B, 1)), _fingerprint(FILES))
    store = _store(GREEN, floor)
    res = _run(store, report(a=1, b=1))
    assert _ledger_line(res) == (
        f"TDD ledger: still GREEN. {PHASE_RULES[GREEN]}: the run records 2 tests, fewer than the 3 earlier runs "
        "recorded (tests/test_a.py: 1 of 2).")
    assert store.get().phase_ledger.phase == GREEN and store.get().test_floor is floor


def test_refactor_with_fewer_tests_and_changed_test_files_lowers_the_floor():
    store = _store(REFACTOR, TestFloor(((A, 2), (B, 1)), _fingerprint(FILES)))
    changed = {**FILES, A: "def test_a():\n    assert True\n"}
    res = _run(store, report(a=1, b=1), changed)
    assert _ledger_line(res) == (
        f"TDD ledger: REFACTOR passed, so this sub-requirement's cycle is complete. {PHASE_RULES[REFACTOR]}: this "
        "run met it. Also observed: the run records 2 tests, fewer than the 3 earlier runs recorded "
        f"(tests/test_a.py: 1 of 2), {FLOOR_ACCEPTED}.")
    assert store.get().test_floor == TestFloor(((A, 1), (B, 1)), _fingerprint(changed))
    assert store.get().phase_ledger.refactor_passed


def test_refactor_with_fewer_tests_and_the_same_test_files_is_refused():
    floor = TestFloor(((A, 2), (B, 1)), _fingerprint(FILES))
    store = _store(REFACTOR, floor)
    res = _run(store, report(a=1, b=1))
    assert "still REFACTOR" in res.content and store.get().test_floor is floor


def test_a_red_run_that_counts_lowers_the_floor_only_for_the_file_it_changed():
    """The item removed a wrong test from `A`; `B`, untouched, keeps its floor."""
    store = _store(RED, TestFloor(((A, 3), (B, 2)), "old"))
    edited = {**FILES, A: "def test_a():\n    assert 1\n"}
    res = _run(store, report(a=2, b=2), edited)
    assert _ledger_line(res) == (
        f"TDD ledger: RED → GREEN. {PHASE_RULES[RED]}: they changed, and this run counts. Also observed: the run "
        "records 4 tests, fewer than the 5 earlier runs recorded (tests/test_a.py: 2 of 3).")
    assert store.get().test_floor == TestFloor(((A, 2), (B, 2)), _fingerprint(edited))
    # GREEN then passes on the new count.
    _run(store, report(a=2, b=2), edited)
    assert store.get().phase_ledger.phase == REFACTOR


def test_a_red_run_that_counts_keeps_the_floor_of_a_file_it_did_not_change():
    """`B` lost a test though the item never touched it: GREEN still refuses."""
    store = _store(RED, TestFloor(((A, 1), (B, 2)), "old"))
    edited = {**FILES, A: "def test_a():\n    assert 1\n"}
    _run(store, report(a=2, b=1), edited)
    assert store.get().phase_ledger.phase == GREEN
    assert store.get().test_floor == TestFloor(((A, 2), (B, 2)), _fingerprint(edited))
    res = _run(store, report(a=2, b=1), edited)
    assert _ledger_line(res).endswith("fewer than the 4 earlier runs recorded (tests/test_b.py: 1 of 2).")
    assert store.get().phase_ledger.phase == GREEN


def test_a_red_run_that_does_not_count_leaves_the_floor():
    floor = TestFloor(((A, 2), (B, 1)), "old")
    store = _store(RED, floor)  # the test files have not changed since the item started
    res = _run(store, report(a=1, b=1))
    assert "TDD ledger: still RED." in res.content and store.get().test_floor is floor
    # Nor does one in which a declared test file did not run, though the test files changed.
    store = _store(RED, floor)
    _run(store, report(a=1, b=1), {**FILES, "tests/test_c.py": "def test_c():\n    pass\n"})
    assert store.get().phase_ledger.phase == RED and store.get().test_floor is floor


def test_a_tester_respawn_or_rewind_that_removes_a_test_no_longer_keeps_green_from_passing():
    store = _store(RED, start={})
    _run(store, report(a=2, b=1))
    assert store.get().test_floor == TestFloor(((A, 2), (B, 1)), _fingerprint(FILES))
    # GREEN with a test gone: refused, as long as the tests are locked.
    _run(store, report(a=1, b=1))
    assert store.get().phase_ledger.phase == GREEN
    # Back to RED (the baseline and hashes taken again, as phase-on-spawn and a rewind take them): the tester
    # removes the wrong test from `A`, and that RED run counts.
    store.update(lambda s: dataclasses.replace(s, phase_ledger=PhaseLedger(), tdd_item_test_baseline=_fingerprint(
        FILES), tdd_item_test_hashes=_hashes(FILES)))
    fixed = {**FILES, A: "def test_a():\n    assert 2\n"}
    _run(store, report(a=1, b=1), fixed)
    assert store.get().test_floor == TestFloor(((A, 1), (B, 1)), _fingerprint(fixed))
    _run(store, report(a=1, b=1), fixed)
    assert store.get().phase_ledger.phase == REFACTOR


def test_a_background_run_writes_no_floor():
    store = _store(GREEN)
    _run(store, report(a=2), shared=False)
    assert store.get().test_floor is None and store.get().last_test_run is None


def test_the_floor_carries_across_sub_requirements_and_the_hashes_are_taken_again():
    store = AppStateStore(AppState(tdd_plan=TddPlan.from_texts(["a", "b"]), project_config=TEST_PROJECT_CONFIG,
                                   phase_ledger=PhaseLedger(phase=GREEN, tests_written=True)))
    _run(store, report(a=4))
    floor = store.get().test_floor
    edited = {**FILES, A: "changed"}
    ctx = tool_context_for(store, workspace=FakeWorkspace(files=dict(edited)))
    update_tdd_plan(ctx, lambda plan: plan.replace_pending(["c"]))
    assert store.get().phase_ledger == PhaseLedger() and store.get().last_test_run is None
    assert store.get().test_floor is floor and floor is not None
    assert store.get().tdd_item_test_hashes == _hashes(edited)


def test_a_new_session_starts_with_no_floor_and_no_hashes():
    assert (AppState().test_floor, AppState().tdd_item_test_hashes) == (None, None)
