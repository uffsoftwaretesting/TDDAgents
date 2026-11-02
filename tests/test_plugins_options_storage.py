"""Plugin option storage and substitution (`app/utils/plugins/plugin_options_storage.py`) and `validate_user_config`."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

from app.types.plugin import LoadedPlugin
from app.utils.plugins import plugin_options_storage as pos
from app.utils.plugins.mcpb_handler import validate_user_config
from app.utils.plugins.plugin_directories import plugin_data_dir_path
from app.utils.secure_storage.plain_text_storage import plain_text_storage
from app.utils.settings.settings import SettingsLocations


@pytest.fixture(autouse=True)
def _fresh_cache():
    pos.clear_plugin_options_cache()
    yield
    pos.clear_plugin_options_cache()


@pytest.fixture
def locations(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    return SettingsLocations(project_root=project, home=tmp_path / "home", managed_dir=tmp_path / "managed")


def _user_settings(locations):
    path = locations.user_config_dir / "settings.json"
    return json.loads(path.read_text()) if path.exists() else None


def _write_user_settings(locations, data):
    locations.user_config_dir.mkdir(parents=True, exist_ok=True)
    (locations.user_config_dir / "settings.json").write_text(json.dumps(data))


SCHEMA: dict[str, dict[str, Any]] = {
    "endpoint": {"type": "string", "title": "Endpoint", "description": "d"},
    "token": {"type": "string", "title": "Token", "description": "d", "sensitive": True},
}


# ── validate_user_config ──────────────────────────────────────────────────────

def test_validate_user_config_required_and_types():
    schema: dict[str, dict[str, Any]] = {
        "a": {"type": "string", "title": "Alpha", "description": "", "required": True},
        "b": {"type": "number", "title": "", "description": "", "min": 1, "max": 5.5},
        "c": {"type": "boolean", "title": "C", "description": ""},
        "d": {"type": "file", "title": "D", "description": ""},
        "e": {"type": "string", "title": "E", "description": "", "multiple": True},
        "f": {"type": "string", "title": "F", "description": ""},
    }
    result = validate_user_config({"a": "", "b": 0, "c": "yes", "d": 3, "e": ["x", 1], "f": ["x"]}, schema)
    assert result.valid is False
    assert result.errors == [
        "Alpha is required but not provided",
        "b must be at least 1",
        "C must be a boolean",
        "D must be a path string",
        "E must be an array of strings",
        "F must be a string, not an array",
    ]
    assert validate_user_config({"b": 6}, {"b": schema["b"]}).errors == ["b must be at most 5.5"]
    assert validate_user_config({"b": True}, {"b": schema["b"]}).errors == ["b must be a number"]
    assert validate_user_config({"f": 3}, {"f": schema["f"]}).errors == ["F must be a string"]
    assert validate_user_config({"a": "x", "e": ["x"]}, schema).valid is True


def test_validate_user_config_optional_missing_is_valid():
    assert validate_user_config({}, {"x": {"type": "number", "title": "X", "description": ""}}).valid is True


# ── load / save / delete ──────────────────────────────────────────────────────

def test_save_splits_sensitive_and_load_merges(locations):
    pos.save_plugin_options("p@m", {"endpoint": "https://e", "token": 42}, SCHEMA, locations)
    assert _user_settings(locations) == {"pluginConfigs": {"p@m": {"options": {"endpoint": "https://e"}}}}
    assert plain_text_storage.read() == {"pluginSecrets": {"p@m": {"token": "42"}}}
    assert pos.load_plugin_options("p@m", locations) == {"endpoint": "https://e", "token": "42"}


def test_secure_storage_wins_on_collision_and_load_is_memoized(locations):
    _write_user_settings(locations, {"pluginConfigs": {"p@m": {"options": {"token": "plain", "x": 1}}}})
    plain_text_storage.update({"pluginSecrets": {"p@m": {"token": "secret"}}})
    assert pos.load_plugin_options("p@m", locations) == {"token": "secret", "x": 1}
    plain_text_storage.update({"pluginSecrets": {"p@m": {"token": "changed"}}})
    assert pos.load_plugin_options("p@m", locations)["token"] == "secret"
    pos.clear_plugin_options_cache()
    assert pos.load_plugin_options("p@m", locations)["token"] == "changed"


def test_save_scrubs_keys_moving_between_stores(locations):
    _write_user_settings(locations, {"pluginConfigs": {"p@m": {"options": {"token": "old-plain", "keep": "k"}}}})
    plain_text_storage.update({"pluginSecrets": {"p@m": {"endpoint": "old-secret", "other": "o"}}})
    pos.save_plugin_options("p@m", {"endpoint": "e", "token": "t"}, SCHEMA, locations)
    assert _user_settings(locations) == {"pluginConfigs": {"p@m": {"options": {"keep": "k", "endpoint": "e"}}}}
    assert plain_text_storage.read() == {"pluginSecrets": {"p@m": {"other": "o", "token": "t"}}}


def test_save_with_only_a_scrub_of_secure_storage_rewrites_it(locations):
    plain_text_storage.update({"pluginSecrets": {"p@m": {"endpoint": "old"}}, "other": True})
    pos.save_plugin_options("p@m", {"endpoint": "new"}, SCHEMA, locations)
    assert plain_text_storage.read() == {"pluginSecrets": {"p@m": {}}, "other": True}


def test_save_writes_no_settings_when_nothing_non_sensitive(locations):
    pos.save_plugin_options("p@m", {"token": True}, SCHEMA, locations)
    assert _user_settings(locations) is None
    assert plain_text_storage.read() == {"pluginSecrets": {"p@m": {"token": "true"}}}


def test_save_does_not_copy_other_sources_into_user_settings(locations):
    project_settings = locations.project_root / ".tddagents" / "settings.json"
    project_settings.parent.mkdir(parents=True)
    project_settings.write_text(json.dumps({"permissions": {"allow": ["Read"]}}))
    pos.save_plugin_options("p@m", {"endpoint": "e"}, SCHEMA, locations)
    assert _user_settings(locations) == {"pluginConfigs": {"p@m": {"options": {"endpoint": "e"}}}}


def test_save_clears_the_load_cache(locations):
    assert pos.load_plugin_options("p@m", locations) == {}
    pos.save_plugin_options("p@m", {"endpoint": "e"}, SCHEMA, locations)
    assert pos.load_plugin_options("p@m", locations) == {"endpoint": "e"}


def test_save_raises_when_secure_storage_fails(locations, monkeypatch):
    failing = SimpleNamespace(read=lambda: None, update=lambda data: SimpleNamespace(success=False, warning=None))
    monkeypatch.setattr(pos, "get_secure_storage", lambda: failing)
    with pytest.raises(RuntimeError, match="Failed to save sensitive plugin options for p@m to secure storage"):
        pos.save_plugin_options("p@m", {"token": "t", "endpoint": "e"}, SCHEMA, locations)
    assert _user_settings(locations) is None


def test_save_raises_when_the_settings_file_is_corrupt(locations):
    locations.user_config_dir.mkdir(parents=True)
    (locations.user_config_dir / "settings.json").write_text("{broken")
    with pytest.raises(RuntimeError, match="Failed to save plugin options for p@m: Invalid JSON syntax"):
        pos.save_plugin_options("p@m", {"endpoint": "e"}, SCHEMA, locations)


def test_delete_removes_settings_entry_and_secrets_with_server_keys(locations):
    _write_user_settings(locations, {"pluginConfigs": {"p@m": {"options": {"a": 1}}, "q@m": {"options": {"b": 2}}}})
    plain_text_storage.update({
        "pluginSecrets": {"p@m": {"t": "1"}, "p@m/server": {"k": "v"}, "p@mx": {"z": "z"}},
        "keep": 1,
    })
    pos.load_plugin_options("p@m", locations)
    pos.delete_plugin_options("p@m", locations)
    assert _user_settings(locations) == {"pluginConfigs": {"q@m": {"options": {"b": 2}}}}
    assert plain_text_storage.read() == {"pluginSecrets": {"p@mx": {"z": "z"}}, "keep": 1}
    assert pos.load_plugin_options("p@m", locations) == {}


def test_delete_drops_empty_plugin_secrets_and_skips_settings_without_entry(locations):
    plain_text_storage.update({"pluginSecrets": {"p@m": {"t": "1"}}, "keep": 1})
    pos.delete_plugin_options("p@m", locations)
    assert plain_text_storage.read() == {"keep": 1}
    assert _user_settings(locations) is None


def test_delete_when_entry_only_in_project_settings_leaves_a_valid_user_file(locations):
    project_settings = locations.project_root / ".tddagents" / "settings.json"
    project_settings.parent.mkdir(parents=True)
    project_settings.write_text(json.dumps({"pluginConfigs": {"p@m": {"options": {"a": 1}}}}))
    pos.delete_plugin_options("p@m", locations)
    assert _user_settings(locations) == {"pluginConfigs": {}}


def test_delete_without_secrets_leaves_storage_untouched(locations):
    plain_text_storage.update({"pluginSecrets": {"other@m": {"t": "1"}}})
    pos.delete_plugin_options("p@m", locations)
    assert plain_text_storage.read() == {"pluginSecrets": {"other@m": {"t": "1"}}}


# ── get_unconfigured_options ──────────────────────────────────────────────────

def _plugin(user_config):
    manifest: Any = {"name": "p"}
    if user_config is not None:
        manifest["userConfig"] = user_config
    return LoadedPlugin(name="p", manifest=manifest, path="/x", source="p@m", repository="p@m")


def test_unconfigured_options_lists_only_failing_fields(locations):
    schema: dict[str, dict[str, Any]] = {
        "a": {"type": "string", "title": "A", "description": "", "required": True},
        "b": {"type": "number", "title": "B", "description": "", "required": True},
    }
    _write_user_settings(locations, {"pluginConfigs": {"p@m": {"options": {"a": "set"}}}})
    assert pos.get_unconfigured_options(_plugin(schema), locations) == {"b": schema["b"]}


def test_unconfigured_options_empty_when_valid_or_no_schema(locations):
    assert pos.get_unconfigured_options(_plugin(None), locations) == {}
    assert pos.get_unconfigured_options(_plugin({}), locations) == {}
    schema = {"a": {"type": "string", "title": "A", "description": ""}}
    assert pos.get_unconfigured_options(_plugin(schema), locations) == {}


def test_storage_id_is_the_plugin_source():
    assert pos.get_plugin_storage_id(_plugin(None)) == "p@m"


# ── substitutions ─────────────────────────────────────────────────────────────

def test_substitute_plugin_root_and_data():
    plugin = SimpleNamespace(path="/plugins/p$&", source="p@m")
    out = pos.substitute_plugin_variables("${CLAUDE_PLUGIN_ROOT}/bin ${CLAUDE_PLUGIN_DATA}/db", plugin)
    data_dir = plugin_data_dir_path("p@m")
    assert out == f"/plugins/p$&/bin {data_dir}/db"
    assert os.path.isdir(data_dir)


def test_plugin_data_stays_literal_without_source_and_is_not_created():
    plugin = SimpleNamespace(path="/root", source=None)
    assert pos.substitute_plugin_variables("${CLAUDE_PLUGIN_ROOT} ${CLAUDE_PLUGIN_DATA}", plugin) == (
        "/root ${CLAUDE_PLUGIN_DATA}"
    )


def test_data_dir_is_created_only_when_referenced():
    pos.substitute_plugin_variables("${CLAUDE_PLUGIN_ROOT}", SimpleNamespace(path="/r", source="lazy@m"))
    assert not os.path.exists(plugin_data_dir_path("lazy@m"))


def test_substitute_user_config_variables_formats_like_javascript():
    values = {"s": "x", "n": 3.0, "f": 1.5, "b": False, "l": ["a", "b"]}
    out = pos.substitute_user_config_variables(
        "${user_config.s} ${user_config.n} ${user_config.f} ${user_config.b} ${user_config.l}", values
    )
    assert out == "x 3 1.5 false a,b"


def test_substitute_user_config_variables_raises_on_missing_key():
    with pytest.raises(ValueError, match="Missing required user configuration value: k. This should have been"):
        pos.substitute_user_config_variables("${user_config.k}", {})


def test_substitute_in_content_masks_sensitive_and_keeps_unknown():
    out = pos.substitute_user_config_in_content(
        "${user_config.endpoint} ${user_config.token} ${user_config.unknown}",
        {"endpoint": "https://e", "token": "t"},
        SCHEMA,
    )
    assert out == "https://e [sensitive option 'token' not available in skill content] ${user_config.unknown}"
