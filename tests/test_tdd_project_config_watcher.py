"""
`.tddagents/project.json` is read whenever it changes during a session (2026-11-08):
`app/tdd/project_config_watcher.py` (the port of `cronScheduler.ts`'s file watch) and
`app/tdd/validate_project_config_edit.py` (the port of `validateInputForSettingsFileEdit`).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.plan import TddPlan
from app.tdd.project_config import PROJECT_CONFIG_FILE, ProjectConfig, invalid_project_config_note
from app.tdd.project_config_watcher import (
    FILE_STABILITY_MS,
    ProjectConfigWatcher,
    apply_project_config,
    apply_project_config_text,
    start_project_config_watcher,
)
from app.tdd.suite_fingerprint import fingerprint_test_files
from app.tdd.validate_project_config_edit import (
    PROJECT_CONFIG_VALIDATION_ERROR_CODE,
    is_project_config_path,
    validate_input_for_project_config_edit,
)
from app.workspace.local import LocalWorkspace
from tests.conftest import FakeWorkspace
from tests.plan_helpers import CURRENT_ITEM_PLAN

CONFIG = ProjectConfig(test="npx vitest run", test_files=("tests/**/*.test.ts",),
                       test_support_files=("vitest.config.ts",))
FILES = {"tests/a.test.ts": "it('a')", "vitest.config.ts": "export default {}", "src/a.ts": "x"}


def store(config: ProjectConfig | None = CONFIG, plan: TddPlan = CURRENT_ITEM_PLAN,
          baseline: str | None = "the item's baseline") -> AppStateStore:
    return AppStateStore(AppState(tdd_plan=plan, project_config=config, tdd_item_test_baseline=baseline,
                                  phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))


def apply_text(s: AppStateStore, ws: Any, text: str | None) -> bool:
    return apply_project_config_text(s.get, s.update, ws, text)


# ── what a change does ───────────────────────────────────────────────────────

def test_an_edited_valid_configuration_is_the_sessions_and_its_echo_changes_nothing():
    s, ws = store(), FakeWorkspace(files=dict(FILES))
    edited = replace(CONFIG, test="npx vitest run --reporter=junit")
    assert apply_text(s, ws, edited.to_json()) is True
    assert s.get().project_config == edited and s.get().project_config_issues == ()
    # Only the command changed: which files are tests did not, so RED's baseline stays.
    assert s.get().tdd_item_test_baseline == "the item's baseline"
    # TDDAgents' own write (or any equal content) is idempotent, as cron's reload.
    assert apply_text(s, ws, edited.to_json()) is False


def test_new_test_patterns_take_reds_baseline_again_and_keep_the_ledger():
    s, ws = store(), FakeWorkspace(files=dict(FILES))
    widened = replace(CONFIG, test_files=("tests/", "vitest.config.ts"))
    assert apply_text(s, ws, widened.to_json()) is True
    assert s.get().tdd_item_test_baseline == fingerprint_test_files(ws, widened)
    assert s.get().tdd_item_test_baseline != "the item's baseline"
    assert s.get().phase_ledger == PhaseLedger(phase=TddPhase.GREEN, tests_written=True)


def test_without_a_current_sub_requirement_the_baseline_is_left_alone():
    s = store(plan=TddPlan())
    assert apply_project_config(s.get, s.update, FakeWorkspace(files=dict(FILES)),
                                replace(CONFIG, exclude=("dist/",))) is True
    assert s.get().tdd_item_test_baseline == "the item's baseline"


def test_an_invalid_file_is_no_configuration_with_its_issues():
    s, ws = store(), FakeWorkspace(files=dict(FILES))
    broken = json.dumps({"version": 1, "testFiles": ["tests/"], "surprise": True})
    assert apply_text(s, ws, broken) is True
    state = s.get()
    assert state.project_config is None
    assert state.project_config_issues == (
        "unknown keys: surprise (the keys are version, setup, test, listTests, junitReport, coverageReport, testFiles, "
        "testSupportFiles, manifestFiles, exclude)",
        "`test` is required: the shell command that runs the whole test suite")
    # The patterns went away with the configuration: the baseline follows them.
    assert state.tdd_item_test_baseline is None
    assert apply_text(s, ws, "{ not json") is True
    assert s.get().project_config_issues[0].startswith("the configuration is not valid JSON: ")


def test_a_deleted_file_is_no_configuration_without_issues():
    s = store()
    assert apply_text(s, FakeWorkspace(files=dict(FILES)), None) is True
    assert s.get().project_config is None and s.get().project_config_issues == ()


def test_a_valid_file_after_an_invalid_one_clears_the_issues():
    s, ws = store(), FakeWorkspace(files=dict(FILES))
    apply_text(s, ws, "[]")
    assert s.get().project_config_issues == ("the configuration must be a JSON object",)
    assert apply_text(s, ws, CONFIG.to_json()) is True
    assert s.get().project_config == CONFIG and s.get().project_config_issues == ()


def test_the_note_names_the_file_and_its_issues():
    assert invalid_project_config_note(()) == ""
    note = invalid_project_config_note(("a", "b"))
    assert note == f" {PROJECT_CONFIG_FILE} is invalid, so it declares nothing: a; b."


# ── the watch ────────────────────────────────────────────────────────────────

def test_a_change_counts_once_the_file_stayed_the_same_and_a_deletion_at_once(tmp_path: Path):
    path = tmp_path / "project.json"
    path.write_text("first", encoding="utf-8")
    seen: list[str | None] = []
    watcher = ProjectConfigWatcher(path, seen.append)
    asyncio.run(watcher.initialize())
    asyncio.run(watcher.dispose())
    watcher.poll_once(now=0.0)
    assert seen == []  # `ignoreInitial`: the file as it was at open
    path.write_text("second, longer", encoding="utf-8")
    watcher.poll_once(now=10.0)
    watcher.poll_once(now=10.0 + (FILE_STABILITY_MS - 1) / 1000)
    assert seen == []  # not stable yet
    watcher.poll_once(now=10.0 + FILE_STABILITY_MS / 1000)
    assert seen == ["second, longer"]
    watcher.poll_once(now=20.0)
    assert seen == ["second, longer"]  # unchanged since
    path.unlink()
    watcher.poll_once(now=21.0)
    assert seen == ["second, longer", None]
    path.write_text("third", encoding="utf-8")
    watcher.poll_once(now=30.0)
    watcher.poll_once(now=31.0)
    assert seen == ["second, longer", None, "third"]


def test_a_file_that_keeps_changing_is_read_only_once_it_settles(tmp_path: Path):
    path = tmp_path / "project.json"
    seen: list[str | None] = []
    watcher = ProjectConfigWatcher(path, seen.append, stability_ms=1000)
    watcher.poll_once(now=0.0)
    path.write_text("a", encoding="utf-8")
    watcher.poll_once(now=1.0)
    path.write_text("ab", encoding="utf-8")
    watcher.poll_once(now=1.9)
    watcher.poll_once(now=2.5)
    assert seen == []
    watcher.poll_once(now=2.9)
    assert seen == ["ab"]


def test_the_session_watcher_applies_an_approved_edit_of_the_projects_file(tmp_path: Path):
    ws = LocalWorkspace(str(tmp_path))
    for name, text in FILES.items():
        ws.write_file(name, text)
    ws.write_file(PROJECT_CONFIG_FILE, CONFIG.to_json())
    s = store()

    async def scenario() -> None:
        watcher = await start_project_config_watcher(ws, s.get, s.update)
        assert watcher is not None and watcher.path == str(tmp_path / PROJECT_CONFIG_FILE)
        await watcher.dispose()
        edited = replace(CONFIG, test_files=("tests/", "vitest.config.ts"))
        ws.write_file(PROJECT_CONFIG_FILE, edited.to_json())
        watcher.poll_once(now=100.0)
        watcher.poll_once(now=101.0)
        assert s.get().project_config == edited

    asyncio.run(scenario())


def test_a_workspace_without_a_host_folder_is_not_watched():
    s = store()
    assert asyncio.run(start_project_config_watcher(FakeWorkspace(), s.get, s.update)) is None


def test_the_poll_loop_reads_a_change_and_stops_on_dispose(tmp_path: Path):
    path = tmp_path / "project.json"
    seen: list[str | None] = []

    async def scenario() -> None:
        watcher = ProjectConfigWatcher(path, seen.append, stability_ms=0, poll_interval_ms=5)
        await watcher.initialize()
        path.write_text("x", encoding="utf-8")
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.01)
        await watcher.dispose()
        assert watcher.disposed

    asyncio.run(scenario())
    assert seen == ["x"]


# ── the Edit check ───────────────────────────────────────────────────────────

VALID = CONFIG.to_json()


@pytest.mark.parametrize(("path", "expected"), [
    ("/proj/.tddagents/project.json", True), (".tddagents/project.json", True),
    ("/proj/.tddagents/settings.json", False), ("/elsewhere/.tddagents/project.json", False),
])
def test_only_the_projects_file_is_checked(path: str, expected: bool):
    assert is_project_config_path(path, "/proj") is expected


def test_an_edit_that_would_break_a_valid_file_is_refused_with_its_issues():
    result = validate_input_for_project_config_edit(
        "/proj/.tddagents/project.json", "/proj", VALID, lambda: VALID.replace('"test"', '"tests"'))
    assert result is not None and not result.valid
    assert result.error_code == PROJECT_CONFIG_VALIDATION_ERROR_CODE == 10
    assert result.message.startswith(f"{PROJECT_CONFIG_FILE} validation failed after edit:\n- unknown keys: tests")
    assert "\n- `test` is required" in result.message


def test_an_edit_that_excludes_the_tests_is_refused():
    """Gap (d): the Edit check uses `parse_project_config`, so it refuses an `exclude` that hides tests."""
    data = json.loads(VALID)
    edited = json.dumps({**data, "exclude": [*data.get("exclude", []), "tests/", "*.log"]})
    result = validate_input_for_project_config_edit("/proj/.tddagents/project.json", "/proj", VALID, lambda: edited)
    assert result is not None and not result.valid
    assert result.message.endswith(
        "\n- `exclude` accepts generated directories only (patterns ending in `/` that cover no `testFiles` "
        "pattern): tests/, *.log")


def test_a_watched_file_that_excludes_the_tests_is_no_configuration():
    s, ws = store(), FakeWorkspace(files=dict(FILES))
    assert apply_text(s, ws, replace(CONFIG, exclude=("tests/",)).to_json()) is True
    assert s.get().project_config is None
    assert s.get().project_config_issues == (
        "`exclude` accepts generated directories only (patterns ending in `/` that cover no `testFiles` pattern): "
        "tests/",)


def test_a_valid_edit_an_invalid_file_and_another_file_pass():
    good = VALID.replace("npx vitest run", "npm test")
    assert validate_input_for_project_config_edit("/proj/.tddagents/project.json", "/proj", VALID,
                                                  lambda: good) is None
    # Already invalid: the edit may be the fix, so nothing is refused.
    assert validate_input_for_project_config_edit("/proj/.tddagents/project.json", "/proj", "{",
                                                  lambda: "{{") is None

    def never() -> str:
        raise AssertionError("another file's content is never built")

    assert validate_input_for_project_config_edit("/proj/src/a.json", "/proj", VALID, never) is None


def test_the_edit_tool_refuses_to_break_the_projects_configuration():
    from app.tool import tool_context_for
    from app.tools.file_edit_tool.file_edit_tool import build_edit_tool
    from app.tools.file_read_tool.file_read_tool import build_read_file_tool

    files = {PROJECT_CONFIG_FILE: VALID, "notes.json": VALID}
    ctx = tool_context_for(store(), workspace=FakeWorkspace(files=dict(files)))
    for path in files:
        asyncio.run(build_read_file_tool().call({"file_path": path}, ctx))
    tool = build_edit_tool()
    breaking = {"file_path": PROJECT_CONFIG_FILE, "old_string": '"version": 1', "new_string": '"version": 2'}
    result = tool.validate_input(breaking, ctx)
    assert (result.valid, result.error_code) == (False, PROJECT_CONFIG_VALIDATION_ERROR_CODE)
    assert result.message == f"{PROJECT_CONFIG_FILE} validation failed after edit:\n- `version` must be 1"
    fine = {"file_path": PROJECT_CONFIG_FILE, "old_string": "npx vitest run", "new_string": "npm test"}
    assert tool.validate_input(fine, ctx).valid
    # Another file with the same content is not the configuration: no check.
    assert tool.validate_input({**breaking, "file_path": "notes.json"}, ctx).valid
