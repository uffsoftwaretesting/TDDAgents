"""
Tests for refresh.py, PluginInstallationManager, performStartupChecks, pluginStartupCheck,
headlessPluginInstall, pluginAutoupdate and the startup bookkeeping (offline: directory
marketplaces, fakes for the stage-3 operations).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from app.bootstrap.state import set_inline_plugins
from app.cli import print as print_module
from app.services.plugins import plugin_installation_manager as pim
from app.state.app_state_store import AppState, AppStateStore, MarketplaceInstallationStatus
from app.types.permissions import PermissionRuleSource
from app.types.plugin import LoadedPlugin
from app.utils.plugins import headless_plugin_install as hpi
from app.utils.plugins import installed_plugins_manager as ipm
from app.utils.plugins import marketplace_manager as mm
from app.utils.plugins import perform_startup_checks as psc
from app.utils.plugins import plugin_autoupdate as pau
from app.utils.plugins import plugin_loader as pl
from app.utils.plugins import plugin_startup_check as pstc
from app.utils.plugins import refresh
from app.utils.plugins.reconciler import ReconcileResult
from app.utils.plugins.zip_cache import reset_session_plugin_cache
from app.utils.settings.settings import SettingsLocations
from app.utils.settings.settings_cache import clear_plugin_settings_base


@pytest.fixture(autouse=True)
def _fresh_state():
    def reset() -> None:
        mm.clear_marketplaces_cache()
        ipm.clear_installed_plugins_cache()
        ipm.reset_migration_state()
        pl.clear_plugin_cache()
        set_inline_plugins([])
        clear_plugin_settings_base()
        reset_session_plugin_cache()
        pau._reset_for_tests()

    reset()
    yield
    reset()


@pytest.fixture
def locations(tmp_path: Path) -> SettingsLocations:
    project = tmp_path / "project"
    project.mkdir()
    managed = tmp_path / "managed"
    managed.mkdir()
    return SettingsLocations(project_root=project, home=tmp_path / "home", managed_dir=managed)


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def user_settings(locations: SettingsLocations, **data: Any) -> None:
    write_json(locations.user_config_dir / "settings.json", data)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def installed(path: str) -> Any:
    return {"version": "1", "installedAt": "2026-01-01T00:00:00.000Z", "installPath": path}


TWO_HOOKS = [{"type": "command", "command": "a"}, {"type": "command", "command": "b"}]
HOOKS = {"PreToolUse": [{"matcher": "Bash", "hooks": TWO_HOOKS}],
         "Stop": [{"hooks": [{"type": "command", "command": "c"}]}]}


def make_marketplace_dir(root: Path) -> Path:
    write_json(root / ".tddagents-plugin" / "marketplace.json", {
        "name": "team", "owner": {"name": "O"},
        "plugins": [{"name": "alpha", "source": "./alpha", "version": "1.0.0"}, {"name": "beta", "source": "./beta"}],
    })
    write_json(root / "alpha" / ".tddagents-plugin" / "plugin.json", {"name": "alpha", "version": "1.0.0"})
    write_json(root / "alpha" / "hooks" / "hooks.json", {"hooks": HOOKS})
    write_json(root / "beta" / ".tddagents-plugin" / "plugin.json", {"name": "beta"})
    return root


@pytest.fixture
def team(tmp_path: Path, locations: SettingsLocations) -> Path:
    root = make_marketplace_dir(tmp_path / "mkt")
    run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    mm.clear_marketplaces_cache()
    return root


# ── refreshActivePlugins ──────────────────────────────────────────────────────

def test_refresh_swaps_the_plugins_into_app_state_and_counts_them(team, locations):
    user_settings(locations, enabledPlugins={"alpha@team": True, "beta@team": False, "ghost@team": True})
    stale = LoadedPlugin(name="old", manifest={"name": "old"}, path="/x", source="old@x", repository="old@x")
    kept: Any = {"type": "lsp-server-crashed", "source": "plugin:alpha:ts", "plugin": "alpha", "serverName": "ts",
                 "exitCode": 1}
    store = AppStateStore(state=AppState())
    store.update(lambda s: replace(s, plugins=replace(s.plugins, enabled=(stale,), needs_refresh=True,
                                                      errors=(kept, {"type": "generic-error", "source": "x",
                                                                     "error": "dropped"}))))
    result = run(refresh.refresh_active_plugins(store.update, locations))
    state = store.get()
    assert [p.source for p in state.plugins.enabled] == ["alpha@team"]
    assert [p.source for p in state.plugins.disabled] == ["beta@team"]
    assert state.plugins.needs_refresh is False and state.mcp_plugin_reconnect_key == 1
    assert [e["type"] for e in state.plugins.errors] == ["lsp-server-crashed", "plugin-not-found"]
    assert (result.enabled_count, result.disabled_count, result.hook_count, result.error_count) == (1, 1, 3, 1)
    assert (result.command_count, result.mcp_count, result.lsp_count, result.plugin_commands) == (0, 0, 0, [])
    assert result.agent_count == len(result.agent_definitions) > 0


def test_refresh_reloads_from_disk_and_counts_a_hook_load_failure(team, locations, monkeypatch):
    user_settings(locations, enabledPlugins={"alpha@team": True})
    first = run(pl.load_all_plugins(locations))

    async def broken_hooks(_: SettingsLocations) -> None:
        raise RuntimeError("hooks exploded")

    monkeypatch.setattr(refresh, "_load_plugin_hooks", broken_hooks)
    store = AppStateStore(state=AppState())
    result = run(refresh.refresh_active_plugins(store.update, locations))
    assert store.get().plugins.enabled[0] is not first.enabled[0]
    assert result.error_count == 1


def test_merge_plugin_errors_keeps_component_errors_and_deduplicates():
    lsp: Any = {"type": "generic-error", "source": "lsp-manager", "error": "boom"}
    comp: Any = {"type": "component-load-failed", "source": "plugin:a", "plugin": "a", "component": "commands",
                 "path": "/p", "reason": "r"}
    other: Any = {"type": "generic-error", "source": "x", "error": "old"}
    assert refresh.merge_plugin_errors((lsp, comp, other), [lsp]) == [comp, lsp]


# ── performBackgroundPluginInstallations / performStartupChecks ───────────────

def test_a_new_marketplace_is_installed_in_the_background_then_refreshed(tmp_path, locations):
    root = make_marketplace_dir(tmp_path / "declared")
    user_settings(locations, enabledPlugins={"alpha@team": True},
                  extraKnownMarketplaces={"team": {"source": {"source": "directory", "path": str(root)}}})
    store = AppStateStore(state=AppState())
    statuses: list[tuple[str, ...]] = []

    def record() -> None:
        statuses.append(tuple(m.status for m in store.get().plugins.installation_status.marketplaces))

    store.subscribe(record)
    run(pim.perform_background_plugin_installations(store.update, locations))
    assert statuses[:3] == [("pending",), ("installing",), ("installed",)]
    assert [p.source for p in store.get().plugins.enabled] == ["alpha@team"]
    assert store.get().mcp_plugin_reconnect_key == 1


def test_nothing_pending_only_resets_the_status(team, locations):
    user_settings(locations, extraKnownMarketplaces={"team": {"source": {"source": "directory", "path": str(team)}}})
    store = AppStateStore(state=AppState())
    store.update(lambda s: replace(s, plugins=replace(s.plugins, installation_status=replace(
        s.plugins.installation_status, marketplaces=(MarketplaceInstallationStatus("old", "failed"),)))))
    run(pim.perform_background_plugin_installations(store.update, locations))
    assert store.get().plugins.installation_status.marketplaces == ()
    assert store.get().mcp_plugin_reconnect_key == 0


def test_a_failed_marketplace_is_marked_failed(tmp_path, locations):
    user_settings(locations, extraKnownMarketplaces={
        "gone": {"source": {"source": "directory", "path": str(tmp_path / "missing")}}})
    store = AppStateStore(state=AppState())
    run(pim.perform_background_plugin_installations(store.update, locations))
    (status,) = store.get().plugins.installation_status.marketplaces
    assert (status.name, status.status) == ("gone", "failed") and status.error


def test_updates_only_flag_needs_refresh_and_a_failed_refresh_falls_back(tmp_path, locations, monkeypatch):
    user_settings(locations, extraKnownMarketplaces={"m": {"source": {"source": "github", "repo": "o/r"}}})
    cleared: list[str | None] = []
    monkeypatch.setattr(pim, "clear_plugin_cache", lambda reason=None: cleared.append(reason))

    async def updated(_: SettingsLocations, __: Any = None) -> ReconcileResult:
        return ReconcileResult(updated=["m"])

    monkeypatch.setattr(pim, "reconcile_marketplaces", updated)
    store = AppStateStore(state=AppState())
    run(pim.perform_background_plugin_installations(store.update, locations))
    assert store.get().plugins.needs_refresh is True
    assert cleared == ["performBackgroundPluginInstallations: marketplaces reconciled"]

    async def installed(_: SettingsLocations, __: Any = None) -> ReconcileResult:
        return ReconcileResult(installed=["m"])

    async def broken_refresh(*_: Any) -> Any:
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(pim, "reconcile_marketplaces", installed)
    monkeypatch.setattr(pim, "refresh_active_plugins", broken_refresh)
    store = AppStateStore(state=AppState())
    run(pim.perform_background_plugin_installations(store.update, locations))
    assert store.get().plugins.needs_refresh is True
    assert cleared[-1] == "performBackgroundPluginInstallations: auto-refresh failed"


def test_startup_checks_need_trust_and_flag_a_seed_change(locations, monkeypatch):
    calls: list[Any] = []

    async def background(_: Any, __: SettingsLocations, refresh: Any) -> None:
        calls.append(("background", refresh))

    async def seeds_changed() -> bool:
        return True

    monkeypatch.setattr(psc, "perform_background_plugin_installations", background)
    monkeypatch.setattr(psc, "register_seed_marketplaces", seeds_changed)
    store = AppStateStore(state=AppState())
    run(psc.perform_startup_checks(store.update, locations, trust_accepted=False))
    assert calls == [] and store.get().plugins.needs_refresh is False
    run(psc.perform_startup_checks(store.update, locations, trust_accepted=True))
    assert calls == [("background", None)] and store.get().plugins.needs_refresh is True

    async def session_refresh() -> None:
        pass

    run(psc.perform_startup_checks(store.update, locations, trust_accepted=True, refresh=session_refresh))
    assert calls[-1] == ("background", session_refresh)


def test_a_new_install_refreshes_through_the_sessions_refresh(locations, monkeypatch):
    user_settings(locations, extraKnownMarketplaces={"m": {"source": {"source": "github", "repo": "o/r"}}})

    async def installed(_: SettingsLocations, __: Any = None) -> ReconcileResult:
        return ReconcileResult(installed=["m"])

    async def plain_refresh(*_: Any) -> Any:
        raise AssertionError("the plain refresh must not run when the session gives its own")

    refreshed: list[str] = []

    async def session_refresh() -> None:
        refreshed.append("session")

    monkeypatch.setattr(pim, "reconcile_marketplaces", installed)
    monkeypatch.setattr(pim, "refresh_active_plugins", plain_refresh)
    store = AppStateStore(state=AppState())
    run(pim.perform_background_plugin_installations(store.update, locations, session_refresh))
    assert refreshed == ["session"] and store.get().plugins.needs_refresh is False


# ── pluginStartupCheck ────────────────────────────────────────────────────────

def test_enabled_plugins_and_their_editable_scopes(tmp_path, locations):
    extra = tmp_path / "extra"
    write_json(extra / ".tddagents" / "settings.json", {"enabledPlugins": {"a@m": True, "b@m": True, "c@m": True}})
    locs = replace(locations, additional_directories=(str(extra),))
    user_settings(locs, enabledPlugins={"b@m": False, "d@m": True, "nomarket": True})
    write_json(locs.project_root / ".tddagents" / "settings.json", {"enabledPlugins": {"e@m": True, "d@m": True}})
    assert run(pstc.check_enabled_plugins(locs)) == ["a@m", "c@m", "d@m", "e@m"]
    assert pstc.get_plugin_editable_scopes(locs) == {"a@m": "flag", "c@m": "flag", "d@m": "project", "e@m": "project"}
    assert pstc.is_persistable_scope("user") and not pstc.is_persistable_scope("flag")
    assert pstc.setting_source_to_scope(PermissionRuleSource.POLICY_SETTINGS) == "managed"


def test_missing_plugins_are_enabled_listed_and_not_installed(team, locations):
    ipm.add_installed_plugin("beta@team", installed("/opt/beta"))
    missing = run(pstc.find_missing_plugins(["alpha@team", "beta@team", "ghost@team"], locations))
    assert missing == ["alpha@team"]


def test_install_selected_plugins_registers_and_enables_them(team, locations):
    progress: list[tuple[str, int, int]] = []
    result = run(pstc.install_selected_plugins(["alpha@team", "ghost@team"], locations,
                                               on_progress=lambda *a: progress.append(a)))
    assert result.installed == ["alpha@team"]
    assert [(f.name, f.error) for f in result.failed] == [("ghost@team", "Plugin not found in any marketplace")]
    assert progress == [("alpha@team", 1, 2), ("ghost@team", 2, 2)]
    entry = ipm.load_installed_plugins_from_disk()["plugins"]["alpha@team"][0]
    assert entry["installPath"] == str(team / "alpha") and entry["scope"] == "user"
    settings = json.loads((locations.user_config_dir / "settings.json").read_text())
    assert settings["enabledPlugins"] == {"alpha@team": True}


# ── installPluginsForHeadless ─────────────────────────────────────────────────

async def _no_uninstall(plugin_id: str, scope: str) -> None:
    raise AssertionError("nothing is delisted here")


def test_headless_install_reconciles_declared_marketplaces(tmp_path, locations):
    assert run(hpi.install_plugins_for_headless(locations, uninstall_plugin_op=_no_uninstall)) is False
    root = make_marketplace_dir(tmp_path / "declared")
    user_settings(locations, extraKnownMarketplaces={"team": {"source": {"source": "directory", "path": str(root)}}})
    assert run(hpi.install_plugins_for_headless(locations, uninstall_plugin_op=_no_uninstall)) is True
    assert "team" in json.loads(Path(mm.get_known_marketplaces_file()).read_text())
    assert run(hpi.install_plugins_for_headless(locations, uninstall_plugin_op=_no_uninstall)) is False


def test_headless_install_in_zip_cache_mode_skips_unsupported_sources(tmp_path, locations, monkeypatch):
    cache = tmp_path / "zc"
    monkeypatch.setenv("TDDAGENTS_PLUGIN_USE_ZIP_CACHE", "1")
    monkeypatch.setenv("TDDAGENTS_PLUGIN_CACHE_DIR", str(cache))
    monkeypatch.setattr(hpi, "_register_session_cache_cleanup", lambda: None)
    root = make_marketplace_dir(tmp_path / "declared")
    user_settings(locations, extraKnownMarketplaces={"team": {"source": {"source": "directory", "path": str(root)}}})
    assert run(hpi.install_plugins_for_headless(locations, uninstall_plugin_op=_no_uninstall)) is False
    assert (cache / "marketplaces").is_dir() and (cache / "plugins").is_dir()
    assert json.loads((cache / "known_marketplaces.json").read_text()) == {}


# ── pluginAutoupdate ──────────────────────────────────────────────────────────

def test_autoupdate_skip_switches(monkeypatch):
    for name in ("DISABLE_AUTOUPDATER", "FORCE_AUTOUPDATE_PLUGINS", "TDDAGENTS_DISABLE_NONESSENTIAL_TRAFFIC"):
        monkeypatch.delenv(name, raising=False)
    assert pau.should_skip_plugin_autoupdate() is False
    monkeypatch.setenv("TDDAGENTS_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    assert pau.get_essential_traffic_only_reason() == "TDDAGENTS_DISABLE_NONESSENTIAL_TRAFFIC"
    assert pau.should_skip_plugin_autoupdate() is True
    monkeypatch.setenv("FORCE_AUTOUPDATE_PLUGINS", "true")
    assert pau.should_skip_plugin_autoupdate() is False
    monkeypatch.delenv("TDDAGENTS_DISABLE_NONESSENTIAL_TRAFFIC")
    monkeypatch.delenv("FORCE_AUTOUPDATE_PLUGINS")
    monkeypatch.setenv("DISABLE_AUTOUPDATER", "1")
    assert pau.should_skip_plugin_autoupdate() is True


@dataclass
class _OpResult:
    success: bool
    already_up_to_date: bool | None = None
    old_version: str | None = "1"
    new_version: str | None = "2"
    message: str = ""


def test_update_plugins_for_marketplaces_uses_relevant_installations(locations):
    other_project = str(locations.project_root.parent / "other")
    ipm.add_installed_plugin("a@team", installed("/a"))
    ipm.add_installed_plugin("b@team", installed("/b"), "project", other_project)
    ipm.add_installed_plugin("c@Team", installed("/c"), "local", str(locations.project_root))
    ipm.add_installed_plugin("d@elsewhere", installed("/d"))
    ipm.add_installed_plugin("e@team", installed("/e"))
    seen: list[tuple[str, str]] = []

    async def op(plugin_id: str, scope: str) -> _OpResult:
        seen.append((plugin_id, scope))
        if plugin_id == "e@team":
            return _OpResult(success=True, already_up_to_date=True)
        return _OpResult(success=True)

    updated = run(pau.update_plugins_for_marketplaces({"team"}, locations, update_plugin_op=op))
    assert sorted(seen) == [("a@team", "user"), ("c@Team", "local"), ("e@team", "user")]
    assert sorted(updated) == ["a@team", "c@Team"]


def test_background_autoupdate_refreshes_auto_update_marketplaces_and_notifies(team, locations, monkeypatch):
    for name in ("DISABLE_AUTOUPDATER", "TDDAGENTS_DISABLE_NONESSENTIAL_TRAFFIC"):
        monkeypatch.delenv(name, raising=False)
    user_settings(locations, extraKnownMarketplaces={
        "team": {"source": {"source": "directory", "path": str(team)}, "autoUpdate": True}})
    ipm.add_installed_plugin("alpha@team", installed("/a"))
    refreshed: list[str] = []

    async def fake_refresh(name: str, _: Any = None, *, disable_credential_helper: bool = False) -> None:
        assert disable_credential_helper is True
        refreshed.append(name)

    async def op(plugin_id: str, scope: str) -> _OpResult:
        return _OpResult(success=True)

    monkeypatch.setattr(pau, "refresh_marketplace", fake_refresh)

    async def go() -> None:
        await pau.auto_update_marketplaces_and_plugins_in_background(locations, update_plugin_op=op)

    run(go())
    assert refreshed == ["team"]
    delivered: list[list[str]] = []
    unregister = pau.on_plugins_auto_updated(delivered.append)
    assert delivered == [["alpha@team"]]
    run(go())
    assert delivered == [["alpha@team"], ["alpha@team"]]
    unregister()
    run(go())
    assert len(delivered) == 2


def test_auto_updated_plugin_names_come_from_pending_updates(monkeypatch):
    monkeypatch.setattr(pau, "has_pending_updates", lambda: True)
    monkeypatch.setattr(pau, "get_pending_updates_details",
                        lambda: [ipm.PendingUpdate(plugin_id="p@m", scope="user", old_version="1", new_version="2")])
    assert pau.get_auto_updated_plugin_names() == ["p"]
    monkeypatch.setattr(pau, "has_pending_updates", lambda: False)
    assert pau.get_auto_updated_plugin_names() == []


# ── the startup bookkeeping (main.tsx) ────────────────────────────────────────

def test_startup_bookkeeping_awaits_the_sync_only_when_non_interactive(locations, monkeypatch):
    from app.utils.plugins import cache_utils

    order: list[str] = []

    async def init(_: SettingsLocations) -> None:
        await asyncio.sleep(0)
        order.append("init")

    async def cleanup() -> None:
        order.append("cleanup")

    monkeypatch.setattr(ipm, "initialize_versioned_plugins", init)
    monkeypatch.setattr(cache_utils, "cleanup_orphaned_plugin_versions_in_background", cleanup)

    async def headless() -> list[str]:
        await print_module.initialize_plugins_at_startup(locations, interactive=False)
        seen = list(order)
        await asyncio.gather(*print_module._PLUGIN_STARTUP_TASKS)
        return seen

    assert run(headless()) == ["init"] and order == ["init", "cleanup"]
    order.clear()

    async def interactive() -> list[str]:
        await print_module.initialize_plugins_at_startup(locations, interactive=True)
        seen = list(order)
        await asyncio.gather(*print_module._PLUGIN_STARTUP_TASKS)
        return seen

    assert run(interactive()) == [] and order == ["init", "cleanup"]


def test_startup_bookkeeping_failures_are_logged(locations, monkeypatch):
    async def init(_: SettingsLocations) -> None:
        raise RuntimeError("disk gone")

    monkeypatch.setattr(ipm, "initialize_versioned_plugins", init)
    run(print_module.initialize_plugins_at_startup(locations, interactive=False))
    assert os.path.isdir(str(locations.project_root))
