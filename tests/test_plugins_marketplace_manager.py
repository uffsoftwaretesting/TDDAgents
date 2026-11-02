"""Tests for app/utils/plugins/marketplace_manager.py (offline: directories, settings, local bare repos)."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import shutil
import socket
import ssl
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.utils.errors import ConfigParseError
from app.utils.exec_file_no_throw import ExecResult
from app.utils.plugins import marketplace_manager as mm
from app.utils.settings.settings import SettingsLocations

GIT = shutil.which("git")


def run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    mm.clear_marketplaces_cache()
    monkeypatch.delenv("TDDAGENTS_PLUGIN_SEED_DIR", raising=False)
    monkeypatch.delenv("TDDAGENTS_PLUGIN_CACHE_DIR", raising=False)
    monkeypatch.delenv("TDDAGENTS_PLUGIN_GIT_TIMEOUT_MS", raising=False)
    yield
    mm.clear_marketplaces_cache()


@pytest.fixture
def locations(tmp_path) -> SettingsLocations:
    project = tmp_path / "project"
    project.mkdir()
    managed = tmp_path / "managed"
    managed.mkdir()
    return SettingsLocations(project_root=project, home=tmp_path / "home", managed_dir=managed)


def manifest(name: str, plugins: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"name": name, "owner": {"name": "Owner"}, "plugins": plugins or [{"name": "p1", "source": "./p1"}]}


def make_marketplace_dir(root: Path, name: str, plugins: list[dict[str, Any]] | None = None) -> Path:
    (root / ".tddagents-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".tddagents-plugin" / "marketplace.json").write_text(json.dumps(manifest(name, plugins)))
    return root


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def known_config() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(Path(mm.get_known_marketplaces_file()).read_text())
    return data


# ── known_marketplaces.json ──────────────────────────────────────────────────

def test_paths_live_under_the_plugins_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    assert mm.get_known_marketplaces_file() == str(tmp_path / "cfg" / "plugins" / "known_marketplaces.json")
    assert mm.get_marketplaces_cache_dir() == str(tmp_path / "cfg" / "plugins" / "marketplaces")


def test_missing_config_loads_empty():
    assert run(mm.load_known_marketplaces_config()) == {}


def test_corrupted_schema_raises_config_parse_error_with_issue_paths():
    write_json(Path(mm.get_known_marketplaces_file()), {"m": {"source": {"source": "github"}, "lastUpdated": "x"}})
    with pytest.raises(ConfigParseError) as info:
        run(mm.load_known_marketplaces_config())
    assert str(info.value).startswith("Marketplace configuration file is corrupted: ")
    assert "m.source.repo" in str(info.value)
    assert info.value.file_path == mm.get_known_marketplaces_file()
    assert run(mm.load_known_marketplaces_config_safe()) == {}


def test_malformed_json_raises_with_helpful_message():
    path = Path(mm.get_known_marketplaces_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    with pytest.raises(RuntimeError, match="^Failed to load marketplace configuration: "):
        run(mm.load_known_marketplaces_config())
    assert run(mm.load_known_marketplaces_config_safe()) == {}


def test_save_validates_and_writes_two_space_json(tmp_path):
    config = {"m": {"source": {"source": "directory", "path": "/x", "extra": 1}, "installLocation": "/x",
                    "lastUpdated": "t"}}
    run(mm.save_known_marketplaces_config(config))
    text = Path(mm.get_known_marketplaces_file()).read_text()
    assert text.startswith('{\n  "m": {')
    assert "extra" not in text
    with pytest.raises(ConfigParseError, match="^Invalid marketplace config: "):
        run(mm.save_known_marketplaces_config({"m": {"source": {"source": "nope"}}}))


# ── adding ────────────────────────────────────────────────────────────────────

def test_add_directory_source_registers_the_users_path_and_is_idempotent(tmp_path, locations):
    root = make_marketplace_dir(tmp_path / "mkt", "my-mkt")
    progress: list[str] = []
    result = run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations, progress.append))
    assert (result.name, result.already_materialized) == ("my-mkt", False)
    entry = known_config()["my-mkt"]
    assert entry["installLocation"] == str(root)
    assert entry["source"] == {"source": "directory", "path": str(root)}
    assert entry["lastUpdated"].endswith("Z")
    again = run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    assert (again.name, again.already_materialized) == ("my-mkt", True)
    assert root.exists()


def test_add_relative_directory_resolves_against_the_process_directory(tmp_path, locations, monkeypatch):
    make_marketplace_dir(tmp_path / "rel" / "mkt", "rel-mkt")
    monkeypatch.chdir(tmp_path / "rel")
    result = run(mm.add_marketplace_source({"source": "directory", "path": "./mkt"}, locations))
    assert result.resolved_source == {"source": "directory", "path": str(tmp_path / "rel" / "mkt")}


def test_add_file_source_reads_the_json_path(tmp_path, locations):
    root = make_marketplace_dir(tmp_path / "f", "file-mkt")
    path = root / ".tddagents-plugin" / "marketplace.json"
    result = run(mm.add_marketplace_source({"source": "file", "path": str(path)}, locations))
    assert result.name == "file-mkt"
    assert known_config()["file-mkt"]["installLocation"] == str(root)


def test_add_missing_directory_reports_the_checked_path(tmp_path, locations):
    expected = "Marketplace file not found at .*nowhere/.tddagents-plugin/marketplace.json"
    with pytest.raises(RuntimeError, match=expected):
        run(mm.add_marketplace_source({"source": "directory", "path": str(tmp_path / "nowhere")}, locations))


def test_reserved_name_from_a_non_github_source_is_rejected(tmp_path, locations):
    root = make_marketplace_dir(tmp_path / "official", "claude-plugins-official")
    with pytest.raises(ValueError, match="reserved for official Anthropic marketplaces"):
        run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    assert run(mm.load_known_marketplaces_config()) == {}


def test_npm_sources_are_not_implemented(locations):
    with pytest.raises(RuntimeError, match="NPM marketplace sources not yet implemented"):
        run(mm.add_marketplace_source({"source": "npm", "package": "pkg"}, locations))


def test_blocklisted_source_is_refused_before_any_disk_work(tmp_path, locations):
    root = make_marketplace_dir(tmp_path / "mkt", "blocked")
    write_json(locations.managed_dir / "managed-settings.json",
               {"blockedMarketplaces": [{"source": "directory", "path": str(root)}]})
    with pytest.raises(RuntimeError) as info:
        run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    assert str(info.value) == f"Marketplace source 'dir:{root}' is blocked by enterprise policy."
    assert not Path(mm.get_known_marketplaces_file()).exists()


def test_empty_allowlist_blocks_everything(tmp_path, locations):
    write_json(locations.managed_dir / "managed-settings.json", {"strictKnownMarketplaces": []})
    with pytest.raises(RuntimeError) as info:
        run(mm.add_marketplace_source({"source": "directory", "path": str(tmp_path)}, locations))
    assert str(info.value).endswith("is blocked by enterprise policy. No external marketplaces are allowed.")


def test_allowlist_message_lists_sources_and_github_tip(locations):
    write_json(locations.managed_dir / "managed-settings.json", {"strictKnownMarketplaces": [
        {"source": "hostPattern", "hostPattern": "^ghe\\.corp$"}, {"source": "github", "repo": "a/b"},
    ]})
    with pytest.raises(RuntimeError) as info:
        run(mm.add_marketplace_source({"source": "github", "repo": "x/y"}, locations))
    message = str(info.value)
    assert message.startswith("Marketplace source 'github:x/y' (github.com) is blocked by enterprise policy.")
    assert "Allowed sources: hostPattern:^ghe\\.corp$, github:a/b" in message
    assert 'Tip: The shorthand "x/y" assumes github.com.' in message
    assert "git@your-github-host.com:x/y.git" in message


def test_settings_source_synthesizes_the_manifest_in_the_cache(locations):
    source = {"source": "settings", "name": "inline-mkt",
              "plugins": [{"name": "p", "source": {"source": "github", "repo": "o/p"}}]}
    result = run(mm.add_marketplace_source(source, locations))
    assert result.name == "inline-mkt"
    cached = Path(mm.get_marketplaces_cache_dir()) / "inline-mkt" / ".tddagents-plugin" / "marketplace.json"
    assert json.loads(cached.read_text())["owner"] == {"name": "settings"}
    marketplace = run(mm.get_marketplace("inline-mkt"))
    assert marketplace["plugins"][0]["strict"] is True


# ── url sources (HTTP replaced) ───────────────────────────────────────────────

def test_url_source_downloads_validates_and_caches_under_the_marketplace_name(locations, monkeypatch):
    seen: dict[str, Any] = {}

    async def fake_get(url, headers, timeout_ms):
        seen.update(url=url, headers=dict(headers), timeout=timeout_ms)
        return mm.HttpResponse(status=200, data=manifest("url-mkt"))

    monkeypatch.setattr(mm, "http_get", fake_get)
    source = {"source": "url", "url": "https://example.com/m.json", "headers": {"User-Agent": "x", "Auth": "t"}}
    result = run(mm.add_marketplace_source(source, locations))
    assert result.name == "url-mkt"
    assert seen["headers"] == {"User-Agent": "TDDAgents-Plugin-Manager", "Auth": "t"}
    assert seen["timeout"] == 10000
    cached = Path(mm.get_marketplaces_cache_dir()) / "url-mkt"
    assert cached.is_file()
    assert known_config()["url-mkt"]["installLocation"] == str(cached)
    mm.clear_marketplaces_cache()
    assert run(mm.get_marketplace("url-mkt"))["name"] == "url-mkt"


def test_url_source_with_invalid_body_leaves_no_temporary_file(locations, monkeypatch):
    async def fake_get(url, headers, timeout_ms):
        return mm.HttpResponse(status=200, data="<html>")

    monkeypatch.setattr(mm, "http_get", fake_get)
    with pytest.raises(ConfigParseError, match="^Invalid marketplace schema from URL: "):
        run(mm.add_marketplace_source({"source": "url", "url": "https://example.com/m.json"}, locations))
    assert os.listdir(mm.get_marketplaces_cache_dir()) == []


def _transport_error(cause: BaseException) -> httpx.ConnectError:
    try:
        raise httpx.ConnectError("connect failed") from cause
    except httpx.ConnectError as exc:
        return exc


@pytest.mark.parametrize(("error", "expected"), [
    (_transport_error(ConnectionRefusedError(111, "refused")),
     "Could not connect to https://***:***@example.com/m.json."),
    (_transport_error(socket.gaierror(-2, "Name or service not known")),
     "Could not connect to https://***:***@example"),
    (_transport_error(TimeoutError(errno.ETIMEDOUT, "timed out")),
     "Request timed out while downloading marketplace from https://***:***@example"),
    (_transport_error(ssl.SSLError("bad cert")),
     "Failed to download marketplace from https://***:***@example.com/m.json: "),
    # axios's own timeout is ECONNABORTED: the generic message.
    (httpx.ReadTimeout("slow"),
     "Failed to download marketplace from https://***:***@example.com/m.json: timeout of 10000ms exceeded"),
    (httpx.HTTPStatusError("x", request=httpx.Request("GET", "https://e"),
                           response=httpx.Response(404, request=httpx.Request("GET", "https://e"))),
     "HTTP 404 error while downloading marketplace from https://***:***@example.com/m.json. The marketplace file"),
    (OSError("boom"), "Failed to download marketplace from https://***:***@example.com/m.json: boom"),
])
def test_url_download_errors_are_explained_with_credentials_redacted(tmp_path, monkeypatch, error, expected):
    async def fake_get(url, headers, timeout_ms):
        raise error

    monkeypatch.setattr(mm, "http_get", fake_get)
    with pytest.raises(RuntimeError) as info:
        run(mm.cache_marketplace_from_url("https://u:secret@example.com/m.json", str(tmp_path / "m.json")))
    assert str(info.value).startswith(expected)
    assert "secret" not in str(info.value)


# ── reading ───────────────────────────────────────────────────────────────────

def test_get_plugin_by_id_and_cache_only(tmp_path, locations):
    root = make_marketplace_dir(tmp_path / "mkt", "m1", [{"name": "alpha", "source": "./alpha"}])
    run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    found = run(mm.get_plugin_by_id("alpha@m1"))
    assert found is not None
    assert found.entry["name"] == "alpha"
    assert found.marketplace_install_location == str(root)
    assert run(mm.get_plugin_by_id_cache_only("alpha@m1")) == found
    assert run(mm.get_plugin_by_id("beta@m1")) is None
    assert run(mm.get_plugin_by_id("alpha@nope")) is None
    assert run(mm.get_plugin_by_id("alpha")) is None
    assert run(mm.get_marketplace_cache_only("m1"))["name"] == "m1"
    assert run(mm.get_marketplace_cache_only("nope")) is None


def test_cache_only_returns_none_when_the_cache_is_broken(tmp_path, locations):
    root = make_marketplace_dir(tmp_path / "mkt", "m1")
    run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    (root / ".tddagents-plugin" / "marketplace.json").write_text("{broken")
    assert run(mm.get_marketplace_cache_only("m1")) is None
    assert run(mm.get_plugin_by_id_cache_only("p1@m1")) is None


def test_get_marketplace_is_memoized_until_cleared(tmp_path, locations):
    root = make_marketplace_dir(tmp_path / "mkt", "m1", [{"name": "old", "source": "./old"}])
    run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    assert run(mm.get_marketplace("m1"))["plugins"][0]["name"] == "old"
    make_marketplace_dir(root, "m1", [{"name": "new", "source": "./new"}])
    assert run(mm.get_marketplace("m1"))["plugins"][0]["name"] == "old"
    mm.clear_marketplaces_cache()
    assert run(mm.get_marketplace("m1"))["plugins"][0]["name"] == "new"


def test_get_marketplace_unknown_name_lists_the_available_ones(tmp_path, locations):
    run(mm.add_marketplace_source({"source": "directory", "path": str(make_marketplace_dir(tmp_path / "a", "aa"))},
                                  locations))
    with pytest.raises(ValueError, match="^Marketplace 'zz' not found in configuration. Available marketplaces: aa$"):
        run(mm.get_marketplace("zz"))


def test_legacy_relative_path_entry_gets_actionable_error():
    write_json(Path(mm.get_known_marketplaces_file()), {"m": {
        "source": {"source": "directory", "path": "./rel"}, "installLocation": "./rel", "lastUpdated": "t"}})
    with pytest.raises(ValueError, match="has a relative source path \\(./rel\\) in known_marketplaces.json"):
        run(mm.get_marketplace("m"))


def test_get_marketplace_refetches_a_missing_cache_and_updates_last_updated(tmp_path, locations):
    root = make_marketplace_dir(tmp_path / "mkt", "m1")
    run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    config = known_config()
    config["m1"]["installLocation"] = str(tmp_path / "gone")
    config["m1"]["lastUpdated"] = "old"
    write_json(Path(mm.get_known_marketplaces_file()), config)
    assert run(mm.get_marketplace("m1"))["name"] == "m1"
    assert known_config()["m1"]["lastUpdated"] != "old"


def test_read_cached_marketplace_falls_back_to_the_location_file(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest("filemkt")))
    assert run(mm.read_cached_marketplace(str(path)))["name"] == "filemkt"
    path.write_text(json.dumps({"name": "x"}))
    with pytest.raises(ConfigParseError, match="^Invalid schema: .*owner"):
        run(mm.read_cached_marketplace(str(path)))


# ── intent layer ──────────────────────────────────────────────────────────────

def test_declared_marketplaces_precedence_and_implicit_official(tmp_path, locations):
    add_dir = tmp_path / "added"
    write_json(add_dir / ".tddagents" / "settings.json", {
        "extraKnownMarketplaces": {"shared": {"source": {"source": "github", "repo": "add/dir"}}},
    })
    write_json(locations.project_root / ".tddagents" / "settings.json", {
        "enabledPlugins": {"tool@claude-plugins-official": True},
        "extraKnownMarketplaces": {"shared": {"source": {"source": "github", "repo": "proj/repo", "junk": 1}}},
    })
    locs = SettingsLocations(project_root=locations.project_root, home=locations.home,
                             managed_dir=locations.managed_dir, additional_directories=(str(add_dir),))
    declared = mm.get_declared_marketplaces(locs)
    assert declared["claude-plugins-official"] == {
        "source": {"source": "github", "repo": "anthropics/claude-plugins-official"}, "sourceIsFallback": True}
    assert declared["shared"]["source"] == {"source": "github", "repo": "proj/repo"}


def test_disabled_official_plugin_declares_nothing(locations):
    write_json(locations.project_root / ".tddagents" / "settings.json",
               {"enabledPlugins": {"tool@claude-plugins-official": False}})
    assert mm.get_declared_marketplaces(locations) == {}


def test_declaring_source_prefers_local_then_project_then_user(locations):
    entry = {"extraKnownMarketplaces": {"m": {"source": {"source": "github", "repo": "a/b"}}}}
    assert mm.get_marketplace_declaring_source("m", locations) is None
    write_json(locations.user_config_dir / "settings.json", entry)
    assert getattr(mm.get_marketplace_declaring_source("m", locations), "value", None) == "userSettings"
    write_json(locations.project_root / ".tddagents" / "settings.json", entry)
    assert getattr(mm.get_marketplace_declaring_source("m", locations), "value", None) == "projectSettings"
    write_json(locations.project_root / ".tddagents" / "settings.local.json", entry)
    assert getattr(mm.get_marketplace_declaring_source("m", locations), "value", None) == "localSettings"


def test_save_marketplace_to_settings_writes_user_settings_by_default(locations):
    mm.save_marketplace_to_settings("m", {"source": {"source": "github", "repo": "a/b"}}, locations)
    written = json.loads((locations.user_config_dir / "settings.json").read_text())
    assert written == {"extraKnownMarketplaces": {"m": {"source": {"source": "github", "repo": "a/b"}}}}


def test_set_auto_update_writes_state_and_the_declaring_source(tmp_path, locations):
    root = make_marketplace_dir(tmp_path / "mkt", "m1")
    run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    write_json(locations.project_root / ".tddagents" / "settings.json",
               {"extraKnownMarketplaces": {"m1": {"source": {"source": "directory", "path": str(root)}}}})
    run(mm.set_marketplace_auto_update("m1", True, locations))
    assert known_config()["m1"]["autoUpdate"] is True
    project = json.loads((locations.project_root / ".tddagents" / "settings.json").read_text())
    assert project["extraKnownMarketplaces"]["m1"] == {"source": {"source": "directory", "path": str(root)},
                                                       "autoUpdate": True}
    assert not (locations.user_config_dir / "settings.json").exists()
    with pytest.raises(ValueError, match="^Marketplace 'zz' not found. Available marketplaces: m1$"):
        run(mm.set_marketplace_auto_update("zz", True, locations))


# ── removal ───────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_cleanup(monkeypatch):
    """The installed-plugins, cache and options cleanup replaced by recorders."""
    from app.utils.plugins import cache_utils
    from app.utils.plugins import installed_plugins_manager as ipm

    calls: dict[str, Any] = {"orphaned": [], "options": []}
    removed = ipm.RemovedPlugins(orphaned_paths=["/cache/m1/p1/1.0"], removed_plugin_ids=["p1@m1"])

    def remove_all(name):
        calls["removed"] = name
        return removed

    async def mark(path):
        calls["orphaned"].append(path)

    monkeypatch.setattr(ipm, "remove_all_plugins_for_marketplace", remove_all)
    monkeypatch.setattr(cache_utils, "mark_plugin_version_orphaned", mark)
    monkeypatch.setattr(mm, "delete_plugin_options", lambda plugin_id, locs: calls["options"].append(plugin_id))
    return calls


def test_remove_cleans_config_caches_settings_and_installed_plugins(tmp_path, locations, fake_cleanup):
    run(mm.add_marketplace_source({"source": "settings", "name": "m1", "plugins": []}, locations))
    stale_json = Path(mm.get_marketplaces_cache_dir()) / "m1.json"
    stale_json.write_text("{}")
    write_json(locations.user_config_dir / "settings.json", {
        "extraKnownMarketplaces": {"m1": {"source": {"source": "settings", "name": "m1", "plugins": []}},
                                   "other": {"source": {"source": "github", "repo": "o/r"}}},
        "enabledPlugins": {"p1@m1": True, "q@other": True},
    })
    data_dir = Path(os.environ["TDDAGENTS_CONFIG_DIR"]) / "plugins" / "data" / "p1-m1"
    data_dir.mkdir(parents=True)
    run(mm.remove_marketplace_source("m1", locations))
    assert run(mm.load_known_marketplaces_config()) == {}
    assert not (Path(mm.get_marketplaces_cache_dir()) / "m1").exists()
    assert not stale_json.exists()
    user = json.loads((locations.user_config_dir / "settings.json").read_text())
    assert user == {"extraKnownMarketplaces": {"other": {"source": {"source": "github", "repo": "o/r"}}},
                    "enabledPlugins": {"q@other": True}}
    assert fake_cleanup["removed"] == "m1"
    assert fake_cleanup["orphaned"] == ["/cache/m1/p1/1.0"]
    assert fake_cleanup["options"] == ["p1@m1"]
    assert not data_dir.exists()


def test_remove_unknown_marketplace_raises(locations):
    with pytest.raises(ValueError, match="^Marketplace 'nope' not found$"):
        run(mm.remove_marketplace_source("nope", locations))


def test_remove_keeps_a_users_local_directory(tmp_path, locations, fake_cleanup):
    root = make_marketplace_dir(tmp_path / "mkt", "m1")
    run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    run(mm.remove_marketplace_source("m1", locations))
    assert (root / ".tddagents-plugin" / "marketplace.json").exists()


# ── seed directories ──────────────────────────────────────────────────────────

def test_seed_marketplaces_register_once_and_are_read_only(tmp_path, locations, monkeypatch):
    seed = tmp_path / "seed"
    make_marketplace_dir(seed / "marketplaces" / "seeded", "seeded")
    write_json(seed / "known_marketplaces.json", {"seeded": {
        "source": {"source": "github", "repo": "corp/seeded"}, "installLocation": "/build/time/path",
        "lastUpdated": "2025-01-01T00:00:00.000Z"},
        "absent": {"source": {"source": "github", "repo": "corp/absent"}, "installLocation": "/x", "lastUpdated": "t"}})
    monkeypatch.setenv("TDDAGENTS_PLUGIN_SEED_DIR", str(seed))
    assert run(mm.register_seed_marketplaces()) is True
    entry = known_config()["seeded"]
    assert entry["installLocation"] == str(seed / "marketplaces" / "seeded")
    assert entry["autoUpdate"] is False
    assert "absent" not in known_config()
    assert run(mm.register_seed_marketplaces()) is False
    assert mm.seed_dir_for(entry["installLocation"]) == str(seed)
    with pytest.raises(ValueError, match="read-only seed directory"):
        run(mm.remove_marketplace_source("seeded", locations))
    with pytest.raises(ValueError, match="auto-update is always disabled for seed content"):
        run(mm.set_marketplace_auto_update("seeded", True, locations))
    with pytest.raises(RuntimeError, match="is seed-managed"):
        run(mm.refresh_marketplace("seeded"))


def test_no_seed_dirs_registers_nothing():
    assert run(mm.register_seed_marketplaces()) is False


# ── refresh ───────────────────────────────────────────────────────────────────

def test_refresh_rejects_a_corrupted_install_location(tmp_path):
    write_json(Path(mm.get_known_marketplaces_file()), {"m": {
        "source": {"source": "github", "repo": "a/b"}, "installLocation": str(tmp_path), "lastUpdated": "t"}})
    with pytest.raises(RuntimeError, match="has a corrupted installLocation"):
        run(mm.refresh_marketplace("m"))


def test_refresh_skips_settings_sources_and_validates_local_ones(tmp_path, locations):
    run(mm.add_marketplace_source({"source": "settings", "name": "inl", "plugins": []}, locations))
    before = known_config()["inl"]["lastUpdated"]
    run(mm.refresh_marketplace("inl"))
    assert known_config()["inl"]["lastUpdated"] == before
    root = make_marketplace_dir(tmp_path / "mkt", "loc")
    run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    progress: list[str] = []
    run(mm.refresh_marketplace("loc", progress.append))
    assert progress == ["Validating local marketplace"]
    (root / ".tddagents-plugin" / "marketplace.json").unlink()
    with pytest.raises(RuntimeError, match="^Failed to refresh marketplace 'loc': "):
        run(mm.refresh_marketplace("loc"))
    with pytest.raises(ValueError, match="^Marketplace 'zz' not found. Available marketplaces: "):
        run(mm.refresh_marketplace("zz"))


def test_refresh_all_continues_past_failures(tmp_path, locations):
    good = make_marketplace_dir(tmp_path / "good", "good")
    run(mm.add_marketplace_source({"source": "directory", "path": str(good)}, locations))
    bad = make_marketplace_dir(tmp_path / "bad", "bad")
    run(mm.add_marketplace_source({"source": "directory", "path": str(bad)}, locations))
    shutil.rmtree(bad)
    config = known_config()
    for name in config:
        config[name]["lastUpdated"] = "old"
    write_json(Path(mm.get_known_marketplaces_file()), config)
    run(mm.refresh_all_marketplaces())
    after = known_config()
    assert after["good"]["lastUpdated"] != "old"
    assert after["bad"]["lastUpdated"] == "old"


# ── git (local repositories only) ─────────────────────────────────────────────

def _git(*args: str, cwd: Path) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@e", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run([GIT or "git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.fixture
def bare_repo(tmp_path) -> tuple[Path, Path]:
    if GIT is None:
        pytest.skip("git is not installed")
    work = tmp_path / "work"
    make_marketplace_dir(work, "git-mkt", [{"name": "first", "source": "./first"}])
    _git("init", "-q", "-b", "main", cwd=work)
    _git("add", ".", cwd=work)
    _git("commit", "-q", "-m", "init", cwd=work)
    bare = tmp_path / "remote.git"
    _git("clone", "-q", "--bare", str(work), str(bare), cwd=tmp_path)
    return work, bare


def test_git_source_clones_renames_and_refreshes(locations, bare_repo):
    work, bare = bare_repo
    url = f"file://{bare}"
    progress: list[str] = []
    result = run(mm.add_marketplace_source({"source": "git", "url": url}, locations, progress.append))
    assert result.name == "git-mkt"
    location = Path(mm.get_marketplaces_cache_dir()) / "git-mkt"
    assert known_config()["git-mkt"]["installLocation"] == str(location)
    assert (location / ".tddagents-plugin" / "marketplace.json").exists()
    assert any(p.startswith("Cloning repository (timeout: 120s): file://") for p in progress)
    assert [n for n in os.listdir(mm.get_marketplaces_cache_dir())] == ["git-mkt"]

    make_marketplace_dir(work, "git-mkt", [{"name": "second", "source": "./second"}])
    _git("commit", "-q", "-am", "update", cwd=work)
    _git("push", "-q", str(bare), "main", cwd=work)
    run(mm.refresh_marketplace("git-mkt"))
    assert run(mm.get_marketplace("git-mkt"))["plugins"][0]["name"] == "second"


def test_git_clone_of_a_missing_repository_fails_and_cleans_up(tmp_path, locations):
    if GIT is None:
        pytest.skip("git is not installed")
    with pytest.raises(RuntimeError, match="^Failed to clone marketplace repository: "):
        run(mm.add_marketplace_source({"source": "git", "url": f"file://{tmp_path}/missing.git"}, locations))
    assert os.listdir(mm.get_marketplaces_cache_dir()) == []


def test_reconcile_sparse_checkout_on_a_missing_directory_asks_for_a_reclone(tmp_path):
    result = run(mm.reconcile_sparse_checkout(str(tmp_path / "absent"), None))
    assert result.code != 0


# ── pure helpers ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("url", "expected"), [
    ("https://user:token@github.com/org/repo", "https://***:***@github.com/org/repo"),
    ("https://:token@github.com/org/repo", "https://:***@github.com/org/repo"),
    ("https://token@github.com/org/repo", "https://***@github.com/org/repo"),
    ("https://github.com/org/repo", "https://github.com/org/repo"),
    ("ssh://git@host/repo", "ssh://git@host/repo"),
    ("owner/repo", "owner/repo"),
])
def test_redact_url_credentials(url, expected):
    assert mm.redact_url_credentials(url) == expected


def test_redact_headers():
    assert mm.redact_headers({"Authorization": "secret"}) == {"Authorization": "***REDACTED***"}


@pytest.mark.parametrize(("source", "expected"), [
    ({"source": "github", "repo": "owner/repo"}, "owner-repo"),
    ({"source": "npm", "package": "@scope/pkg"}, "scope-pkg"),
    ({"source": "file", "path": "/a/b/market.json"}, "market"),
    ({"source": "directory", "path": "/a/b/dir"}, "dir"),
])
def test_cache_path_for_source(source, expected):
    assert mm.get_cache_path_for_source(source) == expected


def test_cache_path_for_other_sources_is_a_timestamp():
    assert mm.get_cache_path_for_source({"source": "url", "url": "x"}).startswith("temp_")


@pytest.mark.parametrize(("value", "expected"), [
    (None, 120000), ("5000", 5000), ("5000ms", 5000), ("0", 120000), ("-3", 120000), ("abc", 120000),
])
def test_git_timeout_env(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("TDDAGENTS_PLUGIN_GIT_TIMEOUT_MS", value)
    assert mm.get_plugin_git_timeout_ms() == expected


@pytest.mark.parametrize(("stderr", "error", "prefix"), [
    ("x", "Command timed out after 1 milliseconds: git", "Git pull timed out after 120s."),
    ("REMOTE HOST IDENTIFICATION HAS CHANGED", None, "SSH host key for this marketplace's git host has changed"),
    ("Host key verification failed", None, "SSH host key verification failed while updating marketplace."),
    ("Permission denied (publickey)", None, "SSH authentication failed while updating marketplace."),
    ("Could not resolve host: x", None, "Network error while updating marketplace."),
])
def test_pull_error_messages(stderr, error, prefix):
    out = mm.enhance_git_pull_error_messages(ExecResult(stdout="", stderr=stderr, code=1, error=error))
    assert out.stderr.startswith(prefix)
    assert out.stderr.endswith(f"Original error: {stderr}")


def test_pull_error_untouched_when_unrecognized_or_successful():
    plain = ExecResult(stdout="", stderr="weird", code=1)
    assert mm.enhance_git_pull_error_messages(plain) is plain
    ok = ExecResult(stdout="", stderr="Host key verification failed", code=0)
    assert mm.enhance_git_pull_error_messages(ok) is ok


def test_ssh_host_and_auth_markers():
    assert mm.extract_ssh_host("git@github.com:o/r.git") == "github.com"
    assert mm.extract_ssh_host("https://github.com/o/r") is None
    assert mm.is_authentication_error("fatal: could not read Username")
    assert not mm.is_authentication_error("fatal: repository not found")


def test_git_clone_failure_messages(monkeypatch):
    async def fake_exec(file, args, **kwargs):
        return ExecResult(stdout="", stderr=stderr_value, code=128, error=None)

    monkeypatch.setattr(mm, "exec_file_no_throw_with_cwd", fake_exec)
    stderr_value = "Host key verification failed"
    out = run(mm.git_clone("git@corp.example:o/r.git", "/tmp/x"))
    assert "ssh -T git@corp.example" in out.stderr
    stderr_value = "remote: HTTP 403"
    assert run(mm.git_clone("https://h/o/r", "/tmp/x")).stderr.startswith("HTTPS authentication failed.")
    stderr_value = ""
    assert run(mm.git_clone("https://h/o/r", "/tmp/x")).stderr == (
        "git clone exited with code 128 (no stderr output). Run with --debug to see the full command.")


def test_git_clone_redacts_credentials_from_the_result(monkeypatch):
    async def fake_exec(file, args, **kwargs):
        assert "https://u:secret@h/o/r" in args
        return ExecResult(stdout="", stderr="fatal: https://u:secret@h/o/r not found", code=128,
                          error="Command failed: git clone https://u:secret@h/o/r")

    monkeypatch.setattr(mm, "exec_file_no_throw_with_cwd", fake_exec)
    out = run(mm.git_clone("https://u:secret@h/o/r", "/tmp/x"))
    assert "secret" not in out.stderr
    assert "https://***:***@h/o/r" in out.stderr


def test_safe_call_progress_swallows_callback_errors():
    def boom(_: str) -> None:
        raise RuntimeError("callback")

    mm.safe_call_progress(boom, "msg")
    mm.safe_call_progress(None, "msg")
