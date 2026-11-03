"""
The session's clean final run (`app/tdd/final_run.py`, 2026-11-03) and the language-generic core,
exercised on a fake POSIX-sh ecosystem (`tests/script_project.py`): no Python, no network.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Any

import app.main as entry
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.final_run import (
    FINAL_RUN_DIRNAME,
    NO_CONFIG,
    NO_RIPGREP,
    UNSUPPORTED,
    FinalRun,
    copy_project,
    run_final,
)
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.run_tests_tool.run_tests_tool import build_run_tests_tool
from app.tool import tool_context_for
from app.workspace.local import LocalWorkspace
from tests.conftest import FakeWorkspace
from tests.plan_helpers import CURRENT_ITEM_PLAN
from tests.script_project import SCRIPT_PROJECT_CONFIG, make_script_project

SKIP_TEST = "exit 77\n"
COMMAND = "Test: sh run-tests.sh"


def final(ws: LocalWorkspace) -> Any:
    return run_final(ws, ws.root.parent)


def test_a_project_whose_dependencies_are_all_declared_passes(script_project: LocalWorkspace):
    result = final(script_project)
    assert result.summary == "passed (exit 0: the test command completed, coverage could not be measured)"
    assert list(result.lines[:3]) == ["Setup: sh setup.sh", "Setup exit code: 0", "Setup output:"]
    assert result.lines[3] == "installed: strings"
    assert list(result.lines[5:9]) == [COMMAND, "Test exit code: 0 (the test command completed)",
                                       "JUnit report: 1 test(s), 0 failed, 0 errored, 0 skipped",
                                       "Coverage: not measured"]
    assert result.lines[-1] == "1 tests, 0 failed, 0 skipped"
    # The copy is gone, and the project itself was never touched by the run.
    assert not (script_project.root.parent / FINAL_RUN_DIRNAME).exists()
    assert not script_project.exists(".tddagents/reports/junit.xml")


def test_a_dependency_installed_but_never_declared_fails_in_the_clean_copy(tmp_path: Path):
    """The CPF incident, in any language: the workspace passes, the clean copy does not."""
    ws = make_script_project(tmp_path / "thread" / "project", deps="")
    store = AppStateStore(AppState(tdd_plan=CURRENT_ITEM_PLAN, project_config=SCRIPT_PROJECT_CONFIG,
                                   phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))
    asyncio.run(build_run_tests_tool(None).call({}, tool_context_for(store, workspace=ws)))
    assert store.get().phase_ledger.phase == TddPhase.REFACTOR  # the workspace has it installed
    result = final(ws)
    assert result.summary == ("NOT passing (exit 1: the test command failed, coverage could not be measured); the "
                              "test command exited 1, 1 test(s) failed")
    assert "JUnit report: 1 test(s), 1 failed, 0 errored, 0 skipped" in result.lines


def test_a_setup_that_fails_is_not_passing_with_its_output(tmp_path: Path):
    ws = make_script_project(tmp_path / "thread" / "project", deps="strings\nleft-pad\n")
    result = final(ws)
    assert result.summary == "NOT passing (setup exited 3)"
    assert result.lines == ("Setup: sh setup.sh", "Setup exit code: 3", "Setup output:",
                            "no such dependency: left-pad")
    assert not (ws.root.parent / FINAL_RUN_DIRNAME).exists()


def test_a_skipped_test_is_not_passing(script_project: LocalWorkspace):
    script_project.write_file("tests/test_later.sh", SKIP_TEST)
    result = final(script_project)
    assert result.summary == ("NOT passing (exit 0: the test command completed, coverage could not be measured); "
                              "1 test(s) were skipped")


def test_a_report_that_names_no_files_leaves_which_ran_unknown(tmp_path: Path):
    config = dataclasses.replace(SCRIPT_PROJECT_CONFIG, test="NO_FILES=1 sh run-tests.sh")
    ws = make_script_project(tmp_path / "thread" / "project", config=config)
    result = final(ws)
    assert result.summary.startswith("passed (exit 0")
    assert "Declared test files the JUnit report does not name: unknown (the report names no files)" in result.lines


def test_a_declared_test_file_the_report_does_not_name_is_not_passing(script_project: LocalWorkspace):
    script_project.write_file("tests/test_other.sh", "true\n")
    script_project.write_file("run-tests.sh", script_project.read_file("run-tests.sh").replace(
        "tests/test_*.sh", "tests/test_greet.sh"))
    result = final(script_project)
    assert result.summary.endswith("; a declared test file did not run: tests/test_other.sh")
    assert "Declared test files the JUnit report does not name: tests/test_other.sh" in result.lines


def test_a_command_that_writes_no_report_is_not_passing(tmp_path: Path):
    config = dataclasses.replace(SCRIPT_PROJECT_CONFIG, test="echo done", setup=None)
    ws = make_script_project(tmp_path / "thread" / "project", config=config)
    result = final(ws)
    assert result.summary == ("NOT passing (exit 0: the test command completed, coverage could not be measured); "
                              "the test command wrote no JUnit report")
    assert result.lines[0] == "Setup: none declared"
    assert "JUnit report: none was written" in result.lines


def copied_files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_the_copy_leaves_out_the_environment_git_tddagents_and_what_gitignore_ignores(tmp_path: Path):
    source = tmp_path / "src"
    ws = make_script_project(source)
    for path in (".git/HEAD", ".tddagents/settings.json", ".tddagents/reports/junit.xml", "build/out.o",
                 "lib/notes.log", "nested/.git/x", ".hg/store", "nested/.svn/entries"):
        ws.write_file(path, "x")
    ws.write_file(".gitignore", "build/\n*.log\n")
    copy_project(source, tmp_path / "copy", SCRIPT_PROJECT_CONFIG.rules)
    assert copied_files(tmp_path / "copy") == [
        ".gitignore", ".tddagents/project.json", "deps.txt", "lib/greet.sh", "run-tests.sh", "setup.sh",
        "tests/test_greet.sh", "vendor/strings/lib.sh"]
    assert (tmp_path / "copy/.tddagents/project.json").read_text() == ws.read_file(".tddagents/project.json")


def test_the_copy_honours_every_nested_ignore_file_as_ripgrep_lists_the_project(tmp_path: Path):
    """
    User decision 2026-11-03: the copy is upstream's listing (`rg --files --hidden`), so a nested
    `.gitignore` (and `.ignore`) applies in its own directory, with no `.git` in the project, while hidden
    files are copied and nothing above the project root is read.
    """
    (tmp_path / ".gitignore").write_text("*\n")  # above the project: never read
    source = tmp_path / "src"
    ws = make_script_project(source)
    for path in ("lib/gen/out.sh", "lib/keep.sh", "tests/cache/x", "tests/fixtures/data.txt", "docs/draft.md",
                 "docs/final.md", "dist/bundle.js", ".github/workflows/ci.yml", ".env.example"):
        ws.write_file(path, "x")
    ws.write_file(".gitignore", "/dist\n")
    ws.write_file("lib/.gitignore", "gen/\n")
    ws.write_file("tests/.gitignore", "cache/\n")
    ws.write_file("docs/.ignore", "draft.md\n")
    ws.write_file("env/strings/lib.sh", "x")  # the declared `exclude`
    copy_project(source, tmp_path / "copy", SCRIPT_PROJECT_CONFIG.rules)
    assert copied_files(tmp_path / "copy") == [
        ".env.example", ".github/workflows/ci.yml", ".gitignore", ".tddagents/project.json", "deps.txt",
        "docs/.ignore", "docs/final.md", "lib/.gitignore", "lib/greet.sh", "lib/keep.sh", "run-tests.sh",
        "setup.sh", "tests/.gitignore", "tests/fixtures/data.txt", "tests/test_greet.sh", "vendor/strings/lib.sh"]
    assert (tmp_path / "copy/setup.sh").stat().st_mode == (source / "setup.sh").stat().st_mode


def test_an_undeclared_dependency_kept_out_by_a_nested_gitignore_is_not_passing(tmp_path: Path):
    """The CPF incident again, the installed tree ignored by a nested `.gitignore` instead of `exclude`."""
    config = dataclasses.replace(SCRIPT_PROJECT_CONFIG, exclude=(), setup=None)
    ws = make_script_project(tmp_path / "thread" / "project", deps="", config=config)
    ws.write_file("env/.gitignore", "*\n")
    assert asyncio.run(build_run_tests_tool(None).call({}, tool_context_for(AppStateStore(AppState(
        tdd_plan=CURRENT_ITEM_PLAN, project_config=config,
        phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True))), workspace=ws))).exit_code == 0
    result = final(ws)
    assert result.summary.startswith("NOT passing (exit 1: the test command failed"), result.summary


def test_without_ripgrep_the_clean_final_run_is_unsupported(script_project: LocalWorkspace, monkeypatch: Any):
    from app.utils.ripgrep import RipgrepTimeoutError, RipgrepUnavailableError

    def unavailable(*_: Any) -> list[str]:
        raise RipgrepUnavailableError("ripgrep is not available at rg")

    monkeypatch.setattr("app.tdd.final_run.rip_grep", unavailable)
    assert final(script_project) == FinalRun(f"{NO_RIPGREP} (ripgrep is not available at rg)", ())
    assert NO_RIPGREP == "clean final run unsupported: ripgrep, which lists the project's files, could not run"

    def timed_out(*_: Any) -> list[str]:
        raise RipgrepTimeoutError("timed out")

    monkeypatch.setattr("app.tdd.final_run.rip_grep", timed_out)
    assert final(script_project) == FinalRun("could not copy the project: timed out", ())
    assert not (script_project.root.parent / FINAL_RUN_DIRNAME).exists()


def test_the_scrubbed_environment_has_no_session_script(script_project: LocalWorkspace, monkeypatch: Any,
                                                        tmp_path: Path):
    script = tmp_path / "env.sh"
    script.write_text("exit 9\n")
    monkeypatch.setenv("TDDAGENTS_ENV_FILE", str(script))
    assert script_project.execute("true").exit_code == 9  # the workspace honours it
    assert final(script_project).summary.startswith("passed")


def test_no_local_folder_or_no_declaration(tmp_path: Path):
    assert final_of(FakeWorkspace(kind="sandbox")) == (UNSUPPORTED, ())
    assert final_of(FakeWorkspace(kind="local")) == (UNSUPPORTED, ())
    assert final_of(LocalWorkspace(tmp_path / "p")) == (NO_CONFIG, ())
    assert (UNSUPPORTED, NO_CONFIG) == ("clean final run unsupported: the workspace is not a local folder",
                                        "not run: the project declares no test command")


def final_of(ws: Any) -> tuple[str, tuple[str, ...]]:
    result = run_final(ws, "/nonexistent")
    return result.summary, result.lines


def test_main_writes_the_final_run_beside_the_reports(script_project: LocalWorkspace, tmp_path: Path):
    artifacts = script_project.root.parent / "metrics_and_logging"
    artifacts.mkdir()
    summary = entry.write_final_test_run(str(artifacts), script_project)
    assert summary.startswith("passed (exit 0")
    text = (artifacts / "final_test_run.txt").read_text()
    assert f"Project: {script_project.root}\nResult: {summary}\n" in text
    assert "Setup: sh setup.sh" in text and COMMAND in text


# ── the language-generic core on the same ecosystem ─────────────────────────


def test_red_green_refactor_on_a_shell_project(tmp_path: Path):
    ws = make_script_project(tmp_path / "thread" / "project")
    ws.delete_file("tests/test_greet.sh")
    store = AppStateStore(AppState(tdd_plan=CURRENT_ITEM_PLAN, project_config=SCRIPT_PROJECT_CONFIG))
    from app.tdd.suite_fingerprint import fingerprint_test_files

    store.update(lambda s: dataclasses.replace(s, tdd_item_test_baseline=fingerprint_test_files(
        ws, SCRIPT_PROJECT_CONFIG)))
    ctx = tool_context_for(store, workspace=ws)
    tool = build_run_tests_tool(None)
    ws.write_file("tests/test_shout.sh", ". ./env/strings/lib.sh\n. ./lib/shout.sh\n[ \"$(shout hi)\" = HI! ]\n")
    red = asyncio.run(tool.call({}, ctx))
    assert "TDD ledger: RED → GREEN." in red.content and red.exit_code == 1
    ws.write_file("lib/shout.sh", "shout() { printf '%s!' \"$(upper \"$1\")\"; }\n")
    green = asyncio.run(tool.call({}, ctx))
    assert "TDD ledger: GREEN → REFACTOR." in green.content, green.content
    refactor = asyncio.run(tool.call({}, ctx))
    assert store.get().phase_ledger.is_cycle_complete, refactor.content
    # The dependency tree never counts as tests: `env/` is excluded.
    ws.write_file("env/strings/tests/test_x.sh", "exit 1\n")
    assert fingerprint_test_files(ws, SCRIPT_PROJECT_CONFIG) == fingerprint_test_files(
        LocalWorkspace(ws.root), SCRIPT_PROJECT_CONFIG)


def test_the_saved_config_is_the_scripts(script_project: LocalWorkspace):
    from app.tdd.project_config import load_project_config

    assert load_project_config(script_project) == SCRIPT_PROJECT_CONFIG
