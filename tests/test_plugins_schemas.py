"""Tests for app/utils/plugins/schemas.py and the plugin keys of the settings schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.types.permissions import PermissionRuleSource
from app.utils.plugins import schemas as s
from app.utils.settings.settings import (
    SettingsLocations,
    get_settings_for_source,
    parse_settings_file,
)
from app.utils.settings.types import plugin_settings_schema_errors


def _issues(schema, value):
    result = s.safe_parse(schema, value)
    assert not result.success
    return [(i.path_string(), i.code, i.message) for i in result.issues]


# ── reserved names ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["anthropic-official", "official-claude-plugins", "claude-marketplace-v2",
                                  "Claude_Official", "claude-plugins", "anthropic marketplace"])
def test_impersonating_names_are_blocked(name):
    assert s.is_blocked_official_name(name)


@pytest.mark.parametrize("name", ["claude-plugins-official", "my-claude-marketplace", "tools", "Agent-Skills"])
def test_allowed_and_ordinary_names_are_not_blocked(name):
    assert not s.is_blocked_official_name(name)


def test_non_ascii_names_are_blocked():
    assert s.is_blocked_official_name("аnthropic-tools")  # Cyrillic a


def test_reserved_name_needs_the_official_github_org():
    assert s.validate_official_name_source("agent-skills", {"source": "github", "repo": "anthropics/skills"}) is None
    assert "reserved" in (
        s.validate_official_name_source("agent-skills", {"source": "github", "repo": "me/skills"}) or "")
    assert s.validate_official_name_source(
        "agent-skills", {"source": "git", "url": "git@github.com:anthropics/skills.git"}) is None
    assert "Only repositories" in (s.validate_official_name_source(
        "agent-skills", {"source": "git", "url": "https://gitlab.com/x/y.git"}) or "")
    assert "can only be used with GitHub sources" in (s.validate_official_name_source(
        "agent-skills", {"source": "directory", "path": "/x"}) or "")
    assert s.validate_official_name_source("mine", {"source": "directory", "path": "/x"}) is None


def test_auto_update_defaults_on_for_official_marketplaces_only():
    assert s.is_marketplace_auto_update("claude-plugins-official", {})
    assert not s.is_marketplace_auto_update("knowledge-work-plugins", {})
    assert not s.is_marketplace_auto_update("mine", {})
    assert s.is_marketplace_auto_update("mine", {"autoUpdate": True})
    assert not s.is_marketplace_auto_update("claude-plugins-official", {"autoUpdate": False})


# ── the manifest ───────────────────────────────────────────────────────────────

def test_manifest_strips_unknown_keys_and_normalizes_dependencies():
    data: Any = s.parse(s.PluginManifestSchema, {
        "name": "fmt",
        "vendorExtension": {"x": 1},
        "dependencies": ["base", "util@tools@^1.2", {"name": "lib", "marketplace": "mkt", "version": "2"}],
        "lspServers": {"ts": {"command": "tsls", "extensionToLanguage": {".ts": "typescript"}}},
    })
    assert "vendorExtension" not in data
    assert data["dependencies"] == ["base", "util@tools", "lib@mkt"]
    assert data["lspServers"]["ts"]["transport"] == "stdio"


def test_parse_never_mutates_its_input():
    raw: dict[str, Any] = {"name": "p", "lspServers": {"a": {"command": "x", "extensionToLanguage": {".a": "a"}}}}
    s.parse(s.PluginManifestSchema, raw)
    assert "transport" not in raw["lspServers"]["a"]


@pytest.mark.parametrize(("value", "expected"), [
    ({"name": "a b"}, ("name", "custom", 'Plugin name cannot contain spaces. Use kebab-case (e.g., "my-plugin")')),
    ({"name": ""}, ("name", "too_small", "Plugin name cannot be empty")),
    ({"name": 3}, ("name", "invalid_type", "Invalid input: expected string, received number")),
    ({"name": "a", "version": 1}, ("version", "invalid_type", "Invalid input: expected string, received number")),
    ({"name": "a", "homepage": "nope"}, ("homepage", "invalid_format", "Invalid URL")),
    ({"name": "a", "hooks": 5}, ("hooks", "invalid_union", "Invalid input")),
    ({"name": "a", "dependencies": ["Bad Name"]},
     ("dependencies.0", "invalid_format", "Dependency must be a plugin name, optionally qualified with @marketplace")),
    ({"name": "a", "author": {"name": ""}}, ("author.name", "too_small", "Author name cannot be empty")),
])
def test_manifest_issues_carry_zod_paths_codes_and_messages(value, expected):
    assert expected in _issues(s.PluginManifestSchema, value)


def test_missing_required_field_is_reported_at_its_path():
    issues = _issues(s.PluginManifestSchema, {})
    assert [(p, c) for p, c, _ in issues] == [("name", "invalid_type")]


def test_strict_nested_objects_reject_unknown_keys():
    issues = _issues(s.PluginManifestSchema, {
        "name": "a", "userConfig": {"KEY": {"type": "string", "title": "t", "description": "d", "bogus": 1}}})
    assert ("userConfig.KEY", "unrecognized_keys", 'Unrecognized key: "bogus"') in issues


def test_user_config_keys_must_be_identifiers():
    issues = _issues(s.PluginManifestSchema, {
        "name": "a", "userConfig": {"1x": {"type": "string", "title": "t", "description": "d"}}})
    assert issues == [("userConfig.1x", "invalid_key", "Invalid key in record")]


def test_component_paths_must_be_relative():
    assert _issues(s.PluginManifestSchema, {"name": "a", "agents": "agents/x.md"}) == [
        ("agents", "invalid_union", "Invalid input")]
    manifest = {"name": "a", "agents": ["./a.md"], "skills": "./skills", "mcpServers": "./server.mcpb",
                "hooks": ["./h.json", {}]}
    data = s.parse(s.PluginManifestSchema, manifest)
    assert data["mcpServers"] == "./server.mcpb"


def test_command_metadata_needs_exactly_one_of_source_and_content():
    assert s.safe_parse(s.CommandMetadataSchema, {"source": "./a.md"}).success
    assert s.safe_parse(s.CommandMetadataSchema, {"content": "# hi"}).success
    message = 'Command must have either "source" (file path) or "content" (inline markdown), but not both'
    assert ("", "custom", message) in _issues(s.CommandMetadataSchema, {"source": "./a.md", "content": "x"})
    assert ("", "custom", message) in _issues(s.CommandMetadataSchema, {})


def test_lsp_server_config_rules():
    message = "Command should not contain spaces. Use args array for arguments."
    issues = _issues(s.LspServerConfigSchema, {"command": "a b", "extensionToLanguage": {".a": "a"}})
    assert ("command", "custom", message) in issues
    assert s.safe_parse(s.LspServerConfigSchema, {"command": "/opt/my tool/ls", "extensionToLanguage": {".a": "a"}}
                        ).success
    assert ("extensionToLanguage", "custom", "extensionToLanguage must have at least one mapping") in _issues(
        s.LspServerConfigSchema, {"command": "x", "extensionToLanguage": {}})
    assert ("startupTimeout", "too_small", "Too small: expected number to be >0") in _issues(
        s.LspServerConfigSchema, {"command": "x", "extensionToLanguage": {".a": "a"}, "startupTimeout": 0})


# ── sources and marketplaces ───────────────────────────────────────────────────

def test_marketplace_source_is_discriminated_on_source():
    assert s.parse(s.MarketplaceSourceSchema, {"source": "github", "repo": "o/r", "extra": 1}) == {
        "source": "github", "repo": "o/r"}
    assert _issues(s.MarketplaceSourceSchema, {"source": "nope"}) == [("source", "invalid_union", "Invalid input")]
    assert _issues(s.MarketplaceSourceSchema, {"source": "url", "url": "x"}) == [("url", "invalid_format",
                                                                                  "Invalid URL")]
    assert ("package", "custom", "Package name cannot contain path traversal patterns") in _issues(
        s.MarketplaceSourceSchema, {"source": "npm", "package": "../etc"})


def test_settings_source_rejects_reserved_names_and_relative_plugins():
    issues = _issues(s.MarketplaceSourceSchema, {"source": "settings", "name": "claude-plugins-official",
                                                 "plugins": []})
    assert issues[0][0] == "name" and issues[0][2].startswith("Reserved official marketplace names")
    issues = _issues(s.MarketplaceSourceSchema, {"source": "settings", "name": "inline-mkt",
                                                 "plugins": [{"name": "p", "source": "./p"}]})
    assert issues[0][0] == "plugins.0"
    assert issues[0][2].startswith("Plugins in a settings-sourced marketplace must use remote sources")


@pytest.mark.parametrize(("name", "message"), [
    ("my market", 'Marketplace name cannot contain spaces. Use kebab-case (e.g., "my-marketplace")'),
    ("a/b", 'Marketplace name cannot contain path separators (/ or \\), ".." sequences, or be "."'),
    (".", 'Marketplace name cannot contain path separators (/ or \\), ".." sequences, or be "."'),
    ("inline", 'Marketplace name "inline" is reserved for --plugin-dir session plugins'),
    ("Builtin", 'Marketplace name "builtin" is reserved for built-in plugins'),
    ("", "Marketplace must have a name"),
])
def test_marketplace_name_rules(name, message):
    assert ("name", message) in [(p, m) for p, _, m in _issues(
        s.PluginMarketplaceSchema, {"name": name, "owner": {"name": "o"}, "plugins": []})]


def test_marketplace_entries_default_strict_and_keep_sources():
    data = s.parse(s.PluginMarketplaceSchema, {
        "name": "tools", "owner": {"name": "o"},
        "plugins": [{"name": "a", "source": "./a"},
                    {"name": "b", "source": {"source": "git-subdir", "url": "o/r", "path": "p"}, "strict": False}],
    })
    assert data["plugins"][0]["strict"] is True
    assert data["plugins"][1]["strict"] is False


def test_plugin_source_union_reports_one_issue_at_its_path():
    issues = _issues(s.PluginMarketplaceSchema, {
        "name": "tools", "owner": {"name": "o"},
        "plugins": [{"name": "b", "source": {"source": "github", "repo": "o/r", "sha": "abc"}}]})
    assert issues == [("plugins.0.source", "invalid_union", "Invalid input")]


def test_local_source_predicates():
    assert s.is_local_plugin_source("./p")
    assert not s.is_local_plugin_source({"source": "npm", "package": "x"})
    assert s.is_local_marketplace_source({"source": "directory", "path": "/x"})
    assert not s.is_local_marketplace_source({"source": "github", "repo": "o/r"})


def test_installed_plugins_file_keys_are_plugin_ids():
    assert s.safe_parse(s.InstalledPluginsFileSchemaV2,
                        {"version": 2, "plugins": {"a@b": [{"scope": "user", "installPath": "/x"}]}}).success
    assert _issues(s.InstalledPluginsFileSchemaV2, {"version": 2, "plugins": {"bad": []}}) == [
        ("plugins.bad", "invalid_key", "Invalid key in record")]
    assert s.parse(s.InstalledPluginsFileSchema, {"version": 1, "plugins": {}})["version"] == 1


def test_parse_raises_with_issues_and_format_issues_renders_them():
    with pytest.raises(s.SchemaValidationError) as caught:
        s.parse(s.PluginIdSchema, "no-marketplace")
    assert s.format_issues(caught.value.issues) == ": Plugin ID must be in format: plugin@marketplace"
    assert json.loads(str(caught.value))[0]["code"] == "invalid_format"


def test_unrecognized_key_issues_for_strict_validation():
    keys = s.typed_dict_keys(s.PluginManifest)
    assert "name" in keys and "userConfig" in keys
    assert s.unrecognized_key_issues({"name": "a", "x": 1, "y": 2}, keys) == [
        s.SchemaIssue((), 'Unrecognized keys: "x", "y"', "unrecognized_keys")]
    assert s.unrecognized_key_issues({"name": "a"}, keys) == []


@pytest.mark.parametrize(("value", "ok"), [
    ("https://example.com/m.json", True), ("mailto:a@b", True), ("https://", False), ("nope", False),
    ("http:///host", True),  # WHATWG skips the extra slash: http://host/
])
def test_is_url(value, ok):
    assert s.is_url(value) is ok


# ── settings ──────────────────────────────────────────────────────────────────

def test_valid_plugin_settings_have_no_errors():
    assert plugin_settings_schema_errors({
        "enabledPlugins": {"a@b": True, "c@d": ["^1.0"]},
        "extraKnownMarketplaces": {"team": {"source": {"source": "github", "repo": "o/r"}}},
        "strictKnownMarketplaces": [{"source": "hostPattern", "hostPattern": "^github\\.com$"}],
        "pluginConfigs": {"a@b": {"options": {"k": 1}}},
        "pluginTrustMessage": "vetted",
        "unrelated": object(),
    }) == []


def test_invalid_plugin_settings_are_reported():
    errors = plugin_settings_schema_errors({"enabledPlugins": {"a@b": "yes"}})
    assert errors and errors[0].startswith("enabledPlugins.a@b")


def test_settings_marketplace_name_must_match_its_key():
    errors = plugin_settings_schema_errors({"extraKnownMarketplaces": {
        "team": {"source": {"source": "settings", "name": "other", "plugins": []}}}})
    assert errors == [('extraKnownMarketplaces.team.source.name: Settings-sourced marketplace name must match its '
                      'extraKnownMarketplaces key (got key "team" but source.name "other")')]


def test_a_settings_file_with_invalid_plugin_keys_is_ignored(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"enabledPlugins": {"a@b": 3}, "permissions": {"allow": ["Read"]}}))
    assert parse_settings_file(path) is None
    path.write_text(json.dumps({"enabledPlugins": {"a@b": True}}))
    assert parse_settings_file(path) == {"enabledPlugins": {"a@b": True}}


def test_policy_enabled_plugins_read_through_locations(tmp_path: Path):
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "managed-settings.json").write_text(json.dumps({"enabledPlugins": {"x@y": False}}))
    locations = SettingsLocations(project_root=tmp_path, home=tmp_path, managed_dir=managed)
    assert get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations) == {
        "enabledPlugins": {"x@y": False}}
