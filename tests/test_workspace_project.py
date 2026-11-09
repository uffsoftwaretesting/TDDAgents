"""
Phase 13: each session's project has its own folder outside TDDAgents', with settings seeded from
TDDAgents' defaults and every lookup rooted at it. Since 2026-11-03 no environment is built for it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.cli.print import project_root_of, run_permission_context
from app.query.config import build_run_config
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.junit_report import JUnitReport
from app.tdd.run_tests_tool.run_tests_tool import PHASE_RULES, SuiteExecutionResult, build_run_tests_tool
from app.tool import tool_context_for
from app.types.permissions import PermissionMode
from app.workspace.local import LocalWorkspace, workspaces_root
from app.workspace.project import SHIPPED_SETTINGS, prepare_project
from tests.conftest import FakeWorkspace
from tests.plan_helpers import CURRENT_ITEM_PLAN, TEST_PROJECT_CONFIG

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

def test_a_project_is_a_folder_with_seeded_settings_and_no_git_of_ours(tmp_path):
    """As upstream, TDDAgents never runs `git init` nor commits (Phase 13, user decision)."""
    project = prepare_project(tmp_path / "p")
    assert sorted(p.name for p in project.iterdir()) == [".tddagents"]
    assert (project / ".tddagents" / "settings.json").read_text() == SHIPPED_SETTINGS.read_text()
    assert SHIPPED_SETTINGS == REPO / ".tddagents" / "settings.json"


def test_preparing_again_never_overwrites_the_projects_settings(tmp_path):
    project = prepare_project(tmp_path / "p")
    (project / ".tddagents" / "settings.json").write_text('{"permissions": {}}')
    prepare_project(project)
    assert (project / ".tddagents" / "settings.json").read_text() == '{"permissions": {}}'


def test_without_shipped_settings_none_are_seeded(tmp_path):
    project = prepare_project(tmp_path / "p", shipped_settings=tmp_path / "absent.json")
    assert not (project / ".tddagents").exists()


def test_the_shipped_settings_hold_no_allow_rules_and_the_sandbox():
    """
    As upstream, which ships no allow rules (user decision 2026-11-03): the default mode; and since
    2026-11-08 the sandbox (plan section 4): on, required, never bypassed, a private HOME with the project
    readable, `project.json` written only through an approved edit, and the registries' hosts.
    """
    assert json.loads(SHIPPED_SETTINGS.read_text()) == {
        "permissions": {"defaultMode": "acceptEdits"},
        "sandbox": {
            "enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
            "filesystem": {"denyRead": ["~"], "allowRead": ["."], "denyWrite": [".tddagents/project.json"]},
            "network": {"allowedDomains": [
                "pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "storage.googleapis.com",
                "proxy.golang.org", "sum.golang.org", "index.crates.io", "static.crates.io",
                "repo.maven.apache.org", "repo1.maven.org", "services.gradle.org", "plugins.gradle.org",
                "api.nuget.org", "rubygems.org", "index.rubygems.org", "repo.packagist.org"]},
        },
    }


def test_an_external_environment_script_runs_after_the_profile(tmp_path):
    # `bashProvider.ts`: the session env script (upstream's `CLAUDE_ENV_FILE`) runs after the shell
    # profile, so a profile that prepends its own bin (as `~/.profile` does) cannot shadow it.
    home = tmp_path / "home"
    (home / "bin").mkdir(parents=True)
    (home / ".bash_profile").write_text(f'export PATH="{home / "bin"}:$PATH"\nexport WHO=profile\n')
    script = tmp_path / "env.sh"
    script.write_text("export WHO=script\n")
    ws = LocalWorkspace(tmp_path / "p", env={"TDDAGENTS_ENV_FILE": str(script), "HOME": str(home)})
    assert ws.execute('echo "$WHO"').stdout.strip() == "script"


# ── everything is read from the project ──────────────────────────────────────

def test_settings_come_from_the_project_not_the_process_cwd(tmp_path, monkeypatch):
    cwd = tmp_path / "elsewhere"
    (cwd / ".tddagents").mkdir(parents=True)
    (cwd / ".tddagents" / "settings.json").write_text(json.dumps({"permissions": {"defaultMode": "plan"}}))
    monkeypatch.chdir(cwd)
    project = prepare_project(tmp_path / "p")
    ws = LocalWorkspace(project)
    assert project_root_of(ws) == project
    perm = run_permission_context(build_run_config("r"), ws, home=tmp_path / "h",
                                  managed_dir=tmp_path / "etc")
    assert perm.mode == PermissionMode.ACCEPT_EDITS  # the project's seeded settings
    assert project_root_of(object()) == cwd and project_root_of(ws, tmp_path) == tmp_path


# ── a skipped test is not a pass ─────────────────────────────────────────────


def skip_lines(phase: TddPhase) -> list[str]:
    """The observation and the ledger line of a passing run with one skipped test (2026-10-28: no advice)."""
    from app.tdd.run_tests_tool.run_tests_tool import PHASE_RULES

    return ["Exit code 0 (the test command completed)", "JUnit report: 4 test(s), 0 failed, 0 errored, 1 skipped",
            "Declared test files the run did not run: unknown (the JUnit report names no test files)",
            "Coverage: not measured",
            f"TDD ledger: still {phase.value}. {PHASE_RULES[phase]}: 1 test(s) were skipped, the JUnit report names "
            "no test files."]


@pytest.mark.parametrize("phase", [TddPhase.GREEN, TddPhase.REFACTOR])
def test_a_pass_with_skips_does_not_pass_green_or_refactor(phase):
    ledger = PhaseLedger(phase=phase, tests_written=True, green_passed=phase == TddPhase.REFACTOR)
    store = AppStateStore(AppState(tdd_plan=CURRENT_ITEM_PLAN, project_config=TEST_PROJECT_CONFIG, phase_ledger=ledger))
    result = SuiteExecutionResult(exit_code=0, stdout="3 passed, 1 skipped", report=JUnitReport(tests=4, skipped=1))
    res = asyncio.run(build_run_tests_tool(lambda c: result).call({}, tool_context_for(store)))
    assert res.content.split("\n\n", 1)[0].splitlines() == skip_lines(phase)
    assert store.get().phase_ledger == ledger
    assert not store.get().phase_ledger.is_cycle_complete


def test_skips_do_not_matter_in_red():
    store = AppStateStore(AppState(project_config=TEST_PROJECT_CONFIG))
    ctx = tool_context_for(store, workspace=FakeWorkspace(files={"tests/test_a.py": "x"}))
    result = SuiteExecutionResult(exit_code=0, report=JUnitReport(tests=1, skipped=1))
    asyncio.run(build_run_tests_tool(lambda c: result).call({}, ctx))
    assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.GREEN, tests_written=True)


def test_real_pytest_skips_keep_green_until_the_tool_is_there(python_workspace):
    ws = python_workspace
    ws.write_file("calc.py", "def add(a, b):\n    return a + b\n")
    ws.write_file("tests/test_calc.py", (
        "import pytest\nfrom calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
        "\n\ndef test_lint():\n    pytest.importorskip('not_installed_anywhere')\n"))
    store = AppStateStore(AppState(tdd_plan=CURRENT_ITEM_PLAN, project_config=TEST_PROJECT_CONFIG,
                                   phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))
    res = asyncio.run(build_run_tests_tool(None).call({}, tool_context_for(store, workspace=ws)))
    assert res.exit_code == 0 and "JUnit report: 2 test(s), 0 failed, 0 errored, 1 skipped\n" in res.content
    assert (f"TDD ledger: still GREEN. {PHASE_RULES[TddPhase.GREEN]}: 1 test(s) were skipped.") in res.content
    assert store.get().phase_ledger.phase == TddPhase.GREEN


# ── what the model is told ───────────────────────────────────────────────────

def test_the_environment_names_the_project_and_whether_it_is_a_git_repo_but_no_venv(tmp_path):
    """As upstream: the working directory and git, nothing about how to install (Phase 13)."""
    import subprocess as sp

    from app.context import compute_env_info, compute_simple_env_info

    project = prepare_project(tmp_path / "p")
    ws = LocalWorkspace(project)
    assert f" - Primary working directory: {project}\n  - Is a git repository: false\n" in compute_simple_env_info(
        ws, "m")
    sp.run(["git", "init", "--quiet", str(project)], check=True)  # the model (or the user) may make it one
    simple = compute_simple_env_info(ws, "m")
    assert "  - Is a git repository: true\n" in simple
    worker = compute_env_info(ws, "m")
    assert "Is directory a git repo: Yes\n" in worker
    for text in (simple, worker):
        assert "venv" not in text and "virtual" not in text


def test_the_bash_prompt_carries_upstreams_commit_rule():
    from app.tools.bash_tool.bash_tool import build_bash_tool

    prompt = build_bash_tool().prompt
    assert "# Committing changes with git" in prompt
    assert "NEVER commit changes unless the user explicitly asks you to." in prompt
    assert "Only create commits when requested by the user. If unclear, ask first." in prompt
