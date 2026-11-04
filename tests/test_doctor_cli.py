"""
`python -m app.main doctor` (`main.tsx`'s `doctor` command, `cli/handlers/util.tsx` -> `doctorHandler`) and
the version constant (`MACRO.VERSION`) wherever TDDAgents shows or sends it.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest

import app.cli.handlers.util as util
import app.main as main_module
from app.components.design_system.dialog import CANCELLED
from app.constants.product import VERSION
from app.utils.settings.settings import SettingsLocations
from app.utils.user_agent import get_tddagents_user_agent
from tests.dialog_fakes import FakeDialog


def test_the_version_is_one_constant():
    assert VERSION == "0.1.0"
    assert get_tddagents_user_agent() == "tddagents/0.1.0"
    from app.tools.web_fetch_tool.utils import USER_AGENT

    assert USER_AGENT == "TDDAgents-User (tddagents/0.1.0)"


# ── the line dialog ──────────────────────────────────────────────────────────

def test_the_line_dialog_prints_and_reads_a_line():
    out = io.StringIO()
    dialog = util.LineDialog(io.StringIO("no\nyes\n"), out)
    dialog.write("hello")
    answer = asyncio.run(dialog.ask("Pick?", lambda line: line if line == "yes" else None, "Again", CANCELLED))
    assert answer == "yes" and out.getvalue() == "hello\nPick?\nAgain\n"


def test_end_of_input_is_the_cancelled_answer():
    dialog = util.LineDialog(io.StringIO(""), io.StringIO())
    assert asyncio.run(dialog.ask("Pick?", lambda line: line, "Again", CANCELLED)) is CANCELLED


# ── the handler ──────────────────────────────────────────────────────────────

class FakeManager:
    def __init__(self) -> None:
        self.tools: list[Any] = []
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakePlugins:
    errors = [{"type": "generic-error", "source": "lint@team", "plugin": "lint", "error": "boom"}]


@pytest.fixture
def doctor_env(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".tddagents").mkdir(parents=True)
    locations = SettingsLocations(project_root=project, home=tmp_path / "home", managed_dir=tmp_path / "managed")
    manager = FakeManager()
    connected: list[tuple[Any, ...]] = []

    async def connect(root, locs, *, is_non_interactive_session):
        connected.append((root, locs, is_non_interactive_session))
        return manager

    async def load_plugins(locs):
        assert locs is locations
        return FakePlugins()

    monkeypatch.setattr("app.cli.print.connect_mcp_servers", connect)
    monkeypatch.setattr("app.utils.plugins.plugin_loader.load_all_plugins", load_plugins)
    return project, locations, manager, connected


def test_the_doctor_screen_without_a_session(doctor_env):
    project, locations, manager, connected = doctor_env
    # A wrongly typed key nothing reads makes the file invalid, as upstream, and the screen says so.
    (project / ".tddagents" / "settings.json").write_text(json.dumps({"model": 3}), encoding="utf-8")
    dialog = FakeDialog("")
    assert asyncio.run(util.doctor_handler(locations, project, dialog)) == 0
    text = dialog.text()
    assert text.startswith("Checking installation status…\nDiagnostics\n└ Currently running: development (0.1.0)")
    assert "Invalid Settings" in text and "model" in text and "Expected string, but received number" in text
    assert "└ lint@team [lint]: boom" in text and text.endswith("Press Enter to continue…")
    # The project's `.mcp.json` servers are connected without the approval prompt, then closed.
    assert connected == [(project, locations, True)] and manager.closed


def test_the_connections_are_closed_when_the_screen_fails(doctor_env, monkeypatch):
    project, locations, manager, _ = doctor_env

    async def broken(io_: Any, context: Any) -> str:
        raise RuntimeError("screen")

    monkeypatch.setattr("app.commands.doctor.doctor.call", broken)
    with pytest.raises(RuntimeError, match="screen"):
        asyncio.run(util.doctor_handler(locations, project, FakeDialog("")))
    assert manager.closed


def test_the_context_is_the_directorys(doctor_env):
    project, locations, manager, _ = doctor_env
    context, returned = asyncio.run(util.doctor_context(locations, project))
    assert returned is manager and context.locations is locations
    assert Path(context.workspace.root) == project and context.tools == []
    assert context.plugin_errors == FakePlugins.errors and context.failed_agent_files() == []
    assert context.active_agents  # the built-in agents
    assert not context.permission_context.always_allow_rules  # a fresh app state's empty context


# ── the subcommand ───────────────────────────────────────────────────────────

def test_main_routes_doctor_to_the_handler(monkeypatch, tmp_path):
    seen: list[Any] = []

    async def handler(locations, project, io_=None):
        seen.append((locations.project_root, project))
        return 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(util, "doctor_handler", handler)
    assert main_module.main(["doctor"]) == 0
    assert seen == [(tmp_path, tmp_path)]


def test_doctor_takes_no_options(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main_module.main(["doctor", "--fast"])
    assert exit_info.value.code == 2
    with pytest.raises(SystemExit):
        main_module.main(["doctor", "--help"])
    out = capsys.readouterr().out
    assert out.startswith("usage: python -m app.main doctor [-h]") and "TDDAgents auto-updater" in out
