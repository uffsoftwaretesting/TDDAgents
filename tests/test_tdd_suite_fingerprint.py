"""
Phase 12: the fingerprint of a workspace's test files (`app/tdd/suite_fingerprint.py`), the
evidence that a sub-requirement's RED wrote or changed tests.
"""

from __future__ import annotations

from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.suite_fingerprint import (
    EMPTY_FINGERPRINT,
    fingerprint_test_files,
    have_tests_changed,
    spawn_test_baseline,
)
from app.tool import tool_context_for
from app.workspace.local import LocalWorkspace
from tests.conftest import FakeWorkspace


def fp(files: dict[str, str]) -> str | None:
    return fingerprint_test_files(FakeWorkspace(files=files))


def test_the_same_test_files_give_the_same_fingerprint_in_any_order():
    a = fp({"tests/test_a.py": "A", "tests/test_b.py": "B", "calc.py": "x"})
    b = fp({"calc.py": "y", "tests/test_b.py": "B", "tests/test_a.py": "A"})
    assert a == b and a is not None and a != EMPTY_FINGERPRINT


def test_adding_editing_or_deleting_a_test_file_changes_it():
    base = fp({"tests/test_a.py": "A"})
    assert fp({"tests/test_a.py": "A", "tests/test_b.py": "B"}) != base
    assert fp({"tests/test_a.py": "A2"}) != base
    assert fp({}) != base


def test_a_rename_changes_it():
    assert fp({"tests/test_a.py": "A"}) != fp({"tests/test_b.py": "A"})


def test_production_files_and_caches_do_not_count():
    base = fp({"tests/test_a.py": "A"})
    assert fp({"tests/test_a.py": "A", "calc.py": "code", "src/x.py": "y"}) == base
    assert fp({"tests/test_a.py": "A", "tests/__pycache__/test_a.cpython-314.pyc": "\x00",
               ".pytest_cache/v/cache/lastfailed": "{}", "tests/.pytest_cache/x": "1",
               ".tddagents/coverage/report.json": "{}"}) == base


def test_every_test_path_form_counts():
    for path in ("tests/test_a.py", "test/helpers.py", "pkg/tests/data.json", "test_root.py", "calc_test.py",
                 "tests/conftest.py"):
        assert fp({path: "x"}) != EMPTY_FINGERPRINT, path


def test_no_test_files_is_the_empty_fingerprint():
    assert fp({"calc.py": "x"}) == EMPTY_FINGERPRINT


def test_no_workspace_or_a_failing_listing_is_none():
    class Broken:
        def list_files(self, path: str = ".", depth: int = 1) -> list:
            raise RuntimeError("down")

    assert fingerprint_test_files(None) is None
    assert fingerprint_test_files(object()) is None
    assert fingerprint_test_files(Broken()) is None


def test_an_unreadable_test_file_still_counts_by_its_path():
    ws = FakeWorkspace(files={"tests/test_a.py": "A"})
    original = ws.read_file

    def read(path: str) -> str:
        if path == "tests/test_a.py":
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
        return original(path)

    ws.read_file = read  # type: ignore[method-assign]
    unreadable = fingerprint_test_files(ws)
    assert unreadable not in (None, EMPTY_FINGERPRINT, fp({"tests/test_a.py": "A"}))


def test_a_real_workspace_counts_files_written_by_any_means(tmp_path):
    ws = LocalWorkspace(str(tmp_path / "ws"))
    ws.write_file("calc.py", "x = 1\n")
    base = fingerprint_test_files(ws)
    assert base == EMPTY_FINGERPRINT
    ws.execute("mkdir -p tests/deep && printf 'def test_a():\\n    pass\\n' > tests/deep/test_a.py")
    changed = fingerprint_test_files(ws)
    assert changed not in (None, base)
    ws.execute("python3 -c 'pass' ; mkdir -p tests/deep/__pycache__ && touch tests/deep/__pycache__/x.pyc")
    assert fingerprint_test_files(ws) == changed


def test_changed_compares_against_the_items_baseline():
    assert have_tests_changed("a", "b") is True
    assert have_tests_changed("a", "a") is False
    assert have_tests_changed(None, "a") is False
    # No baseline could be taken: any test file counts as new.
    assert have_tests_changed("a", None) is True
    assert have_tests_changed(EMPTY_FINGERPRINT, None) is False


def test_a_spawn_back_to_red_takes_a_new_baseline_and_any_other_keeps_it():
    ws = FakeWorkspace(files={"tests/test_a.py": "A"})
    ctx = tool_context_for(AppStateStore(AppState(tdd_item_test_baseline="kept")), workspace=ws)
    assert spawn_test_baseline(PhaseLedger(), ctx) == fingerprint_test_files(ws)
    assert spawn_test_baseline(PhaseLedger(phase=TddPhase.GREEN, tests_written=True), ctx) == "kept"
    assert spawn_test_baseline(PhaseLedger(), tool_context_for(AppStateStore())) is None
