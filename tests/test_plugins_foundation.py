"""Tests for the small plugin foundation modules (identifier, directories, policy, versioning, ...)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.types.permissions import PermissionRuleSource
from app.types.plugin import LoadedPlugin, get_plugin_error_message
from app.utils.plugins import git_availability, plugin_flagging
from app.utils.plugins.add_dir_plugin_settings import (
    get_add_dir_enabled_plugins,
    get_add_dir_extra_marketplaces,
)
from app.utils.plugins.dependency_resolver import (
    DependencyLookupResult,
    find_reverse_dependents,
    format_dependency_count_suffix,
    format_reverse_dependents_suffix,
    get_enabled_plugin_ids_for_scope,
    qualify_dependency,
    resolve_dependency_closure,
    verify_and_demote,
)
from app.utils.plugins.managed_plugins import get_managed_plugin_names
from app.utils.plugins.official_marketplace import (
    OFFICIAL_MARKETPLACE_NAME,
    OFFICIAL_MARKETPLACE_SOURCE,
)
from app.utils.plugins.parse_marketplace_input import parse_marketplace_input
from app.utils.plugins.plugin_directories import (
    PLUGIN_PACKAGE_DIR,
    delete_plugin_data_dir,
    get_plugin_data_dir,
    get_plugin_data_dir_size,
    get_plugin_seed_dirs,
    get_plugins_directory,
    plugin_data_dir_path,
    set_use_cowork_plugins,
)
from app.utils.plugins.plugin_identifier import (
    build_plugin_id,
    is_official_marketplace_name,
    parse_plugin_identifier,
    scope_to_setting_source,
    setting_source_to_scope,
)
from app.utils.plugins.plugin_policy import is_plugin_blocked_by_policy
from app.utils.plugins.plugin_versioning import (
    calculate_plugin_version,
    get_version_from_path,
    is_versioned_path,
)
from app.utils.plugins.schemas import (
    MarketplaceSourceSchema,
    is_blocked_official_name,
    safe_parse,
)
from app.utils.plugins.walk_plugin_markdown import walk_plugin_markdown
from app.utils.settings.settings import SettingsLocations

SHA = "0123456789abcdef0123456789abcdef01234567"


def _locations(tmp_path: Path, *, policy: dict | None = None, user: dict | None = None,
               add_dirs: tuple[str, ...] = ()) -> SettingsLocations:
    managed = tmp_path / "managed"
    managed.mkdir(parents=True, exist_ok=True)
    if policy is not None:
        (managed / "managed-settings.json").write_text(json.dumps(policy))
    home = tmp_path / "home"
    if user is not None:
        (home / ".tddagents").mkdir(parents=True, exist_ok=True)
        (home / ".tddagents" / "settings.json").write_text(json.dumps(user))
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    return SettingsLocations(project_root=project, home=home, managed_dir=managed, additional_directories=add_dirs)


# ── identifiers ───────────────────────────────────────────────────────────────

def test_parse_plugin_identifier_uses_the_first_two_parts():
    assert parse_plugin_identifier("fmt") == parse_plugin_identifier("fmt")
    assert parse_plugin_identifier("fmt").marketplace is None
    parsed = parse_plugin_identifier("fmt@tools@extra")
    assert (parsed.name, parsed.marketplace) == ("fmt", "tools")
    assert parse_plugin_identifier("@tools").name == ""


def test_build_plugin_id_and_official_names():
    assert build_plugin_id("a", "b") == "a@b"
    assert build_plugin_id("a") == "a"
    assert is_official_marketplace_name("Claude-Plugins-Official")
    assert not is_official_marketplace_name("mine")
    assert not is_official_marketplace_name(None)


def test_scope_and_source_mapping():
    assert scope_to_setting_source("project") == PermissionRuleSource.PROJECT_SETTINGS
    assert setting_source_to_scope(PermissionRuleSource.LOCAL_SETTINGS) == "local"
    with pytest.raises(ValueError, match="Cannot install plugins to managed scope"):
        scope_to_setting_source("managed")
    with pytest.raises(ValueError):
        setting_source_to_scope(PermissionRuleSource.POLICY_SETTINGS)


def test_official_marketplace_constants_are_consistent():
    assert OFFICIAL_MARKETPLACE_SOURCE == {"source": "github", "repo": "anthropics/claude-plugins-official"}
    assert not is_blocked_official_name(OFFICIAL_MARKETPLACE_NAME)
    assert safe_parse(MarketplaceSourceSchema, OFFICIAL_MARKETPLACE_SOURCE).success


# ── directories ───────────────────────────────────────────────────────────────

def test_plugins_directory_follows_the_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("TDDAGENTS_PLUGIN_CACHE_DIR", raising=False)
    monkeypatch.delenv("TDDAGENTS_USE_COWORK_PLUGINS", raising=False)
    assert get_plugins_directory() == str(tmp_path / "plugins")
    monkeypatch.setenv("TDDAGENTS_USE_COWORK_PLUGINS", "1")
    assert get_plugins_directory() == str(tmp_path / "cowork_plugins")
    monkeypatch.delenv("TDDAGENTS_USE_COWORK_PLUGINS")
    set_use_cowork_plugins(True)
    try:
        assert get_plugins_directory() == str(tmp_path / "cowork_plugins")
    finally:
        set_use_cowork_plugins(False)
    monkeypatch.setenv("TDDAGENTS_PLUGIN_CACHE_DIR", "~/elsewhere")
    assert get_plugins_directory() == os.path.expanduser("~") + "/elsewhere"


def test_seed_dirs_split_on_the_path_delimiter(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_PLUGIN_SEED_DIR", f"/a{os.pathsep}{os.pathsep}~/b")
    assert get_plugin_seed_dirs() == ["/a", os.path.expanduser("~") + "/b"]
    monkeypatch.delenv("TDDAGENTS_PLUGIN_SEED_DIR")
    assert get_plugin_seed_dirs() == []


def test_plugin_data_dir_lifecycle():
    path = plugin_data_dir_path("my plugin@mkt.v2")
    assert path.endswith(os.path.join("data", "my-plugin-mkt-v2"))
    assert not os.path.exists(path)
    assert asyncio.run(get_plugin_data_dir_size("my plugin@mkt.v2")) is None
    created = get_plugin_data_dir("my plugin@mkt.v2")
    assert os.path.isdir(created)
    assert asyncio.run(get_plugin_data_dir_size("my plugin@mkt.v2")) is None  # empty
    os.makedirs(os.path.join(created, "sub"))
    Path(created, "sub", "f").write_bytes(b"x" * 2048)
    size = asyncio.run(get_plugin_data_dir_size("my plugin@mkt.v2"))
    assert size is not None and size.bytes == 2048 and size.human == "2KB"
    asyncio.run(delete_plugin_data_dir("my plugin@mkt.v2"))
    assert not os.path.exists(created)
    asyncio.run(delete_plugin_data_dir("my plugin@mkt.v2"))  # absent: no error


# ── policy ────────────────────────────────────────────────────────────────────

def test_policy_blocking_and_managed_names(tmp_path):
    locations = _locations(tmp_path, policy={"enabledPlugins": {"bad@m": False, "good@m": True,
                                                                "legacy": True, "x@m": ["^1"]}})
    assert is_plugin_blocked_by_policy("bad@m", locations)
    assert not is_plugin_blocked_by_policy("good@m", locations)
    assert not is_plugin_blocked_by_policy("other@m", locations)
    assert get_managed_plugin_names(locations) == {"bad", "good"}


def test_no_policy_means_no_managed_names(tmp_path):
    assert get_managed_plugin_names(_locations(tmp_path)) is None
    assert get_managed_plugin_names(_locations(tmp_path / "x", policy={"enabledPlugins": {"legacy": True}})) is None


# ── add-dir settings ──────────────────────────────────────────────────────────

def test_add_dir_settings_merge_in_order(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    for d in (first, second):
        (d / ".tddagents").mkdir(parents=True)
    (first / ".tddagents" / "settings.json").write_text(json.dumps({"enabledPlugins": {"a@m": True, "b@m": True}}))
    (first / ".tddagents" / "settings.local.json").write_text(json.dumps({"enabledPlugins": {"a@m": False}}))
    (second / ".tddagents" / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"b@m": False},
        "extraKnownMarketplaces": {"team": {"source": {"source": "github", "repo": "o/r"}}}}))
    locations = _locations(tmp_path, add_dirs=(str(first), str(second)))
    assert get_add_dir_enabled_plugins(locations) == {"a@m": False, "b@m": False}
    assert get_add_dir_extra_marketplaces(locations) == {"team": {"source": {"source": "github", "repo": "o/r"}}}
    assert get_add_dir_enabled_plugins(_locations(tmp_path / "none")) == {}


# ── git availability ──────────────────────────────────────────────────────────

def test_git_availability_is_memoized_and_can_be_pinned_off(monkeypatch):
    git_availability.clear_git_availability_cache()
    calls: list[str] = []

    def which(command: str) -> str:
        calls.append(command)
        return "/usr/bin/" + command

    monkeypatch.setattr("app.utils.plugins.git_availability.shutil.which", which)
    assert asyncio.run(git_availability.check_git_available())
    assert asyncio.run(git_availability.check_git_available())
    assert calls == ["git"]
    git_availability.mark_git_unavailable()
    assert not asyncio.run(git_availability.check_git_available())
    git_availability.clear_git_availability_cache()
    monkeypatch.setattr("app.utils.plugins.git_availability.shutil.which", lambda c: None)
    assert not asyncio.run(git_availability.check_git_available())
    git_availability.clear_git_availability_cache()


# ── markdown walk ─────────────────────────────────────────────────────────────

def test_walk_reports_markdown_with_namespaces_and_stops_at_skill_dirs(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "top.md").write_text("x")
    (tmp_path / "skip.txt").write_text("x")
    (tmp_path / "a" / "b" / "deep.MD").write_text("x")
    skill = tmp_path / "skill"
    (skill / "nested").mkdir(parents=True)
    (skill / "SKILL.md").write_text("x")
    (skill / "ref.md").write_text("x")
    (skill / "nested" / "hidden.md").write_text("x")
    seen: list[tuple[str, tuple[str, ...]]] = []

    async def on_file(path: str, namespace: list[str]) -> None:
        seen.append((os.path.relpath(path, tmp_path), tuple(namespace)))

    asyncio.run(walk_plugin_markdown(str(tmp_path), on_file, stop_at_skill_dir=True))
    assert sorted(seen) == [("a/b/deep.MD", ("a", "b")), ("skill/SKILL.md", ("skill",)),
                            ("skill/ref.md", ("skill",)), ("top.md", ())]
    seen.clear()
    asyncio.run(walk_plugin_markdown(str(tmp_path), on_file))
    assert ("skill/nested/hidden.md", ("skill", "nested")) in seen


def test_walk_of_a_missing_directory_logs_and_returns(tmp_path, caplog):
    async def on_file(path: str, namespace: list[str]) -> None:
        raise AssertionError("no files")

    asyncio.run(walk_plugin_markdown(str(tmp_path / "missing"), on_file, log_label="agents"))
    assert "Failed to scan agents directory" in caplog.text


# ── versioning ────────────────────────────────────────────────────────────────

def test_version_preference_order(tmp_path):
    source = {"source": "github", "repo": "o/r"}
    run = asyncio.run
    assert run(calculate_plugin_version("p@m", source, {"name": "p", "version": "1.2.3"}, None, "9")) == "1.2.3"
    assert run(calculate_plugin_version("p@m", source, {"name": "p"}, None, "9.9")) == "9.9"
    assert run(calculate_plugin_version("p@m", source, None, None, None, SHA)) == SHA[:12]
    assert run(calculate_plugin_version("p@m", source, None, str(tmp_path))) == "unknown"


def test_git_subdir_version_hashes_the_normalized_path():
    source = {"source": "git-subdir", "url": "o/r", "path": ".\\tools\\plugin//"}
    expected = hashlib.sha256(b"tools/plugin").hexdigest()[:8]
    assert asyncio.run(calculate_plugin_version("p@m", source, git_commit_sha=SHA)) == f"{SHA[:12]}-{expected}"


def test_version_from_the_install_paths_git_head(tmp_path):
    git_dir = tmp_path / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    (git_dir / "refs" / "heads" / "main").write_text(SHA + "\n")
    assert asyncio.run(calculate_plugin_version("p@m", "./p", install_path=str(tmp_path))) == SHA[:12]


def test_version_from_versioned_cache_paths():
    assert get_version_from_path("/h/.tddagents/plugins/cache/mkt/plug/1.0.0") == "1.0.0"
    assert get_version_from_path("/h/.tddagents/plugins/cache/mkt/plug") is None
    assert get_version_from_path("/h/other/cache/mkt/plug/1.0.0") is None
    assert is_versioned_path("/x/plugins/cache/a/b/c/d")
    assert not is_versioned_path("/x/plugins/a/b/c")


# ── dependencies ──────────────────────────────────────────────────────────────

def test_qualify_dependency():
    assert qualify_dependency("b", "a@mkt") == "b@mkt"
    assert qualify_dependency("b@other", "a@mkt") == "b@other"
    assert qualify_dependency("b", "a@inline") == "b"
    assert qualify_dependency("b", "a") == "b"


def _lookup(graph: dict[str, list[str]]):
    async def lookup(plugin_id: str) -> DependencyLookupResult | None:
        deps = graph.get(plugin_id)
        return None if deps is None else DependencyLookupResult(tuple(deps))
    return lookup


def test_closure_is_dependencies_first_and_skips_enabled_deps_but_not_the_root():
    graph = {"a@m": ["b", "c@m"], "b@m": ["c"], "c@m": []}
    result = asyncio.run(resolve_dependency_closure("a@m", _lookup(graph), frozenset()))
    assert result.ok and result.closure == ("c@m", "b@m", "a@m")
    result = asyncio.run(resolve_dependency_closure("a@m", _lookup(graph), frozenset({"a@m", "b@m", "c@m"})))
    assert result.closure == ("a@m",)


def test_closure_failures():
    run = asyncio.run
    cycle = run(resolve_dependency_closure("a@m", _lookup({"a@m": ["b"], "b@m": ["a"]}), frozenset()))
    assert (cycle.ok, cycle.reason, cycle.chain) == (False, "cycle", ("a@m", "b@m", "a@m"))
    missing = run(resolve_dependency_closure("a@m", _lookup({"a@m": ["ghost"]}), frozenset()))
    assert (missing.reason, missing.missing, missing.required_by) == ("not-found", "ghost@m", "a@m")
    cross = run(resolve_dependency_closure("a@m", _lookup({"a@m": ["x@other"], "x@other": []}), frozenset()))
    assert (cross.reason, cross.dependency, cross.required_by) == ("cross-marketplace", "x@other", "a@m")
    allowed = run(resolve_dependency_closure("a@m", _lookup({"a@m": ["x@other"], "x@other": []}), frozenset(),
                                             frozenset({"other"})))
    assert allowed.ok and allowed.closure == ("x@other", "a@m")
    already = run(resolve_dependency_closure("a@m", _lookup({"a@m": ["x@other"]}), frozenset({"x@other"})))
    assert already.ok


def _plugin(plugin_id: str, deps: list[str], enabled: bool = True) -> LoadedPlugin:
    name = plugin_id.split("@")[0]
    manifest = {"name": name, "dependencies": deps}
    return LoadedPlugin(name=name, manifest=manifest,  # type: ignore[arg-type]
                        path="/p", source=plugin_id, repository=plugin_id, enabled=enabled)


def test_verify_and_demote_reaches_a_fixed_point():
    plugins = [_plugin("a@m", ["b"]), _plugin("b@m", ["c"]), _plugin("c@m", [], enabled=False),
               _plugin("d@m", ["ghost"]), _plugin("e@m", [])]
    result = verify_and_demote(plugins)
    assert result.demoted == {"a@m", "b@m", "d@m"}
    reasons = {(e["source"], e["dependency"], e["reason"]) for e in result.errors}  # type: ignore[typeddict-item]
    assert reasons == {("b@m", "c@m", "not-enabled"), ("a@m", "b@m", "not-enabled"),
                       ("d@m", "ghost@m", "not-found")}
    assert all(p.enabled is not False or p.source == "c@m" for p in plugins)  # input untouched


def test_verify_and_demote_matches_bare_inline_dependencies_by_name():
    plugins = [_plugin("a@inline", ["b"]), _plugin("b@real", [])]
    assert verify_and_demote(plugins).demoted == set()
    plugins = [_plugin("a@inline", ["b"]), _plugin("b@real", [], enabled=False)]
    result = verify_and_demote(plugins)
    assert result.demoted == {"a@inline"}
    assert result.errors[0]["reason"] == "not-enabled"  # type: ignore[typeddict-item]


def test_reverse_dependents_and_suffixes():
    plugins = [_plugin("a@m", ["b"]), _plugin("c@inline", ["b"]), _plugin("d@m", ["b"], enabled=False),
               _plugin("b@m", [])]
    assert find_reverse_dependents("b@m", plugins) == ["a", "c"]
    assert format_dependency_count_suffix([]) == ""
    assert format_dependency_count_suffix(["x"]) == " (+ 1 dependency)"
    assert format_dependency_count_suffix(["x", "y"]) == " (+ 2 dependencies)"
    assert format_reverse_dependents_suffix(None) == ""
    assert format_reverse_dependents_suffix(["a", "c"]) == " — warning: required by a, c"


def test_enabled_plugin_ids_for_scope(tmp_path):
    locations = _locations(tmp_path, user={"enabledPlugins": {"a@m": True, "b@m": False, "c@m": ["^1"]}})
    assert get_enabled_plugin_ids_for_scope(PermissionRuleSource.USER_SETTINGS, locations) == {"a@m", "c@m"}
    assert get_enabled_plugin_ids_for_scope(PermissionRuleSource.PROJECT_SETTINGS, locations) == set()


# ── flagging ──────────────────────────────────────────────────────────────────

def test_flagging_round_trip_and_expiry(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_PLUGIN_CACHE_DIR", str(tmp_path))
    plugin_flagging.reset_flagged_plugins_cache()
    assert plugin_flagging.get_flagged_plugins() == {}
    asyncio.run(plugin_flagging.add_flagged_plugin("a@m"))
    asyncio.run(plugin_flagging.add_flagged_plugin("b@m"))
    path = tmp_path / "flagged-plugins.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert set(json.loads(path.read_text())["plugins"]) == {"a@m", "b@m"}
    asyncio.run(plugin_flagging.mark_flagged_plugins_seen(["a@m", "zzz"]))
    assert "seenAt" in plugin_flagging.get_flagged_plugins()["a@m"]
    asyncio.run(plugin_flagging.remove_flagged_plugin("b@m"))
    asyncio.run(plugin_flagging.remove_flagged_plugin("b@m"))
    assert set(plugin_flagging.get_flagged_plugins()) == {"a@m"}
    old = (datetime.now(UTC) - timedelta(hours=49)).isoformat().replace("+00:00", "Z")
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    path.write_text(json.dumps({"plugins": {"old@m": {"flaggedAt": old, "seenAt": old},
                                            "new@m": {"flaggedAt": recent, "seenAt": recent},
                                            "bad@m": {"nope": 1}}}))
    plugin_flagging.reset_flagged_plugins_cache()
    asyncio.run(plugin_flagging.load_flagged_plugins())
    assert set(plugin_flagging.get_flagged_plugins()) == {"new@m"}
    assert set(json.loads(path.read_text())["plugins"]) == {"new@m"}
    plugin_flagging.reset_flagged_plugins_cache()


def test_parse_plugins_data_rejects_malformed_documents():
    assert plugin_flagging.parse_plugins_data("[]") == {}
    assert plugin_flagging.parse_plugins_data('{"plugins": null}') == {}


# ── marketplace input ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(("value", "expected"), [
    ("git@github.com:o/r.git", {"source": "git", "url": "git@github.com:o/r.git"}),
    ("deploy@gitlab.com:g/p.git#v1", {"source": "git", "url": "deploy@gitlab.com:g/p.git", "ref": "v1"}),
    ("https://host/x.git#main", {"source": "git", "url": "https://host/x.git", "ref": "main"}),
    ("https://dev.azure.com/o/p/_git/r", {"source": "git", "url": "https://dev.azure.com/o/p/_git/r"}),
    ("https://github.com/o/r", {"source": "git", "url": "https://github.com/o/r.git"}),
    ("https://www.github.com/o/r/tree/main#dev",
     {"source": "git", "url": "https://www.github.com/o/r/tree/main.git", "ref": "dev"}),
    ("https://example.com/m.json", {"source": "url", "url": "https://example.com/m.json"}),
    ("https://github.com/o", {"source": "url", "url": "https://github.com/o"}),
    ("o/r", {"source": "github", "repo": "o/r"}),
    ("o/r@v2", {"source": "github", "repo": "o/r", "ref": "v2"}),
    ("o/r#v3", {"source": "github", "repo": "o/r", "ref": "v3"}),
    ("x:y/z", None),
    ("@scope/pkg", None),
    ("plain", None),
])
def test_parse_marketplace_input_forms(value, expected):
    assert asyncio.run(parse_marketplace_input(value)) == expected


def test_parse_marketplace_input_local_paths(tmp_path, monkeypatch):
    run = asyncio.run
    manifest = tmp_path / "marketplace.json"
    manifest.write_text("{}")
    other = tmp_path / "notes.txt"
    other.write_text("x")
    assert run(parse_marketplace_input(f"  {manifest}  ")) == {"source": "file", "path": str(manifest)}
    assert run(parse_marketplace_input(str(tmp_path))) == {"source": "directory", "path": str(tmp_path)}
    assert run(parse_marketplace_input(str(other))) == {
        "error": f"File path must point to a .json file (marketplace.json), but got: {other}"}
    assert run(parse_marketplace_input(str(tmp_path / "gone"))) == {
        "error": f"Path does not exist: {tmp_path / 'gone'}"}
    monkeypatch.chdir(tmp_path)
    assert run(parse_marketplace_input("./marketplace.json")) == {"source": "file", "path": str(manifest)}


# ── plugin errors ─────────────────────────────────────────────────────────────

def test_plugin_error_messages():
    assert get_plugin_error_message({"type": "generic-error", "source": "s", "error": "boom"}) == "boom"
    assert get_plugin_error_message({"type": "network-error", "source": "s", "url": "u", "details": "d"}) == (
        "Network error: u - d")
    assert get_plugin_error_message({"type": "network-error", "source": "s", "url": "u"}) == "Network error: u"
    assert get_plugin_error_message({"type": "mcp-server-suppressed-duplicate", "source": "s", "plugin": "p",
                                     "serverName": "srv", "duplicateOf": "plugin:other:srv"}) == (
        'MCP server "srv" skipped — same command/URL as server provided by plugin "other"')
    assert get_plugin_error_message({"type": "lsp-server-crashed", "source": "s", "plugin": "p",
                                     "serverName": "ts", "exitCode": None}) == (
        'Plugin "p" LSP server "ts" crashed with exit code unknown')
    assert get_plugin_error_message({"type": "marketplace-blocked-by-policy", "source": "s", "marketplace": "m",
                                     "blockedByBlocklist": True, "allowedSources": []}) == (
        "Marketplace 'm' is blocked by enterprise policy")
    assert get_plugin_error_message({"type": "dependency-unsatisfied", "source": "s", "plugin": "p",
                                     "dependency": "d@m", "reason": "not-found"}) == (
        'Dependency "d@m" is not found in any configured marketplace')
    assert get_plugin_error_message({"type": "plugin-cache-miss", "source": "s", "plugin": "p",
                                     "installPath": "/x"}) == 'Plugin "p" not cached at /x — run /plugins to refresh'


def test_the_plugin_package_folder_is_renamed_tddagents_plugin() -> None:
    """User decision (2026-11-02): upstream's `.claude-plugin/` is `.tddagents-plugin/`."""
    assert PLUGIN_PACKAGE_DIR == ".tddagents-plugin"
