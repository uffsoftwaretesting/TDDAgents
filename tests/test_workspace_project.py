"""
Phase 13: each session's project has its own folder outside TDDAgents', a git working tree with
its own venv, settings seeded from TDDAgents' defaults, and every lookup rooted at it.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app.cli.print import project_root_of, run_permission_context
from app.query.config import build_run_config
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.run_tests_tool.run_tests_tool import (
    SuiteExecutionResult,
    build_run_tests_tool,
    skipped_count,
)
from app.tool import tool_context_for
from app.types.permissions import PermissionMode
from app.workspace.local import LocalWorkspace, workspaces_root
from app.workspace.project import GITIGNORE, SHIPPED_SETTINGS, prepare_project
from tests.conftest import FakeWorkspace

REPO = Path(__file__).resolve().parents[1]


# ── where projects live ──────────────────────────────────────────────────────

def test_projects_live_under_the_config_home_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("TDDAGENTS_WORKSPACES_DIR", raising=False)
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "home"))
    assert workspaces_root() == (tmp_path / "home" / "workspaces").resolve()
    monkeypatch.setenv("TDDAGENTS_WORKSPACES_DIR", str(tmp_path / "elsewhere"))
    assert workspaces_root() == (tmp_path / "elsewhere").resolve()
    ws = LocalWorkspace.for_run("tdd-1")
    assert ws.root == (tmp_path / "elsewhere" / "tdd-1" / "project").resolve()
    assert REPO not in ws.root.parents


# ── the project folder ───────────────────────────────────────────────────────

def test_a_project_is_a_git_tree_with_an_ignore_file_and_seeded_settings(tmp_path):
    project = prepare_project(tmp_path / "p")
    assert (project / ".git").is_dir()
    assert (project / ".gitignore").read_text() == GITIGNORE
    assert (project / ".tddagents" / "settings.json").read_text() == SHIPPED_SETTINGS.read_text()
    assert SHIPPED_SETTINGS == REPO / ".tddagents" / "settings.json"
    status = subprocess.run(["git", "status", "--porcelain", "--ignored"], cwd=project, capture_output=True,
                            text=True, check=True).stdout
    assert ".gitignore" in status  # untracked: TDDAgents never commits


def test_preparing_again_never_overwrites_the_projects_own_files(tmp_path):
    project = prepare_project(tmp_path / "p")
    (project / ".gitignore").write_text("mine\n")
    (project / ".tddagents" / "settings.json").write_text('{"permissions": {}}')
    head = (project / ".git" / "HEAD").read_text()
    prepare_project(project)
    assert (project / ".gitignore").read_text() == "mine\n"
    assert (project / ".tddagents" / "settings.json").read_text() == '{"permissions": {}}'
    assert (project / ".git" / "HEAD").read_text() == head


def test_without_shipped_settings_none_are_seeded(tmp_path):
    project = prepare_project(tmp_path / "p", shipped_settings=tmp_path / "absent.json")
    assert not (project / ".tddagents").exists()


def test_the_projects_venv_comes_first_on_path_even_after_the_login_profile(tmp_path, session_python):
    # `bash -l` runs the profile after the environment is set; a profile that prepends its own
    # bin (as `~/.profile` does with `~/.local/bin`) must not shadow the project's venv.
    home = tmp_path / "home"
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "python").write_text("#!/bin/sh\necho shadow\n")
    (home / "bin" / "python").chmod(0o755)
    (home / ".bash_profile").write_text(f'export PATH="{home / "bin"}:$PATH"\n')
    ws = LocalWorkspace(tmp_path / "p", env={**session_python.env(), "HOME": str(home)})
    out = ws.execute("python -c 'import sys; print(sys.prefix)'").stdout.strip()
    assert out == str(session_python.venv_dir)
    assert ws.virtual_env == session_python.venv_dir
    assert LocalWorkspace(tmp_path / "q").virtual_env is None


# ── everything is read from the project ──────────────────────────────────────

def test_settings_come_from_the_project_not_the_process_cwd(tmp_path, monkeypatch):
    cwd = tmp_path / "elsewhere"
    (cwd / ".tddagents").mkdir(parents=True)
    (cwd / ".tddagents" / "settings.json").write_text(json.dumps({"permissions": {"defaultMode": "plan"}}))
    monkeypatch.chdir(cwd)
    project = prepare_project(tmp_path / "p")
    ws = LocalWorkspace(project)
    assert project_root_of(ws) == project
    perm = run_permission_context(build_run_config("r", postgres_checkpointing=False), ws, home=tmp_path / "h",
                                  managed_dir=tmp_path / "etc")
    assert perm.mode == PermissionMode.ACCEPT_EDITS  # the project's seeded settings
    assert project_root_of(object()) == cwd and project_root_of(ws, tmp_path) == tmp_path


# ── a skipped test is not a pass ─────────────────────────────────────────────

@pytest.mark.parametrize("output, count", [
    ("== 3 passed, 1 skipped in 0.1s ==", 1),
    ("== 2 skipped in 0.1s ==", 2),
    ("== 1 passed, 10 skipped, 1 warning in 0.1s ==", 10),
    ("== 4 passed in 0.1s ==", 0),
    ("", 0),
])
def test_skipped_tests_are_counted_from_pytests_summary(output, count):
    assert skipped_count(output) == count


SKIP_HEADER = ("Tests ran without failures, but 1 test(s) were skipped, so this run does not count as passing: a "
               "skipped test checks nothing. Install what they need into the project's virtual environment (and "
               "record it in the requirements files), or fix why they skip, then run RunTests again. The skip "
               "reasons are listed in the short test summary below.")


@pytest.mark.parametrize("phase", [TddPhase.GREEN, TddPhase.REFACTOR])
def test_a_pass_with_skips_does_not_pass_green_or_refactor(phase):
    ledger = PhaseLedger(phase=phase, tests_written=True, green_passed=phase == TddPhase.REFACTOR)
    store = AppStateStore(AppState(phase_ledger=ledger))
    result = SuiteExecutionResult(exit_code=0, stdout="== 3 passed, 1 skipped ==")
    res = asyncio.run(build_run_tests_tool(lambda p: result).call({}, tool_context_for(store)))
    assert res.content.splitlines()[0] == SKIP_HEADER
    assert store.get().phase_ledger == ledger
    assert not store.get().phase_ledger.is_cycle_complete


def test_an_injected_runner_can_report_its_skips():
    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))
    result = SuiteExecutionResult(exit_code=0, stdout="all good", skipped=1)
    asyncio.run(build_run_tests_tool(lambda p: result).call({}, tool_context_for(store)))
    assert store.get().phase_ledger.phase == TddPhase.GREEN
    zero = SuiteExecutionResult(exit_code=0, stdout="== 1 skipped ==", skipped=0)
    asyncio.run(build_run_tests_tool(lambda p: zero).call({}, tool_context_for(store)))
    assert store.get().phase_ledger.phase == TddPhase.REFACTOR


def test_skips_do_not_matter_in_red():
    store = AppStateStore()
    ctx = tool_context_for(store, workspace=FakeWorkspace(files={"tests/test_a.py": "x"}))
    result = SuiteExecutionResult(exit_code=0, stdout="== 1 skipped ==")
    asyncio.run(build_run_tests_tool(lambda p: result).call({}, ctx))
    assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.GREEN, tests_written=True)


def test_real_pytest_skips_keep_green_until_the_tool_is_there(python_workspace):
    ws = python_workspace
    ws.write_file("calc.py", "def add(a, b):\n    return a + b\n")
    ws.write_file("tests/test_calc.py", (
        "import pytest\nfrom calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
        "\n\ndef test_lint():\n    pytest.importorskip('not_installed_anywhere')\n"))
    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))
    res = asyncio.run(build_run_tests_tool(None).call({"test_path": "tests"}, tool_context_for(store, workspace=ws)))
    assert res.exit_code == 0 and res.content.startswith("Tests ran without failures, but 1 test(s) were skipped")
    assert "SKIPPED" in res.content and "not_installed_anywhere" in res.content  # -rs lists the reason
    assert store.get().phase_ledger.phase == TddPhase.GREEN


# ── what the model is told ───────────────────────────────────────────────────

def test_the_environment_names_the_project_its_git_repo_and_its_venv(tmp_path):
    from app.context import compute_env_info, compute_simple_env_info

    project = prepare_project(tmp_path / "p")
    ws = LocalWorkspace(project, env={"VIRTUAL_ENV": str(project / ".venv")})
    simple = compute_simple_env_info(ws, "m")
    assert f" - Primary working directory: {project}\n  - Is a git repository: true\n" in simple
    assert f" - Python virtual environment: {project / '.venv'} (active for Bash and RunTests)\n" in simple
    worker = compute_env_info(ws, "m")
    assert f"Is directory a git repo: Yes\nPython virtual environment: {project / '.venv'}" in worker


def test_the_project_section_tells_the_model_to_install_and_record_dependencies():
    from app.context import _file_section

    text = _file_section("environment.md")()
    for phrase in ("pip install", "requirements.txt", "requirements-dev.txt", "Never let a test skip",
                   "NEVER commit changes unless the user explicitly asks you to"):
        assert phrase in text
    assert "{{" not in text
