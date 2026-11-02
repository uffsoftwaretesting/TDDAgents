"""
The approval of a project's `.mcp.json` servers: the dialogs (`app/components/mcp_server_*`), the
pending servers asked (`app/services/mcp_server_approval.py`), the setup step after the trust dialog
(`app/interactive_helpers.py`, `app/session/driver.py`) and the settings errors that skip it
(`app/utils/settings/all_errors.py`).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.components.design_system.dialog import CANCELLED
from app.components.mcp_server_approval_dialog import (
    approval_dialog_title,
    listed_servers,
    mcp_server_approval_dialog,
    on_change,
)
from app.components.mcp_server_dialog_copy import MCP_SERVER_DIALOG_COPY
from app.components.mcp_server_multiselect_dialog import (
    SUBTITLE,
    mcp_server_multiselect_dialog,
    multiselect_dialog_title,
    multiselect_hint,
    parse_selection,
    reject_all,
)
from app.components.trust_dialog.trust_dialog import TrustDialogExit
from app.interactive_helpers import (
    ESCAPE,
    PROMPT,
    SetupDialogExit,
    SetupDialogIO,
    read_setup_line,
    setup_settings_locations,
    show_mcpjson_server_approvals,
)
from app.services.mcp_server_approval import handle_mcpjson_server_approvals, pending_project_servers
from app.utils.settings.all_errors import get_settings_with_all_errors
from app.utils.settings.settings import (
    MANAGED_DIR,
    SettingsLocations,
    get_settings_file_errors,
    has_skip_dangerous_mode_permission_prompt,
)
from tests.dialog_fakes import ESC, FakeDialog
from tests.test_repl import FakeIO

STDIO = {"command": "node", "args": ["server.js"]}


def write(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return path


@pytest.fixture
def loc(tmp_path: Path) -> SettingsLocations:
    return SettingsLocations(project_root=tmp_path / "proj", home=tmp_path / "home", managed_dir=tmp_path / "etc")


def local(loc: SettingsLocations) -> dict[str, Any]:
    path = loc.project_root / ".tddagents" / "settings.local.json"
    return json.loads(path.read_text()) if path.exists() else {}


def project_settings(loc: SettingsLocations, data: object) -> None:
    write(loc.project_root / ".tddagents" / "settings.json", data)


def mcp_json(loc: SettingsLocations, *names: str) -> None:
    write(loc.project_root / ".mcp.json", {"mcpServers": {name: STDIO for name in names}})


# ── the single-server dialog ─────────────────────────────────────────────────

def test_the_dialog_text_and_its_options(loc: SettingsLocations) -> None:
    io = FakeDialog("2")
    asyncio.run(mcp_server_approval_dialog(io, "db", loc))
    assert io.asked == ["\n".join([
        "New MCP server found in .mcp.json: db", "",
        "MCP servers may execute code or access system resources. All tool calls require approval.", "",
        "  1. Use this and all future MCP servers in this project",
        "  2. Use this MCP server",
        "  3. Continue without using this MCP server",
        "Choose 1-3 · Esc to cancel"])]
    assert approval_dialog_title("x") == "New MCP server found in .mcp.json: x"


@pytest.mark.parametrize(("lines", "saved"), [
    (("1",), {"enabledMcpjsonServers": ["db"], "enableAllProjectMcpServers": True}),
    (("2",), {"enabledMcpjsonServers": ["db"]}),
    (("9", "3"), {"disabledMcpjsonServers": ["db"]}),
    ((ESC,), {"disabledMcpjsonServers": ["db"]}),
])
def test_each_choice_is_saved_in_local_settings(loc: SettingsLocations, lines: tuple[Any, ...],
                                                saved: dict[str, Any]) -> None:
    io = FakeDialog(*lines)
    asyncio.run(mcp_server_approval_dialog(io, "db", loc))
    assert local(loc) == saved
    assert not (loc.project_root / ".tddagents" / "settings.json").exists()


def test_the_lists_are_read_merged_and_never_duplicated(loc: SettingsLocations) -> None:
    project_settings(loc, {"enabledMcpjsonServers": ["shared"], "disabledMcpjsonServers": ["old"]})
    on_change("yes", "db", loc)
    assert local(loc) == {"enabledMcpjsonServers": ["shared", "db"]}
    on_change("yes", "db", loc)
    on_change("yes", "shared", loc)
    assert local(loc) == {"enabledMcpjsonServers": ["shared", "db"]}
    on_change("no", "db", loc)
    on_change("no", "old", loc)
    assert local(loc)["disabledMcpjsonServers"] == ["old", "db"]
    on_change("yes_all", "shared", loc)
    assert local(loc) == {"enabledMcpjsonServers": ["shared", "db"], "disabledMcpjsonServers": ["old", "db"],
                          "enableAllProjectMcpServers": True}


def test_a_list_that_is_not_a_list_reads_as_empty() -> None:
    assert listed_servers({"enabledMcpjsonServers": "db"}, "enabledMcpjsonServers") == []
    assert listed_servers({"enabledMcpjsonServers": ["a", 2]}, "enabledMcpjsonServers") == ["a", "2"]
    assert listed_servers({}, "disabledMcpjsonServers") == []


def test_a_failed_write_is_logged(loc: SettingsLocations, caplog: pytest.LogCaptureFixture) -> None:
    write(loc.project_root / ".tddagents" / "settings.local.json", "{not json")
    with caplog.at_level(logging.ERROR, logger="app.components.mcp_server_approval_dialog"):
        on_change("yes", "db", loc)
    assert "Invalid JSON syntax in settings file at" in caplog.text
    assert (loc.project_root / ".tddagents" / "settings.local.json").read_text() == "{not json"


# ── the multi-select ─────────────────────────────────────────────────────────

def test_the_multiselect_text(loc: SettingsLocations) -> None:
    io = FakeDialog("")
    asyncio.run(mcp_server_multiselect_dialog(io, ["a", "b"], loc))
    assert io.asked == ["\n".join([
        "2 new MCP servers found in .mcp.json", "Select any you wish to enable.", "",
        MCP_SERVER_DIALOG_COPY, "", "  1. a", "  2. b",
        "Choose any of 1-2 to enable, e.g. 1,3 · Enter to enable all · Esc to reject all"])]
    assert SUBTITLE == "Select any you wish to enable."
    assert multiselect_dialog_title(["a", "b", "c"]) == "3 new MCP servers found in .mcp.json"
    assert multiselect_hint(4).startswith("Choose any of 1-4 to enable")


def test_parse_selection() -> None:
    names = ["a", "b", "c"]
    assert parse_selection(names, "") == names
    assert parse_selection(names, "  ") == names
    assert parse_selection(names, "3, 1") == ["c", "a"]
    assert parse_selection(names, "2 2") == ["b"]
    for bad in ("0", "4", "x", "1,x", "-1"):
        assert parse_selection(names, bad) is None


def test_enter_enables_every_server(loc: SettingsLocations) -> None:
    asyncio.run(mcp_server_multiselect_dialog(FakeDialog(""), ["a", "b"], loc))
    assert local(loc) == {"enabledMcpjsonServers": ["a", "b"]}


def test_the_unselected_are_rejected(loc: SettingsLocations) -> None:
    project_settings(loc, {"enabledMcpjsonServers": ["x", "c"], "disabledMcpjsonServers": ["y", "a"]})
    io = FakeDialog("9", "3")
    asyncio.run(mcp_server_multiselect_dialog(io, ["a", "b", "c"], loc))
    assert "Choose any of 1-3, e.g. 1,3." in io.out
    assert local(loc) == {"enabledMcpjsonServers": ["x", "c"], "disabledMcpjsonServers": ["y", "a", "b"]}


def test_selecting_every_server_rejects_none(loc: SettingsLocations) -> None:
    asyncio.run(mcp_server_multiselect_dialog(FakeDialog("2,1"), ["a", "b"], loc))
    assert local(loc) == {"enabledMcpjsonServers": ["a", "b"]}


def test_esc_rejects_every_server(loc: SettingsLocations) -> None:
    project_settings(loc, {"disabledMcpjsonServers": ["b", "z"]})
    asyncio.run(mcp_server_multiselect_dialog(FakeDialog(ESC), ["a", "b"], loc))
    assert local(loc) == {"disabledMcpjsonServers": ["b", "z", "a"]}
    reject_all(["c"], loc)
    assert local(loc) == {"disabledMcpjsonServers": ["b", "z", "a", "c"]}


# ── which servers are asked ──────────────────────────────────────────────────

def test_only_pending_servers_are_asked(loc: SettingsLocations) -> None:
    mcp_json(loc, "a", "b", "c")
    project_settings(loc, {"enabledMcpjsonServers": ["a"], "disabledMcpjsonServers": ["b"]})
    assert pending_project_servers(loc) == ["c"]
    io = FakeDialog("2")
    asyncio.run(handle_mcpjson_server_approvals(io, loc))
    assert io.asked[0].startswith(approval_dialog_title("c"))
    assert local(loc) == {"enabledMcpjsonServers": ["a", "c"]}
    assert pending_project_servers(loc) == []


def test_several_pending_servers_are_asked_together(loc: SettingsLocations) -> None:
    mcp_json(loc, "a", "b")
    io = FakeDialog("1")
    asyncio.run(handle_mcpjson_server_approvals(io, loc))
    assert io.asked[0].startswith(multiselect_dialog_title(["a", "b"]))
    assert local(loc) == {"enabledMcpjsonServers": ["a"], "disabledMcpjsonServers": ["b"]}


@pytest.mark.parametrize("settings", [None, {"enableAllProjectMcpServers": True}])
def test_nothing_is_asked_without_pending_servers(loc: SettingsLocations, settings: Any) -> None:
    if settings is None:
        write(loc.project_root / "sub" / "x.txt", "")  # no `.mcp.json` anywhere
    else:
        mcp_json(loc, "a")
        project_settings(loc, settings)
    io = FakeDialog()
    asyncio.run(handle_mcpjson_server_approvals(io, loc))
    assert io.asked == [] and not (loc.project_root / ".tddagents" / "settings.local.json").exists()


def test_an_accepted_bypass_dialog_leaves_nothing_pending(loc: SettingsLocations) -> None:
    mcp_json(loc, "a")
    write(loc.user_config_dir / "settings.json", {"skipDangerousModePermissionPrompt": True})
    assert has_skip_dangerous_mode_permission_prompt(loc)
    assert pending_project_servers(loc) == []


@pytest.mark.parametrize("where", ["user", "local", "flag", "policy"])
def test_the_bypass_acceptance_is_read_from_every_trusted_source(tmp_path: Path, where: str) -> None:
    flag = tmp_path / "flag.json"
    loc = SettingsLocations(project_root=tmp_path / "proj", home=tmp_path / "home", managed_dir=tmp_path / "etc",
                            flag_settings_path=flag)
    path = {"user": loc.user_config_dir / "settings.json",
            "local": loc.project_root / ".tddagents" / "settings.local.json",
            "flag": flag, "policy": tmp_path / "etc" / "managed-settings.json"}[where]
    write(path, {"skipDangerousModePermissionPrompt": False})
    assert not has_skip_dangerous_mode_permission_prompt(loc)
    write(path, {"skipDangerousModePermissionPrompt": True})
    assert has_skip_dangerous_mode_permission_prompt(loc)


def test_the_project_cannot_accept_the_bypass_dialog(loc: SettingsLocations) -> None:
    project_settings(loc, {"skipDangerousModePermissionPrompt": True})
    assert not has_skip_dangerous_mode_permission_prompt(loc)


# ── settings errors skip the step ────────────────────────────────────────────

def test_settings_file_errors(tmp_path: Path, loc: SettingsLocations) -> None:
    assert get_settings_file_errors(loc) == []
    project_settings(loc, {})
    write(loc.user_config_dir / "settings.json", "[1]")
    bad_local = write(loc.project_root / ".tddagents" / "settings.local.json", "{nope")
    (tmp_path / "etc" / "managed-settings.json").mkdir(parents=True)  # not a file: nothing to report
    assert get_settings_file_errors(loc) == [loc.user_config_dir / "settings.json", bad_local]


def test_all_errors_join_the_settings_and_the_mcp_configs(loc: SettingsLocations) -> None:
    assert get_settings_with_all_errors(loc) == []
    write(loc.project_root / ".mcp.json", {"mcpServers": {"bad": {"type": "http"}}})
    write(loc.project_root / ".tddagents" / "settings.local.json", "{nope")
    errors = get_settings_with_all_errors(loc)
    assert errors[0] == f"{loc.project_root / '.tddagents' / 'settings.local.json'}: invalid settings file, ignored"
    assert len(errors) == 2 and errors[1].startswith(f"{loc.project_root / '.mcp.json'}: ")


def test_a_user_scope_mcp_error_names_its_scope(loc: SettingsLocations, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    write(tmp_path / "cfg" / ".tddagents.json", {"mcpServers": {"bad": {"type": "http"}}})
    errors = get_settings_with_all_errors(loc)
    assert len(errors) == 1 and errors[0].startswith("user: ")


def test_invalid_settings_skip_the_approvals(loc: SettingsLocations) -> None:
    mcp_json(loc, "a")
    write(loc.user_config_dir / "settings.json", "{nope")
    io = FakeIO()
    asyncio.run(show_mcpjson_server_approvals(io, asyncio.Event(), loc))
    assert io.out == [] and io.prompts == []


def test_valid_settings_ask_on_the_terminal(loc: SettingsLocations) -> None:
    mcp_json(loc, "a")
    io = FakeIO()
    io.lines.put_nowait("x")
    io.lines.put_nowait("2")
    asyncio.run(show_mcpjson_server_approvals(io, asyncio.Event(), loc))
    assert io.out[0].startswith(approval_dialog_title("a")) and io.out[1] == "Choose 1-3."
    assert io.prompts == [PROMPT, PROMPT] and local(loc) == {"enabledMcpjsonServers": ["a"]}


# ── the terminal before the REPL ─────────────────────────────────────────────

def test_setup_dialog_lines() -> None:
    async def go() -> list[Any]:
        io = FakeIO()
        dialog = SetupDialogIO(io, asyncio.Event())
        dialog.write("hello")
        for line in ("x", "7"):
            io.lines.put_nowait(line)
        answer = await dialog.ask("pick", lambda line: int(line) if line.isdigit() else None, "again", CANCELLED)
        return [answer, io.out]

    assert asyncio.run(go()) == [7, ["hello", "pick", "again"]]


def test_closing_the_input_exits_zero() -> None:
    async def go() -> None:
        io = FakeIO()
        io.lines.put_nowait(None)
        await SetupDialogIO(io, asyncio.Event()).ask("pick", lambda line: line, "again", CANCELLED)

    with pytest.raises(SetupDialogExit) as closed:
        asyncio.run(go())
    assert isinstance(closed.value, TrustDialogExit) and closed.value.exit_code == 0


def test_esc_cancels_and_is_consumed() -> None:
    async def go() -> tuple[Any, bool, Any]:
        io = FakeIO()
        escape = asyncio.Event()
        dialog = SetupDialogIO(io, escape)
        asking = asyncio.create_task(dialog.ask("pick", lambda line: line, "again", CANCELLED))
        while not io.prompts:
            await asyncio.sleep(0.005)
        escape.set()
        answer = await asking
        io.lines.put_nowait("typed")
        return answer, escape.is_set(), await read_setup_line(io, escape)

    assert asyncio.run(go()) == (CANCELLED, False, "typed")


def test_read_setup_line_reports_esc() -> None:
    async def go() -> object:
        escape = asyncio.Event()
        escape.set()
        return await read_setup_line(FakeIO(), escape)

    assert asyncio.run(go()) is ESCAPE


def test_setup_settings_locations(tmp_path: Path) -> None:
    assert setup_settings_locations(tmp_path) == SettingsLocations(project_root=tmp_path, managed_dir=Path(MANAGED_DIR))
    assert setup_settings_locations(str(tmp_path), home=str(tmp_path / "h"), managed_dir=str(tmp_path / "m"),
                                    settings_path=str(tmp_path / "f.json")) == SettingsLocations(
        project_root=tmp_path, home=tmp_path / "h", managed_dir=tmp_path / "m", flag_settings_path=tmp_path / "f.json")


# ── the interactive session asks after the trust dialog ─────────────────────

def drive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lines: list[str | None]) -> list[Any]:
    from app.session.driver import SessionOptions, develop_interactive

    opened: list[Any] = []

    @asynccontextmanager
    async def open_run(**kwargs: Any) -> Any:
        # The session opens (and connects its MCP servers) only once the approvals are saved.
        opened.append((kwargs, local(setup_settings_locations(tmp_path))))
        locations = SimpleNamespace(project_root=tmp_path)
        yield SimpleNamespace(on_message=None, ctx=SimpleNamespace(),
                              mcp=SimpleNamespace(settings_locations=lambda: locations),
                              store=SimpleNamespace(update=lambda f: None))

    class Repl:
        def __init__(self, run: Any, io: Any, verbose: bool = False) -> None:
            pass

        async def run_session(self, request: Any, prompt: Any) -> str:
            return "ended"

    async def no_checks(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr("app.cli.print.open_session_run", open_run)
    monkeypatch.setattr("app.screens.repl.Repl", Repl)
    monkeypatch.setattr("app.cli.print.outcome_of", lambda run, terminal: terminal)
    monkeypatch.setattr("app.utils.plugins.perform_startup_checks.perform_startup_checks", no_checks)
    io = FakeIO()
    for line in lines:
        io.lines.put_nowait(line)
    outcome: Any = develop_interactive("t", "req", SimpleNamespace(root=tmp_path), SessionOptions(),
                                       io_factory=lambda on_interrupt: io, project_root=tmp_path,
                                       home=tmp_path / "home", managed_dir=tmp_path / "etc")
    assert outcome == "ended"
    return opened


def test_the_session_opens_after_the_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path / ".mcp.json", {"mcpServers": {"db": STDIO}})
    opened = drive(monkeypatch, tmp_path, ["2"])
    assert len(opened) == 1 and opened[0][1] == {"enabledMcpjsonServers": ["db"]}


def test_the_approval_reads_the_sessions_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path / ".mcp.json", {"mcpServers": {"db": STDIO}})
    write(tmp_path / "etc" / "managed-settings.json", {"enabledMcpjsonServers": ["db"]})
    assert drive(monkeypatch, tmp_path, [None])[0][1] == {}  # asked, the closed input would exit


def test_closing_the_input_at_the_approval_never_opens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path / ".mcp.json", {"mcpServers": {"db": STDIO}})
    with pytest.raises(SetupDialogExit):
        drive(monkeypatch, tmp_path, [None])


# ── the run connects only the approved project servers ──────────────────────

def test_an_interactive_run_connects_only_approved_project_servers(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from app.cli.print import open_session_run
    from app.query.deps import get_production_deps
    from app.services.mcp.mcp_connection_manager import McpConnectionManager
    from app.tdd.plan import TddPlan
    from app.types.permissions import PermissionMode, ToolPermissionContext
    from app.workspace.local import LocalWorkspace

    project = tmp_path / "proj"
    write(project / ".mcp.json", {"mcpServers": {"db": STDIO}})
    connected: list[list[str]] = []

    async def connect(self: Any, configs: Any) -> None:
        connected.append(sorted(configs))

    monkeypatch.setattr(McpConnectionManager, "connect", connect)

    async def model(state: Any, config: Any) -> Any:
        raise AssertionError("no turn is submitted")
        yield  # pragma: no cover

    async def opened(interactive: bool) -> bool:
        perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
        async with open_session_run(
            run_id=f"mcpjson-{interactive}", plan=TddPlan(), workspace=LocalWorkspace(str(tmp_path / "ws")),
            deps=dataclasses.replace(get_production_deps(), call_model=model), permission_context=perm,
            project_root=project, home=tmp_path / "home", managed_dir=tmp_path / "etc", interactive=interactive,
        ) as run:
            flag: bool = run.mcp.is_non_interactive_session
            return flag

    assert asyncio.run(opened(True)) is False
    assert asyncio.run(opened(False)) is True
    assert connected == [[], ["db"]]
