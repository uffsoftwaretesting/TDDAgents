"""Tests for plugin delisting, the orphaned-version GC and its ripgrep exclusions."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import pytest

from app.utils.plugins import cache_utils, plugin_blocklist, plugin_flagging
from app.utils.plugins import installed_plugins_manager as ipm
from app.utils.plugins import marketplace_manager as mm
from app.utils.plugins import orphaned_plugin_filter as opf
from app.utils.plugins import plugin_options_storage as pos
from app.utils.plugins.plugin_directories import get_plugins_directory
from app.utils.plugins.plugin_loader import get_plugin_cache_path
from app.utils.settings.settings import SettingsLocations


@pytest.fixture(autouse=True)
def _fresh_state():
    mm.clear_marketplaces_cache()
    ipm.clear_installed_plugins_cache()
    ipm.reset_migration_state()
    plugin_flagging.reset_flagged_plugins_cache()
    opf.clear_plugin_cache_exclusions()
    yield
    mm.clear_marketplaces_cache()
    ipm.clear_installed_plugins_cache()
    plugin_flagging.reset_flagged_plugins_cache()
    opf.clear_plugin_cache_exclusions()


@pytest.fixture
def locations(tmp_path: Path) -> SettingsLocations:
    project = tmp_path / "project"
    project.mkdir()
    return SettingsLocations(project_root=project, home=tmp_path / "home", managed_dir=tmp_path / "managed")


def install(plugin_id: str, scope: Any, path: str = "/x", project: str | None = None) -> None:
    ipm.add_installed_plugin(plugin_id, {"version": "1", "installedAt": "t", "installPath": path}, scope, project)


# ── delisting ─────────────────────────────────────────────────────────────────

def test_detect_delisted_plugins_matches_the_marketplace_suffix():
    installed: Any = {"version": 2, "plugins": {"a@m": [], "gone@m": [], "gone@other": [], "a@m-two": []}}
    marketplace: Any = {"name": "m", "owner": {"name": "o"}, "plugins": [{"name": "a", "source": "./a"}]}
    assert plugin_blocklist.detect_delisted_plugins(installed, marketplace, "m") == ["gone@m"]


def make_marketplace(root: Path, name: str, listed: list[str], force: bool) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    manifest = {"name": name, "owner": {"name": "o"}, "forceRemoveDeletedPlugins": force,
                "plugins": [{"name": n, "source": f"./{n}"} for n in listed]}
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps(manifest))
    return root


def test_delisted_plugins_are_uninstalled_from_user_scopes_and_flagged(tmp_path, locations):
    asyncio.run(mm.add_marketplace_source(
        {"source": "directory", "path": str(make_marketplace(tmp_path / "strict", "strict", ["kept"], True))},
        locations))
    asyncio.run(mm.add_marketplace_source(
        {"source": "directory", "path": str(make_marketplace(tmp_path / "lax", "lax", [], False))}, locations))
    install("kept@strict", "user")
    install("gone@strict", "user")
    install("gone@strict", "project", project="/p")
    install("gone@strict", "managed")
    install("managed-only@strict", "managed")
    install("flagged@strict", "user")
    install("gone@lax", "user")
    asyncio.run(plugin_flagging.add_flagged_plugin("flagged@strict"))
    plugin_flagging.reset_flagged_plugins_cache()
    calls: list[tuple[str, str]] = []

    async def uninstall(plugin_id: str, scope: str) -> None:
        calls.append((plugin_id, scope))
        if scope == "project":
            raise RuntimeError("cannot")

    flagged = asyncio.run(plugin_blocklist.detect_and_uninstall_delisted_plugins(uninstall_plugin_op=uninstall))
    assert flagged == ["gone@strict"]
    # Every user-controllable scope is attempted, a failure does not stop the rest, managed is left.
    assert calls == [("gone@strict", "user"), ("gone@strict", "project")]
    assert set(plugin_flagging.get_flagged_plugins()) == {"flagged@strict", "gone@strict"}


def test_an_unavailable_marketplace_is_logged_and_skipped(tmp_path, locations, caplog):
    root = make_marketplace(tmp_path / "strict", "strict", [], True)
    asyncio.run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    (root / ".claude-plugin" / "marketplace.json").unlink()
    mm.clear_marketplaces_cache()
    install("gone@strict", "user")

    async def uninstall(plugin_id: str, scope: str) -> None:
        raise AssertionError("nothing to uninstall")

    with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Plugins"):
        flagged = asyncio.run(plugin_blocklist.detect_and_uninstall_delisted_plugins(uninstall_plugin_op=uninstall))
    assert flagged == []
    assert 'Failed to check for delisted plugins in "strict"' in caplog.text


# ── cache maintenance ─────────────────────────────────────────────────────────

def version_dir(*parts: str) -> Path:
    path = Path(get_plugin_cache_path(), *parts)
    path.mkdir(parents=True)
    (path / "f").write_text("x")
    return path


def age(path: Path, days: float) -> None:
    then = time.time() - days * 24 * 60 * 60
    os.utime(path, (then, then))


def test_cleanup_marks_new_orphans_deletes_old_ones_and_unmarks_installed():
    installed = version_dir("m", "p", "2.0")
    (installed / ".orphaned_at").write_text("1")
    install("p@m", "user", path=str(installed))
    fresh = version_dir("m", "p", "1.0")
    stale = version_dir("m", "q", "0.1")
    (stale / ".orphaned_at").write_text("1")
    age(stale / ".orphaned_at", 8)
    recent = version_dir("m", "r", "0.1")
    (recent / ".orphaned_at").write_text("1")
    age(recent / ".orphaned_at", 6)
    asyncio.run(cache_utils.cleanup_orphaned_plugin_versions_in_background())
    assert not (installed / ".orphaned_at").exists()
    assert (fresh / ".orphaned_at").read_text().isdigit()
    assert not stale.exists() and not stale.parent.exists()
    assert recent.exists()


def test_cleanup_is_skipped_in_zip_mode_and_when_the_registry_cannot_load(monkeypatch):
    orphan = version_dir("m", "p", "1.0")
    monkeypatch.setenv("TDDAGENTS_PLUGIN_USE_ZIP_CACHE", "1")
    asyncio.run(cache_utils.cleanup_orphaned_plugin_versions_in_background())
    assert not (orphan / ".orphaned_at").exists()
    monkeypatch.delenv("TDDAGENTS_PLUGIN_USE_ZIP_CACHE")

    def broken() -> Any:
        raise RuntimeError("corrupt")

    monkeypatch.setattr(cache_utils, "load_installed_plugins_from_disk", broken)
    asyncio.run(cache_utils.cleanup_orphaned_plugin_versions_in_background())
    assert not (orphan / ".orphaned_at").exists()


def test_cleanup_never_follows_a_symlinked_directory(tmp_path):
    outside = tmp_path / "outside" / "1.0"
    outside.mkdir(parents=True)
    plugin = Path(get_plugin_cache_path(), "m", "p")
    plugin.mkdir(parents=True)
    (plugin / "1.0").symlink_to(outside, target_is_directory=True)
    asyncio.run(cache_utils.cleanup_orphaned_plugin_versions_in_background())
    assert not (outside / ".orphaned_at").exists()


def test_mark_orphaned_logs_a_missing_directory(tmp_path, caplog):
    with caplog.at_level(logging.DEBUG, logger="TDDOrchestrator.Plugins"):
        asyncio.run(cache_utils.mark_plugin_version_orphaned(str(tmp_path / "absent")))
    assert "Failed to write .orphaned_at" in caplog.text


def test_clear_all_caches_clears_the_options_memo(locations, monkeypatch):
    pos.load_plugin_options("p@m", locations)
    assert pos._load_cache
    cache_utils.clear_all_caches()
    assert not pos._load_cache


# ── ripgrep exclusions ────────────────────────────────────────────────────────

def test_exclusions_name_each_orphaned_version_and_are_frozen():
    orphan = version_dir("m", "p", "1.0")
    (orphan / ".orphaned_at").write_text("1")
    version_dir("m", "p", "2.0")
    expected = ["!**/m/p/1.0/**"]
    assert asyncio.run(opf.get_glob_exclusions_for_plugin_cache()) == expected
    other = version_dir("m", "q", "3.0")
    (other / ".orphaned_at").write_text("1")
    assert asyncio.run(opf.get_glob_exclusions_for_plugin_cache()) == expected
    opf.clear_plugin_cache_exclusions()
    assert sorted(asyncio.run(opf.get_glob_exclusions_for_plugin_cache())) == ["!**/m/p/1.0/**", "!**/m/q/3.0/**"]


def test_exclusions_only_for_searches_overlapping_the_cache(tmp_path):
    orphan = version_dir("m", "p", "1.0")
    (orphan / ".orphaned_at").write_text("1")
    cache = get_plugin_cache_path()
    assert asyncio.run(opf.get_glob_exclusions_for_plugin_cache(str(tmp_path / "elsewhere"))) == []
    assert asyncio.run(opf.get_glob_exclusions_for_plugin_cache(get_plugins_directory())) == ["!**/m/p/1.0/**"]
    assert asyncio.run(opf.get_glob_exclusions_for_plugin_cache(os.path.join(cache, "m"))) == ["!**/m/p/1.0/**"]
    assert asyncio.run(opf.get_glob_exclusions_for_plugin_cache("/")) == ["!**/m/p/1.0/**"]


def test_no_cache_directory_means_no_exclusions():
    assert asyncio.run(opf.get_glob_exclusions_for_plugin_cache()) == []
