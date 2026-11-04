"""
Tests for the plugins in a session (`app/cli/print.py`): components loaded when the run opens,
`DevelopmentRun.refresh_plugins` behind `/reload-plugins` and the `reload_plugins` control
request, the headless background install, and the housekeeping autoupdate. Offline: an inline
(`--plugin-dir`) plugin and a scripted model.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.bootstrap.state import set_inline_plugins
from app.cli.print import HeadlessOptions, open_session_run, run_headless
from app.commands.commands import (
    Command,
    SlashCommand,
    format_description_with_source,
    get_commands,
    reload_plugins_message,
)
from app.query.deps import get_production_deps
from app.screens.repl import Repl
from app.skills.load_skills_dir import SkillRegistry
from app.tdd.plan import TddPlan
from app.tools.agent_tool.load_agents_dir import parse_agents_from_json
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.plugins import headless_plugin_install as hpi
from app.utils.plugins import installed_plugins_manager as ipm
from app.utils.plugins import load_plugin_commands as lpc
from app.utils.plugins import marketplace_manager as mm
from app.utils.plugins import plugin_autoupdate as pau
from app.utils.plugins import plugin_loader as pl
from app.utils.plugins import refresh
from app.utils.plugins.cache_utils import clear_all_caches
from app.utils.settings.settings_cache import clear_plugin_settings_base
from tests.test_plugins_components import make_toolkit, write
from tests.test_repl import FakeIO
from tests.test_stream_json import (
    Feed,
    Model,
    control,
    lines_of,
    responses,
    run_options,
    user,
)

REVIEWER = {"description": "Reviews code", "prompt": "You review"}


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_SYNC_PLUGIN_INSTALL", raising=False)

    def reset() -> None:
        mm.clear_marketplaces_cache()
        ipm.clear_installed_plugins_cache()
        clear_all_caches()
        set_inline_plugins([])
        clear_plugin_settings_base()
        pau._reset_for_tests()

    reset()
    yield
    reset()


def inline(root: Path) -> None:
    set_inline_plugins([str(root)])
    pl.clear_plugin_cache()


def toolkit_without_mcp(root: Path) -> Path:
    make_toolkit(root)
    (root / ".mcp.json").unlink()
    return root


def test_the_run_opens_with_the_plugins_and_reloads_them(python_workspace: Any, tmp_path: Path) -> None:
    root = toolkit_without_mcp(tmp_path / "kit")
    inline(root)
    perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, should_avoid_permission_prompts=False)

    async def scenario() -> tuple[list[str], list[str], str, bool]:
        async with open_session_run(run_id="loop-plugins", plan=TddPlan(), workspace=python_workspace,
                                    deps=get_production_deps(), permission_context=perm, project_root=tmp_path,
                                    home=tmp_path / "home", managed_dir=tmp_path / "etc",
                                    agents=parse_agents_from_json({"reviewer": REVIEWER})) as run:
            registry = run.ctx.skill_registry
            before = [c.name for c in registry.plugin_commands()]
            agent_tool = next(t for t in run.ctx.tools if t.name == "Agent")
            assert "kit:helper" in agent_tool.prompt
            assert run.get_agent_definitions is not None
            assert {"kit:helper", "reviewer"} <= set(run.get_agent_definitions())
            registry.sent_skill_names[None] = {"x"}
            write(root / "commands" / "later.md", "---\ndescription: Later\n---\nL")
            io = FakeIO()
            await Repl(run, io).run_command(SlashCommand("reload-plugins"))
            after = [c.name for c in registry.plugin_commands()]
            return before, after, io.text(), bool(registry.sent_skill_names)

    before, after, text, sent = asyncio.run(scenario())
    # The commands (in directory order), then the skills.
    assert sorted(before[:3]) == ["kit:bundle", "kit:hello", "kit:ops:deploy"] and before[3] == "kit:greet"
    assert "kit:later" in after and not sent
    assert text.startswith("Reloaded: 1 plugin · 4 skills · ") and "2 hooks · 0 plugin MCP servers" in text


def test_reload_plugins_control_request(python_workspace: Any, tmp_path: Path) -> None:
    root = make_toolkit(tmp_path / "kit")
    inline(root)
    feed = Feed(control("i", "initialize", agents={"reviewer": dict(REVIEWER)}))
    written: list[str] = []

    def write_out(text: str) -> None:
        written.append(text)
        got = responses(lines_of(written))
        if "i" in got and "r" not in got and not getattr(write_out, "sent", False):
            write_out.sent = True  # type: ignore[attr-defined]
            write(root / "commands" / "later.md", "---\ndescription: Later\n---\nL")
            feed.send(control("r", "reload_plugins"))
        if "r" in got and not getattr(write_out, "closed", False):
            write_out.closed = True  # type: ignore[attr-defined]
            feed.close()

    options = HeadlessOptions(output_format="stream-json", input_format="stream-json", verbose=True)
    asyncio.run(run_headless("plugins-reload", None, workspace=python_workspace, options=options,
                             input_blocks=feed.blocks(), write_stdout=write_out,
                             **run_options(tmp_path, Model([]))))
    got = responses(lines_of(written))
    init, reload = got["i"]["response"], got["r"]["response"]
    assert {"name": "kit:hello", "description": "(kit) Say hello", "argumentHint": "<who>"} in init["commands"]
    names = [c["name"] for c in reload["commands"]]
    assert "kit:later" in names and "kit:later" not in [c["name"] for c in init["commands"]]
    assert {"name": "reviewer", "description": "Reviews code"} in reload["agents"]
    assert {"name": "kit:helper", "description": "Helps"} in reload["agents"]
    assert reload["plugins"] == [{"name": "kit", "path": str(root), "source": "kit@inline"}]
    (server,) = [s for s in reload["mcpServers"] if s["name"] == "plugin:kit:files"]
    assert server["scope"] == "dynamic" and server["status"] in ("failed", "connected")
    assert reload["error_count"] == 0


def test_a_headless_run_installs_plugins_in_the_background(python_workspace: Any, tmp_path: Path,
                                                           monkeypatch) -> None:
    calls: list[str] = []

    async def installed(locations: Any, *, uninstall_plugin_op: Any = None) -> bool:
        calls.append("install")
        return True

    original = refresh.refresh_active_plugins

    async def refreshed(*args: Any, **kwargs: Any) -> Any:
        calls.append("refresh")
        return await original(*args, **kwargs)

    monkeypatch.setattr(hpi, "install_plugins_for_headless", installed)
    monkeypatch.setattr(refresh, "refresh_active_plugins", refreshed)
    monkeypatch.setenv("TDDAGENTS_SYNC_PLUGIN_INSTALL", "1")
    monkeypatch.setenv("TDDAGENTS_SYNC_PLUGIN_INSTALL_TIMEOUT_MS", "20000")
    written: list[str] = []
    asyncio.run(run_headless("plugins-sync", "Build X", workspace=python_workspace,
                             options=HeadlessOptions(output_format="json"), write_stdout=written.append,
                             **run_options(tmp_path, Model([[AIMessage(content="done")]]))))
    assert calls == ["install", "refresh"]
    assert lines_of(written)[0]["result"] == "done"


def test_a_slow_sync_install_times_out(python_workspace: Any, tmp_path: Path, monkeypatch) -> None:
    async def slow(locations: Any, *, uninstall_plugin_op: Any = None) -> bool:
        await asyncio.sleep(30)
        return False

    monkeypatch.setattr(hpi, "install_plugins_for_headless", slow)
    monkeypatch.setenv("TDDAGENTS_SYNC_PLUGIN_INSTALL", "1")
    monkeypatch.setenv("TDDAGENTS_SYNC_PLUGIN_INSTALL_TIMEOUT_MS", "50")
    written: list[str] = []
    asyncio.run(asyncio.wait_for(run_headless(
        "plugins-slow", "Build X", workspace=python_workspace, options=HeadlessOptions(output_format="json"),
        write_stdout=written.append, **run_options(tmp_path, Model([[AIMessage(content="done")]]))), 20))
    assert lines_of(written)[0]["result"] == "done"


def test_housekeeping_starts_the_autoupdate_once(python_workspace: Any, tmp_path: Path, monkeypatch) -> None:
    started: list[Any] = []

    def autoupdate(locations: Any, *, update_plugin_op: Any = None) -> Any:
        started.append(locations)

        async def nothing() -> None:
            return None

        return asyncio.ensure_future(nothing())

    import app.utils.background_housekeeping as housekeeping

    monkeypatch.setattr(housekeeping, "auto_update_marketplaces_and_plugins_in_background", autoupdate)
    perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)

    async def scenario() -> None:
        async with open_session_run(run_id="loop-housekeeping", plan=TddPlan(), workspace=python_workspace,
                                    deps=dataclasses.replace(get_production_deps()), permission_context=perm,
                                    project_root=tmp_path, home=tmp_path / "home",
                                    managed_dir=tmp_path / "etc") as run:
            run.start_housekeeping()
            run.start_housekeeping()
            await asyncio.sleep(0)

    asyncio.run(scenario())
    assert len(started) == 1 and started[0].project_root == tmp_path


def test_reload_without_plugin_support_is_an_error(python_workspace: Any, tmp_path: Path) -> None:
    from app.cli.print import HeadlessStreaming

    class Run:
        refresh_plugins = None
        settings_locations = None
        on_message = None
        ctx = SimpleNamespace()

    streaming = HeadlessStreaming.__new__(HeadlessStreaming)
    streaming.run_ = Run()  # type: ignore[assignment]
    sent: list[tuple[str, str]] = []
    streaming.respond_error = lambda message, error, **_: sent.append((message["request_id"], error))  # type: ignore
    asyncio.run(streaming._control_reload_plugins({"request_id": "x"}, {}))
    assert sent == [("x", "Plugins are not available in this session")]
    io = FakeIO()
    asyncio.run(Repl(Run(), io)._reload_plugins())  # type: ignore[arg-type]
    assert io.text() == "Plugins are not available in this session."
    assert user("x")["type"] == "user"


def test_a_plugin_command_shows_its_plugin_in_the_command_list():
    registry = SkillRegistry([])
    registry.set_plugin_commands([lpc.PluginSkillCommand(
        name="kit:hello", description="Say hello", markdown_content="", source="plugin", loaded_from=None,
        skill_root=None, plugin_name="kit", argument_hint="<who>")])
    (command, *_) = get_commands(registry)
    assert (command.argument_hint, format_description_with_source(command)) == ("<who>", "(kit) Say hello")
    assert format_description_with_source(Command("x", "Plain", "prompt", source="plugin")) == "Plain (plugin)"


def test_the_reload_message_counts_each_component() -> None:
    result = refresh.RefreshActivePluginsResult(
        enabled_count=1, disabled_count=0, command_count=3, agent_count=5, hook_count=2, mcp_count=1, lsp_count=0,
        error_count=0)
    assert reload_plugins_message(result) == (
        "Reloaded: 1 plugin · 3 skills · 5 agents · 2 hooks · 1 plugin MCP server · 0 plugin LSP servers")
    assert reload_plugins_message(dataclasses.replace(result, error_count=2)).endswith(
        "\n2 errors during load. Run /doctor for details.")
