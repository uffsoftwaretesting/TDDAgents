"""
RunTests and the final run in the sandbox: the suite runs sandboxed whenever the run's sandbox is on
(`excludedCommands` and `dangerouslyDisableSandbox` never apply to it), and the final run builds its own
sandbox over the clean copy (the project's settings copied in, `--settings` still applying, unknown hosts
denied), refuses to run when a required sandbox cannot, and resets it afterwards. Fake `bwrap` and `socat`
stand on `PATH`.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from app.state.app_state_store import AppStateStore
from app.tdd.final_run import FINAL_RUN_DIRNAME, final_run_sandbox, run_final
from app.tdd.run_tests_tool.run_tests_tool import _default_test_runner, sandbox_for_tests
from app.tool import tool_context_for
from app.utils.sandbox.sandbox_adapter import SandboxUnavailableError
from app.workspace.local import LocalWorkspace
from tests.conftest import FakeWorkspace
from tests.sandbox_doubles import bwrap_calls, install_fake_sandbox_tools
from tests.script_project import SCRIPT_PROJECT_CONFIG


@dataclass
class Sandbox:
    enabled: bool = True
    excluded: list[str] = field(default_factory=lambda: ["sh:*"])

    def is_sandboxing_enabled(self) -> bool:
        return self.enabled

    def get_excluded_commands(self) -> list[str]:
        return self.excluded

    def are_unsandboxed_commands_allowed(self) -> bool:
        return True


def test_the_suite_is_sandboxed_whenever_the_sandbox_is_on() -> None:
    assert sandbox_for_tests(SimpleContext(None)) is False
    assert sandbox_for_tests(SimpleContext(Sandbox(enabled=False))) is False
    # The test command matches an excluded pattern, and still runs sandboxed.
    assert sandbox_for_tests(SimpleContext(Sandbox())) is True


@dataclass
class SimpleContext:
    sandbox_manager: Any


def test_run_tests_hands_the_flag_to_the_workspace() -> None:
    ws = FakeWorkspace(kind="local")
    store = AppStateStore()
    store.update(lambda s: replace(s, project_config=SCRIPT_PROJECT_CONFIG))
    ctx = tool_context_for(store, workspace=ws)
    for sandbox, expected in ((None, False), (Sandbox(), True)):
        ws.sandboxed_log.clear()
        ctx.sandbox_manager = sandbox
        asyncio.run(_default_test_runner(ctx))
        assert ws.sandboxed_log == [expected]


# ── the final run ──────────────────────────────────────────────────────────────


@pytest.fixture
def fake_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = install_fake_sandbox_tools(tmp_path / "fakebin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'fakebin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_BWRAP_LOG", str(log))
    return log


def project_settings(ws: LocalWorkspace, sandbox: dict[str, Any], name: str = "settings.json") -> None:
    path = ws.root / ".tddagents" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sandbox": sandbox}))


def wrapped(log: Path) -> list[list[str]]:
    return [call for call in bwrap_calls(log) if call[:2] == ["--new-session", "--die-with-parent"]]


def test_the_final_run_sandboxes_setup_and_test_in_the_clean_copy(
        script_project: LocalWorkspace, fake_tools: Path) -> None:
    project_settings(script_project, {"enabled": True, "failIfUnavailable": True,
                                      "network": {"allowedDomains": ["pypi.org"]}})
    project_settings(script_project, {"excludedCommands": ["sh:*"]}, "settings.local.json")
    result = run_final(script_project, script_project.root.parent)
    assert result.summary.startswith("passed"), result.summary
    calls = wrapped(fake_tools)
    assert len(calls) == 2, "the declared setup, then the declared test"
    copy = str(script_project.root.parent / FINAL_RUN_DIRNAME / "project")
    for argv in calls:
        binds = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--bind"]
        assert copy in binds and str(script_project.root) not in binds
        assert "--unshare-net" in argv
    assert not (script_project.root.parent / FINAL_RUN_DIRNAME).exists()


def test_the_copy_carries_the_projects_settings(script_project: LocalWorkspace, fake_tools: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    import app.tdd.final_run as final_run

    project_settings(script_project, {"enabled": True})
    project_settings(script_project, {"excludedCommands": []}, "settings.local.json")
    seen: list[list[str]] = []
    real = final_run.final_run_sandbox

    def spy(target: Path, settings_path: str | None) -> Any:
        seen.append(sorted(os.listdir(target / ".tddagents")))
        return real(target, settings_path)

    monkeypatch.setattr(final_run, "final_run_sandbox", spy)
    run_final(script_project, script_project.root.parent)
    assert seen == [["project.json", "settings.json", "settings.local.json"]]


def test_a_required_sandbox_that_cannot_run_means_no_final_run(script_project: LocalWorkspace, fake_tools: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_BWRAP_PROBE_EXIT", "1")
    project_settings(script_project, {"enabled": True, "failIfUnavailable": True})
    result = run_final(script_project, script_project.root.parent)
    assert result.summary == (
        "NOT passing (sandbox required but unavailable: sandbox.enabled is set but dependencies are missing: "
        "bubblewrap (bwrap) cannot create a sandbox: bwrap: No permissions to create new namespace · install "
        "missing tools (e.g. apt install bubblewrap socat) or run /sandbox for details)")
    assert result.lines == () and wrapped(fake_tools) == []


def test_the_sessions_settings_flag_still_applies(script_project: LocalWorkspace, fake_tools: Path,
                                                  tmp_path: Path) -> None:
    project_settings(script_project, {"enabled": True, "failIfUnavailable": True})
    flag = tmp_path / "flag.json"
    flag.write_text(json.dumps({"sandbox": {"enabled": False}}))
    result = run_final(script_project, script_project.root.parent, settings_path=str(flag))
    assert result.summary.startswith("passed") and bwrap_calls(fake_tools) == []


def test_without_sandbox_settings_nothing_is_wrapped(script_project: LocalWorkspace, fake_tools: Path) -> None:
    assert run_final(script_project, script_project.root.parent).summary.startswith("passed")
    assert bwrap_calls(fake_tools) == []


def test_the_final_runs_sandbox_asks_no_one_and_is_reset(tmp_path: Path, fake_tools: Path) -> None:
    target = tmp_path / "copy"
    (target / ".tddagents").mkdir(parents=True)
    (target / ".tddagents" / "settings.json").write_text(json.dumps({"sandbox": {"enabled": True}}))
    sandbox = final_run_sandbox(target, None)
    try:
        assert sandbox.is_sandboxing_enabled() and sandbox.get_proxy_port()
        assert sandbox.original_cwd == str(target)
        assert asyncio.run(sandbox.base.filter_network_request(443, "unknown.example.com", None)) is False
    finally:
        sandbox.reset()
    assert sandbox.get_proxy_port() is None
    (target / ".tddagents" / "settings.json").write_text(json.dumps({"sandbox": {"enabled": True,
                                                                                 "failIfUnavailable": True}}))
    os.environ["FAKE_BWRAP_PROBE_EXIT"] = "1"
    try:
        with pytest.raises(SandboxUnavailableError, match="sandbox required but unavailable"):
            final_run_sandbox(target, None)
    finally:
        del os.environ["FAKE_BWRAP_PROBE_EXIT"]


def test_main_passes_the_settings_flag_to_the_final_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import app.main as entry
    from app.tdd.final_run import FinalRun

    seen: list[Any] = []

    def fake_run_final(workspace: Any, run_dir: Any, *, settings_path: str | None = None) -> FinalRun:
        seen.append(settings_path)
        return FinalRun("passed", ())

    monkeypatch.setattr("app.tdd.final_run.run_final", fake_run_final)
    (tmp_path / "artifacts").mkdir()
    entry.write_final_test_run(str(tmp_path / "artifacts"), SimpleContext(None), "/x/flag.json")
    assert seen == ["/x/flag.json"]
