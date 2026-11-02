"""
Where Python's defaults differ from Node's, the plugin port keeps upstream's behaviour:
regex anchors and classes, JSON numbers, `null` versus absent, Dirent symlink semantics, Node's
`fs.rm`, UTF-8 decoding with replacement, axios's status handling, a shared memoized load.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.utils.plugins import installed_plugins_manager as ipm
from app.utils.plugins import marketplace_manager as mm
from app.utils.plugins import plugin_options_storage as pos
from app.utils.plugins import schemas as s
from app.utils.plugins import validate_plugin as vp
from app.utils.plugins.mcpb_handler import validate_user_config
from app.utils.plugins.plugin_loader import get_plugin_cache_path, validate_git_url
from app.utils.plugins.walk_plugin_markdown import walk_plugin_markdown
from app.utils.secure_storage.plain_text_storage import plain_text_storage
from app.utils.settings.settings import (
    SettingsLocations,
    load_settings,
    parse_settings_file,
)


@pytest.fixture(autouse=True)
def _fresh_state():
    mm.clear_marketplaces_cache()
    ipm.clear_installed_plugins_cache()
    ipm.reset_migration_state()
    pos.clear_plugin_options_cache()
    yield
    mm.clear_marketplaces_cache()
    ipm.clear_installed_plugins_cache()
    pos.clear_plugin_options_cache()


@pytest.fixture
def locations(tmp_path: Path) -> SettingsLocations:
    project = tmp_path / "project"
    project.mkdir()
    return SettingsLocations(project_root=project, home=tmp_path / "home", managed_dir=tmp_path / "managed")


# ── schemas ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["a@b\n", "\u017fa@b", "\u212aa@b"])
def test_plugin_ids_follow_javascript_regex_semantics(value):
    assert not s.safe_parse(s.PluginIdSchema, value).success


def test_user_config_keys_and_package_names_are_ascii_and_anchored():
    def option(key: str) -> dict[str, Any]:
        return {"name": "p", "userConfig": {key: {"type": "string", "title": "t", "description": "d"}}}

    assert s.safe_parse(s.PluginManifestSchema, option("api_KEY1")).success
    assert not s.safe_parse(s.PluginManifestSchema, option("té")).success
    assert not s.safe_parse(s.PluginManifestSchema, option("key\n")).success
    assert not s.safe_parse(s.MarketplaceSourceSchema, {"source": "npm", "package": "abc\n"}).success
    assert s.safe_parse(s.MarketplaceSourceSchema, {"source": "npm", "package": "@scope/abc"}).success
    assert not s.safe_parse(s.DependencyRefSchema, "dep\n").success


def test_json_numbers_keep_their_type():
    manifest = {"name": "p", "userConfig": {"n": {
        "type": "number", "title": "t", "description": "d", "default": 5, "min": 1, "max": 2.5}}}
    option = s.parse(s.PluginManifestSchema, manifest)["userConfig"]["n"]
    assert (option["default"], option["min"], option["max"]) == (5, 1, 2.5)
    assert isinstance(option["default"], int) and isinstance(option["min"], int)
    lsp = s.parse(s.LspServerConfigSchema, {"command": "x", "extensionToLanguage": {".py": "python"},
                                            "startupTimeout": 1000.0, "maxRestarts": 0})
    assert lsp["startupTimeout"] == 1000 and isinstance(lsp["startupTimeout"], int)
    assert not s.safe_parse(s.LspServerConfigSchema, {"command": "x", "extensionToLanguage": {".py": "p"},
                                                      "startupTimeout": 1.5}).success


def test_issue_shapes_follow_zod():
    strict = {"name": "p", "userConfig": {"k": {"type": "string", "title": "t", "description": "d", "a": 1, "b": 2}}}
    issues = s.safe_parse(s.PluginManifestSchema, strict).issues
    assert [(i.path_string(), i.code, i.message) for i in issues] == [
        ("userConfig.k", "unrecognized_keys", 'Unrecognized keys: "a", "b"')]
    sha = {"source": "github", "repo": "o/r", "sha": "a" * 41}
    assert [(i.code, i.message) for i in s.safe_parse(s.PluginSourceSchema, sha).issues] == [
        ("invalid_union", "Invalid input")]
    short = s.safe_parse(s.GitSubdirPluginSourceSchema if hasattr(s, "GitSubdirPluginSourceSchema") else
                         s.PluginSourceSchema, {"source": "git-subdir", "url": "https://h/r", "path": "p",
                                                "sha": "a" * 39})
    assert not short.success


# ── settings ──────────────────────────────────────────────────────────────────

def write(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


def test_settings_keep_the_parsed_plugin_keys(tmp_path: Path):
    path = write(tmp_path / "settings.json", {
        "extraKnownMarketplaces": {"m": {"source": {"source": "github", "repo": "o/r", "junk": 1}, "x": 2}},
        "strictKnownMarketplaces": [{"source": "url", "url": "https://h/m.json", "junk": True}],
        "unrelated": {"kept": True},
    })
    parsed = parse_settings_file(path)
    assert parsed == {
        "extraKnownMarketplaces": {"m": {"source": {"source": "github", "repo": "o/r"}}},
        "strictKnownMarketplaces": [{"source": "url", "url": "https://h/m.json"}],
        "unrelated": {"kept": True},
    }


def test_settings_with_a_null_enabled_plugin_are_ignored(tmp_path: Path):
    assert parse_settings_file(write(tmp_path / "s.json", {"enabledPlugins": {"p@m": None}})) is None
    assert parse_settings_file(write(tmp_path / "t.json", {"enabledPlugins": {"p@m": ["^1"]}})) == {
        "enabledPlugins": {"p@m": ["^1"]}}


def test_the_settings_name_check_reports_alongside_other_issues(tmp_path: Path, caplog):
    path = write(tmp_path / "s.json", {
        "pluginTrustMessage": 3,
        "extraKnownMarketplaces": {"k": {"source": {"source": "settings", "name": "other", "plugins": []}}},
    })
    assert parse_settings_file(path) is None
    assert "pluginTrustMessage" in caplog.text
    assert 'got key "k" but source.name "other"' in caplog.text


def test_plugin_option_integers_survive_the_settings_load(locations: SettingsLocations):
    assert locations.home is not None
    write(locations.home / ".tddagents" / "settings.json", {"pluginConfigs": {"p@m": {"options": {"n": 5}}}})
    assert load_settings(locations)["pluginConfigs"]["p@m"]["options"]["n"] == 5
    assert pos.load_plugin_options("p@m", locations) == {"n": 5}
    assert pos.substitute_user_config_variables("${user_config.n}", {"n": 5}) == "5"


# ── null versus absent ────────────────────────────────────────────────────────

def test_a_stored_null_is_a_value_not_an_absent_key():
    schema = {"k": {"type": "string", "title": "Key", "description": "d", "required": True}}
    assert validate_user_config({}, schema).errors == ["Key is required but not provided"]
    assert validate_user_config({"k": None}, schema).errors == ["Key must be a string"]
    assert validate_user_config({"k": ""}, schema).errors == ["Key is required but not provided"]
    assert pos.substitute_user_config_variables("${user_config.k}", {"k": None}) == "null"
    with pytest.raises(ValueError, match="Missing required user configuration value: k"):
        pos.substitute_user_config_variables("${user_config.k}", {})
    assert pos.substitute_user_config_in_content("${user_config.k}", {"k": None}, {}) == "null"
    assert pos.substitute_user_config_in_content("${user_config.k}", {}, {}) == "${user_config.k}"


def test_delete_options_clears_an_empty_entry_and_drops_empty_secrets(locations: SettingsLocations):
    assert locations.home is not None
    settings = write(locations.home / ".tddagents" / "settings.json", {"pluginConfigs": {"p@m": {}, "q@m": {}}})
    plain_text_storage.update({"pluginSecrets": {"p@m": {"t": "1"}, "p@m/server": {"u": "2"}}, "other": 1})
    pos.delete_plugin_options("p@m", locations)
    assert json.loads(settings.read_text())["pluginConfigs"] == {"q@m": {}}
    assert plain_text_storage.read() == {"other": 1}


def test_the_options_memo_is_per_settings_set(tmp_path: Path):
    first = SettingsLocations(project_root=tmp_path, home=tmp_path / "a", managed_dir=tmp_path / "none")
    second = SettingsLocations(project_root=tmp_path, home=tmp_path / "b", managed_dir=tmp_path / "none")
    write(tmp_path / "a" / ".tddagents" / "settings.json", {"pluginConfigs": {"p@m": {"options": {"x": "a"}}}})
    write(tmp_path / "b" / ".tddagents" / "settings.json", {"pluginConfigs": {"p@m": {"options": {"x": "b"}}}})
    assert pos.load_plugin_options("p@m", first) == {"x": "a"}
    assert pos.load_plugin_options("p@m", second) == {"x": "b"}


# ── filesystem ────────────────────────────────────────────────────────────────

def test_delete_plugin_cache_removes_a_file_or_a_link(tmp_path: Path):
    cache = Path(get_plugin_cache_path())
    zipped = cache / "m" / "p" / "1.0.zip"
    zipped.parent.mkdir(parents=True)
    zipped.write_text("zip")
    ipm.delete_plugin_cache(str(zipped))
    assert not zipped.exists() and not zipped.parent.exists()
    target = tmp_path / "real"
    target.mkdir()
    link = cache / "m" / "q" / "2.0"
    link.parent.mkdir(parents=True)
    link.symlink_to(target, target_is_directory=True)
    ipm.delete_plugin_cache(str(link))
    assert not os.path.lexists(link) and target.exists()


def test_removing_the_last_installation_drops_an_empty_entry():
    path = Path(ipm.get_installed_plugins_file_path())
    write(path, {"version": 2, "plugins": {"p@m": [], "q@m": []}})
    ipm.remove_plugin_installation("p@m", "user")
    assert json.loads(path.read_text())["plugins"] == {"q@m": []}


def test_installed_plugins_json_is_written_as_utf8(tmp_path: Path):
    ipm.add_installed_plugin("p@m", {"version": "1", "installedAt": "t", "installPath": "/café"}, "user")
    assert '"/café"' in Path(ipm.get_installed_plugins_file_path()).read_text(encoding="utf-8")


def test_the_legacy_cache_cleanup_never_follows_links(tmp_path: Path):
    outside = tmp_path / "outside"
    (outside / "v" / "x").mkdir(parents=True)
    cache = Path(get_plugin_cache_path())
    cache.mkdir(parents=True)
    (cache / "linked").symlink_to(outside, target_is_directory=True)
    (cache / "flat").mkdir()
    # A legacy `installed_plugins_v2.json` is renamed into place, then the flat cache is cleaned.
    write(Path(ipm.get_installed_plugins_v2_file_path()), {"version": 2, "plugins": {}})
    ipm.migrate_to_single_plugin_file()
    assert outside.exists() and os.path.lexists(cache / "linked")
    assert not (cache / "flat").exists()


def test_validate_git_url_ignores_surrounding_whitespace():
    assert validate_git_url(" https://host/r.git ") == " https://host/r.git "
    with pytest.raises(ValueError, match="Invalid git URL"):
        validate_git_url(" ftp://host/r ")


def test_walk_and_collect_skip_symlinks(tmp_path: Path):
    root = tmp_path / "plugin"
    (root / "commands").mkdir(parents=True)
    (root / "commands" / "real.md").write_text("x")
    (root / "commands" / "loop").symlink_to(root, target_is_directory=True)
    (root / "commands" / "alias.md").symlink_to(root / "commands" / "real.md")
    seen: list[str] = []

    async def on_file(path: str, namespace: list[str]) -> None:
        seen.append(os.path.relpath(path, root))

    asyncio.run(walk_plugin_markdown(str(root), on_file))
    assert seen == [os.path.join("commands", "real.md")]
    collected = asyncio.run(vp.collect_markdown(str(root / "commands"), False))
    assert [os.path.basename(p) for p in collected] == ["real.md"]


def test_walk_logs_a_failing_callback_and_continues(tmp_path: Path, caplog):
    (tmp_path / "a.md").write_text("x")

    async def explode(path: str, namespace: list[str]) -> None:
        raise RuntimeError("bad file")

    asyncio.run(walk_plugin_markdown(str(tmp_path), explode, log_label="skills"))
    assert "Failed to scan skills directory" in caplog.text


def test_invalid_utf8_reads_with_replacement_characters(tmp_path: Path):
    manifest = tmp_path / ".tddagents-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(b'{"name": "p\xff"}')
    result = asyncio.run(vp.validate_plugin_manifest(str(manifest)))
    assert result.success is True
    assert any("kebab-case" in w.message for w in result.warnings)


def test_kebab_case_check_rejects_a_trailing_newline(tmp_path: Path):
    manifest = tmp_path / "plugin.json"
    manifest.write_text(json.dumps({"name": "my-plugin\n", "version": "1.0.0", "description": "d",
                                    "author": {"name": "a"}}))
    result = asyncio.run(vp.validate_plugin_manifest(str(manifest)))
    assert any(w.path == "name" and "kebab-case" in w.message for w in result.warnings)


def test_validate_manifest_propagates_stat_errors_other_than_enoent(tmp_path: Path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(NotADirectoryError):
        asyncio.run(vp.validate_manifest(str(blocker / "child")))


# ── marketplace manager ───────────────────────────────────────────────────────

def test_http_get_rejects_non_2xx_and_parses_json_or_keeps_text(monkeypatch):
    responses = {
        "https://h/ok.json": httpx.Response(200, json={"a": 1}),
        "https://h/text": httpx.Response(200, text="<html>"),
        "https://h/not-modified": httpx.Response(304),
        "https://h/missing": httpx.Response(404),
    }
    real_client = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs["max_redirects"] == 5 and kwargs["timeout"] == 10
        return real_client(transport=httpx.MockTransport(lambda request: responses[str(request.url)]), **kwargs)

    monkeypatch.setattr("app.utils.plugins.marketplace_manager.httpx.AsyncClient", client)
    assert asyncio.run(mm.http_get("https://h/ok.json", {}, 10000)) == mm.HttpResponse(status=200, data={"a": 1})
    assert asyncio.run(mm.http_get("https://h/text", {}, 10000)).data == "<html>"
    for url, status in (("https://h/not-modified", 304), ("https://h/missing", 404)):
        with pytest.raises(httpx.HTTPStatusError, match=f"Request failed with status code {status}"):
            asyncio.run(mm.http_get(url, {}, 10000))


def test_concurrent_first_loads_share_one_load(tmp_path: Path, locations: SettingsLocations, monkeypatch):
    root = tmp_path / "mkt"
    (root / ".tddagents-plugin").mkdir(parents=True)
    (root / ".tddagents-plugin" / "marketplace.json").write_text(json.dumps(
        {"name": "team", "owner": {"name": "o"}, "plugins": []}))
    asyncio.run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    mm.clear_marketplaces_cache()
    loads: list[str] = []
    real = mm._get_marketplace_uncached

    async def counted(name: str) -> Any:
        loads.append(name)
        await asyncio.sleep(0)
        return await real(name)

    monkeypatch.setattr(mm, "_get_marketplace_uncached", counted)

    async def both() -> list[Any]:
        return list(await asyncio.gather(mm.get_marketplace("team"), mm.get_marketplace("team")))

    first, second = asyncio.run(both())
    assert first is second and loads == ["team"]


def test_a_failed_load_is_memoized_until_cleared(locations: SettingsLocations):
    with pytest.raises(ValueError, match="not found in configuration"):
        asyncio.run(mm.get_marketplace("nope"))
    with pytest.raises(ValueError, match="not found in configuration") as again:
        asyncio.run(mm.get_marketplace("nope"))
    assert again.value.__traceback__ is not None


def test_the_git_timeout_ignores_non_ascii_digits(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_PLUGIN_GIT_TIMEOUT_MS", "١٢٠")
    assert mm.get_plugin_git_timeout_ms() == mm.DEFAULT_PLUGIN_GIT_TIMEOUT_MS


def test_a_known_marketplaces_file_with_invalid_utf8_reports_a_parse_error():
    path = Path(mm.get_known_marketplaces_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff{")
    with pytest.raises(RuntimeError, match="^Failed to load marketplace configuration"):
        asyncio.run(mm.load_known_marketplaces_config())
