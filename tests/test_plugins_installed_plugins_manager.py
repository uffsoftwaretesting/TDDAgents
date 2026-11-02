"""Tests for app/utils/plugins/installed_plugins_manager.py (upstream installedPluginsManager.ts)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.utils.plugins import installed_plugins_manager as ipm
from app.utils.plugins.marketplace_manager import PluginByIdResult
from app.utils.plugins.plugin_loader import (
    get_plugin_cache_path,
    get_versioned_cache_path,
)
from app.utils.settings.settings import SettingsLocations
from tests.plugin_git_helpers import make_repo, manifest


@pytest.fixture(autouse=True)
def _fresh_state():
    ipm.clear_installed_plugins_cache()
    ipm.reset_migration_state()
    yield
    ipm.clear_installed_plugins_cache()
    ipm.reset_migration_state()


@pytest.fixture
def locations(tmp_path: Path) -> SettingsLocations:
    project = tmp_path / "project"
    project.mkdir()
    return SettingsLocations(project_root=project, home=tmp_path / "home", managed_dir=tmp_path / "managed")


def _write_settings(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _file() -> Path:
    return Path(ipm.get_installed_plugins_file_path())


def _write_file(data: Any) -> None:
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps(data), encoding="utf-8")


def _read_file() -> Any:
    return json.loads(_file().read_text(encoding="utf-8"))


V1 = {
    "version": 1,
    "plugins": {
        "fmt@tools": {"version": "1.0", "installedAt": "2024-01-01T00:00:00Z", "installPath": "/old/fmt"},
    },
}


def test_paths():
    assert ipm.get_installed_plugins_file_path().endswith(os.path.join("plugins", "installed_plugins.json"))
    assert ipm.get_installed_plugins_v2_file_path().endswith(os.path.join("plugins", "installed_plugins_v2.json"))


def test_load_missing_file_is_empty_v2():
    assert ipm.load_installed_plugins_v2() == {"version": 2, "plugins": {}}
    assert ipm.load_installed_plugins_from_disk() == {"version": 2, "plugins": {}}


def test_v1_is_converted_in_memory_to_user_scope_at_the_versioned_path():
    _write_file(V1)
    data = ipm.load_installed_plugins_v2()
    assert data == {
        "version": 2,
        "plugins": {
            "fmt@tools": [{
                "scope": "user",
                "installPath": get_versioned_cache_path("fmt@tools", "1.0"),
                "version": "1.0",
                "installedAt": "2024-01-01T00:00:00Z",
            }],
        },
    }
    # The file itself is untouched by a load.
    assert _read_file()["version"] == 1


def test_a_corrupt_file_loads_as_empty_state():
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text("{nope", encoding="utf-8")
    assert ipm.load_installed_plugins_v2() == {"version": 2, "plugins": {}}
    _write_file({"version": 2, "plugins": {"not-an-id": []}})
    assert ipm.load_installed_plugins_from_disk() == {"version": 2, "plugins": {}}


def test_load_is_cached_until_cleared():
    _write_file({"version": 2, "plugins": {}})
    assert ipm.load_installed_plugins_v2()["plugins"] == {}
    _write_file({"version": 2, "plugins": {"a@b": [{"scope": "user", "installPath": "/x"}]}})
    assert ipm.load_installed_plugins_v2()["plugins"] == {}
    assert "a@b" in ipm.load_installed_plugins_from_disk()["plugins"]
    ipm.clear_installed_plugins_cache()
    assert "a@b" in ipm.load_installed_plugins_v2()["plugins"]


def test_migration_converts_v1_in_place_once():
    _write_file(V1)
    ipm.migrate_to_single_plugin_file()
    assert _read_file()["version"] == 2
    _write_file(V1)
    ipm.migrate_to_single_plugin_file()
    assert _read_file()["version"] == 1  # already ran this session
    ipm.reset_migration_state()
    ipm.migrate_to_single_plugin_file()
    assert _read_file()["version"] == 2


def test_migration_renames_the_legacy_v2_file_and_cleans_the_legacy_cache():
    cache = Path(get_plugin_cache_path())
    (cache / "legacy-unref").mkdir(parents=True)
    (cache / "legacy-ref").mkdir()
    (cache / "mkt" / "plug" / "1.0").mkdir(parents=True)
    legacy_v2 = Path(ipm.get_installed_plugins_v2_file_path())
    legacy_v2.write_text(json.dumps({
        "version": 2,
        "plugins": {"r@m": [{"scope": "user", "installPath": str(cache / "legacy-ref")}]},
    }), encoding="utf-8")
    ipm.migrate_to_single_plugin_file()
    assert not legacy_v2.exists()
    assert "r@m" in _read_file()["plugins"]
    assert not (cache / "legacy-unref").exists()
    assert (cache / "legacy-ref").exists()
    assert (cache / "mkt" / "plug" / "1.0").exists()


def test_add_and_remove_plugin_installation_per_scope_and_project():
    ipm.add_plugin_installation("p@m", "user", "/u", {"version": "1"})
    ipm.add_plugin_installation("p@m", "project", "/p", {"version": "2"}, "/proj")
    ipm.add_plugin_installation("p@m", "user", "/u2", {"version": "3", "installedAt": "then"})
    entries = _read_file()["plugins"]["p@m"]
    assert [(e["scope"], e["installPath"], e.get("projectPath")) for e in entries] == [
        ("user", "/u2", None), ("project", "/p", "/proj"),
    ]
    assert entries[0]["installedAt"] == "then" and "lastUpdated" in entries[0]
    assert "gitCommitSha" not in entries[0]
    ipm.remove_plugin_installation("p@m", "project", "/other")
    assert len(_read_file()["plugins"]["p@m"]) == 2
    ipm.remove_plugin_installation("p@m", "project", "/proj")
    ipm.remove_plugin_installation("p@m", "user")
    assert _read_file()["plugins"] == {}
    ipm.remove_plugin_installation("absent@m", "user")


def test_in_memory_snapshot_ignores_background_updates_until_reset():
    ipm.add_installed_plugin("p@m", {"version": "1", "installedAt": "t", "installPath": "/v1"})
    assert ipm.get_in_memory_installed_plugins()["plugins"]["p@m"][0]["installPath"] == "/v1"
    ipm.update_installation_path_on_disk("p@m", "user", None, "/v2", "2", "abc")
    assert ipm.get_in_memory_installed_plugins()["plugins"]["p@m"][0]["installPath"] == "/v1"
    disk = ipm.load_installed_plugins_from_disk()["plugins"]["p@m"][0]
    assert (disk["installPath"], disk["version"], disk["gitCommitSha"]) == ("/v2", "2", "abc")
    assert ipm.has_pending_updates() is True
    assert ipm.get_pending_update_count() == 1
    assert ipm.get_pending_updates_details() == [ipm.PendingUpdate("p@m", "user", "1", "2")]
    ipm.reset_in_memory_state()
    ipm.clear_installed_plugins_cache()
    assert ipm.has_pending_updates() is False


def test_update_on_disk_is_a_no_op_for_an_unknown_plugin_or_scope():
    ipm.add_installed_plugin("p@m", {"version": "1", "installedAt": "t", "installPath": "/v1"})
    before = _read_file()
    ipm.update_installation_path_on_disk("q@m", "user", None, "/v2", "2")
    ipm.update_installation_path_on_disk("p@m", "project", "/proj", "/v2", "2")
    assert _read_file() == before


def test_remove_all_plugins_for_marketplace():
    ipm.add_installed_plugin("a@m", {"version": "1", "installedAt": "t", "installPath": "/a"})
    ipm.add_installed_plugin("a@m", {"version": "1", "installedAt": "t", "installPath": "/a"}, "project", "/p")
    ipm.add_installed_plugin("b@m", {"version": "1", "installedAt": "t", "installPath": "/b"})
    ipm.add_installed_plugin("c@other", {"version": "1", "installedAt": "t", "installPath": "/c"})
    removed = ipm.remove_all_plugins_for_marketplace("m")
    assert removed == ipm.RemovedPlugins(orphaned_paths=["/a", "/b"], removed_plugin_ids=["a@m", "b@m"])
    assert list(_read_file()["plugins"]) == ["c@other"]
    assert ipm.remove_all_plugins_for_marketplace("") == ipm.RemovedPlugins([], [])


def test_is_plugin_installed_needs_a_relevant_installation_and_a_settings_entry(locations: SettingsLocations):
    ipm.add_installed_plugin("p@m", {"version": "1", "installedAt": "t", "installPath": "/x"}, "project", "/elsewhere")
    assert ipm.is_plugin_installed("p@m", locations) is False
    ipm.add_installed_plugin("p@m", {"version": "1", "installedAt": "t", "installPath": "/x"}, "local",
                             str(locations.project_root))
    assert ipm.is_plugin_installed("p@m", locations) is False  # not in enabledPlugins
    _write_settings(locations.project_root / ".tddagents" / "settings.json", {"enabledPlugins": {"p@m": False}})
    assert ipm.is_plugin_installed("p@m", locations) is True
    assert ipm.is_plugin_globally_installed("p@m", locations) is False
    ipm.add_installed_plugin("p@m", {"version": "1", "installedAt": "t", "installPath": "/x"}, "user")
    assert ipm.is_plugin_globally_installed("p@m", locations) is True
    assert ipm.is_plugin_installed("absent@m", locations) is False


def test_remove_installed_plugin_returns_v1_metadata():
    ipm.add_installed_plugin("p@m", {"version": "1", "installedAt": "t", "installPath": "/x", "gitCommitSha": "s"})
    assert ipm.remove_installed_plugin("p@m") == {
        "version": "1", "installedAt": "t", "installPath": "/x", "gitCommitSha": "s",
    }
    assert ipm.remove_installed_plugin("p@m") is None


def test_delete_plugin_cache_removes_the_version_and_an_empty_plugin_dir():
    version_dir = Path(get_versioned_cache_path("p@m", "1"))
    version_dir.mkdir(parents=True)
    (version_dir / "f").write_text("x")
    ipm.delete_plugin_cache(str(version_dir))
    assert not version_dir.exists()
    assert not version_dir.parent.exists()
    keep = Path(get_versioned_cache_path("q@m", "1"))
    other = Path(get_versioned_cache_path("q@m", "2"))
    keep.mkdir(parents=True)
    other.mkdir(parents=True)
    ipm.delete_plugin_cache(str(keep))
    assert other.exists()


def test_initialize_and_sync_from_enabled_plugins(tmp_path: Path, locations: SettingsLocations, monkeypatch: Any):
    market = tmp_path / "market"
    make_repo(market, {"plugins/loc/.claude-plugin/plugin.json": manifest("loc", version="2.5")})
    cached = Path(get_plugin_cache_path()) / "ext"
    (cached / ".claude-plugin").mkdir(parents=True)
    (cached / ".claude-plugin" / "plugin.json").write_text(manifest("ext"))
    entries = {
        "loc@m": {"name": "loc", "source": "./plugins/loc"},
        "ext@m": {"name": "ext", "source": {"source": "github", "repo": "o/r"}, "version": "9.9"},
        "uncached@m": {"name": "uncached", "source": {"source": "github", "repo": "o/u"}},
    }

    async def fake_get_plugin_by_id(plugin_id: str) -> PluginByIdResult | None:
        entry = entries.get(plugin_id)
        if entry is None:
            return None
        return PluginByIdResult(entry=entry, marketplace_install_location=str(market))  # type: ignore[arg-type]

    monkeypatch.setattr(ipm, "get_plugin_by_id", fake_get_plugin_by_id)
    assert locations.home is not None
    _write_settings(locations.home / ".tddagents" / "settings.json",
                    {"enabledPlugins": {"loc@m": True, "ext@m": True, "missing@m": True, "legacy": True}})
    _write_settings(locations.project_root / ".tddagents" / "settings.local.json",
                    {"enabledPlugins": {"uncached@m": True, "ext@m": True}})
    asyncio.run(ipm.initialize_versioned_plugins(locations))
    plugins = _read_file()["plugins"]
    assert sorted(plugins) == ["ext@m", "loc@m"]
    loc = plugins["loc@m"][0]
    assert (loc["scope"], loc["version"], loc["installPath"]) == (
        "user", "2.5", get_versioned_cache_path("loc@m", "2.5"),
    )
    assert len(loc["gitCommitSha"]) == 40
    ext = plugins["ext@m"][0]
    # The local settings file is the most specific scope; no manifest version -> the entry's.
    assert (ext["scope"], ext["projectPath"], ext["version"]) == ("local", str(locations.project_root), "9.9")
    assert ipm.get_in_memory_installed_plugins()["plugins"].keys() == plugins.keys()


def test_sync_updates_a_scope_and_skips_when_everything_exists(locations: SettingsLocations, monkeypatch: Any):
    ipm.add_installed_plugin("p@m", {"version": "1", "installedAt": "t", "installPath": "/x"}, "user")
    _write_settings(locations.project_root / ".tddagents" / "settings.json", {"enabledPlugins": {"p@m": True}})

    async def unreachable(plugin_id: str) -> None:
        raise AssertionError("no lookup for a plugin already in the file")

    monkeypatch.setattr(ipm, "get_plugin_by_id", unreachable)
    asyncio.run(ipm.migrate_from_enabled_plugins(locations))
    # Every plugin exists: the fast path returns before touching scopes.
    assert _read_file()["plugins"]["p@m"][0]["scope"] == "user"
    _write_settings(locations.project_root / ".tddagents" / "settings.json",
                    {"enabledPlugins": {"p@m": True, "q@m": True}})

    async def none(plugin_id: str) -> None:
        return None

    monkeypatch.setattr(ipm, "get_plugin_by_id", none)
    ipm.clear_installed_plugins_cache()
    asyncio.run(ipm.migrate_from_enabled_plugins(locations))
    entry = _read_file()["plugins"]["p@m"][0]
    assert (entry["scope"], entry["projectPath"]) == ("project", str(locations.project_root))
