"""
Tests for pluginOperations, pluginCliCommands, the `plugin` CLI handlers and `python -m app.main
plugin …` (offline: directory marketplaces with local plugin sources).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app import main as main_module
from app.bootstrap.state import set_inline_plugins
from app.cli.handlers import plugins as handlers
from app.plugins import builtin_plugins
from app.services.plugins import plugin_cli_commands as cli
from app.services.plugins import plugin_operations as ops
from app.types.plugin import BuiltinPluginDefinition
from app.utils.plugins import headless_plugin_install as hpi
from app.utils.plugins import installed_plugins_manager as ipm
from app.utils.plugins import marketplace_manager as mm
from app.utils.plugins import plugin_autoupdate as pau
from app.utils.plugins import plugin_loader as pl
from app.utils.plugins.cache_utils import clear_all_caches
from app.utils.plugins.plugin_directories import set_use_cowork_plugins
from app.utils.settings.settings import SettingsLocations
from app.utils.settings.settings_cache import clear_plugin_settings_base


@pytest.fixture(autouse=True)
def _fresh_state():
    def reset() -> None:
        mm.clear_marketplaces_cache()
        ipm.clear_installed_plugins_cache()
        ipm.reset_migration_state()
        clear_all_caches()
        set_inline_plugins([])
        builtin_plugins.clear_builtin_plugins()
        clear_plugin_settings_base()
        set_use_cowork_plugins(False)

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


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def settings_of(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def user_file(locations: SettingsLocations) -> Path:
    return locations.user_config_dir / "settings.json"


def project_file(locations: SettingsLocations) -> Path:
    return locations.project_root / ".tddagents" / "settings.json"


@pytest.fixture
def team(tmp_path: Path, locations: SettingsLocations) -> Path:
    root = tmp_path / "mkt"
    write_json(root / ".tddagents-plugin" / "marketplace.json", {
        "name": "team", "owner": {"name": "O"},
        "plugins": [{"name": "alpha", "source": "./alpha", "description": "First"},
                    {"name": "beta", "source": "./beta", "dependencies": ["alpha"]}],
    })
    write_json(root / "alpha" / ".tddagents-plugin" / "plugin.json", {"name": "alpha", "version": "1.0.0"})
    write_json(root / "beta" / ".tddagents-plugin" / "plugin.json",
               {"name": "beta", "version": "0.1.0", "dependencies": ["alpha"]})
    run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    mm.clear_marketplaces_cache()
    return root


# ── install ───────────────────────────────────────────────────────────────────

def test_install_writes_the_setting_and_records_the_installation(team, locations):
    result = run(ops.install_plugin_op("alpha@team", "user", locations=locations))
    assert (result.success, result.plugin_id, result.plugin_name, result.scope) == (True, "alpha@team", "alpha",
                                                                                    "user")
    assert result.message == "Successfully installed plugin: alpha@team (scope: user)"
    assert settings_of(user_file(locations))["enabledPlugins"] == {"alpha@team": True}
    (installation,) = ipm.load_installed_plugins_v2()["plugins"]["alpha@team"]
    assert installation["scope"] == "user" and installation["version"] == "1.0.0"


def test_install_by_bare_name_searches_every_marketplace_and_resolves_dependencies(team, locations):
    result = run(ops.install_plugin_op("beta", "project", locations=locations))
    assert result.success and result.plugin_id == "beta@team"
    assert result.message.startswith("Successfully installed plugin: beta@team (scope: project)")
    assert settings_of(project_file(locations))["enabledPlugins"] == {"alpha@team": True, "beta@team": True}


def test_install_failures(team, locations):
    missing = run(ops.install_plugin_op("ghost", locations=locations))
    assert (missing.success, missing.message) == (False, 'Plugin "ghost" not found in any configured marketplace')
    elsewhere = run(ops.install_plugin_op("ghost@other", locations=locations))
    assert elsewhere.message == 'Plugin "ghost" not found in marketplace "other"'
    write_json(locations.managed_dir / "managed-settings.json", {"enabledPlugins": {"alpha@team": False}})
    blocked = run(ops.install_plugin_op("alpha@team", locations=locations))
    assert blocked.message == 'Plugin "alpha" is blocked by your organization\'s policy and cannot be installed'
    with pytest.raises(ValueError, match='Invalid scope "managed"'):
        run(ops.install_plugin_op("alpha@team", "managed", locations=locations))  # type: ignore[arg-type]


# ── enable / disable ──────────────────────────────────────────────────────────

def test_enable_and_disable_resolve_the_scope_from_settings(team, locations):
    run(ops.install_plugin_op("alpha@team", "project", locations=locations))
    again = run(ops.enable_plugin_op("alpha", locations=locations))
    assert (again.success, again.message) == (False, 'Plugin "alpha" is already enabled')
    off = run(ops.disable_plugin_op("alpha", locations=locations))
    assert (off.success, off.scope, off.message) == (
        True, "project", "Successfully disabled plugin: alpha (scope: project)")
    assert settings_of(project_file(locations))["enabledPlugins"]["alpha@team"] is False
    wrong = run(ops.enable_plugin_op("alpha", "user", locations=locations))
    assert wrong.message == ('Plugin "alpha" is installed at project scope, not user. Use --scope project or omit '
                             "--scope to auto-detect.")


def test_a_local_override_masks_a_project_enablement(team, locations):
    run(ops.install_plugin_op("alpha@team", "project", locations=locations))
    result = run(ops.disable_plugin_op("alpha", "local", locations=locations))
    assert (result.success, result.scope) == (True, "local")
    local = locations.project_root / ".tddagents" / "settings.local.json"
    assert settings_of(local)["enabledPlugins"] == {"alpha@team": False}
    assert settings_of(project_file(locations))["enabledPlugins"]["alpha@team"] is True


def test_disabling_a_dependency_names_its_dependents(team, locations):
    run(ops.install_plugin_op("beta@team", "user", locations=locations))
    result = run(ops.disable_plugin_op("alpha@team", locations=locations))
    assert result.success and result.reverse_dependents == ["beta"]
    assert result.message.endswith("required by beta")


def test_enable_edge_cases(team, locations):
    unknown = run(ops.enable_plugin_op("ghost", locations=locations))
    assert unknown.message == ('Plugin "ghost" not found in any editable settings scope. Use plugin@marketplace '
                               "format.")
    scoped = run(ops.enable_plugin_op("ghost", "user", locations=locations))
    assert scoped.message == 'Plugin "ghost" not found in settings. Use plugin@marketplace format.'
    fresh = run(ops.enable_plugin_op("ghost@team", locations=locations))
    assert (fresh.success, fresh.scope) == (True, "user")
    write_json(locations.managed_dir / "managed-settings.json", {"enabledPlugins": {"alpha@team": False}})
    blocked = run(ops.enable_plugin_op("alpha@team", locations=locations))
    assert blocked.message == ('Plugin "alpha@team" is blocked by your organization\'s policy and cannot be '
                               "enabled")


def test_a_built_in_plugin_is_set_at_user_scope(locations):
    builtin_plugins.register_builtin_plugin(BuiltinPluginDefinition(name="helper", description="d"))
    result = run(ops.disable_plugin_op("helper@builtin", locations=locations))
    assert (result.success, result.message, result.scope) == (True, "Successfully disabled built-in plugin: helper",
                                                              "user")
    assert settings_of(user_file(locations))["enabledPlugins"] == {"helper@builtin": False}


def test_disable_all(team, locations):
    assert run(ops.disable_all_plugins_op(locations=locations)).message == "No enabled plugins to disable"
    run(ops.install_plugin_op("beta@team", "user", locations=locations))
    result = run(ops.disable_all_plugins_op(locations=locations))
    assert (result.success, result.message) == (True, "Disabled 2 plugins")


# ── uninstall ─────────────────────────────────────────────────────────────────

def test_uninstall_removes_the_setting_the_record_and_the_data(team, locations):
    from app.utils.plugins.plugin_directories import get_plugin_data_dir

    run(ops.install_plugin_op("alpha@team", "user", locations=locations))
    data_dir = Path(get_plugin_data_dir("alpha@team"))
    result = run(ops.uninstall_plugin_op("alpha", "user", locations=locations))
    assert (result.success, result.message) == (True, "Successfully uninstalled plugin: alpha (scope: user)")
    assert "alpha@team" not in settings_of(user_file(locations)).get("enabledPlugins", {})
    assert "alpha@team" not in ipm.load_installed_plugins_v2()["plugins"]
    assert not data_dir.exists()


def test_uninstall_points_at_the_right_scope(team, locations):
    run(ops.install_plugin_op("alpha@team", "project", locations=locations))
    project = run(ops.uninstall_plugin_op("alpha@team", "user", locations=locations))
    assert project.message == ('Plugin "alpha@team" is enabled at project scope (.tddagents/settings.json, shared '
                               "with your team). To disable just for you: python -m app.main plugin disable "
                               "alpha@team --scope local")
    # A bare name not in this scope's settings resolves to the name itself, as upstream.
    bare = run(ops.uninstall_plugin_op("alpha", "user", locations=locations))
    assert bare.message == 'Plugin "alpha" is not installed in user scope. Use --scope to specify the correct scope.'
    run(ops.install_plugin_op("beta@team", "local", locations=locations))
    local = run(ops.uninstall_plugin_op("beta@team", "user", locations=locations))
    assert local.message == ('Plugin "beta@team" is installed in local scope, not user. Use --scope local to '
                             "uninstall.")
    ghost = run(ops.uninstall_plugin_op("ghost", locations=locations))
    assert ghost.message == 'Plugin "ghost" not found in installed plugins'


def test_uninstall_falls_back_to_the_installation_record_for_a_delisted_plugin(team, locations):
    run(ops.install_plugin_op("alpha@team", "user", locations=locations))
    write_json(team / ".tddagents-plugin" / "marketplace.json", {"name": "team", "owner": {"name": "O"},
                                                                 "plugins": []})
    mm.clear_marketplaces_cache()
    clear_all_caches()
    assert ops.resolve_delisted_plugin_id("alpha") == ("alpha@team", "alpha")
    result = run(ops.uninstall_plugin_op("alpha", "user", locations=locations))
    assert result.success and result.plugin_id == "alpha@team"


# ── update ────────────────────────────────────────────────────────────────────

def test_update_reports_current_then_copies_a_new_version(team, locations):
    run(ops.install_plugin_op("alpha@team", "user", locations=locations))
    current = run(ops.update_plugin_op("alpha@team", "user", locations=locations))
    assert (current.success, current.already_up_to_date, current.message) == (
        True, True, "alpha is already at the latest version (1.0.0).")
    write_json(team / "alpha" / ".tddagents-plugin" / "plugin.json", {"name": "alpha", "version": "2.0.0"})
    updated = run(ops.update_plugin_op("alpha@team", "user", locations=locations))
    assert (updated.old_version, updated.new_version) == ("1.0.0", "2.0.0")
    assert updated.message == 'Plugin "alpha" updated from 1.0.0 to 2.0.0 for scope user. Restart to apply changes.'
    (installation,) = ipm.load_installed_plugins_from_disk()["plugins"]["alpha@team"]
    assert installation["version"] == "2.0.0" and Path(installation["installPath"]).is_dir()


def test_update_failures(team, locations):
    assert run(ops.update_plugin_op("ghost@team", "user", locations=locations)).message == 'Plugin "ghost" not found'
    assert run(ops.update_plugin_op("alpha@team", "user", locations=locations)).message == (
        'Plugin "alpha" is not installed')
    run(ops.install_plugin_op("alpha@team", "user", locations=locations))
    project = run(ops.update_plugin_op("alpha@team", "project", locations=locations))
    assert project.message == f'Plugin "alpha" is not installed at scope project ({locations.project_root})'


def test_the_seams_default_to_the_ported_operations(team, locations, monkeypatch):
    run(ops.install_plugin_op("alpha@team", "user", locations=locations))
    write_json(team / "alpha" / ".tddagents-plugin" / "plugin.json", {"name": "alpha", "version": "3.0.0"})
    assert run(pau.update_plugins_for_marketplaces({"team"}, locations)) == ["alpha@team"]
    uninstall = hpi._default_uninstall_plugin_op(locations)
    result = run(uninstall("alpha@team", "user"))
    assert result.success


def test_installation_lookup_prefers_this_project(locations):
    assert ops.get_plugin_installation_from_v2("x@m", locations) == ("user", None)
    here = str(locations.project_root)
    entry: Any = {"version": "1", "installedAt": "t", "installPath": "/x"}
    ipm.add_installed_plugin("x@m", entry, "project", "/elsewhere")
    assert ops.get_plugin_installation_from_v2("x@m", locations) == ("project", "/elsewhere")
    ipm.add_installed_plugin("x@m", entry)
    assert ops.get_plugin_installation_from_v2("x@m", locations) == ("user", None)
    ipm.add_installed_plugin("x@m", entry, "local", here)
    assert ops.get_plugin_installation_from_v2("x@m", locations) == ("local", here)
    assert ops.get_project_path_for_scope("user", locations) is None and ops.is_installable_scope("local")
    assert not ops.is_plugin_enabled_at_project_scope("x@m", locations)


# ── the CLI ───────────────────────────────────────────────────────────────────

class Output:
    def __init__(self) -> None:
        self.out: list[str] = []
        self.err: list[str] = []

    def kwargs(self) -> dict[str, Any]:
        return {"out": self.out.append, "err": self.err.append}


def test_cli_commands_print_and_return_the_exit_code(team, locations):
    io = Output()
    assert run(cli.install_plugin("alpha@team", locations=locations, **io.kwargs())) == 0
    assert io.out == ['Installing plugin "alpha@team"...', "✔ Successfully installed plugin: alpha@team (scope: user)"]
    assert run(cli.enable_plugin("alpha", locations=locations, **io.kwargs())) == 1
    assert io.err == ['✘ Failed to enable plugin "alpha": Plugin "alpha" is already enabled']
    assert run(cli.disable_all_plugins(locations=locations, **io.kwargs())) == 0
    assert run(cli.disable_all_plugins(locations=locations, **io.kwargs())) == 0
    assert run(cli.update_plugin_cli("alpha@team", "user", locations=locations, **io.kwargs())) == 0
    assert run(cli.uninstall_plugin("alpha", keep_data=True, locations=locations, **io.kwargs())) == 0
    assert io.out[-1] == "✔ Successfully uninstalled plugin: alpha (scope: user)"
    assert cli.handle_plugin_command_error(RuntimeError("x"), "disable-all", None, io.err.append) == 1
    assert io.err[-1] == "✘ Failed to disable all plugins: x"


def test_handlers_validate_their_options(locations):
    io = Output()
    assert run(handlers.plugin_install_handler("a", locations=locations, scope="managed", **io.kwargs())) == 1
    assert run(handlers.plugin_uninstall_handler("a", locations=locations, scope="x", **io.kwargs())) == 1
    assert run(handlers.plugin_enable_handler("a", locations=locations, scope="x", **io.kwargs())) == 1
    assert run(handlers.plugin_disable_handler("a", locations=locations, all_plugins=True, **io.kwargs())) == 1
    assert run(handlers.plugin_disable_handler(None, locations=locations, **io.kwargs())) == 1
    assert run(handlers.plugin_disable_handler(None, locations=locations, all_plugins=True, scope="user",
                                               **io.kwargs())) == 1
    assert run(handlers.plugin_update_handler("a", locations=locations, scope="x", **io.kwargs())) == 1
    assert run(handlers.plugin_install_handler("a", locations=locations, scope="project", cowork=True,
                                               **io.kwargs())) == 1
    assert io.err == [
        "Invalid scope: managed. Must be one of: user, project, local.",
        "Invalid scope: x. Must be one of: user, project, local.",
        'Invalid scope "x". Valid scopes: user, project, local',
        "Cannot use --all with a specific plugin",
        "Please specify a plugin name or use --all to disable all plugins",
        "Cannot use --scope with --all",
        'Invalid scope "x". Valid scopes: user, project, local, managed',
        "--cowork can only be used with user scope",
    ]


def test_plugin_list_text_and_json(team, tmp_path, locations):
    io = Output()
    assert run(handlers.plugin_list_handler(locations=locations, out=io.out.append)) == 0
    assert io.out == ["No plugins installed. Use `python -m app.main plugin install` to install a plugin."]
    run(ops.install_plugin_op("alpha@team", "user", locations=locations))
    inline_root = tmp_path / "solo"
    write_json(inline_root / ".tddagents-plugin" / "plugin.json", {"name": "solo", "version": "9"})
    set_inline_plugins([str(inline_root), str(tmp_path / "missing")])
    pl.clear_plugin_cache()
    io = Output()
    assert run(handlers.plugin_list_handler(locations=locations, out=io.out.append)) == 0
    text = "\n".join(io.out)
    assert "  ❯ alpha@team\n    Version: 1.0.0\n    Scope: user\n    Status: ✔ enabled" in text
    assert "Session-only plugins (--plugin-dir):" in text and "  ❯ solo@inline" in text
    assert "inline[1]: ✘ Path not found" in text
    io = Output()
    assert run(handlers.plugin_list_handler(locations=locations, as_json=True, available=True,
                                            out=io.out.append)) == 0
    listed = json.loads(io.out[0])
    assert [p["id"] for p in listed["installed"]] == ["alpha@team", "solo@inline", "inline[1]"]
    assert listed["installed"][0]["enabled"] is True and listed["installed"][1]["scope"] == "session"
    assert [p["pluginId"] for p in listed["available"]] == ["beta@team"]


def test_marketplace_handlers(tmp_path, locations):
    io = Output()
    assert run(handlers.marketplace_list_handler(**io.kwargs())) == 0 and io.out == ["No marketplaces configured"]
    root = tmp_path / "mkt2"
    write_json(root / ".tddagents-plugin" / "marketplace.json", {"name": "two", "owner": {"name": "O"},
                                                                 "plugins": []})
    assert run(handlers.marketplace_add_handler(str(root), locations=locations, scope="project", **io.kwargs())) == 0
    assert io.out[-1] == "✔ Successfully added marketplace: two (declared in project settings)"
    assert "two" in settings_of(project_file(locations))["extraKnownMarketplaces"]
    io = Output()
    assert run(handlers.marketplace_list_handler(as_json=True, **io.kwargs())) == 0
    (entry,) = json.loads(io.out[0])
    assert (entry["name"], entry["source"], entry["path"]) == ("two", "directory", str(root))
    io = Output()
    assert run(handlers.marketplace_list_handler(**io.kwargs())) == 0
    assert io.out[:3] == ["Configured marketplaces:\n", "  ❯ two", f"    Source: Directory ({root})"]
    assert run(handlers.marketplace_update_handler("two", **io.kwargs())) == 0
    assert run(handlers.marketplace_update_handler(None, **io.kwargs())) == 0
    assert run(handlers.marketplace_remove_handler("two", locations=locations, **io.kwargs())) == 0
    assert io.out[-1] == "✔ Successfully removed marketplace: two"
    assert run(handlers.marketplace_add_handler("???", locations=locations, **io.kwargs())) == 1
    assert run(handlers.marketplace_add_handler(str(root), locations=locations, scope="x", **io.kwargs())) == 1
    assert run(handlers.marketplace_add_handler(str(root), locations=locations, sparse=["a"], **io.kwargs())) == 1
    assert run(handlers.marketplace_remove_handler("nope", locations=locations, **io.kwargs())) == 1
    assert io.err[-1].startswith("✘ Failed to remove marketplace: ")


def test_plugin_validate(tmp_path):
    root = tmp_path / "v"
    write_json(root / ".tddagents-plugin" / "plugin.json", {"name": "v"})
    io = Output()
    assert run(handlers.plugin_validate_handler(str(root / ".tddagents-plugin" / "plugin.json"), **io.kwargs())) == 0
    assert io.out[0].startswith("Validating plugin manifest: ") and io.out[-1].startswith("✔ Validation passed")
    write_json(root / ".tddagents-plugin" / "plugin.json", {"name": 3})
    io = Output()
    assert run(handlers.plugin_validate_handler(str(root / ".tddagents-plugin" / "plugin.json"), **io.kwargs())) == 1
    assert io.out[-1] == "✘ Validation failed"


def test_main_dispatches_the_plugin_subcommand(team, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module, "plugin_cli_locations", lambda thread_id=None: SettingsLocations(
        project_root=tmp_path / "project", home=tmp_path / "home", managed_dir=tmp_path / "managed"))
    assert main_module.main(["plugin", "install", "alpha@team"]) == 0
    assert main_module.main(["plugins", "list", "--json"]) == 0
    assert main_module.main(["plugin", "marketplace", "list"]) == 0
    assert main_module.main(["plugin", "disable", "--all"]) == 0
    assert main_module.main(["plugin", "enable", "alpha@team", "-s", "user"]) == 0
    assert main_module.main(["plugin", "update", "alpha@team"]) == 0
    assert main_module.main(["plugin", "rm", "alpha", "--keep-data"]) == 0
    assert main_module.main(["plugin", "validate", str(team)]) == 0
    assert main_module.main(["plugin", "marketplace", "rm", "team"]) == 0
    assert main_module.main(["plugin", "marketplace", "update"]) == 0
    out = capsys.readouterr().out
    assert "✔ Successfully installed plugin: alpha@team (scope: user)" in out
    assert "✔ Successfully removed marketplace: team" in out
    with pytest.raises(SystemExit):
        main_module.main(["plugin"])
    assert main_module.plugin_cli_locations is not None


def test_the_cli_project_is_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from app.main import plugin_cli_locations as real

    assert real().project_root == tmp_path


def test_thread_id_targets_that_sessions_project_folder(tmp_path, monkeypatch, capsys):
    from app.main import plugin_cli_locations as real

    monkeypatch.chdir(tmp_path)
    workspaces = tmp_path / "workspaces"
    monkeypatch.setenv("TDDAGENTS_WORKSPACES_DIR", str(workspaces))
    project = workspaces / "tdd-a" / "project"
    project.mkdir(parents=True)
    assert real("tdd-a").project_root == project.resolve()
    # A forked session's project is a link to its source's folder.
    (workspaces / "tdd-fork").mkdir()
    (workspaces / "tdd-fork" / "project").symlink_to(project)
    assert real("tdd-fork").project_root == project.resolve()
    with pytest.raises(LookupError, match="^No session found with thread id: tdd-none$"):
        real("tdd-none")
    assert main_module.main(["plugin", "--thread-id", "tdd-none", "list"]) == 1
    assert capsys.readouterr().err.strip() == "No session found with thread id: tdd-none"


def test_main_passes_the_thread_id_to_the_locations(tmp_path, monkeypatch):
    seen: list[Any] = []

    def locations(thread_id: Any = None) -> SettingsLocations:
        seen.append(thread_id)
        return SettingsLocations(project_root=tmp_path / "p", home=tmp_path / "home", managed_dir=tmp_path / "m")

    monkeypatch.setattr(main_module, "plugin_cli_locations", locations)
    assert main_module.main(["plugin", "--thread-id", "tdd-x", "list"]) == 0
    assert main_module.main(["plugin", "list"]) == 0
    assert seen == ["tdd-x", None]
