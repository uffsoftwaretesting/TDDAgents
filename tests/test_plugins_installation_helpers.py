"""Tests for app/utils/plugins/plugin_installation_helpers.py (offline: a directory marketplace)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.utils.plugins import installed_plugins_manager as ipm
from app.utils.plugins import marketplace_manager as mm
from app.utils.plugins import plugin_installation_helpers as pih
from app.utils.plugins.dependency_resolver import ResolutionResult
from app.utils.plugins.plugin_loader import (
    get_plugin_cache_path,
    get_versioned_cache_path,
)
from app.utils.settings.settings import SettingsLocations


@pytest.fixture(autouse=True)
def _fresh_state():
    mm.clear_marketplaces_cache()
    ipm.clear_installed_plugins_cache()
    ipm.reset_migration_state()
    yield
    mm.clear_marketplaces_cache()
    ipm.clear_installed_plugins_cache()
    ipm.reset_migration_state()


@pytest.fixture
def locations(tmp_path: Path) -> SettingsLocations:
    project = tmp_path / "project"
    project.mkdir()
    managed = tmp_path / "managed"
    managed.mkdir()
    return SettingsLocations(project_root=project, home=tmp_path / "home", managed_dir=managed)


ENTRIES: list[dict[str, Any]] = [
    {"name": "app", "source": "./app", "dependencies": ["lib"]},
    {"name": "lib", "source": "./lib", "version": "1.0.0"},
    {"name": "lonely", "source": "./lonely", "dependencies": ["ghost"]},
    {"name": "greedy", "source": "./greedy", "dependencies": ["x@elsewhere"]},
]


@pytest.fixture
def marketplace(tmp_path: Path, locations: SettingsLocations) -> Path:
    root = tmp_path / "mkt"
    (root / ".tddagents-plugin").mkdir(parents=True)
    (root / ".tddagents-plugin" / "marketplace.json").write_text(json.dumps(
        {"name": "team", "owner": {"name": "O"}, "plugins": ENTRIES}))
    for entry in ENTRIES:
        plugin = root / entry["source"]
        (plugin / ".tddagents-plugin").mkdir(parents=True)
        (plugin / ".tddagents-plugin" / "plugin.json").write_text(json.dumps({"name": entry["name"]}))
        (plugin / "README.md").write_text(entry["name"])
    asyncio.run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    return root


def entry(name: str) -> Any:
    return next(e for e in ENTRIES if e["name"] == name)


def user_settings(locations: SettingsLocations) -> Any:
    return json.loads((locations.user_config_dir / "settings.json").read_text())


def install(name: str, locations: SettingsLocations, scope: Any = "user") -> pih.InstallPluginResult:
    params = pih.InstallPluginParams(plugin_id=f"{name}@team", entry=entry(name), marketplace_name="team", scope=scope)
    return asyncio.run(pih.install_plugin_from_marketplace(params, locations=locations))


def write_policy(locations: SettingsLocations, enabled: dict[str, bool]) -> None:
    (locations.managed_dir / "managed-settings.json").write_text(json.dumps({"enabledPlugins": enabled}))


# ── small helpers ─────────────────────────────────────────────────────────────

def test_validate_path_within_base(tmp_path: Path):
    base = str(tmp_path / "base")
    assert pih.validate_path_within_base(base, "./a/b") == os.path.join(base, "a", "b")
    assert pih.validate_path_within_base(base, ".") == base
    with pytest.raises(ValueError, match='^Path traversal detected: "../base2" would escape the base directory$'):
        pih.validate_path_within_base(base, "../base2")


def test_parse_plugin_id_and_timestamp():
    assert pih.parse_plugin_id("p@m") == pih.ParsedPluginId(name="p", marketplace="m")
    assert [pih.parse_plugin_id(v) for v in ("p", "@m", "p@", "a@b@c")] == [None, None, None, None]
    stamp = pih.get_current_timestamp()
    assert stamp.endswith("Z") and len(stamp) == len("2026-01-01T00:00:00.000Z")


def test_format_resolution_errors():
    cycle = ResolutionResult(ok=False, reason="cycle", chain=("a@m", "b@m", "a@m"))
    assert pih.format_resolution_error(cycle) == "Dependency cycle: a@m → b@m → a@m"
    cross = ResolutionResult(ok=False, reason="cross-marketplace", dependency="x@other", required_by="a@m")
    assert pih.format_resolution_error(cross) == (
        'Dependency "x@other" (required by a@m) is in marketplace "other", which is not in the allowlist — '
        "cross-marketplace dependencies are blocked by default. Install it manually first. Add \"other\" to "
        "allowCrossMarketplaceDependenciesOn in the ROOT marketplace's marketplace.json (the marketplace of the "
        "plugin you're installing — only its allowlist applies; no transitive trust)."
    )
    bare = ResolutionResult(ok=False, reason="cross-marketplace", dependency="x", required_by="a@m")
    assert "is in a different marketplace, which" in pih.format_resolution_error(bare)
    missing = ResolutionResult(ok=False, reason="not-found", missing="g@m", required_by="a@m")
    assert pih.format_resolution_error(missing) == (
        'Dependency "g@m" (required by a@m) not found. Is the "m" marketplace added?')
    missing_bare = ResolutionResult(ok=False, reason="not-found", missing="g", required_by="a")
    assert pih.format_resolution_error(missing_bare) == (
        'Dependency "g" (required by a) not found in any configured marketplace')


# ── install ───────────────────────────────────────────────────────────────────

def test_install_enables_and_caches_the_dependency_closure(marketplace: Path, locations: SettingsLocations):
    result = install("app", locations)
    assert result == pih.InstallPluginResult(
        success=True, message="✓ Installed app (+ 1 dependency). Run /reload-plugins to activate.")
    assert user_settings(locations)["enabledPlugins"] == {"lib@team": True, "app@team": True}
    plugins = ipm.load_installed_plugins_from_disk()["plugins"]
    lib = plugins["lib@team"][0]
    # The marketplace entry's version names the versioned cache directory.
    assert (lib["scope"], lib["version"], lib["installPath"]) == (
        "user", "1.0.0", get_versioned_cache_path("lib@team", "1.0.0"))
    assert (Path(lib["installPath"]) / "README.md").read_text() == "lib"
    app = plugins["app@team"][0]
    assert app["version"] == "unknown" and "projectPath" not in app
    assert (Path(app["installPath"]) / "README.md").read_text() == "app"


def test_project_scope_records_the_project_and_keeps_enabled_dependencies(marketplace, locations):
    project_settings = locations.project_root / ".tddagents" / "settings.json"
    project_settings.parent.mkdir(parents=True)
    project_settings.write_text(json.dumps({"enabledPlugins": {"lib@team": ["^1.0.0"], "other@x": False}}))
    assert install("app", locations, scope="project").message == (
        "✓ Installed app. Run /reload-plugins to activate.")
    # A version-constrained dependency is already enabled: left as it is, not re-cached.
    assert json.loads(project_settings.read_text())["enabledPlugins"] == {
        "lib@team": ["^1.0.0"], "other@x": False, "app@team": True}
    plugins = ipm.load_installed_plugins_from_disk()["plugins"]
    assert list(plugins) == ["app@team"]
    assert plugins["app@team"][0]["projectPath"] == str(locations.project_root)


@pytest.mark.parametrize(("blocked", "message"), [
    ("app@team", 'Plugin "app" is blocked by your organization\'s policy and cannot be installed'),
    ("lib@team", 'Cannot install "app": dependency "lib@team" is blocked by your organization\'s policy'),
])
def test_policy_blocks_the_plugin_or_a_dependency_before_any_write(marketplace, locations, blocked, message):
    write_policy(locations, {blocked: False})
    assert install("app", locations) == pih.InstallPluginResult(success=False, error=message)
    assert not (locations.user_config_dir / "settings.json").exists()
    assert not os.path.exists(get_plugin_cache_path())


def test_resolution_failures_are_reported(marketplace, locations):
    assert install("lonely", locations).error == (
        'Dependency "ghost@team" (required by lonely@team) not found. Is the "team" marketplace added?')
    assert (install("greedy", locations).error or "").startswith(
        'Dependency "x@elsewhere" (required by greedy@team) is in marketplace "elsewhere"')


def test_a_local_entry_needs_its_marketplace_location(locations):
    result = asyncio.run(pih.install_resolved_plugin(
        plugin_id="app@team", entry=entry("app"), scope="user", locations=locations))
    assert (result.ok, result.reason, result.plugin_name) == (False, "local-source-no-location", "app")
    params = pih.InstallPluginParams(plugin_id="app@nowhere", entry=entry("app"), marketplace_name="nowhere")
    assert asyncio.run(pih.install_plugin_from_marketplace(params, locations=locations)).error == (
        'Cannot install local plugin "app" without marketplace install location')


def test_settings_write_failure_and_unexpected_errors(marketplace, locations, monkeypatch):
    monkeypatch.setattr(pih, "update_settings_for_source", lambda source, locs, patch: "disk full")
    assert install("app", locations).error == "Failed to update settings: disk full"

    async def boom(plugin_id: str) -> None:
        raise RuntimeError("lookup exploded")

    monkeypatch.setattr(pih, "get_plugin_by_id", boom)
    assert install("app", locations).error == "Failed to install: lookup exploded"


def test_a_plugin_named_like_its_marketplace_moves_into_its_own_subdirectory(tmp_path: Path):
    source = tmp_path / "same"
    (source / ".tddagents-plugin").mkdir(parents=True)
    (source / ".tddagents-plugin" / "plugin.json").write_text(json.dumps({"name": "same", "version": "2.0.0"}))
    path = asyncio.run(pih.cache_and_register_plugin(
        "same@same", {"name": "same", "source": "./same"}, "user", None, str(source)))
    assert path == get_versioned_cache_path("same@same", "2.0.0")
    assert json.loads((Path(path) / ".tddagents-plugin" / "plugin.json").read_text())["name"] == "same"
    assert sorted(os.listdir(Path(get_plugin_cache_path()) / "same")) == ["same"]


def test_register_without_caching_defaults_the_version():
    pih.register_plugin_installation(pih.PluginInstallationInfo(plugin_id="p@m", install_path="/opt/p"))
    entry_ = ipm.load_installed_plugins_from_disk()["plugins"]["p@m"][0]
    assert (entry_["version"], entry_["installPath"], entry_["scope"]) == ("unknown", "/opt/p", "user")
    assert entry_["installedAt"] == entry_["lastUpdated"]


def test_zip_cache_mode_refuses_before_caching(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_PLUGIN_USE_ZIP_CACHE", "1")
    with pytest.raises(NotImplementedError, match="stage 2"):
        asyncio.run(pih.cache_and_register_plugin("p@m", {"name": "p", "source": "./p"}, "user", None, str(tmp_path)))
    assert not os.path.exists(get_plugin_cache_path())
