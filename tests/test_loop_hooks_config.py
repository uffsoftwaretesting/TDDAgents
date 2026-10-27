"""Which hooks a run may execute (`app/utils/hooks/hooks_config_snapshot.py`)."""

from __future__ import annotations

import json
from pathlib import Path

from app.schemas.hooks import CommandHook, HookMatcher
from app.utils.hooks.hooks_config_snapshot import (
    HooksConfigSnapshot,
    capture_hooks_config_snapshot,
    get_hooks_from_allowed_sources,
    is_restricted_to_plugin_only,
    should_allow_managed_hooks_only,
    should_disable_all_hooks_including_managed,
)
from app.utils.settings.settings import SettingsLocations

POLICY_HOOKS = {"Stop": [{"hooks": [{"type": "command", "command": "policy"}]}]}
USER_HOOKS = {"Stop": [{"hooks": [{"type": "command", "command": "user"}]}]}


def test_plugin_only_restriction():
    assert is_restricted_to_plugin_only(None, "hooks") is False
    assert is_restricted_to_plugin_only({}, "hooks") is False
    assert is_restricted_to_plugin_only({"strictPluginOnlyCustomization": True}, "hooks") is True
    assert is_restricted_to_plugin_only({"strictPluginOnlyCustomization": ["hooks"]}, "hooks") is True
    assert is_restricted_to_plugin_only({"strictPluginOnlyCustomization": ["skills"]}, "hooks") is False
    assert is_restricted_to_plugin_only({"strictPluginOnlyCustomization": "hooks"}, "hooks") is False
    assert is_restricted_to_plugin_only({"strictPluginOnlyCustomization": False}, "hooks") is False


def test_allowed_sources_policy_disable_all_wins():
    policy = {"disableAllHooks": True, "allowManagedHooksOnly": True, "hooks": POLICY_HOOKS}
    assert get_hooks_from_allowed_sources(policy, {"hooks": USER_HOOKS}) == {}


def test_allowed_sources_managed_only():
    policy = {"allowManagedHooksOnly": True, "hooks": POLICY_HOOKS}
    assert get_hooks_from_allowed_sources(policy, {"hooks": USER_HOOKS}) == POLICY_HOOKS
    assert get_hooks_from_allowed_sources({"allowManagedHooksOnly": True}, {"hooks": USER_HOOKS}) == {}


def test_allowed_sources_plugin_only():
    policy = {"strictPluginOnlyCustomization": ["hooks"], "hooks": POLICY_HOOKS}
    assert get_hooks_from_allowed_sources(policy, {"hooks": USER_HOOKS}) == POLICY_HOOKS
    assert get_hooks_from_allowed_sources({"strictPluginOnlyCustomization": True}, {"hooks": USER_HOOKS}) == {}


def test_allowed_sources_non_policy_disable_leaves_policy_hooks():
    merged = {"disableAllHooks": True, "hooks": USER_HOOKS}
    assert get_hooks_from_allowed_sources({"hooks": POLICY_HOOKS}, merged) == POLICY_HOOKS
    assert get_hooks_from_allowed_sources(None, merged) == {}


def test_allowed_sources_merged_by_default():
    assert get_hooks_from_allowed_sources(None, {"hooks": USER_HOOKS}) == USER_HOOKS
    assert get_hooks_from_allowed_sources({"hooks": POLICY_HOOKS}, {"hooks": USER_HOOKS}) == USER_HOOKS
    assert get_hooks_from_allowed_sources(None, {}) == {}
    assert get_hooks_from_allowed_sources({"disableAllHooks": False}, {"disableAllHooks": False, "hooks": None}) == {}


def test_managed_only_gate():
    assert should_allow_managed_hooks_only({"allowManagedHooksOnly": True}, {}) is True
    assert should_allow_managed_hooks_only(None, {"disableAllHooks": True}) is True
    assert should_allow_managed_hooks_only({"disableAllHooks": True}, {"disableAllHooks": True}) is False
    assert should_allow_managed_hooks_only({"disableAllHooks": False}, {"disableAllHooks": True}) is True
    assert should_allow_managed_hooks_only(None, {}) is False
    assert should_allow_managed_hooks_only({"allowManagedHooksOnly": "yes"}, {"disableAllHooks": "yes"}) is False


def test_disable_all_including_managed():
    assert should_disable_all_hooks_including_managed({"disableAllHooks": True}) is True
    assert should_disable_all_hooks_including_managed({"disableAllHooks": 1}) is False
    assert should_disable_all_hooks_including_managed({}) is False
    assert should_disable_all_hooks_including_managed(None) is False


def test_snapshot_defaults_and_lookup():
    snap = HooksConfigSnapshot()
    assert (dict(snap.hooks), snap.managed_only, snap.disable_all) == ({}, False, False)
    assert snap.matchers_for("Stop") == ()
    assert snap.has_hook_for_event("Stop") is False
    m = HookMatcher(hooks=())
    snap = HooksConfigSnapshot(hooks={"Stop": (m,), "PreToolUse": ()})
    assert snap.matchers_for("Stop") == (m,)
    assert isinstance(snap.matchers_for("Stop"), tuple)
    assert snap.has_hook_for_event("Stop") is True
    assert snap.has_hook_for_event("PreToolUse") is False


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _locations(tmp_path: Path) -> SettingsLocations:
    return SettingsLocations(project_root=tmp_path / "proj", home=tmp_path / "home", managed_dir=tmp_path / "etc")


def test_capture_merges_every_source(tmp_path):
    loc = _locations(tmp_path)
    _write(tmp_path / "home/.tddagents/settings.json", {"hooks": USER_HOOKS})
    _write(tmp_path / "proj/.tddagents/settings.json",
           {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "project"}]}]}})
    snap = capture_hooks_config_snapshot(loc)
    assert [m.hooks for m in snap.matchers_for("Stop")] == [
        (CommandHook(command="user"),), (CommandHook(command="project"),)]
    assert (snap.managed_only, snap.disable_all) == (False, False)


def test_capture_with_policy_gates(tmp_path):
    loc = _locations(tmp_path)
    _write(tmp_path / "home/.tddagents/settings.json", {"hooks": USER_HOOKS})
    _write(tmp_path / "etc/managed-settings.json", {"allowManagedHooksOnly": True, "hooks": POLICY_HOOKS})
    snap = capture_hooks_config_snapshot(loc)
    assert [m.hooks for m in snap.matchers_for("Stop")] == [(CommandHook(command="policy"),)]
    assert (snap.managed_only, snap.disable_all) == (True, False)


def test_capture_with_policy_disable_all(tmp_path):
    loc = _locations(tmp_path)
    _write(tmp_path / "home/.tddagents/settings.json", {"hooks": USER_HOOKS})
    _write(tmp_path / "etc/managed-settings.json", {"disableAllHooks": True, "hooks": POLICY_HOOKS})
    snap = capture_hooks_config_snapshot(loc)
    assert dict(snap.hooks) == {}
    assert (snap.managed_only, snap.disable_all) == (False, True)


def test_capture_with_no_files(tmp_path):
    snap = capture_hooks_config_snapshot(_locations(tmp_path))
    assert dict(snap.hooks) == {}
    assert (snap.managed_only, snap.disable_all) == (False, False)


# ── an invalid hooks block invalidates its whole settings file (upstream) ─────

def test_an_invalid_hook_discards_the_whole_settings_file(tmp_path, caplog):
    """
    `parseSettingsFileUncached`: `SettingsSchema().safeParse` failing returns `settings: null`,
    so the file's hooks, permission rules and `defaultMode` are all ignored.
    """
    import json as _json
    import logging as _logging

    from app.utils.hooks.hooks_config_snapshot import capture_hooks_config_snapshot
    from app.utils.settings.settings import (
        SettingsLocations,
        load_permission_rules,
        parse_settings_file,
    )

    proj = tmp_path / "proj"
    (proj / ".tddagents").mkdir(parents=True)
    path = proj / ".tddagents" / "settings.json"
    path.write_text(_json.dumps({
        "permissions": {"deny": ["Bash(rm:*)"], "defaultMode": "acceptEdits"},
        "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": 42}]}]},
    }))
    with caplog.at_level(_logging.WARNING):
        assert parse_settings_file(path) is None
    assert any("Invalid settings" in r.getMessage() for r in caplog.records)
    locations = SettingsLocations(project_root=proj, home=tmp_path / "home", managed_dir=tmp_path / "etc")
    assert capture_hooks_config_snapshot(locations).hooks == {}
    assert load_permission_rules(proj, tmp_path / "home") == []


def test_each_hook_schema_violation_invalidates_the_file(tmp_path):
    import json as _json

    from app.utils.settings.settings import parse_settings_file

    bad_documents = [
        {"hooks": {"NoSuchEvent": []}},
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x", "timeout": None}]}]}},
        {"hooks": {"Stop": [{"matcher": 3, "hooks": []}]}},
        {"hooks": {"Stop": [{"hooks": [{"type": "nope"}]}]}},
        {"hooks": []},
    ]
    for i, document in enumerate(bad_documents):
        path = tmp_path / f"s{i}.json"
        path.write_text(_json.dumps(document))
        assert parse_settings_file(path) is None, document
    good = tmp_path / "good.json"
    good.write_text(_json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x", "extra": 1}]}]}}))
    assert parse_settings_file(good) == {
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x", "extra": 1}]}]}
    }
