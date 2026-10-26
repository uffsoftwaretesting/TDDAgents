"""
Phase 3 — permissions and settings wired at runtime.

Covers the modules the phase added (`dangerous.py`, `permission_request.py`, `messages.py`),
the settings sources added to `settings.py` (flag, policy with drop-ins,
`allowManagedPermissionRulesOnly`, mode resolution, `initializeToolPermissionContext`), the
PermissionRequest decision parsing (`app/loop/hooks/` since Phase 4), and the D6 structural invariant against
the shipped settings: no settings-loaded allow lets a RED-phase call write production code.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.loop.permissions.dangerous as dg
import app.loop.permissions.settings as st
from app.loop.context.app_state import AppState, AppStateStore
from app.loop.context.tool_context import tool_context_for
from app.loop.hooks.config import HooksConfigSnapshot
from app.loop.hooks.execute import HookDispatcher
from app.loop.hooks.output import AggregatedHookResult
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.gate import has_permissions_to_use_tool
from app.loop.permissions.messages import (
    MODE_TITLES,
    SOURCE_DISPLAY_NAMES,
    create_permission_request_message,
    escape_rule_content,
    permission_mode_title,
    permission_rule_source_display_string,
    permission_rule_value_to_string,
)
from app.loop.permissions.permission_request import (
    PERMISSION_REQUEST_EVENT,
    permission_request_decision,
    run_permission_request_hooks_for_headless_agent,
)
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)

ALLOW, DENY, ASK = PermissionBehavior.ALLOW, PermissionBehavior.DENY, PermissionBehavior.ASK
S = PermissionRuleSource
REPO = Path(__file__).parent.parent


def write(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def allow(content: str | None, source: S = S.PROJECT_SETTINGS, tool: str = "Bash") -> PermissionRule:
    return PermissionRule(tool, ALLOW, content, source)


# ── messages ─────────────────────────────────────────────────────────────────

def test_message_tables_are_upstreams():
    assert dict(SOURCE_DISPLAY_NAMES) == {
        S.USER_SETTINGS: "user settings", S.PROJECT_SETTINGS: "shared project settings",
        S.LOCAL_SETTINGS: "project local settings", S.FLAG_SETTINGS: "command line arguments",
        S.POLICY_SETTINGS: "enterprise managed settings", S.CLI_ARG: "CLI argument",
        S.COMMAND: "command configuration", S.SESSION: "current session"}
    assert MODE_TITLES[PermissionMode.ACCEPT_EDITS] == "Accept edits"
    assert MODE_TITLES[PermissionMode.DONT_ASK] == "Don't Ask"
    assert permission_mode_title("nonsense") == "Default"
    assert permission_mode_title(PermissionMode.BUBBLE) == "Default"
    assert permission_mode_title("plan") == "Plan Mode"
    assert permission_rule_source_display_string("weird") == "weird"
    assert permission_rule_source_display_string("policySettings") == "enterprise managed settings"


def test_rule_value_to_string_escapes_content():
    assert permission_rule_value_to_string("Bash", None) == "Bash"
    assert permission_rule_value_to_string("Bash", "") == "Bash"
    assert permission_rule_value_to_string("Bash", "echo (a)\\") == "Bash(echo \\(a\\)\\\\)"
    assert escape_rule_content("a\\(b)") == "a\\\\\\(b\\)"


@pytest.mark.parametrize("reason, expected", [
    (None, "The agent requested permissions to use T, but you haven't granted it yet."),
    ({}, "The agent requested permissions to use T, but you haven't granted it yet."),
    ({"type": "hook", "hookName": "H", "reason": "why"}, "Hook 'H' blocked this action: why"),
    ({"type": "hook", "hookName": "H"}, "Hook 'H' requires approval for this T command"),
    ({"type": "rule", "rule": PermissionRule("Bash", ASK, "npm:*", S.LOCAL_SETTINGS)},
     "Permission rule 'Bash(npm:*)' from project local settings requires approval for this T command"),
    ({"type": "rule", "rule": "not a rule"},
     "The agent requested permissions to use T, but you haven't granted it yet."),
    ({"type": "workingDir", "reason": "wd"}, "wd"),
    ({"type": "safetyCheck", "reason": "sc"}, "sc"),
    ({"type": "other", "reason": "ot"}, "ot"),
    ({"type": "asyncAgent", "reason": "aa"}, "aa"),
    ({"type": "mode", "mode": PermissionMode.PLAN},
     "Current permission mode (Plan Mode) requires approval for this T command"),
    ({"type": "mode"}, "Current permission mode (Default) requires approval for this T command"),
    ({"type": "unknown"}, "The agent requested permissions to use T, but you haven't granted it yet."),
])
def test_create_permission_request_message(reason, expected):
    assert create_permission_request_message("T", reason) == expected


def test_subcommand_results_message():
    asking = PermissionResult(behavior=ASK)
    passing = PermissionResult(behavior=PermissionBehavior.PASSTHROUGH)
    allowed = PermissionResult(behavior=ALLOW)
    one = {"type": "subcommandResults", "reasons": {"echo x > f": asking, "ls": allowed}}
    assert create_permission_request_message("Bash", one) == (
        "This Bash command contains multiple operations. The following part requires approval: echo x")
    two = {"type": "subcommandResults", "reasons": {"a > f": asking, "b": passing}}
    assert create_permission_request_message("Other", two) == (
        "This Other command contains multiple operations. The following parts require approval: a > f, b")
    assert create_permission_request_message("Bash", {"type": "subcommandResults", "reasons": {"ls": allowed}}) == (
        "This Bash command contains multiple operations that require approval")
    assert create_permission_request_message("Bash", {"type": "subcommandResults", "reasons": None}) == (
        "This Bash command contains multiple operations that require approval")


# ── dangerous allow rules (the TDD managed tier) ─────────────────────────────

@pytest.mark.parametrize("content", [
    None, "", "*", " * ", "python", "python:*", "python*", "python *", "python -c *", "python -m pytest:*",
    "PYTHON:*", "npm run:*", "bash", "sh -c*", "env:*", "xargs", "sudo *", "eval", "exec:*", "ssh",
    "zsh", "fish", "node", "deno", "tsx", "ruby", "perl", "php", "lua", "npx", "bunx", "yarn run",
    "pnpm run", "bun run", "python3", "python2",
])
def test_dangerous_bash_permissions(content):
    assert dg.is_dangerous_bash_permission("Bash", content)


@pytest.mark.parametrize("content", [
    "pytest:*", "pip install:*", "pip install -r requirements.txt", "python -m", "python -c x",
    "npm install", "git status", "ls:*", "pythonic", "python foo",
])
def test_safe_bash_permissions(content):
    assert not dg.is_dangerous_bash_permission("Bash", content)


def test_only_bash_and_agent_rules_can_be_dangerous():
    assert not dg.is_dangerous_bash_permission("Edit", None)
    assert dg.is_dangerous_task_permission("Agent", "tester")
    assert dg.is_dangerous_task_permission("Task", None)
    assert not dg.is_dangerous_task_permission("Bash", None)
    assert dg.is_dangerous_classifier_permission("Agent", None)
    assert dg.is_dangerous_classifier_permission("Bash", "python:*")
    assert not dg.is_dangerous_classifier_permission("Bash", "pytest:*")
    assert dg.DANGEROUS_BASH_PATTERNS[:len(dg.CROSS_PLATFORM_CODE_EXEC)] == dg.CROSS_PLATFORM_CODE_EXEC
    assert dg.DANGEROUS_BASH_PATTERNS[len(dg.CROSS_PLATFORM_CODE_EXEC):] == (
        "zsh", "fish", "eval", "exec", "env", "xargs", "sudo")


def test_find_dangerous_only_looks_at_allow_rules():
    deny = PermissionRule("Bash", DENY, "python:*", S.PROJECT_SETTINGS)
    rules = (allow("pytest:*"), allow("python:*"), deny, allow(None, tool="Agent"))
    assert dg.find_dangerous_classifier_permissions(rules) == (rules[1], rules[3])


def test_strip_removes_editable_sources_only_and_records_them(caplog):
    editable_sources = (S.USER_SETTINGS, S.PROJECT_SETTINGS, S.LOCAL_SETTINGS, S.SESSION, S.CLI_ARG)
    editable = [allow("python:*", s) for s in editable_sources]
    kept = [allow("python:*", S.POLICY_SETTINGS), allow("python:*", S.FLAG_SETTINGS),
            allow("python:*", S.COMMAND), allow("pytest:*")]
    ctx = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, always_allow_rules=(*editable, *kept))
    with caplog.at_level(logging.INFO, logger="TDDOrchestrator.Permissions"):
        stripped = dg.strip_dangerous_permissions(ctx)
    assert stripped.always_allow_rules == tuple(kept)
    assert stripped.stripped_dangerous_rules == tuple(editable)
    assert stripped.mode == PermissionMode.ACCEPT_EDITS
    messages = [r.getMessage() for r in caplog.records]
    assert "Ignoring dangerous permission Bash(python:*) from projectSettings (bypasses the TDD phase gate)" in messages
    assert len(messages) == 8
    assert dg.PERMISSION_UPDATE_DESTINATIONS == frozenset(
        {S.USER_SETTINGS, S.PROJECT_SETTINGS, S.LOCAL_SETTINGS, S.SESSION, S.CLI_ARG})


def test_strip_names_tool_wide_rules_with_a_star(caplog):
    with caplog.at_level(logging.INFO, logger="TDDOrchestrator.Permissions"):
        out = dg.strip_dangerous_permissions(ToolPermissionContext(always_allow_rules=(allow(None),)))
    assert out.always_allow_rules == ()
    assert caplog.records[0].getMessage() == (
        "Ignoring dangerous permission Bash(*) from projectSettings (bypasses the TDD phase gate)")


def test_strip_is_identity_on_a_clean_context():
    ctx = ToolPermissionContext(always_allow_rules=(allow("pytest:*"),))
    assert dg.strip_dangerous_permissions(ctx) == ctx


# ── settings sources ─────────────────────────────────────────────────────────

def locations(tmp_path: Path, flag: Path | None = None) -> st.SettingsLocations:
    return st.SettingsLocations(project_root=tmp_path / "proj", home=tmp_path / "home",
                                flag_settings_path=flag, managed_dir=tmp_path / "etc")


def test_source_order_and_constants():
    assert st.SETTING_SOURCES == (S.USER_SETTINGS, S.PROJECT_SETTINGS, S.LOCAL_SETTINGS,
                                  S.FLAG_SETTINGS, S.POLICY_SETTINGS)
    assert st.SETTINGS_SOURCES == st.SETTING_SOURCES[:3]
    assert (st.MANAGED_DIR, st.MANAGED_SETTINGS_FILENAME, st.MANAGED_DROP_IN_DIRNAME) == (
        "/etc/tddagents", "managed-settings.json", "managed-settings.d")
    assert st.SettingsLocations(project_root=Path("/p")).managed_dir == Path("/etc/tddagents")
    assert st.SettingsLocations(project_root=Path("/p")).user_config_dir == Path.home() / ".tddagents"


def test_settings_file_paths_and_roots(tmp_path):
    flag = tmp_path / "flags" / "s.json"
    loc = locations(tmp_path, flag)
    assert st.settings_file_path_for_source(S.USER_SETTINGS, loc) == tmp_path / "home/.tddagents/settings.json"
    assert st.settings_file_path_for_source(S.PROJECT_SETTINGS, loc) == tmp_path / "proj/.tddagents/settings.json"
    assert st.settings_file_path_for_source(S.LOCAL_SETTINGS, loc) == (
        tmp_path / "proj/.tddagents/settings.local.json")
    assert st.settings_file_path_for_source(S.POLICY_SETTINGS, loc) == tmp_path / "etc/managed-settings.json"
    assert st.settings_file_path_for_source(S.FLAG_SETTINGS, loc) == flag
    assert st.settings_file_path_for_source(S.SESSION, loc) is None
    assert st.settings_paths(tmp_path / "proj", tmp_path / "home") == [
        tmp_path / "home/.tddagents/settings.json", tmp_path / "proj/.tddagents/settings.json",
        tmp_path / "proj/.tddagents/settings.local.json"]
    assert st.settings_root_path_for_source(S.USER_SETTINGS, loc) == tmp_path / "home/.tddagents"
    assert st.settings_root_path_for_source(S.FLAG_SETTINGS, loc) == (tmp_path / "flags").resolve()
    assert st.settings_root_path_for_source(S.FLAG_SETTINGS, locations(tmp_path)) is None
    assert st.settings_root_path_for_source(S.PROJECT_SETTINGS, loc) is None
    assert st.settings_root_path_for_source(S.POLICY_SETTINGS, loc) is None


def test_parse_settings_file(tmp_path, caplog):
    assert st.parse_settings_file(tmp_path / "missing") is None
    assert st.parse_settings_file(write(tmp_path / "empty.json", "  \n")) == {}
    assert st.parse_settings_file(write(tmp_path / "ok.json", {"a": 1})) == {"a": 1}
    with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Permissions"):
        assert st.parse_settings_file(write(tmp_path / "list.json", [1])) is None
        assert st.parse_settings_file(write(tmp_path / "bad.json", "{")) is None
    assert f"Ignoring malformed settings at {tmp_path / 'list.json'}: not a JSON object" in [
        r.getMessage() for r in caplog.records]


def test_merge_settings_is_lodash_merge_with_array_union():
    base: dict[str, Any] = {"permissions": {"allow": ["a", "b"], "defaultMode": "default"}, "x": 1, "keep": True}
    top = {"permissions": {"allow": ["b", "c"], "defaultMode": "plan"}, "x": {"y": 2}, "new": None}
    assert st.merge_settings(base, top) == {
        "permissions": {"allow": ["a", "b", "c"], "defaultMode": "plan"},
        "x": {"y": 2}, "keep": True, "new": None}
    assert base["permissions"]["allow"] == ["a", "b"]  # not mutated
    assert st.merge_settings({"a": [1]}, {"a": 2}) == {"a": 2}
    assert st.merge_settings({"a": {"b": 1}}, {"a": [1]}) == {"a": [1]}


def test_managed_settings_base_then_sorted_drop_ins(tmp_path):
    etc = tmp_path / "etc"
    assert st.load_managed_file_settings(etc) is None
    write(etc / "managed-settings.json", {"permissions": {"allow": ["Bash(base)"]}, "v": "base"})
    write(etc / "managed-settings.d/20-b.json", {"permissions": {"allow": ["Bash(b)"]}, "v": "b"})
    write(etc / "managed-settings.d/10-a.json", {"permissions": {"allow": ["Bash(a)"]}, "v": "a"})
    write(etc / "managed-settings.d/.hidden.json", {"v": "hidden"})
    write(etc / "managed-settings.d/notes.txt", "{}")
    write(etc / "managed-settings.d/empty.json", "")
    (etc / "managed-settings.d/dir.json").mkdir()
    assert st.load_managed_file_settings(etc) == {
        "permissions": {"allow": ["Bash(base)", "Bash(a)", "Bash(b)"]}, "v": "b"}


def test_managed_drop_ins_alone_count_and_empty_base_does_not(tmp_path):
    etc = tmp_path / "etc"
    write(etc / "managed-settings.json", "{}")
    assert st.load_managed_file_settings(etc) is None
    write(etc / "managed-settings.d/x.json", {"v": 1})
    assert st.load_managed_file_settings(etc) == {"v": 1}


def test_load_settings_merges_every_source_in_order_and_reads_a_file_once(tmp_path):
    flag = write(tmp_path / "flag.json", {"v": "flag", "flagonly": 1})
    loc = locations(tmp_path, flag)
    write(tmp_path / "home/.tddagents/settings.json", {"v": "user", "useronly": 1})
    write(tmp_path / "proj/.tddagents/settings.json", {"v": "project"})
    write(tmp_path / "proj/.tddagents/settings.local.json", {"v": "local"})
    assert st.load_settings(loc) == {"v": "flag", "useronly": 1, "flagonly": 1}
    write(tmp_path / "etc/managed-settings.json", {"v": "policy"})
    assert st.load_settings(loc)["v"] == "policy"
    # A flag file that *is* the project file is read once, not twice.
    same = locations(tmp_path, tmp_path / "proj/.tddagents/settings.json")
    write(tmp_path / "proj/.tddagents/settings.json", {"permissions": {"allow": ["x"]}})
    write(tmp_path / "etc/managed-settings.json", "{}")
    assert st.load_settings(same) == {"v": "local", "useronly": 1, "permissions": {"allow": ["x"]}}


def test_rules_from_every_source_and_managed_only(tmp_path):
    flag = write(tmp_path / "flag.json", {"permissions": {"deny": ["Bash(f)"]}})
    loc = locations(tmp_path, flag)
    write(tmp_path / "proj/.tddagents/settings.json", {"permissions": {"allow": ["Bash(p)"]}})
    write(tmp_path / "etc/managed-settings.json", {"permissions": {"ask": ["Bash(m)"]}})
    assert st.load_all_permission_rules_from_disk(loc) == [
        PermissionRule("Bash", ALLOW, "p", S.PROJECT_SETTINGS),
        PermissionRule("Bash", DENY, "f", S.FLAG_SETTINGS),
        PermissionRule("Bash", ASK, "m", S.POLICY_SETTINGS)]
    assert st.should_allow_managed_permission_rules_only(loc) is False
    write(tmp_path / "etc/managed-settings.json",
          {"allowManagedPermissionRulesOnly": True, "permissions": {"ask": ["Bash(m)"]}})
    assert st.should_allow_managed_permission_rules_only(loc) is True
    assert st.load_all_permission_rules_from_disk(loc) == [PermissionRule("Bash", ASK, "m", S.POLICY_SETTINGS)]
    write(tmp_path / "etc/managed-settings.json", {"allowManagedPermissionRulesOnly": "yes"})
    assert st.should_allow_managed_permission_rules_only(loc) is False


def test_get_settings_for_source(tmp_path):
    loc = locations(tmp_path)
    assert st.get_settings_for_source(S.FLAG_SETTINGS, loc) is None
    assert st.get_settings_for_source(S.SESSION, loc) is None
    write(tmp_path / "etc/managed-settings.d/a.json", {"z": 1})
    assert st.get_settings_for_source(S.POLICY_SETTINGS, loc) == {"z": 1}
    assert st.get_permission_rules_for_source(S.POLICY_SETTINGS, loc) == []


def test_load_permission_rules_ignores_flag_and_policy(tmp_path):
    write(tmp_path / "proj/.tddagents/settings.json", {"permissions": {"allow": ["Bash(p)"]}})
    assert st.load_permission_rules(tmp_path / "proj", tmp_path / "home") == [
        PermissionRule("Bash", ALLOW, "p", S.PROJECT_SETTINGS)]


@pytest.mark.parametrize("tools, expected", [
    ([], []),
    (["", "Bash"], ["Bash"]),
    (["Bash,Edit"], ["Bash", "Edit"]),
    (["Bash Edit  Read"], ["Bash", "Edit", "Read"]),
    (["Bash(git status, git log) Edit"], ["Bash(git status, git log)", "Edit"]),
    ([" a ,, b "], ["a", "b"]),
    (["Bash(x)y,z"], ["Bash(x)y", "z"]),
])
def test_parse_tool_list_from_cli(tools, expected):
    assert st.parse_tool_list_from_cli(tools) == expected


def test_permission_mode_from_string():
    for mode in ("default", "acceptEdits", "plan", "bypassPermissions", "dontAsk"):
        assert st.permission_mode_from_string(mode) == PermissionMode(mode)
    # Phase 4b ports the classifier, so `auto` is a user-addressable mode; `bubble` never is.
    assert st.permission_mode_from_string("auto") == PermissionMode.AUTO
    assert st.permission_mode_from_string("bubble") == PermissionMode.DEFAULT
    assert st.permission_mode_from_string("nope") == PermissionMode.DEFAULT


def _mode(cli=None, skip=False, settings=None):
    return st.initial_permission_mode_from_cli(
        permission_mode_cli=cli, dangerously_skip_permissions=skip, settings=settings or {})


def test_initial_mode_precedence():
    assert _mode() == (PermissionMode.DEFAULT, None)
    assert _mode(settings={"permissions": {"defaultMode": "acceptEdits"}}) == (PermissionMode.ACCEPT_EDITS, None)
    assert _mode(cli="plan", settings={"permissions": {"defaultMode": "acceptEdits"}}) == (PermissionMode.PLAN, None)
    assert _mode(cli="plan", skip=True) == (PermissionMode.BYPASS_PERMISSIONS, None)
    assert _mode(cli="", settings={"permissions": {"defaultMode": ""}}) == (PermissionMode.DEFAULT, None)
    assert _mode(settings={"permissions": {"defaultMode": 3}}) == (PermissionMode.DEFAULT, None)
    assert _mode(settings={"permissions": "x"}) == (PermissionMode.DEFAULT, None)


def test_initial_mode_skips_disabled_bypass(caplog):
    disabled = {"permissions": {"disableBypassPermissionsMode": "disable", "defaultMode": "acceptEdits"}}
    with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Permissions"):
        assert _mode(skip=True, settings=disabled) == (
            PermissionMode.ACCEPT_EDITS, "Bypass permissions mode was disabled by settings")
    assert "bypassPermissions mode is disabled by settings" in [r.getMessage() for r in caplog.records]
    only_bypass = {"permissions": {"disableBypassPermissionsMode": "disable", "defaultMode": "bypassPermissions"}}
    assert _mode(settings=only_bypass) == (PermissionMode.DEFAULT, "Bypass permissions mode was disabled by settings")
    not_disabled = {"permissions": {"disableBypassPermissionsMode": "enable"}}
    assert _mode(cli="bypassPermissions", settings=not_disabled) == (PermissionMode.BYPASS_PERMISSIONS, None)


def test_validate_additional_directory(tmp_path):
    work = tmp_path / "ws"
    (work / "inner").mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    write(tmp_path / "file.txt", "x")
    assert st.validate_additional_directory(str(other), work) == (str(other), None)
    assert st.validate_additional_directory("../other", work) == (str(other), None)
    assert st.validate_additional_directory(str(tmp_path / "gone"), work) == (None, None)
    assert st.validate_additional_directory(str(tmp_path / "file.txt"), work) == (
        None, f"{tmp_path / 'file.txt'} is not a directory.")
    assert st.validate_additional_directory(str(work / "inner"), work) == (None, None)
    assert st.validate_additional_directory(str(work), work) == (None, None)
    assert st.validate_additional_directory(str(other), None) == (str(other), None)


def test_initialize_tool_permission_context(tmp_path):
    flag = write(tmp_path / "flags/s.json", {"permissions": {"additionalDirectories": [str(tmp_path / "extra")]}})
    (tmp_path / "extra").mkdir()
    (tmp_path / "added").mkdir()
    write(tmp_path / "nota", "x")
    loc = locations(tmp_path, flag)
    write(tmp_path / "proj/.tddagents/settings.json", {"permissions": {"allow": ["Bash(p)"], "deny": ["Read(x)"]}})
    ctx, warnings = st.initialize_tool_permission_context(
        loc, permission_mode=PermissionMode.PLAN,
        allowed_tools_cli=["Bash(a) Edit"], disallowed_tools_cli=["WebFetch"],
        add_dirs=[str(tmp_path / "added"), str(tmp_path / "extra"), str(tmp_path / "nota")],
        working_dir=tmp_path / "ws")
    assert ctx.mode == PermissionMode.PLAN
    assert ctx.always_allow_rules == (
        PermissionRule("Bash", ALLOW, "p", S.PROJECT_SETTINGS),
        PermissionRule("Bash", ALLOW, "a", S.CLI_ARG), PermissionRule("Edit", ALLOW, None, S.CLI_ARG))
    assert ctx.always_deny_rules == (
        PermissionRule("Read", DENY, "x", S.PROJECT_SETTINGS), PermissionRule("WebFetch", DENY, None, S.CLI_ARG))
    assert ctx.always_ask_rules == ()
    assert ctx.additional_working_directories == (str(tmp_path / "extra"), str(tmp_path / "added"))
    assert warnings == [f"{tmp_path / 'nota'} is not a directory."]
    assert ctx.should_avoid_permission_prompts is True
    assert ctx.is_bypass_permissions_mode_available is False
    assert dict(ctx.settings_roots) == {
        S.USER_SETTINGS: str(tmp_path / "home/.tddagents"), S.FLAG_SETTINGS: str((tmp_path / "flags").resolve())}


def test_bypass_availability(tmp_path):
    loc = locations(tmp_path)
    for mode, skip, expected in ((PermissionMode.BYPASS_PERMISSIONS, False, True),
                                 (PermissionMode.DEFAULT, True, True),
                                 (PermissionMode.DEFAULT, False, False)):
        ctx, _ = st.initialize_tool_permission_context(
            loc, permission_mode=mode, allow_dangerously_skip_permissions=skip, should_avoid_permission_prompts=False)
        assert ctx.is_bypass_permissions_mode_available is expected
        assert ctx.should_avoid_permission_prompts is False
    write(tmp_path / "home/.tddagents/settings.json", {"permissions": {"disableBypassPermissionsMode": "disable"}})
    ctx, _ = st.initialize_tool_permission_context(loc, permission_mode=PermissionMode.BYPASS_PERMISSIONS)
    assert ctx.is_bypass_permissions_mode_available is False


def test_additional_directories_must_be_strings(tmp_path):
    (tmp_path / "d").mkdir()
    write(tmp_path / "proj/.tddagents/settings.json",
          {"permissions": {"additionalDirectories": [3, str(tmp_path / "d")]}})
    ctx, _ = st.initialize_tool_permission_context(locations(tmp_path), permission_mode=PermissionMode.DEFAULT)
    assert ctx.additional_working_directories == (str(tmp_path / "d"),)
    write(tmp_path / "proj/.tddagents/settings.json", {"permissions": {"additionalDirectories": "nope"}})
    ctx, _ = st.initialize_tool_permission_context(locations(tmp_path), permission_mode=PermissionMode.DEFAULT)
    assert ctx.additional_working_directories == ()


def test_build_tool_permission_context_end_to_end(tmp_path, caplog):
    flag = write(tmp_path / "flag.json",
                 {"permissions": {"defaultMode": "acceptEdits", "additionalDirectories": [str(tmp_path / "f")]}})
    write(tmp_path / "f", "file, not dir")
    write(tmp_path / "proj/.tddagents/settings.json", {"permissions": {"allow": ["Bash(python:*)", "Bash(ls)"]}})
    write(tmp_path / "etc/managed-settings.json",
          {"permissions": {"allow": ["Bash(node:*)"], "disableBypassPermissionsMode": "disable"}})
    with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Permissions"):
        ctx = st.build_tool_permission_context(
            tmp_path / "proj", tmp_path / "home", flag_settings_path=str(flag), managed_dir=str(tmp_path / "etc"),
            dangerously_skip_permissions=True, allowed_tools_cli=["Agent"], disallowed_tools_cli=["Bash(rm:*)"],
            working_dir=str(tmp_path / "ws"))
    assert ctx.mode == PermissionMode.ACCEPT_EDITS
    assert [r.rule_content for r in ctx.always_allow_rules] == ["ls", "node:*"]  # policy survives the strip
    assert [(r.tool_name, r.rule_content) for r in ctx.stripped_dangerous_rules] == [
        ("Bash", "python:*"), ("Agent", None)]
    assert [r.rule_content for r in ctx.always_deny_rules] == ["rm:*"]
    messages = [r.getMessage() for r in caplog.records]
    assert "Bypass permissions mode was disabled by settings" in messages
    assert f"{tmp_path / 'f'} is not a directory." in messages
    defaults = st.build_tool_permission_context(tmp_path / "nowhere", tmp_path / "home2", managed_dir=tmp_path / "e2")
    assert (defaults.mode, defaults.should_avoid_permission_prompts) == (PermissionMode.DEFAULT, True)
    assert defaults.additional_working_directories == ()


# ── PermissionRequest hooks ──────────────────────────────────────────────────

class _Dispatcher(HookDispatcher):
    """A real dispatcher whose hooks are replaced by one scripted PermissionRequest decision."""

    def __init__(self, decision=None, raises=None):
        super().__init__(snapshot=HooksConfigSnapshot(), session_id="s", cwd=".", project_dir=".")
        self.decision, self.raises, self.calls = decision, raises, []

    async def execute_hooks(self, hook_input, tool_use_id, **kwargs):
        self.calls.append((hook_input["hook_event_name"], dict(hook_input), tool_use_id))
        if self.raises:
            raise self.raises
        yield AggregatedHookResult(permission_request_result=self.decision)


def _run(decision=None, dispatcher=None, args=None):
    ctx = tool_context_for(AppStateStore(), hook_dispatcher=dispatcher if dispatcher is not None
                           else _Dispatcher(decision))
    tool = SimpleNamespace(name="Bash")
    tool_args = args if args is not None else {"command": "c"}
    res = asyncio.run(run_permission_request_hooks_for_headless_agent(
        tool, tool_args, "tu", ctx, PermissionMode.DEFAULT))
    return res, ctx


def test_decision_reader():
    assert PERMISSION_REQUEST_EVENT == "PermissionRequest"
    assert permission_request_decision(None) is None
    assert permission_request_decision(SimpleNamespace(permission_request_result={"behavior": "ask"})) is None
    assert permission_request_decision(SimpleNamespace(permission_request_result="allow")) is None
    good = {"behavior": "deny"}
    assert permission_request_decision(SimpleNamespace(permission_request_result=good)) is good


def test_no_dispatcher_or_no_decision_is_none():
    ctx = tool_context_for(AppStateStore())
    tool = SimpleNamespace(name="Bash")
    coro = run_permission_request_hooks_for_headless_agent(tool, {}, "t", ctx, "default")
    assert asyncio.run(coro) is None
    assert _run(None)[0] is None


def test_hook_payload():
    dispatcher = _Dispatcher(None)
    _run(dispatcher=dispatcher)
    assert dispatcher.calls == [("PermissionRequest", {
        "session_id": "s", "transcript_path": "", "cwd": ".", "permission_mode": "default",
        "hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "c"}}, "tu")]


def test_hook_allow_with_and_without_updated_input(caplog):
    res, _ = _run({"behavior": "allow", "updatedInput": {"command": "safe"}})
    assert (res.behavior, res.updated_input, res.decision_reason) == (
        ALLOW, {"command": "safe"}, {"type": "hook", "hookName": "PermissionRequest"})
    res, _ = _run({"behavior": "allow", "updatedInput": "bad"})
    assert res.updated_input == {"command": "c"}
    with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Permissions"):
        res, _ = _run({"behavior": "allow", "updatedPermissions": [{"x": 1}]})
    assert res is None  # malformed updatedPermissions: no decision at all, as upstream's Zod parse
    assert "Ignoring a PermissionRequest decision with malformed updatedPermissions." in [
        r.getMessage() for r in caplog.records]


def test_hook_deny_message_and_interrupt():
    res, ctx = _run({"behavior": "deny", "message": "no"})
    assert (res.behavior, res.message, res.decision_reason) == (
        DENY, "no", {"type": "hook", "hookName": "PermissionRequest", "reason": "no"})
    assert ctx.cancel.cancelled is False
    res, ctx = _run({"behavior": "deny", "interrupt": True})
    assert (res.message, res.decision_reason["reason"]) == ("Permission denied by hook", None)
    assert ctx.cancel.cancelled is True
    _, ctx = _run({"behavior": "deny", "interrupt": "yes"})
    assert ctx.cancel.cancelled is False


def test_hook_failure_falls_through(caplog):
    with caplog.at_level(logging.ERROR, logger="TDDOrchestrator.Permissions"):
        res, _ = _run(dispatcher=_Dispatcher(raises=RuntimeError("boom")))
    assert res is None
    assert "PermissionRequest hook failed for headless agent" in [r.getMessage() for r in caplog.records]


# ── D6 structural invariant, against the shipped settings ────────────────────

def _repo_context(tmp_path: Path, ws: Path) -> ToolPermissionContext:
    return st.build_tool_permission_context(REPO, tmp_path / "home", managed_dir=tmp_path / "etc", working_dir=ws)


def _gate_call(tool_name, args, perm, ledger, ws, dispatcher=None):
    from app.loop.runner import build_builtin_tools

    tool = next(t for t in build_builtin_tools() if t.name == tool_name)
    store = AppStateStore(state=AppState(phase_ledger=ledger, tool_permission_context=perm))
    ctx = tool_context_for(store, workspace=SimpleNamespace(root=str(ws)), hook_dispatcher=dispatcher)
    return asyncio.run(has_permissions_to_use_tool(tool, args, ctx, "tu"))


RED = PhaseLedger(phase=TddPhase.RED)
GREEN = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)


def test_shipped_settings_lose_their_interpreter_allows(tmp_path):
    perm = _repo_context(tmp_path, tmp_path)
    assert perm.mode == PermissionMode.ACCEPT_EDITS
    assert {r.rule_content for r in perm.stripped_dangerous_rules} == {"python:*", "python -m pytest:*"}
    assert not dg.find_dangerous_classifier_permissions(perm.always_allow_rules)


@pytest.mark.parametrize("command", [
    "python -c \"open('calculator.py','w').write('x')\"",
    "python3 -c 'import os'",
    "python -m pytest && python -c 'open(\"app.py\",\"w\")'",
    "sh -c 'echo x > calc.py'",
    "echo 'def add(): ...' > calculator.py",
    "pip install -e . && python setup.py",
])
def test_red_never_lets_bash_write_production_code(tmp_path, command):
    perm = _repo_context(tmp_path, tmp_path)
    res = _gate_call("Bash", {"command": command}, perm, RED, tmp_path)
    assert res.behavior == DENY, command


@pytest.mark.parametrize("tool, args", [
    ("WriteFile", {"file_path": "calculator.py", "content": "x"}),
    ("Edit", {"file_path": "src/app.py", "old_string": "a", "new_string": "b"}),
])
def test_red_never_lets_file_tools_write_production_code(tmp_path, tool, args):
    perm = _repo_context(tmp_path, tmp_path)
    allow_hook = _Dispatcher({"behavior": "allow"})
    res = _gate_call(tool, args, perm, RED, tmp_path, allow_hook)
    assert res.behavior == DENY and res.decision_reason["type"] == "phase_rule"
    assert allow_hook.calls == []


def test_red_lets_tests_be_written_and_green_lets_code_be_written(tmp_path):
    perm = _repo_context(tmp_path, tmp_path)
    test_write = _gate_call("WriteFile", {"file_path": "tests/test_calc.py", "content": "x"}, perm, RED, tmp_path)
    assert test_write.behavior == ALLOW
    code_write = _gate_call("WriteFile", {"file_path": "calculator.py", "content": "x"}, perm, GREEN, tmp_path)
    assert code_write.behavior == ALLOW
    test_edit = _gate_call("WriteFile", {"file_path": "tests/test_calc.py", "content": "x"}, perm, GREEN, tmp_path)
    assert test_edit.behavior == DENY
    pytest_run = _gate_call("Bash", {"command": "pytest -q"}, perm, RED, tmp_path)
    assert pytest_run.behavior == ALLOW


def test_dangerous_files_stay_protected_under_the_shipped_settings(tmp_path):
    perm = _repo_context(tmp_path, tmp_path)
    res = _gate_call("WriteFile", {"file_path": "tests/.tddagents/settings.json", "content": "{}"}, perm, RED,
                     tmp_path)
    assert res.behavior == DENY and res.decision_reason["type"] == "asyncAgent"


# ── mutation-driven pins ─────────────────────────────────────────────────────

def test_find_last_unescaped_scans_every_index_down_to_zero():
    assert st._find_last_unescaped(")ab", ")") == 0
    assert st._find_last_unescaped("a)b", ")") == 1
    assert st._find_last_unescaped("a)bc", ")") == 1


def test_managed_drop_ins_skip_dot_files_and_non_json(tmp_path):
    etc = tmp_path / "etc"
    write(etc / "managed-settings.d/.hidden.json", {"hidden": 1})
    write(etc / "managed-settings.d/notes.txt", {"txt": 1})
    write(etc / "managed-settings.d/z.json", {"z": 1})
    assert st.load_managed_file_settings(etc) == {"z": 1}


def test_load_settings_reaches_policy_without_a_flag_file(tmp_path):
    write(tmp_path / "etc/managed-settings.json", {"v": "policy"})
    assert st.load_settings(locations(tmp_path)) == {"v": "policy"}


def test_load_settings_skips_a_repeated_file_but_not_later_sources(tmp_path):
    project = write(tmp_path / "proj/.tddagents/settings.json", {"v": "project"})
    write(tmp_path / "proj/.tddagents/settings.local.json", {"v": "local"})
    write(tmp_path / "etc/managed-settings.json", {"p": "policy"})
    assert st.load_settings(locations(tmp_path, project)) == {"v": "local", "p": "policy"}


def test_relative_additional_directory_without_a_working_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rel").mkdir()
    assert st.validate_additional_directory("rel", None) == ("rel", None)


def test_additional_directories_inside_the_working_dir_are_skipped(tmp_path):
    work = tmp_path / "ws"
    (work / "inner").mkdir(parents=True)
    ctx, warnings = st.initialize_tool_permission_context(
        locations(tmp_path), permission_mode=PermissionMode.DEFAULT, add_dirs=[str(work / "inner")],
        working_dir=work)
    assert (ctx.additional_working_directories, warnings) == ((), [])
    built = st.build_tool_permission_context(
        tmp_path / "proj", tmp_path / "home", managed_dir=tmp_path / "etc",
        add_dirs=[str(work / "inner"), str(tmp_path)], working_dir=str(work))
    assert built.additional_working_directories == (str(tmp_path),)


def test_claude_worktrees_as_the_last_two_segments():
    from app.loop.permissions.filesystem import is_dangerous_file_path_to_auto_edit

    assert not is_dangerous_file_path_to_auto_edit("/w/.claude/worktrees")


def test_unc_paths_are_suspicious_on_their_own(monkeypatch):
    """`containsVulnerableUncPath` is Windows-only upstream, so the platform is faked."""
    from app.loop.permissions.filesystem import has_suspicious_windows_path_pattern

    assert not has_suspicious_windows_path_pattern("\\\\server\\share\\x")
    monkeypatch.setattr("app.loop.permissions.bash_read_only_commands.get_platform", lambda: "windows")
    assert has_suspicious_windows_path_pattern("\\\\server\\share\\x")


def test_matching_rule_keeps_scanning_patterns_and_roots():
    from app.loop.permissions.filesystem import matching_rule_for_input

    cwd = "/work/ws"
    other, src = PermissionRule("Edit", DENY, "other/**", S.PROJECT_SETTINGS), PermissionRule(
        "Edit", DENY, "src/**", S.PROJECT_SETTINGS)
    assert matching_rule_for_input(f"{cwd}/src/a", ToolPermissionContext(always_deny_rules=(other, src)),
                                   "edit", "deny", cwd) is src
    user_rule = PermissionRule("Edit", DENY, "/secrets/**", S.USER_SETTINGS)
    ctx = ToolPermissionContext(always_deny_rules=(user_rule, src), settings_roots={S.USER_SETTINGS: "/home/u"})
    assert matching_rule_for_input(f"{cwd}/src/a", ctx, "edit", "deny", cwd) is src
    flag_rule = PermissionRule("Edit", DENY, "/x", S.FLAG_SETTINGS)
    sub = PermissionRule("Edit", DENY, "sub", S.PROJECT_SETTINGS)
    ctx = ToolPermissionContext(always_deny_rules=(flag_rule, sub), settings_roots={S.FLAG_SETTINGS: f"{cwd}/sub"})
    assert matching_rule_for_input(f"{cwd}/sub", ctx, "edit", "deny", cwd) is sub


def test_matching_rule_ignores_the_parent_of_the_root():
    from app.loop.permissions.filesystem import matching_rule_for_input

    star = PermissionRule("Edit", DENY, "*", S.PROJECT_SETTINGS)
    assert matching_rule_for_input("/work", ToolPermissionContext(always_deny_rules=(star,)), "edit", "deny",
                                   "/work/ws") is None


def test_is_path_allowed_honours_the_given_candidates_and_floating_allows():
    from app.loop.permissions.filesystem import PathCheckResult, is_path_allowed

    cwd = "/work/ws"
    edits = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
    assert is_path_allowed(f"{cwd}/a", edits, cwd, "write", (f"{cwd}/a", "/etc/elsewhere")) == PathCheckResult(False)
    out = PermissionRule("Edit", ALLOW, "out/**", S.PROJECT_SETTINGS)
    assert is_path_allowed(f"{cwd}/out/a", ToolPermissionContext(always_allow_rules=(out,)), cwd, "write") == (
        PathCheckResult(True, {"type": "rule", "rule": out}))


def test_write_check_resolves_a_relative_path_against_the_working_dir():
    from app.loop.permissions.filesystem import check_write_permission_for_tool

    edits = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
    assert check_write_permission_for_tool("a.py", {}, edits, "/work/ws").behavior == ALLOW


def test_workspace_absolute_path_strips_only_slashes():
    from app.loop.permissions.filesystem import workspace_absolute_path

    assert workspace_absolute_path("/XX/f", "/ws") == "/ws/XX/f"


def test_subcommand_message_tolerates_results_without_a_behavior():
    reasons = {"type": "subcommandResults", "reasons": {"odd": object()}}
    assert create_permission_request_message("Bash", reasons) == (
        "This Bash command contains multiple operations that require approval")


def test_interaction_predicate_receives_the_input():
    tool = SimpleNamespace(
        name="T",
        requires_user_interaction=lambda args: args.get("ask") is True,
        check_permissions=lambda args, ctx: PermissionResult(behavior=ASK, message="m"),
    )
    perm = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
    ctx = tool_context_for(AppStateStore(), permission_context=perm)
    asked = asyncio.run(has_permissions_to_use_tool(tool, {"ask": True}, ctx))
    assert (asked.behavior, asked.message) == (ASK, "m")
    bypassed = asyncio.run(has_permissions_to_use_tool(tool, {}, ctx))
    assert bypassed.behavior == ALLOW


def test_run_permission_context_threads_every_input(tmp_path):
    from app.loop.config import build_run_config
    from app.loop.runner import run_permission_context

    home, etc, project = tmp_path / "home", tmp_path / "etc", tmp_path / "proj"
    write(home / ".tddagents/settings.json", {"permissions": {"deny": ["Bash(from-home)"]}})
    flag = write(tmp_path / "flags/f.json", {"permissions": {"ask": ["Bash(from-flag)"]}})
    write(etc / "managed-settings.json", {"permissions": {"allow": ["Bash(from-policy)"]}})
    (tmp_path / "ws" / "inner").mkdir(parents=True)
    write(project / ".tddagents/settings.json",
          {"permissions": {"additionalDirectories": [str(tmp_path / "ws" / "inner")]}})
    config = build_run_config("r", postgres_checkpointing=False, permission_mode="plan",
                              settings_path=str(flag), dangerously_skip_permissions=True)
    perm = run_permission_context(config, SimpleNamespace(root=str(tmp_path / "ws")), project, home, etc)
    assert perm.mode == PermissionMode.BYPASS_PERMISSIONS
    assert perm.is_bypass_permissions_mode_available is True
    assert [r.rule_content for r in perm.always_deny_rules] == ["from-home"]
    assert [r.rule_content for r in perm.always_ask_rules] == ["from-flag"]
    assert [r.rule_content for r in perm.always_allow_rules] == ["from-policy"]
    assert perm.additional_working_directories == ()  # inside the workspace root: skipped
    plan = run_permission_context(build_run_config("r", postgres_checkpointing=False, permission_mode="plan"),
                                  SimpleNamespace(root=str(tmp_path / "ws")), project, home, etc)
    assert (plan.mode, plan.is_bypass_permissions_mode_available) == (PermissionMode.PLAN, False)


def test_run_permission_context_defaults_to_cwd_and_etc(tmp_path, monkeypatch):
    from app.loop.config import build_run_config
    from app.loop.runner import run_permission_context

    monkeypatch.chdir(tmp_path)
    write(tmp_path / ".tddagents/settings.json", {"permissions": {"defaultMode": "acceptEdits"}})
    seen: dict[str, Any] = {}

    def spy(*args: Any, **kwargs: Any) -> ToolPermissionContext:
        seen.update(kwargs, args=args)
        return ToolPermissionContext()

    monkeypatch.setattr("app.loop.runner.build_tool_permission_context", spy)
    run_permission_context(build_run_config("r", postgres_checkpointing=False), object())
    assert seen["args"] == (tmp_path, None)
    assert seen["managed_dir"] == "/etc/tddagents"
    assert seen["working_dir"] is None
