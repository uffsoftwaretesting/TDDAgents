"""Tests for the load half of app/utils/plugins/plugin_loader.py (offline: directory marketplaces)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.bootstrap.state import set_inline_plugins
from app.plugins import builtin_plugins
from app.types.plugin import BuiltinPluginDefinition, LoadedPlugin
from app.utils.plugins import installed_plugins_manager as ipm
from app.utils.plugins import marketplace_manager as mm
from app.utils.plugins import plugin_loader as pl
from app.utils.plugins.zip_cache import reset_session_plugin_cache
from app.utils.settings.settings import SettingsLocations, load_settings
from app.utils.settings.settings_cache import clear_plugin_settings_base, get_plugin_settings_base


@pytest.fixture(autouse=True)
def _fresh_state():
    def reset() -> None:
        mm.clear_marketplaces_cache()
        ipm.clear_installed_plugins_cache()
        ipm.reset_migration_state()
        pl.clear_plugin_cache()
        set_inline_plugins([])
        builtin_plugins.clear_builtin_plugins()
        clear_plugin_settings_base()
        reset_session_plugin_cache()

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


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def enable(locations: SettingsLocations, plugins: dict[str, Any], **extra: Any) -> None:
    write_json(locations.user_config_dir / "settings.json", {"enabledPlugins": plugins, **extra})


def make_marketplace(root: Path, name: str, entries: list[dict[str, Any]], locations: SettingsLocations) -> Path:
    write_json(root / ".tddagents-plugin" / "marketplace.json", {"name": name, "owner": {"name": "O"},
                                                                 "plugins": entries})
    asyncio.run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    mm.clear_marketplaces_cache()
    return root


def make_plugin(root: Path, manifest: dict[str, Any] | None = None) -> Path:
    if manifest is not None:
        write_json(root / ".tddagents-plugin" / "plugin.json", manifest)
    else:
        root.mkdir(parents=True, exist_ok=True)
    return root


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ── createPluginFromPath ──────────────────────────────────────────────────────

def test_create_plugin_detects_component_directories(tmp_path: Path):
    plugin_dir = make_plugin(tmp_path / "p", {"name": "demo", "version": "1.0.0"})
    for sub in ("commands", "agents", "skills", "output-styles"):
        (plugin_dir / sub).mkdir()
    created = run(pl.create_plugin_from_path(str(plugin_dir), "demo@m", True, "fallback"))
    plugin = created.plugin
    assert created.errors == []
    assert (plugin.name, plugin.source, plugin.repository, plugin.enabled) == ("demo", "demo@m", "demo@m", True)
    assert plugin.commands_path == str(plugin_dir / "commands")
    assert plugin.agents_path == str(plugin_dir / "agents")
    assert plugin.skills_path == str(plugin_dir / "skills")
    assert plugin.output_styles_path == str(plugin_dir / "output-styles")
    assert plugin.hooks_config is None and plugin.settings is None


def test_create_plugin_without_manifest_uses_the_fallback_name(tmp_path: Path):
    plugin_dir = make_plugin(tmp_path / "bare")
    created = run(pl.create_plugin_from_path(str(plugin_dir), "src", False, "bare-name"))
    assert created.plugin.manifest == {"name": "bare-name", "description": "Plugin from src"}
    assert created.plugin.commands_path is None


def test_manifest_paths_replace_directory_detection_and_report_missing_files(tmp_path: Path):
    plugin_dir = make_plugin(tmp_path / "p", {
        "name": "demo",
        "commands": ["./cmd/a.md", "./cmd/missing.md"],
        "agents": "./ag/x.md",
        "skills": ["./sk", "./nope"],
        "outputStyles": "./styles",
    })
    write(plugin_dir / "cmd" / "a.md")
    write(plugin_dir / "ag" / "x.md")
    (plugin_dir / "sk").mkdir()
    (plugin_dir / "styles").mkdir()
    (plugin_dir / "commands").mkdir()  # not auto-detected: the manifest names commands
    created = run(pl.create_plugin_from_path(str(plugin_dir), "demo@m", True, "demo"))
    plugin = created.plugin
    assert plugin.commands_path is None
    assert plugin.commands_paths == [str(plugin_dir / "cmd" / "a.md")]
    assert plugin.agents_paths == [str(plugin_dir / "ag" / "x.md")]
    assert plugin.skills_paths == [str(plugin_dir / "sk")]
    assert plugin.output_styles_paths == [str(plugin_dir / "styles")]
    assert created.errors == [
        {"type": "path-not-found", "source": "demo@m", "plugin": "demo",
         "path": str(plugin_dir / "cmd" / "missing.md"), "component": "commands"},
        {"type": "path-not-found", "source": "demo@m", "plugin": "demo",
         "path": str(plugin_dir / "nope"), "component": "skills"},
    ]


def test_command_mapping_keeps_inline_content_and_existing_sources(tmp_path: Path):
    plugin_dir = make_plugin(tmp_path / "p", {"name": "demo", "commands": {
        "about": {"source": "./README.md", "description": "About"},
        "hello": {"content": "Say hello"},
        "gone": {"source": "./gone.md"},
    }})
    write(plugin_dir / "README.md")
    created = run(pl.create_plugin_from_path(str(plugin_dir), "demo@m", True, "demo"))
    assert created.plugin.commands_paths == [str(plugin_dir / "README.md")]
    assert set(created.plugin.commands_metadata or {}) == {"about", "hello"}
    assert [e["type"] for e in created.errors] == ["path-not-found"]


def test_hooks_merge_standard_file_manifest_files_and_inline_objects(tmp_path: Path):
    hook = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
    other = {"matcher": "Write", "hooks": [{"type": "command", "command": "echo w"}]}
    plugin_dir = make_plugin(tmp_path / "p", {"name": "demo", "hooks": [
        "./extra/hooks.json", "./hooks/hooks.json", {"PostToolUse": [other]}, "./missing.json",
    ]})
    write_json(plugin_dir / "hooks" / "hooks.json", {"hooks": {"PreToolUse": [hook]}})
    write_json(plugin_dir / "extra" / "hooks.json", {"description": "d", "hooks": {"PreToolUse": [other]}})
    created = run(pl.create_plugin_from_path(str(plugin_dir), "demo@m", True, "demo"))
    assert created.plugin.hooks_config == {"PreToolUse": [hook, other], "PostToolUse": [other]}
    kinds = [(e["type"], e.get("hookPath") or e.get("path")) for e in created.errors]
    assert kinds == [
        ("hook-load-failed", str(plugin_dir / "hooks" / "hooks.json")),
        ("path-not-found", str(plugin_dir / "missing.json")),
    ]
    assert "Duplicate hooks file detected: ./hooks/hooks.json" in created.errors[0]["reason"]
    lenient = run(pl.create_plugin_from_path(str(plugin_dir), "demo@m", True, "demo", strict=False))
    assert [e["type"] for e in lenient.errors] == ["path-not-found"]


def test_a_broken_hooks_file_is_reported_not_raised(tmp_path: Path):
    plugin_dir = make_plugin(tmp_path / "p", {"name": "demo"})
    write(plugin_dir / "hooks" / "hooks.json", "{not json")
    created = run(pl.create_plugin_from_path(str(plugin_dir), "demo@m", True, "demo"))
    assert created.plugin.hooks_config is None
    assert created.errors[0]["type"] == "hook-load-failed"


def test_plugin_settings_keep_only_the_agent_key(tmp_path: Path):
    plugin_dir = make_plugin(tmp_path / "p", {"name": "demo", "settings": {"agent": "from-manifest"}})
    created = run(pl.create_plugin_from_path(str(plugin_dir), "demo@m", True, "demo"))
    assert created.plugin.settings == {"agent": "from-manifest"}
    write_json(plugin_dir / "settings.json", {"agent": "from-file", "permissions": {"allow": ["Bash"]}})
    created = run(pl.create_plugin_from_path(str(plugin_dir), "demo@m", True, "demo"))
    assert created.plugin.settings == {"agent": "from-file"}
    write_json(plugin_dir / "settings.json", {"agent": 3})
    created = run(pl.create_plugin_from_path(str(plugin_dir), "demo@m", True, "demo"))
    assert created.plugin.settings == {"agent": "from-manifest"}
    write(plugin_dir / "settings.json", "{broken")
    created = run(pl.create_plugin_from_path(str(plugin_dir), "demo@m", True, "demo"))
    assert created.plugin.settings == {"agent": "from-manifest"}


def test_merge_hooks_settings_concatenates_per_event():
    assert pl.merge_hooks_settings(None, {"A": [1]}) == {"A": [1]}
    assert pl.merge_hooks_settings({"A": [1], "B": [2]}, {"A": [3], "C": [4]}) == {"A": [1, 3], "B": [2], "C": [4]}


# ── mergePluginSources ────────────────────────────────────────────────────────

def _plugin(name: str, source: str) -> LoadedPlugin:
    return LoadedPlugin(name=name, manifest={"name": name}, path="/x", source=source, repository=source, enabled=True)


def test_session_plugins_override_marketplace_ones_unless_managed():
    session = [_plugin("a", "a@inline"), _plugin("locked", "locked@inline")]
    marketplace = [_plugin("a", "a@m"), _plugin("b", "b@m"), _plugin("locked", "locked@m")]
    builtin = [_plugin("c", "c@builtin")]
    plugins, errors = pl.merge_plugin_sources(session, marketplace, builtin, {"locked"})
    assert [p.source for p in plugins] == ["a@inline", "b@m", "locked@m", "c@builtin"]
    assert errors == [{
        "type": "generic-error", "source": "locked@inline", "plugin": "locked",
        "error": '--plugin-dir copy of "locked" ignored: plugin is locked by managed settings',
    }]
    plugins, errors = pl.merge_plugin_sources(session, marketplace, builtin)
    assert [p.source for p in plugins] == ["a@inline", "locked@inline", "b@m", "c@builtin"] and errors == []


# ── loadAllPlugins over a directory marketplace ───────────────────────────────

ENTRIES: list[dict[str, Any]] = [
    {"name": "alpha", "source": "./alpha", "version": "1.0.0"},
    {"name": "beta", "source": "./beta"},
    {"name": "needy", "source": "./needy", "dependencies": ["ghost"]},
]


@pytest.fixture
def team(tmp_path: Path, locations: SettingsLocations) -> Path:
    root = tmp_path / "mkt"
    make_plugin(root / "alpha", {"name": "alpha", "version": "1.0.0", "settings": {"agent": "alpha-agent"}})
    (root / "alpha" / "commands").mkdir()
    make_plugin(root / "beta", {"name": "beta"})
    make_plugin(root / "needy", {"name": "needy", "dependencies": ["ghost"]})
    return make_marketplace(root, "team", ENTRIES, locations)


def test_load_all_plugins_splits_enabled_and_disabled_and_reports_unknown_plugins(team, locations):
    enable(locations, {"alpha@team": True, "beta@team": False, "ghost@team": True, "not-an-id": True})
    result = run(pl.load_all_plugins(locations))
    assert [p.source for p in result.enabled] == ["alpha@team"]
    assert [p.source for p in result.disabled] == ["beta@team"]
    alpha = result.enabled[0]
    assert alpha.path == pl.get_versioned_cache_path("alpha@team", "1.0.0")
    assert alpha.commands_path == os.path.join(alpha.path, "commands")
    assert result.errors == [{"type": "plugin-not-found", "source": "ghost@team", "pluginId": "ghost",
                              "marketplace": "team"}]
    # The enabled plugins' settings are the settings cascade's base layer.
    assert get_plugin_settings_base() == {"agent": "alpha-agent"}
    assert load_settings(locations)["agent"] == "alpha-agent"


def test_file_settings_override_the_plugin_settings_base(team, locations):
    enable(locations, {"alpha@team": True}, agent="user-agent")
    run(pl.load_all_plugins(locations))
    assert get_plugin_settings_base() == {"agent": "alpha-agent"}
    assert load_settings(locations)["agent"] == "user-agent"


def test_clear_all_plugin_caches_clears_the_loader_memo(team, locations):
    from app.utils.plugins.cache_utils import clear_all_plugin_caches

    enable(locations, {"alpha@team": True})
    first = run(pl.load_all_plugins(locations))
    clear_all_plugin_caches()
    assert run(pl.load_all_plugins(locations)) is not first


def test_load_all_plugins_is_memoized_until_cleared_and_warms_the_cache_only_memo(team, locations):
    enable(locations, {"alpha@team": True})
    first = run(pl.load_all_plugins(locations))
    assert run(pl.load_all_plugins(locations)) is first
    assert run(pl.load_all_plugins_cache_only(locations)) is first
    pl.clear_plugin_cache("test")
    assert get_plugin_settings_base() is None
    assert run(pl.load_all_plugins(locations)) is not first


def test_a_missing_dependency_demotes_the_plugin(team, locations):
    enable(locations, {"needy@team": True})
    result = run(pl.load_all_plugins(locations))
    assert result.enabled == [] and [p.source for p in result.disabled] == ["needy@team"]
    assert [e["type"] for e in result.errors] == ["dependency-unsatisfied"]


def test_policy_blocks_a_marketplace_and_fails_closed_on_unknown_ones(team, locations):
    enable(locations, {"alpha@team": True, "x@unregistered": True})
    write_json(locations.managed_dir / "managed-settings.json",
               {"blockedMarketplaces": [{"source": "directory", "path": str(team)}]})
    result = run(pl.load_all_plugins(locations))
    assert result.enabled == []
    by_source = {e["source"]: e for e in result.errors}
    assert by_source["alpha@team"] == {
        "type": "marketplace-blocked-by-policy", "source": "alpha@team", "plugin": "alpha", "marketplace": "team",
        "blockedByBlocklist": True, "allowedSources": [],
    }
    assert by_source["x@unregistered"]["blockedByBlocklist"] is True


def test_an_allowlist_without_the_marketplace_lists_the_allowed_sources(team, locations):
    enable(locations, {"alpha@team": True})
    write_json(locations.managed_dir / "managed-settings.json",
               {"strictKnownMarketplaces": [{"source": "github", "repo": "acme/plugins"}]})
    result = run(pl.load_all_plugins(locations))
    assert result.errors[0]["type"] == "marketplace-blocked-by-policy"
    assert result.errors[0]["blockedByBlocklist"] is False
    assert result.errors[0]["allowedSources"] == ["github:acme/plugins"]


def test_cache_only_reads_local_entries_in_place_and_misses_uncached_external_ones(tmp_path, locations):
    root = tmp_path / "mkt2"
    make_plugin(root / "local", {"name": "local"})
    entries: list[dict[str, Any]] = [{"name": "local", "source": "./local"},
                                     {"name": "remote", "source": {"source": "github", "repo": "o/r"}}]
    make_marketplace(root, "mix", entries, locations)
    enable(locations, {"local@mix": True, "remote@mix": True})
    result = run(pl.load_all_plugins_cache_only(locations))
    assert [p.path for p in result.enabled] == [str(root / "local")]
    assert result.errors == [{"type": "plugin-cache-miss", "source": "remote@mix", "plugin": "remote",
                              "installPath": "(not recorded)"}]
    assert not os.path.exists(pl.get_plugin_cache_path())


def test_the_full_loader_uses_an_external_plugins_versioned_cache(tmp_path, locations):
    root = tmp_path / "mkt3"
    entries = [{"name": "remote", "source": {"source": "github", "repo": "o/r"}, "version": "2.0.0"}]
    make_marketplace(root, "ext", entries, locations)
    cached = make_plugin(Path(pl.get_versioned_cache_path("remote@ext", "2.0.0")), {"name": "remote"})
    enable(locations, {"remote@ext": True})
    result = run(pl.load_all_plugins(locations))
    assert [p.path for p in result.enabled] == [str(cached)] and result.errors == []


def test_a_failed_external_download_is_a_generic_error(tmp_path, locations, monkeypatch):
    root = tmp_path / "mkt4"
    make_marketplace(root, "ext", [{"name": "remote", "source": {"source": "github", "repo": "o/r"}}], locations)

    async def no_network(source: Any, **_: Any) -> Any:
        raise RuntimeError("offline")

    monkeypatch.setattr(pl, "cache_plugin", no_network)
    enable(locations, {"remote@ext": True})
    result = run(pl.load_all_plugins(locations))
    assert result.errors == [{"type": "generic-error", "source": "remote@ext",
                              "error": "Failed to download/cache plugin remote: offline"}]


# ── finishLoadingPluginFromPath: the marketplace entry as manifest or supplement ──

def test_an_entry_without_plugin_json_is_the_manifest(tmp_path, locations):
    root = tmp_path / "mkt5"
    write(root / "bare" / "docs" / "cmd.md")
    write(root / "bare" / "a.md")
    hooks = {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}
    entries = [{"name": "bare", "source": "./bare", "description": "From the entry", "category": "x",
                "commands": "./docs/cmd.md", "agents": ["./a.md", "./b.md"], "skills": "./sk", "hooks": hooks}]
    make_marketplace(root, "m5", entries, locations)
    enable(locations, {"bare@m5": True})
    result = run(pl.load_all_plugins_cache_only(locations))
    plugin = result.enabled[0]
    assert plugin.manifest["description"] == "From the entry"
    assert "source" not in plugin.manifest and "strict" not in plugin.manifest
    assert plugin.commands_paths == [str(root / "bare" / "docs" / "cmd.md")]
    assert plugin.agents_paths == [str(root / "bare" / "a.md")]
    assert plugin.skills_paths is None
    assert plugin.hooks_config == hooks
    assert sorted((e["type"], e.get("component")) for e in result.errors) == [
        ("path-not-found", "agents"), ("path-not-found", "skills")]


def test_a_non_strict_entry_conflicting_with_plugin_json_drops_the_plugin(tmp_path, locations):
    root = tmp_path / "mkt6"
    make_plugin(root / "p", {"name": "p"})
    write(root / "p" / "extra.md")
    make_marketplace(root, "m6", [{"name": "p", "source": "./p", "strict": False, "agents": "./extra.md"}],
                     locations)
    enable(locations, {"p@m6": True})
    result = run(pl.load_all_plugins_cache_only(locations))
    assert result.enabled == []
    assert result.errors[0]["error"].startswith("Plugin p has conflicting manifests")


def test_a_strict_entry_supplements_plugin_json(tmp_path, locations):
    root = tmp_path / "mkt7"
    make_plugin(root / "p", {"name": "p", "agents": "./one.md"})
    write(root / "p" / "one.md")
    write(root / "p" / "two.md")
    make_marketplace(root, "m7", [{"name": "p", "source": "./p", "agents": "./two.md",
                                   "commands": {"x": {"content": "inline"}}}], locations)
    enable(locations, {"p@m7": True})
    plugin = run(pl.load_all_plugins_cache_only(locations)).enabled[0]
    assert plugin.agents_paths == [str(root / "p" / "one.md"), str(root / "p" / "two.md")]
    assert plugin.commands_metadata is None  # inline-only mappings add nothing on supplement, as upstream


# ── --plugin-dir and built-ins ────────────────────────────────────────────────

def test_session_plugins_load_from_plugin_dirs_and_override_installed(team, tmp_path, locations):
    dev = make_plugin(tmp_path / "dev-alpha", {"name": "alpha", "version": "9.9.9"})
    set_inline_plugins([str(dev), str(tmp_path / "nowhere")])
    enable(locations, {"alpha@team": True})
    result = run(pl.load_all_plugins(locations))
    assert [(p.source, p.path) for p in result.enabled] == [("alpha@inline", str(dev))]
    assert result.errors == [{"type": "path-not-found", "source": "inline[1]", "path": str(tmp_path / "nowhere"),
                              "component": "commands"}]


def test_a_broken_session_plugin_is_a_generic_error(tmp_path, locations):
    bad = tmp_path / "bad"
    write(bad / ".tddagents-plugin" / "plugin.json", "{oops")
    set_inline_plugins([str(bad)])
    result = run(pl.load_all_plugins_cache_only(locations))
    assert result.enabled == []
    assert result.errors[0]["source"] == "inline[0]"
    assert result.errors[0]["error"].startswith("Failed to load plugin: Plugin bad has a corrupt manifest")


def test_builtin_plugins_follow_the_user_choice_then_their_default(locations):
    builtin_plugins.register_builtin_plugin(BuiltinPluginDefinition(name="on", description="d"))
    builtin_plugins.register_builtin_plugin(BuiltinPluginDefinition(name="off", description="d",
                                                                    default_enabled=False, version="1"))
    builtin_plugins.register_builtin_plugin(BuiltinPluginDefinition(name="hidden", description="d",
                                                                    is_available=lambda: False))
    enable(locations, {"on@builtin": False})
    result = run(pl.load_all_plugins_cache_only(locations))
    assert result.enabled == []
    assert [(p.source, p.is_builtin, p.path) for p in result.disabled] == [
        ("on@builtin", True, "builtin"), ("off@builtin", True, "builtin")]
    assert result.disabled[1].manifest == {"name": "off", "description": "d", "version": "1"}
    assert builtin_plugins.is_builtin_plugin_id("on@builtin") and not builtin_plugins.is_builtin_plugin_id("on@x")


# ── zip cache mode ────────────────────────────────────────────────────────────

def test_zip_cache_mode_caches_a_zip_and_loads_the_extracted_copy(team, locations, monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_PLUGIN_USE_ZIP_CACHE", "1")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", None)
    enable(locations, {"alpha@team": True})
    result = run(pl.load_all_plugins(locations))
    plugin = result.enabled[0]
    assert os.path.exists(pl.get_versioned_zip_cache_path("alpha@team", "1.0.0"))
    assert plugin.path.startswith(str(tmp_path / "tmp")) and plugin.path.endswith("alpha@team")
    assert (Path(plugin.path) / ".tddagents-plugin" / "plugin.json").exists()
