"""
Tests for the plugin component integrations: commands and skills (loadPluginCommands), agents
(loadPluginAgents), hooks (loadPluginHooks and the plugin hook execution), MCP servers
(mcpPluginIntegration and getClaudeCodeMcpConfigs' plugin half), and refreshActivePlugins with
them. Offline: inline (`--plugin-dir`) plugins on disk.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from app.bootstrap.state import set_inline_plugins
from app.schemas.hooks import CommandHook, HookMatcher
from app.services.mcp import config as mcp_config
from app.services.mcp.types import McpServerConfig
from app.skills.load_skills_dir import SkillCommand, SkillRegistry
from app.state.app_state_store import AppState, AppStateStore
from app.tools.agent_tool.load_agents_dir import (
    AgentFrontmatterError,
    get_agent_definitions_with_overrides,
)
from app.types.plugin import LoadedPlugin
from app.utils.hooks.hooks import (
    HookDispatcher,
    PluginHookContext,
    exec_command_hook,
    get_hooks_config,
    get_matching_hooks,
    plugin_hook_command,
)
from app.utils.hooks.hooks_config_snapshot import HooksConfigSnapshot
from app.utils.plugins import installed_plugins_manager as ipm
from app.utils.plugins import load_plugin_agents as lpa
from app.utils.plugins import load_plugin_commands as lpc
from app.utils.plugins import load_plugin_hooks as lph
from app.utils.plugins import marketplace_manager as mm
from app.utils.plugins import mcp_plugin_integration as mpi
from app.utils.plugins import plugin_loader as pl
from app.utils.plugins.cache_utils import clear_all_caches
from app.utils.plugins.refresh import refresh_active_plugins
from app.utils.session_start import ensure_plugin_hooks_loaded
from app.utils.settings.settings import SettingsLocations
from app.utils.settings.settings_cache import clear_plugin_settings_base


@pytest.fixture(autouse=True)
def _fresh_state():
    def reset() -> None:
        mm.clear_marketplaces_cache()
        ipm.clear_installed_plugins_cache()
        clear_all_caches()
        set_inline_plugins([])
        clear_plugin_settings_base()

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


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_json(path: Path, data: Any) -> Path:
    return write(path, json.dumps(data))


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def make_toolkit(root: Path, manifest: dict[str, Any] | None = None) -> Path:
    """An inline plugin with one of everything."""
    write_json(root / ".tddagents-plugin" / "plugin.json", manifest or {"name": "kit", "version": "1.2.0"})
    write(root / "commands" / "hello.md", "---\ndescription: Say hello\nargument-hint: <who>\n---\n"
                                          "Hello $ARGUMENTS from ${CLAUDE_PLUGIN_ROOT}")
    write(root / "commands" / "ops" / "deploy.md", "# Deploy now\nShip it.")
    write(root / "commands" / "bundle" / "SKILL.md", "---\ndescription: A bundled skill\n---\nBundle body")
    write(root / "commands" / "bundle" / "notes.md", "ignored beside SKILL.md")
    write(root / "skills" / "greet" / "SKILL.md", "---\ndescription: Greet\nname: Greeter\n---\n"
                                                  "In ${TDDAGENTS_SKILL_DIR} for ${TDDAGENTS_SESSION_ID}: $ARGUMENTS")
    write(root / "agents" / "helper.md", "---\ndescription: Helps\ntools: ReadFile\nmodel: inherit\n"
                                         "permissionMode: bypassPermissions\n---\nRoot ${CLAUDE_PLUGIN_ROOT}")
    write(root / "agents" / "team" / "reviewer.md", "---\nname: critic\nwhen-to-use: Reviews\nphase: red\n---\nR")
    write_json(root / "hooks" / "hooks.json", {"hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "echo ${CLAUDE_PLUGIN_ROOT}"}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}],
    }})
    write_json(root / ".mcp.json", {"mcpServers": {
        "files": {"command": "${CLAUDE_PLUGIN_ROOT}/srv", "args": ["--data", "${CLAUDE_PLUGIN_DATA}"],
                  "env": {"TOKEN": "${KIT_TEST_TOKEN:-none}"}},
    }})
    return root


def inline(*roots: Path) -> None:
    set_inline_plugins([str(r) for r in roots])
    pl.clear_plugin_cache()


# ── commands and skills ───────────────────────────────────────────────────────

def test_plugin_commands_are_namespaced_and_a_skill_dir_contributes_only_its_skill(tmp_path, locations):
    root = make_toolkit(tmp_path / "kit")
    inline(root)
    commands = run(lpc.get_plugin_commands(locations))
    by_name = {c.name: c for c in commands}
    assert sorted(by_name) == ["kit:bundle", "kit:hello", "kit:ops:deploy"]
    hello = by_name["kit:hello"]
    assert (hello.source, hello.loaded_from, hello.plugin_name, hello.argument_hint) == ("plugin", None, "kit",
                                                                                         "<who>")
    assert hello.has_user_specified_description and hello.progress_message == "running"
    assert hello.prompt_for_command("Ana") == f"Hello Ana from {root}"
    deploy = by_name["kit:ops:deploy"]
    assert (deploy.description, deploy.has_user_specified_description) == ("Deploy now", False)
    bundle = by_name["kit:bundle"]
    assert (bundle.loaded_from, bundle.progress_message, bundle.is_skill_mode) == ("plugin", "loading", False)
    assert bundle.prompt_for_command("") == "Bundle body"


def test_plugin_skills_carry_their_base_directory_and_placeholders(tmp_path, locations):
    root = make_toolkit(tmp_path / "kit")
    inline(root)
    (skill,) = run(lpc.get_plugin_skills(locations))
    skill_dir = str(root / "skills" / "greet")
    assert (skill.name, skill.user_facing_name(), skill.loaded_from) == ("kit:greet", "Greeter", "plugin")
    assert skill.prompt_for_command("now", session_id="s-1") == (
        f"Base directory for this skill: {skill_dir}\n\nIn {skill_dir} for s-1: now")


def test_skills_path_holding_skill_md_itself_is_one_skill(tmp_path, locations):
    root = tmp_path / "solo"
    write_json(root / ".tddagents-plugin" / "plugin.json", {"name": "solo", "skills": "./one"})
    write(root / "one" / "SKILL.md", "single")
    inline(root)
    assert [s.name for s in run(lpc.get_plugin_skills(locations))] == ["solo:one"]


def test_command_metadata_files_and_inline_content(tmp_path, locations):
    root = tmp_path / "meta"
    write_json(root / ".tddagents-plugin" / "plugin.json", {"name": "meta", "commands": {
        "about": {"source": "./docs/about.md", "description": "From metadata", "argumentHint": "[x]",
                  "allowedTools": ["Bash(git:*)", "ReadFile"]},
        "quick": {"content": "---\ndescription: Inline one\n---\nInline body", "model": "haiku"},
    }})
    write(root / "docs" / "about.md", "---\ndescription: From file\n---\nAbout body")
    inline(root)
    by_name = {c.name: c for c in run(lpc.get_plugin_commands(locations))}
    about = by_name["meta:about"]
    assert (about.description, about.argument_hint, about.allowed_tools) == (
        "From metadata", "[x]", ("Bash(git:*)", "ReadFile"))
    quick = by_name["meta:quick"]
    assert quick.description == "Inline one" and quick.prompt_for_command("") == "Inline body"
    assert quick.model is not None and "haiku" in quick.model


def test_plugin_user_config_reaches_the_prompt_but_sensitive_values_do_not(tmp_path, locations):
    root = tmp_path / "cfg"
    write_json(root / ".tddagents-plugin" / "plugin.json", {"name": "cfg", "userConfig": {
        "team": {"type": "string", "title": "Team", "description": "d"},
        "key": {"type": "string", "title": "Key", "description": "d", "sensitive": True},
    }})
    write(root / "commands" / "who.md", "${user_config.team} / ${user_config.key} / ${user_config.other}")
    inline(root)
    write_json(locations.user_config_dir / "settings.json",
               {"pluginConfigs": {"cfg@inline": {"options": {"team": "blue"}}}})
    (who,) = run(lpc.get_plugin_commands(locations))
    assert who.prompt_for_command("") == (
        "blue / [sensitive option 'key' not available in skill content] / ${user_config.other}")


def test_the_registry_lists_plugin_commands_after_skills_and_filters_the_skill_tool():
    def skill(name: str, **fields: Any) -> SkillCommand:
        base: dict[str, Any] = {"description": "d", "markdown_content": "", "source": "userSettings",
                                "loaded_from": "skills", "skill_root": None}
        base.update(fields)
        return SkillCommand(name=name, **base)

    registry = SkillRegistry([skill("mine")])
    registry.set_plugin_commands([
        lpc.PluginSkillCommand(name="p:described", description="d", markdown_content="", source="plugin",
                               loaded_from=None, skill_root=None, has_user_specified_description=True),
        lpc.PluginSkillCommand(name="p:bare", description="d", markdown_content="", source="plugin",
                               loaded_from=None, skill_root=None),
        lpc.PluginSkillCommand(name="p:when", description="d", markdown_content="", source="plugin",
                               loaded_from="plugin", skill_root=None, when_to_use="sometimes"),
    ])
    assert [c.name for c in registry.commands()] == ["mine", "p:described", "p:bare", "p:when"]
    assert [c.name for c in registry.get_skill_tool_commands()] == ["mine", "p:described", "p:when"]
    assert [c.name for c in registry.plugin_commands()] == ["p:described", "p:bare", "p:when"]


def test_the_command_memo_holds_until_cleared(tmp_path, locations):
    root = make_toolkit(tmp_path / "kit")
    inline(root)
    first = run(lpc.get_plugin_commands(locations))
    write(root / "commands" / "later.md", "later")
    assert len(run(lpc.get_plugin_commands(locations))) == len(first)
    clear_all_caches()
    assert "kit:later" in [c.name for c in run(lpc.get_plugin_commands(locations))]


# ── agents ────────────────────────────────────────────────────────────────────

def test_plugin_agents_are_namespaced_and_cannot_escalate(tmp_path, locations):
    root = make_toolkit(tmp_path / "kit")
    inline(root)
    agents = {a.name: a for a in run(lpa.load_plugin_agents(locations))}
    assert sorted(agents) == ["kit:helper", "kit:team:critic"]
    helper = agents["kit:helper"]
    assert (helper.description, helper.tools, helper.model, helper.source, helper.plugin) == (
        "Helps", ("ReadFile",), "inherit", "plugin", "kit@inline")
    assert helper.permission_mode is None and helper.prompt == f"Root {root}"
    critic = agents["kit:team:critic"]
    assert (critic.description, str(critic.phase)) == ("Reviews", "RED")


def test_plugin_agents_sit_between_built_ins_and_the_users(tmp_path, locations):
    root = make_toolkit(tmp_path / "kit")
    inline(root)
    plugin_agents = run(lpa.load_plugin_agents(locations))
    write(locations.user_config_dir / "agents" / "mine.md", "---\nname: kit:helper\ndescription: Mine\n---\nM")
    definitions = get_agent_definitions_with_overrides(locations.project_root, locations.home,
                                                       plugin_agents=plugin_agents)
    assert definitions["kit:helper"].description == "Mine"
    assert definitions["kit:team:critic"].source == "plugin"


def test_an_invalid_phase_in_a_plugin_agent_fails_loudly(tmp_path, locations):
    root = tmp_path / "bad"
    write_json(root / ".tddagents-plugin" / "plugin.json", {"name": "bad"})
    write(root / "agents" / "x.md", "---\ndescription: X\nphase: purple\n---\nX")
    inline(root)
    with pytest.raises(AgentFrontmatterError, match="purple"):
        run(lpa.load_plugin_agents(locations))


def test_an_agent_without_description_falls_back_to_the_plugin(tmp_path, locations):
    root = tmp_path / "plain"
    write_json(root / ".tddagents-plugin" / "plugin.json", {"name": "plain", "agents": ["./extra.md"]})
    write(root / "extra.md", "Body")
    inline(root)
    (agent,) = run(lpa.load_plugin_agents(locations))
    assert (agent.name, agent.description) == ("plain:extra", "Agent from plain plugin")


# ── hooks ─────────────────────────────────────────────────────────────────────

def dispatcher_for(locations: SettingsLocations, tmp_path: Path, snapshot: HooksConfigSnapshot | None = None
                   ) -> HookDispatcher:
    return HookDispatcher(snapshot=snapshot or HooksConfigSnapshot(), session_id="s", cwd=str(tmp_path),
                          project_dir=str(tmp_path), settings_locations=locations)


def test_plugin_hooks_register_with_their_plugin_and_keep_sdk_callbacks(tmp_path, locations):
    root = make_toolkit(tmp_path / "kit")
    inline(root)
    dispatcher = dispatcher_for(locations, tmp_path)
    sdk = HookMatcher(hooks=(CommandHook(command="sdk"),))
    dispatcher.register_hook_callbacks({"Stop": [sdk]})
    run(lph.load_plugin_hooks(dispatcher))
    stop = dispatcher.registered_matchers["Stop"]
    assert stop[0] is sdk
    assert [(m.plugin_root, m.plugin_name, m.plugin_id) for m in stop[1:]] == [(str(root), "kit", "kit@inline")]
    assert dispatcher.registered_matchers["PreToolUse"][0].matcher == "Bash"
    # Memoized until the cache is cleared: a second load changes nothing.
    dispatcher.registered_matchers["Stop"] = [sdk]
    run(lph.load_plugin_hooks(dispatcher))
    assert dispatcher.registered_matchers["Stop"] == [sdk]
    lph.clear_plugin_hook_cache()
    run(lph.load_plugin_hooks(dispatcher))
    assert len(dispatcher.registered_matchers["Stop"]) == 2


def test_pruning_drops_the_hooks_of_plugins_no_longer_enabled(tmp_path, locations):
    root = make_toolkit(tmp_path / "kit")
    inline(root)
    dispatcher = dispatcher_for(locations, tmp_path)
    sdk = HookMatcher(hooks=(CommandHook(command="sdk"),))
    dispatcher.register_hook_callbacks({"Stop": [sdk]})
    run(lph.load_plugin_hooks(dispatcher))
    inline()
    run(lph.prune_removed_plugin_hooks())
    assert dispatcher.registered_matchers == {"Stop": [sdk]}


def test_managed_only_skips_plugin_hooks_but_not_sdk_callbacks():
    plugin = HookMatcher(hooks=(CommandHook(command="p"),), plugin_root="/p", plugin_name="p", plugin_id="p@m")
    sdk = HookMatcher(hooks=(CommandHook(command="s"),))
    registered = {"Stop": [plugin, sdk]}
    assert get_hooks_config(HooksConfigSnapshot(), "Stop", None, registered) == [plugin, sdk]
    assert get_hooks_config(HooksConfigSnapshot(managed_only=True), "Stop", None, registered) == [sdk]


def test_matched_plugin_hooks_carry_their_source_and_are_not_deduplicated_across_plugins():
    template = "${CLAUDE_PLUGIN_ROOT}/hook.sh"
    a = HookMatcher(hooks=(CommandHook(command=template),), plugin_root="/a", plugin_name="a", plugin_id="a@m")
    b = HookMatcher(hooks=(CommandHook(command=template),), plugin_root="/b", plugin_name="b", plugin_id="b@m")
    matched = get_matching_hooks(HooksConfigSnapshot(), "Stop", {"hook_event_name": "Stop"},
                                 registered_matchers={"Stop": [a, b]})
    assert [(m.hook_source, m.plugin_root, m.plugin_id) for m in matched] == [
        ("plugin:a", "/a", "a@m"), ("plugin:b", "/b", "b@m")]


def test_a_plugin_hook_command_gets_its_variables_and_options(tmp_path, locations):
    root = tmp_path / "p"
    root.mkdir()
    write_json(locations.user_config_dir / "settings.json",
               {"pluginConfigs": {"p@m": {"options": {"level": 3, "loud-mode": True}}}})
    hook = CommandHook(command="run ${CLAUDE_PLUGIN_ROOT} ${CLAUDE_PLUGIN_DATA} ${user_config.level}")
    resolved, env = run(plugin_hook_command(hook, PluginHookContext(str(root), "p@m", locations)))
    data_dir = env["CLAUDE_PLUGIN_DATA"]
    assert resolved.command == f"run {root} {data_dir} 3"
    assert (env["CLAUDE_PLUGIN_ROOT"], env["CLAUDE_PLUGIN_OPTION_LEVEL"], env["CLAUDE_PLUGIN_OPTION_LOUD_MODE"]) == (
        str(root), "3", "true")
    with pytest.raises(RuntimeError, match="Plugin directory does not exist"):
        run(plugin_hook_command(hook, PluginHookContext(str(tmp_path / "gone"), "p@m", locations)))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell")
def test_a_plugin_hook_runs_with_its_environment(tmp_path, locations):
    root = tmp_path / "p"
    root.mkdir()
    hook = CommandHook(command='echo "$CLAUDE_PLUGIN_ROOT|${CLAUDE_PLUGIN_ROOT}"')
    result = run(exec_command_hook(hook, "Stop", "Stop", "{}", None, cwd=str(tmp_path), project_dir=str(tmp_path),
                                   plugin=PluginHookContext(str(root), None, locations)))
    assert result.stdout.strip() == f"{root}|{root}"


def test_session_start_loads_plugin_hooks_unless_managed_only(tmp_path, locations):
    root = make_toolkit(tmp_path / "kit")
    inline(root)
    dispatcher = dispatcher_for(locations, tmp_path)
    run(ensure_plugin_hooks_loaded(dispatcher, "startup"))
    assert dispatcher.registered_matchers["Stop"][0].plugin_name == "kit"
    managed = dispatcher_for(locations, tmp_path, HooksConfigSnapshot(managed_only=True))
    run(ensure_plugin_hooks_loaded(managed, "startup"))
    assert managed.registered_matchers == {}
    run(ensure_plugin_hooks_loaded(None, "startup"))


def test_the_plugin_affecting_settings_snapshot_is_key_sorted(locations):
    write_json(locations.user_config_dir / "settings.json", {"enabledPlugins": {"b@m": True, "a@m": False}})
    snapshot = json.loads(lph.get_plugin_affecting_settings_snapshot(locations))
    assert list(snapshot["enabledPlugins"]) == ["a@m", "b@m"]
    assert snapshot["strictKnownMarketplaces"] == [] and snapshot["blockedMarketplaces"] == []


# ── MCP servers ───────────────────────────────────────────────────────────────

def test_plugin_mcp_servers_are_resolved_and_scoped(tmp_path, locations, monkeypatch):
    monkeypatch.delenv("KIT_TEST_TOKEN", raising=False)
    root = make_toolkit(tmp_path / "kit")
    inline(root)
    (plugin,) = run(pl.load_all_plugins(locations)).enabled
    errors: list[Any] = []
    servers = run(mpi.get_plugin_mcp_servers(plugin, errors, locations))
    config = servers["plugin:kit:files"]
    data_dir = config.env["CLAUDE_PLUGIN_DATA"]
    assert (config.command, config.args, config.scope, config.plugin_source) == (
        f"{root}/srv", ("--data", data_dir), "dynamic", "kit@inline")
    assert (config.env["CLAUDE_PLUGIN_ROOT"], config.env["TOKEN"]) == (str(root), "none")
    assert errors == []


def test_missing_environment_variables_become_an_mcp_error(tmp_path, monkeypatch):
    monkeypatch.delenv("NOPE_VAR", raising=False)
    plugin = LoadedPlugin(name="p", manifest={"name": "p"}, path=str(tmp_path), source="p@m", repository="p@m")
    errors: list[Any] = []
    resolved = mpi.resolve_plugin_mcp_environment({"type": "http", "url": "https://x/${NOPE_VAR}",
                                                   "headers": {"A": "${NOPE_VAR}"}}, plugin, None, errors, "p", "s")
    assert resolved["url"] == "https://x/${NOPE_VAR}"
    assert errors == [{"type": "mcp-config-invalid", "source": "plugin:p", "plugin": "p", "serverName": "s",
                       "validationError": "Missing environment variables: NOPE_VAR"}]


def test_manifest_specs_layer_over_the_default_file(tmp_path, locations):
    root = tmp_path / "multi"
    write_json(root / ".tddagents-plugin" / "plugin.json", {"name": "multi", "mcpServers": [
        "./more.json", {"inline": {"type": "http", "url": "https://inline"}}]})
    write_json(root / ".mcp.json", {"mcpServers": {"a": {"command": "base"}, "bad": {"command": ""}}})
    write_json(root / "more.json", {"a": {"command": "override"}})
    inline(root)
    (plugin,) = run(pl.load_all_plugins(locations)).enabled
    servers = run(mpi.load_plugin_mcp_servers(plugin, [], locations))
    assert servers == {"a": {"command": "override"}, "inline": {"type": "http", "url": "https://inline"}}


def test_dedup_keeps_manual_servers_and_the_first_plugin():
    def stdio(command: str, scope: str = "dynamic") -> McpServerConfig:
        return McpServerConfig(type=None, scope=scope, command=command)

    servers, suppressed = mcp_config.dedup_plugin_mcp_servers(
        {"plugin:a:x": stdio("same"), "plugin:b:x": stdio("same"), "plugin:c:y": stdio("own"),
         "plugin:d:z": stdio("manual"), "plugin:e:sdk": McpServerConfig(type="sdk", scope="dynamic")},
        {"mine": stdio("manual", "user")})
    assert list(servers) == ["plugin:a:x", "plugin:c:y", "plugin:e:sdk"]
    assert suppressed == [("plugin:b:x", "plugin:a:x"), ("plugin:d:z", "mine")]
    proxy = "https://h/v2/ccr-sessions/1?mcp_url=https%3A%2F%2Freal"
    assert mcp_config.unwrap_ccr_proxy_url(proxy) == "https://real"
    assert mcp_config.unwrap_ccr_proxy_url("https://plain") == "https://plain"


def test_claude_code_configs_merge_plugin_servers_below_the_users(tmp_path, locations, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    root = make_toolkit(tmp_path / "kit")
    inline(root)
    servers, _ = run(mcp_config.get_claude_code_mcp_configs(
        locations.project_root, locations, is_non_interactive_session=True))
    assert list(servers) == ["plugin:kit:files"]
    write_json(locations.managed_dir / "managed-settings.json", {"strictPluginOnlyCustomization": ["mcp"]})
    write_json(locations.project_root / ".mcp.json", {"mcpServers": {"proj": {"command": "p"}}})
    servers, _ = run(mcp_config.get_claude_code_mcp_configs(
        locations.project_root, locations, is_non_interactive_session=True))
    assert list(servers) == ["plugin:kit:files"]


# ── refreshActivePlugins with the components ──────────────────────────────────

def test_refresh_loads_every_component(tmp_path, locations):
    root = make_toolkit(tmp_path / "kit")
    inline(root)
    store = AppStateStore(state=AppState())
    dispatcher = dispatcher_for(locations, tmp_path)
    result = run(refresh_active_plugins(store.update, locations, hook_dispatcher=dispatcher))
    assert (result.enabled_count, result.command_count, result.hook_count, result.mcp_count, result.lsp_count) == (
        1, 3, 2, 1, 0)
    assert {"kit:helper", "kit:team:critic"} <= set(result.agent_definitions)
    assert [c.name for c in store.get().plugins.commands] == [c.name for c in result.plugin_commands]
    assert dispatcher.registered_matchers["Stop"][0].plugin_id == "kit@inline"


def test_clearing_the_caches_prunes_hooks_in_the_background(tmp_path, locations):
    root = make_toolkit(tmp_path / "kit")
    inline(root)
    dispatcher = dispatcher_for(locations, tmp_path)

    async def scenario() -> None:
        await lph.load_plugin_hooks(dispatcher)
        inline()
        clear_all_caches()
        await asyncio.gather(*list(lph._background))

    run(scenario())
    assert dispatcher.registered_matchers == {}


def test_prune_without_a_running_loop_is_skipped(tmp_path, locations):
    root = make_toolkit(tmp_path / "kit")
    inline(root)
    dispatcher = dispatcher_for(locations, tmp_path)
    run(lph.load_plugin_hooks(dispatcher))
    clear_all_caches()  # no loop: the prune is skipped, the hooks stay until the next load
    assert dispatcher.registered_matchers["Stop"][0].plugin_name == "kit"
    assert os.path.isdir(root)
