"""
`updatedPermissions` from PermissionRequest hooks: `app/loop/permissions/updates.py` (ports of
`PermissionUpdate.ts`, `addPermissionRulesToSettings`, `updateSettingsForSource`) and their use
in `permission_request.py`, including the two TDD guards: a rewritten input is re-checked, and
applied updates are re-stripped of dangerous allows.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.loop.permissions.updates as up
from app.loop.context.app_state import AppState, AppStateStore
from app.loop.context.tool_context import tool_context_for
from app.loop.hooks.output import AggregatedHookResult
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.permission_request import (
    apply_hook_permission_updates,
    run_permission_request_hooks_for_headless_agent,
)
from app.loop.permissions.settings import (
    SettingsLocations,
    build_tool_permission_context,
)
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)
from tests.hook_doubles import ScriptedDispatcher

S = PermissionRuleSource
ALLOW, DENY, ASK = PermissionBehavior.ALLOW, PermissionBehavior.DENY, PermissionBehavior.ASK


def rules(*values: tuple[str, str | None]) -> list[dict[str, Any]]:
    return [{"toolName": n, **({"ruleContent": c} if c is not None else {})} for n, c in values]


def add(behavior="allow", destination="session", *values) -> dict[str, Any]:
    return {"type": "addRules", "behavior": behavior, "destination": destination, "rules": rules(*values)}


# ── schema ───────────────────────────────────────────────────────────────────

def test_constants():
    assert up.UPDATE_TYPES == {"addRules", "replaceRules", "removeRules", "setMode", "addDirectories",
                               "removeDirectories"}
    assert up.UPDATE_DESTINATIONS == (S.USER_SETTINGS, S.PROJECT_SETTINGS, S.LOCAL_SETTINGS, S.SESSION, S.CLI_ARG)
    assert up.PERSISTABLE_DESTINATIONS == {S.USER_SETTINGS, S.PROJECT_SETTINGS, S.LOCAL_SETTINGS}
    assert [up.supports_persistence(d) for d in up.UPDATE_DESTINATIONS] == [True, True, True, False, False]


def test_parse_each_update_type():
    rule = up.parse_permission_update(add("deny", "localSettings", ("Bash", "rm:*"), ("Edit", None)))
    assert rule == up.PermissionUpdate("addRules", S.LOCAL_SETTINGS, rules=(("Bash", "rm:*"), ("Edit", None)),
                                       behavior=DENY)
    mode = up.parse_permission_update({"type": "setMode", "mode": "acceptEdits", "destination": "session"})
    assert mode == up.PermissionUpdate("setMode", S.SESSION, mode=PermissionMode.ACCEPT_EDITS)
    dirs = up.parse_permission_update({"type": "addDirectories", "directories": ["/a"], "destination": "cliArg"})
    assert dirs == up.PermissionUpdate("addDirectories", S.CLI_ARG, directories=("/a",))
    for kind in ("replaceRules", "removeRules"):
        assert parsed({**add(), "type": kind})[0].type == kind
    assert parsed({"type": "removeDirectories", "directories": [], "destination": "session"})[0].type == (
        "removeDirectories")


@pytest.mark.parametrize("raw", [
    None, "x", {}, {"type": "nope", "destination": "session"},
    {"type": "setMode", "mode": "acceptEdits", "destination": "policySettings"},
    {"type": "setMode", "mode": "acceptEdits", "destination": "flagSettings"},
    {"type": "setMode", "mode": "acceptEdits", "destination": "bogus"},
    {"type": "setMode", "mode": "auto", "destination": "session"},
    {"type": "addRules", "behavior": "passthrough", "destination": "session", "rules": []},
    {"type": "addRules", "behavior": "maybe", "destination": "session", "rules": []},
    {"type": "addRules", "behavior": "allow", "destination": "session", "rules": "Bash"},
    {"type": "addRules", "behavior": "allow", "destination": "session", "rules": ["Bash"]},
    {"type": "addRules", "behavior": "allow", "destination": "session", "rules": [{"toolName": 3}]},
    {"type": "addRules", "behavior": "allow", "destination": "session",
     "rules": [{"toolName": "Bash", "ruleContent": 3}]},
    {"type": "addDirectories", "directories": "/a", "destination": "session"},
    {"type": "addDirectories", "directories": [1], "destination": "session"},
])
def test_invalid_updates(raw):
    assert up.parse_permission_update(raw) is None


def test_parse_list_is_all_or_nothing():
    assert up.parse_permission_updates([add()]) is not None
    assert up.parse_permission_updates([add(), {"type": "x"}]) is None
    assert up.parse_permission_updates("x") is None
    assert up.parse_permission_updates([]) == ()


def test_rule_strings_are_escaped():
    update = up.parse_permission_update(add("allow", "session", ("Bash", "echo (a)"), ("Read", None)))
    assert update is not None and update.rule_strings() == ["Bash(echo \\(a\\))", "Read"]


# ── applyPermissionUpdate ───────────────────────────────────────────────────

def parsed(*raws: dict[str, Any]) -> tuple[up.PermissionUpdate, ...]:
    result = up.parse_permission_updates(list(raws))
    assert result is not None
    return result


def test_set_mode_is_unguarded_as_v2_1_88():
    ctx = up.apply_permission_updates(ToolPermissionContext(), parsed(
        {"type": "setMode", "mode": "bypassPermissions", "destination": "session"}))
    assert ctx.mode == PermissionMode.BYPASS_PERMISSIONS
    assert ctx.is_bypass_permissions_mode_available is False


def test_add_rules_appends_with_the_destination_as_source_and_parses_legacy_names():
    existing = PermissionRule("Bash", ALLOW, "ls", S.PROJECT_SETTINGS)
    ctx = up.apply_permission_updates(ToolPermissionContext(always_allow_rules=(existing,)), parsed(
        add("allow", "session", ("Task", None), ("Bash", "ls")), add("ask", "cliArg", ("Edit", "src/**"))))
    assert ctx.always_allow_rules == (existing, PermissionRule("Agent", ALLOW, None, S.SESSION),
                                      PermissionRule("Bash", ALLOW, "ls", S.SESSION))
    assert ctx.always_ask_rules == (PermissionRule("Edit", ASK, "src/**", S.CLI_ARG),)
    assert ctx.always_deny_rules == ()


def test_replace_rules_overwrites_one_source_of_one_behavior():
    keep = PermissionRule("Bash", DENY, "a", S.PROJECT_SETTINGS)
    gone = PermissionRule("Bash", DENY, "b", S.SESSION)
    other_kind = PermissionRule("Bash", ALLOW, "c", S.SESSION)
    ctx = ToolPermissionContext(always_deny_rules=(keep, gone), always_allow_rules=(other_kind,))
    out = up.apply_permission_update(ctx, parsed({**add("deny", "session", ("Bash", "z")), "type": "replaceRules"})[0])
    assert out.always_deny_rules == (keep, PermissionRule("Bash", DENY, "z", S.SESSION))
    assert out.always_allow_rules == (other_kind,)


def test_remove_rules_filters_by_source_and_serialized_value():
    a = PermissionRule("Bash", ALLOW, "a", S.SESSION)
    b = PermissionRule("Bash", ALLOW, "b", S.SESSION)
    other_source = PermissionRule("Bash", ALLOW, "a", S.PROJECT_SETTINGS)
    ctx = ToolPermissionContext(always_allow_rules=(a, b, other_source))
    out = up.apply_permission_update(ctx, parsed({**add("allow", "session", ("Bash", "a")), "type": "removeRules"})[0])
    assert out.always_allow_rules == (b, other_source)


def test_directories_add_dedup_and_remove():
    ctx = ToolPermissionContext(additional_working_directories=("/a",))
    out = up.apply_permission_updates(ctx, parsed(
        {"type": "addDirectories", "directories": ["/a", "/b", "/b"], "destination": "session"}))
    assert out.additional_working_directories == ("/a", "/b")
    out = up.apply_permission_updates(out, parsed(
        {"type": "removeDirectories", "directories": ["/a", "/zz"], "destination": "userSettings"}))
    assert out.additional_working_directories == ("/b",)


def test_unknown_update_is_identity_and_updates_fold_in_order():
    ctx = ToolPermissionContext()
    assert up.apply_permission_update(ctx, up.PermissionUpdate("other", S.SESSION)) is ctx
    out = up.apply_permission_updates(ctx, parsed(
        add("allow", "session", ("Bash", "x")), {**add("allow", "session", ("Bash", "x")), "type": "removeRules"}))
    assert out.always_allow_rules == ()


# ── on disk ──────────────────────────────────────────────────────────────────

def locations(tmp_path: Path) -> SettingsLocations:
    return SettingsLocations(project_root=tmp_path / "proj", home=tmp_path / "home", managed_dir=tmp_path / "etc")


def read(path: Path) -> Any:
    return json.loads(path.read_text())


def test_update_settings_for_source(tmp_path):
    loc = locations(tmp_path)
    local = tmp_path / "proj/.tddagents/settings.local.json"
    assert up.update_settings_for_source(S.LOCAL_SETTINGS, loc, {"permissions": {"allow": ["a"]}, "x": 1}) is None
    assert read(local) == {"permissions": {"allow": ["a"]}, "x": 1}
    assert local.read_text().endswith("\n")
    up.update_settings_for_source(S.LOCAL_SETTINGS, loc, {"permissions": {"allow": ["b"], "deny": ["d"]}, "x": None})
    assert read(local) == {"permissions": {"allow": ["b"], "deny": ["d"]}}  # arrays replace, None deletes
    user = tmp_path / "home/.tddagents/settings.json"
    up.update_settings_for_source(S.USER_SETTINGS, loc, {"k": {"n": 1}})
    up.update_settings_for_source(S.USER_SETTINGS, loc, {"k": {"m": 2}})
    assert read(user) == {"k": {"n": 1, "m": 2}}
    for read_only in (S.POLICY_SETTINGS, S.FLAG_SETTINGS, S.SESSION, S.CLI_ARG):
        assert up.update_settings_for_source(read_only, loc, {"x": 1}) is None
    assert not (tmp_path / "etc").exists()


def test_update_settings_refuses_invalid_json_and_reports_write_errors(tmp_path):
    loc = locations(tmp_path)
    project = tmp_path / "proj/.tddagents/settings.json"
    project.parent.mkdir(parents=True)
    project.write_text("{oops")
    assert up.update_settings_for_source(S.PROJECT_SETTINGS, loc, {"x": 1}) == (
        f"Invalid JSON syntax in settings file at {project}")
    assert project.read_text() == "{oops"
    project.unlink()
    project.parent.rmdir()
    project.parent.write_text("a file where the settings directory should be")
    error = up.update_settings_for_source(S.PROJECT_SETTINGS, loc, {"x": 1})
    assert error is not None and error.startswith(f"Failed to write settings file at {project}: ")


def test_add_rules_persist_dedups_and_honours_managed_only(tmp_path, caplog):
    loc = locations(tmp_path)
    project = tmp_path / "proj/.tddagents/settings.json"
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps({"permissions": {"allow": ["Task", "Bash(ls)"]}, "keep": True}))
    assert up.add_permission_rules_to_settings(["Agent", "Bash(pwd)"], ALLOW, S.PROJECT_SETTINGS, loc) is True
    assert read(project) == {"permissions": {"allow": ["Task", "Bash(ls)", "Bash(pwd)"]}, "keep": True}
    assert up.add_permission_rules_to_settings(["Bash(ls)"], ALLOW, S.PROJECT_SETTINGS, loc) is True
    assert up.add_permission_rules_to_settings([], ALLOW, S.PROJECT_SETTINGS, loc) is True
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/managed-settings.json").write_text(json.dumps({"allowManagedPermissionRulesOnly": True}))
    assert up.add_permission_rules_to_settings(["Bash(x)"], ALLOW, S.PROJECT_SETTINGS, loc) is False
    assert "Bash(x)" not in read(project)["permissions"]["allow"]


def test_add_rules_persist_reports_a_failed_write(tmp_path, caplog):
    loc = locations(tmp_path)
    project = tmp_path / "proj/.tddagents/settings.json"
    project.parent.mkdir(parents=True)
    project.write_text("{bad")
    with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Permissions"):
        assert up.add_permission_rules_to_settings(["Bash(x)"], DENY, S.PROJECT_SETTINGS, loc) is False
    assert f"Could not persist permission rules: Invalid JSON syntax in settings file at {project}" in [
        r.getMessage() for r in caplog.records]


def test_persist_each_update_type(tmp_path, caplog):
    loc = locations(tmp_path)
    local = tmp_path / "proj/.tddagents/settings.local.json"
    up.persist_permission_updates(parsed(
        add("deny", "localSettings", ("Bash", "rm:*"), ("Bash", "mv:*")),
        {"type": "addDirectories", "directories": ["/d1", "/d2"], "destination": "localSettings"},
        {"type": "addDirectories", "directories": ["/d1"], "destination": "localSettings"},
        {"type": "setMode", "mode": "plan", "destination": "localSettings"},
    ), loc)
    assert read(local) == {"permissions": {"deny": ["Bash(rm:*)", "Bash(mv:*)"],
                                           "additionalDirectories": ["/d1", "/d2"], "defaultMode": "plan"}}
    up.persist_permission_updates(parsed(
        {**add("deny", "localSettings", ("Bash", "rm:*")), "type": "removeRules"},
        {"type": "removeDirectories", "directories": ["/d1"], "destination": "localSettings"},
        {**add("ask", "localSettings", ("Edit", None)), "type": "replaceRules"},
        add("allow", "session", ("Bash", "never-written")),
    ), loc)
    assert read(local) == {"permissions": {"deny": ["Bash(mv:*)"], "additionalDirectories": ["/d2"],
                                           "defaultMode": "plan", "ask": ["Edit"]}}
    local.write_text("{bad")
    with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Permissions"):
        up.persist_permission_update(parsed({"type": "setMode", "mode": "plan", "destination": "localSettings"})[0],
                                     loc)
    assert f"Could not persist permission update: Invalid JSON syntax in settings file at {local}" in [
        r.getMessage() for r in caplog.records]


def test_remove_rules_persist_normalizes_existing_entries(tmp_path):
    loc = locations(tmp_path)
    user = tmp_path / "home/.tddagents/settings.json"
    user.parent.mkdir(parents=True)
    user.write_text(json.dumps({"permissions": {"allow": ["Task", "Bash(*)", "Bash(ls)"], "deny": "x"}}))
    up.persist_permission_update(parsed({**add("allow", "userSettings", ("Agent", None), ("Bash", None)),
                                         "type": "removeRules"})[0], loc)
    assert read(user)["permissions"]["allow"] == ["Bash(ls)"]
    up.persist_permission_update(parsed({**add("deny", "userSettings", ("X", None)), "type": "removeRules"})[0], loc)
    assert read(user)["permissions"]["deny"] == []


def test_settings_locations_for():
    assert up.settings_locations_for(ToolPermissionContext()) is None
    loc = SettingsLocations(project_root=Path("/p"))
    assert up.settings_locations_for(ToolPermissionContext(settings_locations=loc)) is loc


def test_build_context_carries_its_locations(tmp_path):
    ctx = build_tool_permission_context(tmp_path / "proj", tmp_path / "home", managed_dir=tmp_path / "etc")
    assert ctx.settings_locations == SettingsLocations(
        project_root=tmp_path / "proj", home=tmp_path / "home", managed_dir=tmp_path / "etc")


# ── the headless PermissionRequest path ──────────────────────────────────────

def _ctx(perm: ToolPermissionContext, decision: Any, ledger: PhaseLedger | None = None):
    state = AppState(phase_ledger=ledger or PhaseLedger(), tool_permission_context=perm)
    scripted = [AggregatedHookResult(permission_request_result=decision)]
    dispatcher = ScriptedDispatcher(script={"PermissionRequest": scripted})
    return tool_context_for(AppStateStore(state=state), hook_dispatcher=dispatcher,
                            workspace=SimpleNamespace(root="/ws"))


def _write_tool():
    from app.loop.tools.fs import build_write_file_tool

    return build_write_file_tool()


def _run(ctx, tool=None, args=None):
    return asyncio.run(run_permission_request_hooks_for_headless_agent(
        tool or _write_tool(), args or {"file_path": "tests/test_a.py", "content": "x"}, "tu", ctx, "default"))


def test_hook_updates_are_persisted_applied_and_re_stripped(tmp_path):
    perm = build_tool_permission_context(tmp_path / "proj", tmp_path / "home", managed_dir=tmp_path / "etc")
    decision = {"behavior": "allow", "updatedPermissions": [
        add("allow", "localSettings", ("Bash", "python:*"), ("Bash", "ls")),
        {"type": "setMode", "mode": "acceptEdits", "destination": "session"},
    ]}
    ctx = _ctx(perm, decision)
    res = _run(ctx)
    assert res is not None and res.behavior == ALLOW
    applied = ctx.get_app_state().tool_permission_context
    assert applied.mode == PermissionMode.ACCEPT_EDITS
    assert [r.rule_content for r in applied.always_allow_rules] == ["ls"]
    assert [r.rule_content for r in applied.stripped_dangerous_rules] == ["python:*"]
    assert read(tmp_path / "proj/.tddagents/settings.local.json") == {
        "permissions": {"allow": ["Bash(python:*)", "Bash(ls)"]}}  # persisted as upstream; stripped at next load


def test_hook_updates_without_locations_apply_in_memory_only(tmp_path):
    ctx = _ctx(ToolPermissionContext(), {"behavior": "allow", "updatedPermissions": [
        add("deny", "projectSettings", ("Bash", "rm:*"))]})
    _run(ctx)
    assert [r.rule_content for r in ctx.get_app_state().tool_permission_context.always_deny_rules] == ["rm:*"]


def test_apply_hook_permission_updates_ignores_nothing_to_apply():
    store = AppStateStore()
    ctx = tool_context_for(store)
    before = store.get()
    apply_hook_permission_updates(None, ctx)
    apply_hook_permission_updates([], ctx)
    assert store.get() is before


def test_a_rewritten_input_is_re_checked_by_the_phase_tier_and_deny_rules():
    red = PhaseLedger(phase=TddPhase.RED)
    decision = {"behavior": "allow", "updatedInput": {"file_path": "calc.py", "content": "x"},
                "updatedPermissions": [{"type": "setMode", "mode": "plan", "destination": "session"}]}
    ctx = _ctx(ToolPermissionContext(), decision, red)
    res = _run(ctx)
    assert res is not None and res.behavior == DENY and res.decision_reason["type"] == "phase_rule"
    assert ctx.get_app_state().tool_permission_context.mode == PermissionMode.DEFAULT  # updates not applied
    deny = PermissionRule("Edit", DENY, "tests/locked.py", S.PROJECT_SETTINGS)
    ctx = _ctx(ToolPermissionContext(always_deny_rules=(deny,)),
               {"behavior": "allow", "updatedInput": {"file_path": "tests/locked.py", "content": "x"}})
    res = _run(ctx)
    assert res is not None and res.behavior == DENY and res.decision_reason == {"type": "rule", "rule": deny}


def test_a_rewritten_input_that_passes_the_re_check_is_allowed():
    decision = {"behavior": "allow", "updatedInput": {"file_path": "tests/other.py", "content": "y"}}
    res = _run(_ctx(ToolPermissionContext(), decision, PhaseLedger(phase=TddPhase.RED)))
    assert res is not None and (res.behavior, res.updated_input) == (
        ALLOW, {"file_path": "tests/other.py", "content": "y"})


# ── mutation-driven pins ─────────────────────────────────────────────────────

def test_write_layout_and_deleting_an_absent_key(tmp_path):
    loc = locations(tmp_path)
    up.update_settings_for_source(S.LOCAL_SETTINGS, loc, {"a": {"b": 1}, "absent": None})
    assert (tmp_path / "proj/.tddagents/settings.local.json").read_text() == '{\n  "a": {\n    "b": 1\n  }\n}\n'


def test_a_no_op_persist_logs_nothing(tmp_path, caplog):
    loc = locations(tmp_path)
    update = parsed({"type": "addDirectories", "directories": ["/d"], "destination": "localSettings"})[0]
    up.persist_permission_update(update, loc)
    with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Permissions"):
        up.persist_permission_update(update, loc)
    assert caplog.records == []
