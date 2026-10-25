"""Phase 8 MCP: server configuration (`services/mcp/config.ts`, `types.ts`, `utils.ts` ->
`getProjectMcpServerStatus`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.loop.mcp.config import (
    get_claude_code_mcp_configs,
    get_global_config_path,
    get_mcp_configs_by_scope,
    get_project_config_key,
    get_project_mcp_server_status,
    is_mcp_server_allowed_by_policy,
    parse_mcp_config,
    parse_mcp_config_from_file_path,
)
from app.loop.mcp.types import McpServerConfig
from app.loop.permissions.settings import SettingsLocations


def _locations(tmp_path: Path) -> SettingsLocations:
    return SettingsLocations(project_root=tmp_path / "proj", home=tmp_path / "home", managed_dir=tmp_path / "etc")


def _write(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


STDIO = {"command": "node", "args": ["server.js"]}
HTTP = {"type": "http", "url": "https://api.example.com/mcp", "headers": {"X": "1"}}


class TestParse:
    def test_server_types(self):
        config, errors = parse_mcp_config({"mcpServers": {
            "a": STDIO, "b": HTTP, "c": {"type": "sse", "url": "https://s"}, "d": {"type": "ws", "url": "wss://w"},
            "e": {"type": "stdio", "command": "x"},
        }}, expand_vars=False, scope="project")
        assert errors == []
        assert config is not None
        a, b, c, d, e = (config[k] for k in "abcde")
        assert (a.type, a.command, a.args, a.env, a.scope) == (None, "node", ("server.js",), None, "project")
        assert (b.type, b.url, b.headers) == ("http", "https://api.example.com/mcp", {"X": "1"})
        assert (c.type, d.type, e.type, e.args) == ("sse", "ws", "stdio", ())
        assert a.transport == "stdio" and e.transport == "stdio" and b.transport == "http"

    @pytest.mark.parametrize("bad", [
        {"command": ""},
        {"type": "http"},
        {"type": "carrier-pigeon", "url": "x"},
        {"command": "x", "args": "notalist"},
        {"type": "sse", "url": "u", "headers": {"k": 1}},
    ])
    def test_one_invalid_server_rejects_the_file(self, bad):
        config, errors = parse_mcp_config({"mcpServers": {"ok": STDIO, "bad": bad}}, expand_vars=False,
                                          scope="user", file_path="/f.json")
        assert config is None
        assert errors and all(e.message == "Does not adhere to MCP server configuration schema" for e in errors)
        assert errors[0].file == "/f.json" and errors[0].severity == "fatal"

    def test_the_document_needs_an_mcp_servers_object(self):
        assert parse_mcp_config({"servers": {}}, expand_vars=False, scope="user")[0] is None
        assert parse_mcp_config([], expand_vars=False, scope="user")[0] is None

    def test_env_vars_expand_in_every_field_and_missing_ones_warn(self, monkeypatch):
        monkeypatch.setenv("BIN", "/usr/bin/node")
        monkeypatch.setenv("TOKEN", "t0k")
        monkeypatch.delenv("NOPE", raising=False)
        config, errors = parse_mcp_config({"mcpServers": {
            "s": {"command": "${BIN}", "args": ["--key=${TOKEN}", "${NOPE}"], "env": {"K": "${TOKEN}"}},
            "h": {"type": "http", "url": "${BASE:-https://d}/mcp", "headers": {"Authorization": "Bearer ${TOKEN}"}},
        }}, expand_vars=True, scope="project")
        assert config is not None
        s, h = config["s"], config["h"]
        assert (s.command, s.args, s.env) == ("/usr/bin/node", ("--key=t0k", "${NOPE}"), {"K": "t0k"})
        assert (h.url, h.headers) == ("https://d/mcp", {"Authorization": "Bearer t0k"})
        (warning,) = errors
        assert (warning.path, warning.message, warning.severity, warning.server_name) == (
            "mcpServers.s", "Missing environment variables: NOPE", "warning", "s")

    def test_from_file(self, tmp_path):
        missing, errors = parse_mcp_config_from_file_path(tmp_path / "none.json", expand_vars=True, scope="project")
        assert missing is None and errors[0].message == f"MCP config file not found: {tmp_path / 'none.json'}"
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert parse_mcp_config_from_file_path(bad, expand_vars=True, scope="project")[1][0].message == (
            "MCP config is not a valid JSON")
        good = _write(tmp_path / "good.json", {"mcpServers": {"a": STDIO}})
        config, _ = parse_mcp_config_from_file_path(good, expand_vars=True, scope="project")
        assert config is not None and config["a"].command == "node"


class TestScopes:
    def test_the_global_config_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
        assert get_global_config_path() == tmp_path / "cfg" / ".tddagents.json"
        monkeypatch.delenv("TDDAGENTS_CONFIG_DIR")
        assert get_global_config_path() == Path.home() / ".tddagents.json"

    def test_project_config_key_is_the_git_root(self, tmp_path):
        (tmp_path / "repo" / ".git").mkdir(parents=True)
        (tmp_path / "repo" / "sub").mkdir()
        assert get_project_config_key(tmp_path / "repo" / "sub") == str((tmp_path / "repo").resolve())
        (tmp_path / "plain").mkdir()
        assert get_project_config_key(tmp_path / "plain") == str((tmp_path / "plain").resolve())

    def test_project_scope_walks_up_with_the_nearest_file_winning(self, tmp_path):
        cwd = tmp_path / "a" / "b"
        cwd.mkdir(parents=True)
        _write(tmp_path / "a" / ".mcp.json", {"mcpServers": {"outer": STDIO, "both": {"command": "outer"}}})
        _write(cwd / ".mcp.json", {"mcpServers": {"both": {"command": "inner"}}})
        servers, errors = get_mcp_configs_by_scope("project", cwd, _locations(tmp_path))
        assert errors == []
        assert set(servers) >= {"outer", "both"} and servers["both"].command == "inner"
        assert servers["outer"].scope == "project"

    def test_user_and_local_scopes_live_in_the_global_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
        cwd = tmp_path / "proj"
        cwd.mkdir()
        _write(tmp_path / "cfg" / ".tddagents.json", {
            "mcpServers": {"u": STDIO},
            "projects": {get_project_config_key(cwd): {"mcpServers": {"l": HTTP}}},
        })
        user, _ = get_mcp_configs_by_scope("user", cwd, _locations(tmp_path))
        local, _ = get_mcp_configs_by_scope("local", cwd, _locations(tmp_path))
        assert list(user) == ["u"] and user["u"].scope == "user"
        assert list(local) == ["l"] and local["l"].scope == "local"

    def test_enterprise_scope_is_the_managed_file(self, tmp_path):
        _write(tmp_path / "etc" / "managed-mcp.json", {"mcpServers": {"corp": STDIO}})
        servers, _ = get_mcp_configs_by_scope("enterprise", tmp_path, _locations(tmp_path))
        assert servers["corp"].scope == "enterprise"


class TestMerged:
    def _setup(self, tmp_path, monkeypatch, *, settings=None, policy=None, global_extra=None):
        monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
        cwd = tmp_path / "proj"
        cwd.mkdir(exist_ok=True)
        key = get_project_config_key(cwd)
        _write(cwd / ".mcp.json", {"mcpServers": {"p": STDIO, "shared": {"command": "project"}}})
        _write(tmp_path / "cfg" / ".tddagents.json", {
            "mcpServers": {"u": STDIO, "shared": {"command": "user"}},
            "projects": {key: {"mcpServers": {"l": HTTP, "shared": {"command": "local"}}, **(global_extra or {})}},
        })
        if settings is not None:
            _write(cwd / ".tddagents" / "settings.json", settings)
        if policy is not None:
            _write(tmp_path / "etc" / "managed-settings.json", policy)
        return cwd, _locations(tmp_path)

    def test_local_beats_project_beats_user(self, tmp_path, monkeypatch):
        cwd, loc = self._setup(tmp_path, monkeypatch)
        servers, _ = get_claude_code_mcp_configs(cwd, loc)
        assert set(servers) == {"p", "u", "l", "shared"}
        assert (servers["shared"].command, servers["shared"].scope) == ("local", "local")

    def test_rejected_project_servers_are_dropped(self, tmp_path, monkeypatch):
        cwd, loc = self._setup(tmp_path, monkeypatch, settings={"disabledMcpjsonServers": ["p"]})
        assert "p" not in get_claude_code_mcp_configs(cwd, loc)[0]

    def test_disabled_servers_are_dropped(self, tmp_path, monkeypatch):
        cwd, loc = self._setup(tmp_path, monkeypatch, global_extra={"disabledMcpServers": ["u"]})
        assert "u" not in get_claude_code_mcp_configs(cwd, loc)[0]

    def test_an_enterprise_file_is_exclusive(self, tmp_path, monkeypatch):
        cwd, loc = self._setup(tmp_path, monkeypatch)
        _write(tmp_path / "etc" / "managed-mcp.json", {"mcpServers": {"corp": STDIO}})
        assert list(get_claude_code_mcp_configs(cwd, loc)[0]) == ["corp"]

    def test_plugin_only_customization_locks_user_editable_scopes(self, tmp_path, monkeypatch):
        cwd, loc = self._setup(tmp_path, monkeypatch, policy={"strictPluginOnlyCustomization": ["mcp"]})
        assert get_claude_code_mcp_configs(cwd, loc)[0] == {}

    def test_policy_denies_by_name(self, tmp_path, monkeypatch):
        cwd, loc = self._setup(tmp_path, monkeypatch, policy={"deniedMcpServers": [{"serverName": "u"}]})
        assert "u" not in get_claude_code_mcp_configs(cwd, loc)[0]


class TestProjectApproval:
    def test_statuses(self):
        assert get_project_mcp_server_status("my srv", {"disabledMcpjsonServers": ["my_srv"]}) == "rejected"
        assert get_project_mcp_server_status("s", {"enabledMcpjsonServers": ["s"]}) == "approved"
        assert get_project_mcp_server_status("s", {"enableAllProjectMcpServers": True}) == "approved"
        # Headless (`claude -p`): no approval popup, project servers load.
        assert get_project_mcp_server_status("s", {}) == "approved"


class TestPolicy:
    S = McpServerConfig(type=None, command="node", args=("srv.js",), scope="user")
    R = McpServerConfig(type="http", url="https://api.corp.com/mcp", scope="user")

    def test_no_lists_allow_everything(self):
        assert is_mcp_server_allowed_by_policy("x", self.S, {}, {}) is True

    def test_denylist_by_name_command_and_url(self):
        assert not is_mcp_server_allowed_by_policy("x", self.S, {"deniedMcpServers": [{"serverName": "x"}]}, {})
        assert not is_mcp_server_allowed_by_policy(
            "x", self.S, {"deniedMcpServers": [{"serverCommand": ["node", "srv.js"]}]}, {})
        assert is_mcp_server_allowed_by_policy(
            "x", self.S, {"deniedMcpServers": [{"serverCommand": ["node"]}]}, {})
        assert not is_mcp_server_allowed_by_policy(
            "x", self.R, {"deniedMcpServers": [{"serverUrl": "https://*.corp.com/*"}]}, {})

    def test_allowlist(self):
        assert not is_mcp_server_allowed_by_policy("x", self.S, {"allowedMcpServers": []}, {})
        assert is_mcp_server_allowed_by_policy("x", self.S, {"allowedMcpServers": [{"serverName": "x"}]}, {})
        # Command entries present: a stdio server must match one of them, names no longer count.
        allow = {"allowedMcpServers": [{"serverName": "x"}, {"serverCommand": ["other"]}]}
        assert not is_mcp_server_allowed_by_policy("x", self.S, allow, {})
        urls = {"allowedMcpServers": [{"serverUrl": "https://api.corp.com/*"}]}
        assert is_mcp_server_allowed_by_policy("r", self.R, urls, {})
        assert not is_mcp_server_allowed_by_policy("r", McpServerConfig(type="sse", url="https://evil", scope="u"),
                                                   urls, {})

    def test_managed_only_takes_the_allowlist_from_policy(self):
        merged = {"allowedMcpServers": [{"serverName": "x"}]}
        policy = {"allowManagedMcpServersOnly": True, "allowedMcpServers": [{"serverName": "y"}]}
        assert not is_mcp_server_allowed_by_policy("x", self.S, merged, policy)
        assert is_mcp_server_allowed_by_policy("y", self.S, merged, policy)


# ── mutation-gate pins (Phase 8) ────────────────────────────────────────────

class TestServerSchema:
    """`McpServerConfigSchema().safeParse`, rule by rule (`adheres_to_server_schema`)."""

    @pytest.mark.parametrize("raw", [
        {"command": "x"},
        {"type": "stdio", "command": "x", "args": ["a"], "env": {"K": "v"}},
        {"type": "sdk", "name": "n"},
        {"type": "sse", "url": "u", "headers": {"k": "v"}, "headersHelper": "h"},
        {"type": "http", "url": "u", "headers": {"k": "v"}, "headersHelper": "h"},
        {"type": "ws", "url": "u", "headers": {"k": "v"}, "headersHelper": "h"},
        {"type": "sse-ide", "url": "u", "ideName": "i"},
        {"type": "ws-ide", "url": "u", "ideName": "i"},
        {"type": "claudeai-proxy", "url": "u", "id": "i"},
    ])
    def test_accepted(self, raw):
        from app.loop.mcp.types import adheres_to_server_schema

        assert adheres_to_server_schema(raw) is True

    @pytest.mark.parametrize("raw", [
        "not a dict",
        {"type": "nope", "url": "u"},
        {"command": ""},
        {"command": 3},
        {"command": "x", "args": [1]},
        {"command": "x", "env": {"K": 1}},
        {"type": "stdio", "command": "x", "env": "K=v"},
        {"type": "sdk"},
        {"type": "sse"},
        {"type": "http", "url": "u", "headers": {"k": 1}},
        {"type": "ws", "url": "u", "headers": {"k": 1}},
        {"type": "sse", "url": "u", "headersHelper": 3},
        {"type": "http", "url": "u", "headersHelper": 3},
        {"type": "ws", "url": "u", "headersHelper": 3},
        {"type": "sse-ide", "url": "u"},
        {"type": "ws-ide", "url": "u"},
        {"type": "claudeai-proxy", "url": "u", "ideName": "i"},
    ])
    def test_rejected(self, raw):
        from app.loop.mcp.types import adheres_to_server_schema

        assert adheres_to_server_schema(raw) is False

    def test_the_headers_helper_is_kept(self):
        from app.loop.mcp.types import server_config_from_raw

        assert server_config_from_raw({"type": "http", "url": "u", "headersHelper": "h.sh"}, "user").headers_helper == (
            "h.sh")


class TestValidationErrors:
    """`parseMcpConfig` / `parseMcpConfigFromFilePath`'s error objects, field for field."""

    def test_no_servers_object(self):
        from app.loop.mcp.types import ValidationError

        assert parse_mcp_config({}, expand_vars=False, scope="user", file_path="/f") == (None, [ValidationError(
            path="mcpServers", message="Does not adhere to MCP server configuration schema", scope="user",
            severity="fatal", file="/f")])

    def test_an_invalid_server(self):
        from app.loop.mcp.types import ValidationError

        assert parse_mcp_config({"mcpServers": {"b": {}}}, expand_vars=False, scope="local", file_path="/f") == (
            None, [ValidationError(path="mcpServers.b", message="Does not adhere to MCP server configuration schema",
                                   scope="local", severity="fatal", file="/f")])

    def test_missing_variables(self, monkeypatch):
        from app.loop.mcp.types import ValidationError

        monkeypatch.delenv("NOPE_A", raising=False)
        monkeypatch.delenv("NOPE_B", raising=False)
        _, errors = parse_mcp_config({"mcpServers": {"s": {"command": "${NOPE_A}", "args": ["${NOPE_B}"]}}},
                                     expand_vars=True, scope="user", file_path="/f")
        assert errors == [ValidationError(
            path="mcpServers.s", message="Missing environment variables: NOPE_A, NOPE_B", scope="user",
            severity="warning", file="/f", suggestion="Set the following environment variables: NOPE_A, NOPE_B",
            server_name="s")]

    def test_file_errors(self, tmp_path):
        from app.loop.mcp.types import ValidationError

        none = tmp_path / "none.json"
        assert parse_mcp_config_from_file_path(none, expand_vars=True, scope="project") == (None, [ValidationError(
            path="", message=f"MCP config file not found: {none}", scope="project", severity="fatal", file=str(none),
            suggestion="Check that the file path is correct")])
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert parse_mcp_config_from_file_path(bad, expand_vars=True, scope="project") == (None, [ValidationError(
            path="", message="MCP config is not a valid JSON", scope="project", severity="fatal", file=str(bad),
            suggestion="Fix the JSON syntax errors in the file")])
        directory = tmp_path / "dir.json"
        directory.mkdir()
        _, (error,) = parse_mcp_config_from_file_path(directory, expand_vars=True, scope="user")
        assert error.message.startswith("Failed to read file: ") and error == ValidationError(
            path="", message=error.message, scope="user", severity="fatal", file=str(directory),
            suggestion="Check file permissions and ensure the file exists")

    def test_a_files_variables_expand_and_its_warnings_name_the_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BIN_X", "/bin/x")
        monkeypatch.delenv("NOPE_C", raising=False)
        path = _write(tmp_path / "m.json", {"mcpServers": {"s": {"command": "${BIN_X}", "args": ["${NOPE_C}"]}}})
        config, (warning,) = parse_mcp_config_from_file_path(path, expand_vars=True, scope="project")
        assert config is not None and config["s"].command == "/bin/x"
        assert warning.file == str(path)

    def test_only_stdio_and_remote_entries_expand(self, monkeypatch):
        monkeypatch.setenv("U", "https://x")
        config, _ = parse_mcp_config({"mcpServers": {
            "st": {"type": "stdio", "command": "${U}"},
            "se": {"type": "sse", "url": "${U}"},
            "ide": {"type": "sse-ide", "url": "${U}", "ideName": "i"},
        }}, expand_vars=True, scope="user")
        assert config is not None
        assert (config["st"].command, config["se"].url, config["ide"].url) == ("https://x", "https://x", "${U}")


class TestScopeExpansion:
    def test_project_and_user_scopes_expand_variables(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BIN_Y", "/bin/y")
        monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
        cwd = tmp_path / "proj"
        _write(cwd / ".mcp.json", {"mcpServers": {"p": {"command": "${BIN_Y}"}}})
        _write(tmp_path / "cfg" / ".tddagents.json", {"mcpServers": {"u": {"command": "${BIN_Y}"}}})
        project, _ = get_mcp_configs_by_scope("project", cwd, _locations(tmp_path))
        user, _ = get_mcp_configs_by_scope("user", cwd, _locations(tmp_path))
        assert project["p"].command == user["u"].command == "/bin/y"

    def test_the_project_walk_reaches_every_ancestor(self, tmp_path):
        cwd = tmp_path / "a" / "b" / "c"
        cwd.mkdir(parents=True)
        _write(tmp_path / ".mcp.json", {"mcpServers": {"far": STDIO}})
        servers, _ = get_mcp_configs_by_scope("project", cwd, _locations(tmp_path))
        assert "far" in servers

    def test_policy_applies_to_the_enterprise_file_by_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
        _write(tmp_path / "etc" / "managed-mcp.json", {"mcpServers": {"corp": STDIO, "other": STDIO}})
        _write(tmp_path / "etc" / "managed-settings.json", {"deniedMcpServers": [{"serverName": "other"}]})
        (tmp_path / "proj").mkdir()
        assert list(get_claude_code_mcp_configs(tmp_path / "proj", _locations(tmp_path))[0]) == ["corp"]


@pytest.mark.parametrize("kind", ["sse", "http", "ws", "sse-ide", "ws-ide", "claudeai-proxy"])
def test_every_remote_transport_is_matched_by_url(kind):
    """`getServerUrl`: every URL-bearing transport is subject to `serverUrl` policy entries."""
    config = McpServerConfig(type=kind, url="https://evil.example/mcp", scope="user")
    assert not is_mcp_server_allowed_by_policy("x", config, {"deniedMcpServers": [{"serverUrl": "https://evil*"}]}, {})


def test_ws_urls_expand_too(monkeypatch):
    monkeypatch.setenv("U", "wss://x")
    config, _ = parse_mcp_config({"mcpServers": {"w": {"type": "ws", "url": "${U}"}}}, expand_vars=True, scope="user")
    assert config is not None and config["w"].url == "wss://x"


def test_command_policy_applies_to_stdio_servers_only():
    """`getServerCommandArray`: a remote server has no command, whatever its entry says."""
    remote = McpServerConfig(type="http", url="https://u", command="node", scope="user")
    assert is_mcp_server_allowed_by_policy("x", remote, {"deniedMcpServers": [{"serverCommand": ["node"]}]}, {})
    stdio = McpServerConfig(type=None, command="node", args=("srv.js",), scope="user")
    allow = {"allowedMcpServers": [{"serverCommand": ["node", "srv.js"]}]}
    assert is_mcp_server_allowed_by_policy("x", stdio, allow, {})
