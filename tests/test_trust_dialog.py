"""
Workspace trust: the global config's project entries (`app/utils/config.py`), the trust dialog
(`app/components/trust_dialog/trust_dialog.py`), and where the interactive session asks it
(`app/session/driver.py`, `app/main.py`).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.main as entry
from app.bootstrap.state import get_session_trust_accepted, set_session_trust_accepted
from app.components.trust_dialog.trust_dialog import (
    ABILITIES,
    SAFETY_CHECK,
    TrustDialogExit,
    accept_trust,
    render_trust_dialog,
    show_trust_dialog,
)
from app.services.mcp.config import get_global_config_path
from app.utils.config import (
    check_has_trust_dialog_accepted,
    get_current_project_config,
    get_global_config,
    get_project_path_for_config,
    is_path_trusted,
    normalize_path_for_config_key,
    reset_trust_dialog_accepted_cache_for_testing,
    save_current_project_config,
)
from tests.test_repl import FakeIO


@pytest.fixture
def untrusted() -> None:
    """This test's process has not trusted anything (the suite's default trusts every folder)."""
    set_session_trust_accepted(False)
    reset_trust_dialog_accepted_cache_for_testing()


def trust(path: Path) -> None:
    config_path = get_global_config_path()
    data = json.loads(config_path.read_text()) if config_path.exists() else {}
    data.setdefault("projects", {})[str(path)] = {"hasTrustDialogAccepted": True}
    config_path.write_text(json.dumps(data))


# ── the config ───────────────────────────────────────────────────────────────

def test_normalize_path_for_config_key() -> None:
    assert normalize_path_for_config_key("/a/b/../c/") == "/a/c"
    assert normalize_path_for_config_key("C:\\Users\\x") == "C:/Users/x"


def test_project_config_round_trip(tmp_path: Path) -> None:
    assert get_global_config() == {} and get_current_project_config(tmp_path) == {}
    save_current_project_config(tmp_path, lambda c: {**c, "x": 1})
    save_current_project_config(tmp_path, lambda c: {**c, "y": 2})
    assert get_current_project_config(tmp_path) == {"x": 1, "y": 2}
    before = get_global_config_path().read_text()
    save_current_project_config(tmp_path, lambda c: c)  # unchanged: nothing written
    assert get_global_config_path().read_text() == before
    assert get_global_config()["projects"] == {str(tmp_path.resolve()): {"x": 1, "y": 2}}


def test_the_project_key_is_the_git_root(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "sub").mkdir()
    assert get_project_path_for_config(tmp_path / "sub") == str(tmp_path.resolve())


def test_an_unreadable_config_reads_as_empty(tmp_path: Path) -> None:
    get_global_config_path().write_text("[1, 2]")
    assert get_global_config() == {} and get_current_project_config(tmp_path) == {}
    get_global_config_path().write_text("{not json")
    assert get_global_config() == {}
    get_global_config_path().write_text('{"projects": []}')
    assert get_current_project_config(tmp_path) == {}
    get_global_config_path().write_text(json.dumps({"projects": {str(tmp_path.resolve()): "x"}}))
    assert get_current_project_config(tmp_path) == {}


def test_a_failed_save_is_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                 caplog: pytest.LogCaptureFixture) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(blocker / "sub"))
    save_current_project_config(tmp_path, lambda c: {**c, "x": 1})
    assert "Failed to save config with lock" in caplog.text


def test_trust_is_read_from_the_project_and_every_parent(tmp_path: Path, untrusted: None) -> None:
    project = tmp_path / "a" / "b"
    project.mkdir(parents=True)
    assert not check_has_trust_dialog_accepted(project) and not is_path_trusted(project)
    trust(tmp_path / "a")
    assert check_has_trust_dialog_accepted(project) and is_path_trusted(project)
    assert not is_path_trusted(tmp_path)
    save_current_project_config(tmp_path, lambda c: {**c, "hasTrustDialogAccepted": False})
    assert not check_has_trust_dialog_accepted(tmp_path)


def test_trust_is_latched_once_true(tmp_path: Path, untrusted: None) -> None:
    trust(tmp_path)
    assert check_has_trust_dialog_accepted(tmp_path)
    get_global_config_path().write_text("{}")
    assert check_has_trust_dialog_accepted(tmp_path)  # latched
    reset_trust_dialog_accepted_cache_for_testing()
    assert not check_has_trust_dialog_accepted(tmp_path)


def test_session_trust_trusts_every_folder_but_is_not_a_persisted_path(tmp_path: Path, untrusted: None) -> None:
    set_session_trust_accepted(True)
    assert check_has_trust_dialog_accepted(tmp_path) and not is_path_trusted(tmp_path)


# ── the dialog ───────────────────────────────────────────────────────────────

def test_the_dialog_text(tmp_path: Path) -> None:
    assert render_trust_dialog(tmp_path) == "\n".join([
        "Accessing workspace:", "", str(tmp_path), "", SAFETY_CHECK, ABILITIES, "", "  1. Yes, I trust this folder",
        "  2. No, exit", "Choose 1-2 · Esc to cancel"])
    assert ABILITIES == "TDDAgents'll be able to read, edit, and execute files here."
    assert SAFETY_CHECK.startswith("Quick safety check: Is this a project you created or one you trust?")


async def answered(io: FakeIO, escape: asyncio.Event, cwd: Path, *lines: str | None, home: Path | None = None) -> None:
    for line in lines:
        io.lines.put_nowait(line)
    await show_trust_dialog(io, cwd, escape, home=home)


def test_accepting_persists_the_trust(tmp_path: Path, untrusted: None) -> None:
    io = FakeIO()
    asyncio.run(answered(io, asyncio.Event(), tmp_path, "3", "yes", "1", home=tmp_path / "home"))
    assert io.out[0].startswith("Accessing workspace:") and io.out[1:] == ["Choose 1-2.", "Choose 1-2."]
    assert get_current_project_config(tmp_path) == {"hasTrustDialogAccepted": True}
    assert get_session_trust_accepted()
    reset_trust_dialog_accepted_cache_for_testing()
    set_session_trust_accepted(False)
    assert check_has_trust_dialog_accepted(tmp_path)


def test_a_trusted_folder_is_not_asked(tmp_path: Path, untrusted: None) -> None:
    trust(tmp_path)
    io = FakeIO()
    asyncio.run(show_trust_dialog(io, tmp_path, asyncio.Event()))
    assert io.out == [] and io.prompts == [] and get_session_trust_accepted()


@pytest.mark.parametrize("line, code", [("2", 1), (None, 1)])
def test_refusing_exits(tmp_path: Path, untrusted: None, line: str | None, code: int) -> None:
    with pytest.raises(TrustDialogExit) as refused:
        asyncio.run(answered(FakeIO(), asyncio.Event(), tmp_path, line))
    assert refused.value.exit_code == code
    assert get_current_project_config(tmp_path) == {} and not get_session_trust_accepted()


def test_esc_exits_zero(tmp_path: Path, untrusted: None) -> None:
    async def go() -> None:
        escape = asyncio.Event()
        io = FakeIO()
        asking = asyncio.create_task(show_trust_dialog(io, tmp_path, escape))
        while not io.prompts:
            await asyncio.sleep(0.005)
        escape.set()
        await asking

    with pytest.raises(TrustDialogExit) as refused:
        asyncio.run(go())
    assert refused.value.exit_code == 0 and get_current_project_config(tmp_path) == {}


def test_the_home_directory_is_trusted_for_this_process_only(tmp_path: Path, untrusted: None) -> None:
    accept_trust(tmp_path, home=tmp_path)
    assert get_session_trust_accepted() and get_global_config() == {}
    set_session_trust_accepted(False)
    accept_trust(tmp_path / "x", home=tmp_path)
    assert not get_session_trust_accepted() and get_current_project_config(tmp_path / "x") == {
        "hasTrustDialogAccepted": True}


def test_the_real_home_is_the_default(tmp_path: Path, untrusted: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    accept_trust(tmp_path)
    assert get_session_trust_accepted() and get_global_config() == {}


# ── the interactive session asks first ──────────────────────────────────────

def fake_open_session_run(opened: list[Any], checks: list[Any]) -> Any:
    @asynccontextmanager
    async def open_run(**kwargs: Any) -> Any:
        opened.append(kwargs)
        locations = SimpleNamespace(project_root=Path(kwargs["project_root"]))
        yield SimpleNamespace(
            on_message=None, ctx=SimpleNamespace(), mcp=SimpleNamespace(settings_locations=lambda: locations),
            store=SimpleNamespace(update=lambda f: None), refresh_plugins=None)

    return open_run


def drive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lines: list[str | None]) -> tuple[Any, list[Any], list[Any]]:
    from app.session.driver import SessionOptions, develop_interactive

    opened: list[Any] = []
    checks: list[Any] = []
    monkeypatch.setattr("app.cli.print.open_session_run", fake_open_session_run(opened, checks))

    class Repl:
        def __init__(self, run: Any, io: Any, verbose: bool = False) -> None:
            self.run = run

        async def run_session(self, request: Any, prompt: Any) -> str:
            await asyncio.sleep(0.01)  # the startup checks run meanwhile, in the background
            return "ended"

    monkeypatch.setattr("app.screens.repl.Repl", Repl)
    monkeypatch.setattr("app.cli.print.outcome_of", lambda run, terminal: terminal)

    async def startup_checks(set_app_state: Any, locations: Any, *, trust_accepted: bool, refresh: Any) -> None:
        checks.append((locations.project_root, trust_accepted))

    monkeypatch.setattr("app.utils.plugins.perform_startup_checks.perform_startup_checks", startup_checks)
    io = FakeIO()
    for line in lines:
        io.lines.put_nowait(line)
    outcome = develop_interactive("t", "req", SimpleNamespace(root=tmp_path), SessionOptions(),
                                  io_factory=lambda on_interrupt: io, project_root=tmp_path)
    return outcome, opened, checks


def test_an_untrusted_project_is_asked_before_the_session_opens(tmp_path: Path, untrusted: None,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    outcome, opened, checks = drive(monkeypatch, tmp_path, ["1"])
    assert outcome == "ended" and len(opened) == 1
    assert checks == [(tmp_path, True)]  # the plugin startup checks, with the folder trusted
    assert get_current_project_config(tmp_path) == {"hasTrustDialogAccepted": True}


def test_a_refused_project_never_opens(tmp_path: Path, untrusted: None, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(TrustDialogExit):
        drive(monkeypatch, tmp_path, ["2"])
    with pytest.raises(TrustDialogExit):
        drive(monkeypatch, tmp_path, [None])


def test_esc_before_the_repl_opens_answers_the_trust_dialog(tmp_path: Path, untrusted: None,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from app.session.driver import SessionOptions, develop_interactive

    io = FakeIO()

    def factory(on_interrupt: Any) -> FakeIO:
        async def press_esc() -> None:
            while not io.prompts:
                await asyncio.sleep(0.005)
            on_interrupt()

        asyncio.get_running_loop().create_task(press_esc())
        return io

    with pytest.raises(TrustDialogExit) as refused:
        develop_interactive("t", "req", SimpleNamespace(root=tmp_path), SessionOptions(), io_factory=factory,
                            project_root=tmp_path)
    assert refused.value.exit_code == 0


def test_main_exits_with_the_dialogs_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Workspace:
        root = tmp_path / "project"
        run_dir = tmp_path

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise TrustDialogExit(1)

    monkeypatch.setattr("app.cli.print.session_workspace", lambda thread_id: Workspace())
    monkeypatch.setattr(entry, "session_exists", lambda thread_id, workspace: False)
    monkeypatch.setattr(entry, "develop_interactive", refuse)
    assert entry.main(["Build X"]) == 1

    def esc(*args: Any, **kwargs: Any) -> Any:
        raise TrustDialogExit(0)

    monkeypatch.setattr(entry, "develop_interactive", esc)
    assert entry.main(["Build X"]) == 0
