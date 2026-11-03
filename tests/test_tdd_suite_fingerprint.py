"""
Phase 12: the fingerprint of a workspace's test files (`app/tdd/suite_fingerprint.py`), the
evidence that a sub-requirement's RED wrote or changed tests. Since 2026-11-03 the test files are
the ones the project declares, and the walk is pruned on its `exclude` patterns.
"""

from __future__ import annotations

from pathlib import Path

from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.project_config import ProjectConfig
from app.tdd.suite_fingerprint import (
    EMPTY_FINGERPRINT,
    context_test_fingerprint,
    fingerprint_test_files,
    have_tests_changed,
    project_files,
    spawn_test_baseline,
)
from app.tool import tool_context_for
from app.workspace.local import LocalWorkspace
from tests.conftest import FakeWorkspace
from tests.plan_helpers import TEST_PROJECT_CONFIG

#: A JavaScript project: tests are `*.test.js` anywhere, dependencies live in `node_modules/`.
JS = ProjectConfig(test="npx jest", test_files=("*.test.js",), test_support_files=("test/helpers/",),
                   exclude=("node_modules/", "coverage/"))


def fp(files: dict[str, str], config: ProjectConfig = TEST_PROJECT_CONFIG) -> str | None:
    return fingerprint_test_files(FakeWorkspace(files=files), config)


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


def test_production_files_excluded_paths_and_tddagents_do_not_count():
    base = fp({"tests/test_a.py": "A"})
    assert fp({"tests/test_a.py": "A", "calc.py": "code", "src/x.py": "y"}) == base
    assert fp({"tests/test_a.py": "A", "tests/__pycache__/test_a.cpython-314.pyc": "\x00",
               ".pytest_cache/v/cache/lastfailed": "{}", ".venv/lib/tests/test_x.py": "1",
               ".tddagents/reports/junit.xml": "<x/>", ".git/tests/test_x.py": "1"}) == base


def test_a_dependency_tree_never_counts():
    """`node_modules/x/test/y.test.js` is a dependency's test, not the project's."""
    base = fp({"src/a.test.js": "A"}, JS)
    assert fp({"src/a.test.js": "A", "node_modules/x/test/y.test.js": "dep"}, JS) == base
    assert fp({"src/a.test.js": "A", "test/helpers/setup.js": "h"}, JS) != base


def test_every_declared_test_path_form_counts():
    for path in ("tests/test_a.py", "test/helpers.py", "pkg/tests/data.json", "test_root.py", "calc_test.py",
                 "tests/conftest.py"):
        assert fp({path: "x"}) != EMPTY_FINGERPRINT, path


def test_no_test_files_is_the_empty_fingerprint():
    assert fp({"calc.py": "x"}) == EMPTY_FINGERPRINT


def test_no_declaration_no_workspace_or_a_failing_listing_is_none():
    class Broken:
        def list_files(self, path: str = ".", depth: int = 1) -> list[object]:
            raise RuntimeError("down")

    class BrokenWalk:
        def walk_files(self, prune: object) -> list[str]:
            raise RuntimeError("down")

    assert fingerprint_test_files(FakeWorkspace(files={"tests/test_a.py": "A"}), None) is None
    assert fingerprint_test_files(None, TEST_PROJECT_CONFIG) is None
    assert fingerprint_test_files(object(), TEST_PROJECT_CONFIG) is None
    assert fingerprint_test_files(Broken(), TEST_PROJECT_CONFIG) is None
    assert fingerprint_test_files(BrokenWalk(), TEST_PROJECT_CONFIG) is None


def test_an_unreadable_test_file_still_counts_by_its_path():
    ws = FakeWorkspace(files={"tests/test_a.py": "A"})
    original = ws.read_file

    def read(path: str) -> str:
        if path == "tests/test_a.py":
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
        return original(path)

    ws.read_file = read  # type: ignore[method-assign]
    unreadable = fingerprint_test_files(ws, TEST_PROJECT_CONFIG)
    assert unreadable not in (None, EMPTY_FINGERPRINT, fp({"tests/test_a.py": "A"}))


def test_a_real_workspace_counts_files_written_by_any_means(tmp_path: Path):
    ws = LocalWorkspace(str(tmp_path / "ws"))
    ws.write_file("calc.py", "x = 1\n")
    base = fingerprint_test_files(ws, TEST_PROJECT_CONFIG)
    assert base == EMPTY_FINGERPRINT
    ws.execute("mkdir -p tests/deep && printf 'def test_a():\\n    pass\\n' > tests/deep/test_a.py")
    changed = fingerprint_test_files(ws, TEST_PROJECT_CONFIG)
    assert changed not in (None, base)
    ws.execute("mkdir -p tests/deep/__pycache__ && touch tests/deep/__pycache__/x.pyc")
    assert fingerprint_test_files(ws, TEST_PROJECT_CONFIG) == changed


def test_the_local_walk_is_pruned_and_never_enters_an_excluded_directory(tmp_path: Path):
    ws = LocalWorkspace(str(tmp_path / "ws"))
    for path in ("src/a.test.js", "node_modules/x/test/y.test.js", "coverage/lcov.info", ".git/HEAD",
                 ".tddagents/project.json", "README.md"):
        ws.write_file(path, "x")
    pruned: list[str] = []

    def prune(directory: str) -> bool:
        pruned.append(directory)
        return JS.rules.is_excluded(directory)

    assert ws.walk_files(prune) == [
        "README.md", "src/a.test.js"]
    assert sorted(pruned) == [".git/", ".tddagents/", "coverage/", "node_modules/", "src/"]
    # The walker is preferred over a full listing, whose result is filtered the same way.
    assert project_files(ws, JS.rules) == ["README.md", "src/a.test.js"]
    fake = FakeWorkspace(files={"src/a.test.js": "A", "node_modules/x/test/y.test.js": "B"})
    assert project_files(fake, JS.rules) == ["src/a.test.js"]


def test_changed_compares_against_the_items_baseline():
    assert have_tests_changed("a", "b") is True
    assert have_tests_changed("a", "a") is False
    assert have_tests_changed(None, "a") is False
    # No baseline could be taken: any test file counts as new.
    assert have_tests_changed("a", None) is True
    assert have_tests_changed(EMPTY_FINGERPRINT, None) is False


def test_a_context_is_fingerprinted_with_its_declaration():
    ws = FakeWorkspace(files={"tests/test_a.py": "A"})
    declared = tool_context_for(AppStateStore(AppState(project_config=TEST_PROJECT_CONFIG)), workspace=ws)
    assert context_test_fingerprint(declared) == fingerprint_test_files(ws, TEST_PROJECT_CONFIG)
    assert context_test_fingerprint(tool_context_for(AppStateStore(), workspace=ws)) is None


def test_a_spawn_back_to_red_takes_a_new_baseline_and_any_other_keeps_it():
    ws = FakeWorkspace(files={"tests/test_a.py": "A"})
    ctx = tool_context_for(AppStateStore(AppState(tdd_item_test_baseline="kept", project_config=TEST_PROJECT_CONFIG)),
                           workspace=ws)
    assert spawn_test_baseline(PhaseLedger(), ctx) == fingerprint_test_files(ws, TEST_PROJECT_CONFIG)
    assert spawn_test_baseline(PhaseLedger(phase=TddPhase.GREEN, tests_written=True), ctx) == "kept"
    assert spawn_test_baseline(PhaseLedger(), tool_context_for(AppStateStore())) is None
