"""
Live settings (`reference/claude-code/src/utils/settings/changeDetector.ts`, `applySettingsChange.ts`,
`internalWrites.ts`, `hooksConfigSnapshot.ts` -> `updateHooksConfigSnapshot`): a settings file
changing during a session reaches the run's permission rules and hooks, through the ConfigChange
hooks, with the TDD managed tier still applied.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.utils.settings.change_detector as cd
from app.bootstrap.state import get_flag_settings_inline, set_flag_settings_inline
from app.schemas.hooks import CommandHook, HookMatcher
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.plan import ItemStatus, TddPlan
from app.tool import tool_context_for
from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)
from app.utils.hooks.hook_dispatcher import build_hook_dispatcher
from app.utils.hooks.hooks import (
    CONFIG_CHANGE_SOURCES,
    HookDispatcher,
    HookOutsideReplResult,
    execute_config_change_hooks,
    has_blocking_result,
)
from app.utils.hooks.hooks_config_snapshot import HooksConfigSnapshot, update_hooks_config_snapshot
from app.utils.permissions.permission_setup import create_disabled_bypass_permissions_context
from app.utils.permissions.permission_update import (
    PermissionUpdate,
    convert_rules_to_updates,
    sync_permission_rules_from_disk,
    update_settings_for_source,
)
from app.utils.permissions.permissions import has_permissions_to_use_tool
from app.utils.settings.apply_settings_change import apply_settings_change
from app.utils.settings.change_detector import (
    SettingsChangeDetector,
    setting_source_to_config_change_source,
)
from app.utils.settings.internal_writes import (
    clear_internal_writes,
    consume_internal_write,
    mark_internal_write,
)
from app.utils.settings.settings import (
    SettingsLocations,
    build_tool_permission_context,
    get_settings_for_source,
    load_all_permission_rules_from_disk,
    load_settings,
)
from tests.plan_helpers import CURRENT_ITEM_PLAN, TEST_PROJECT_CONFIG

S = PermissionRuleSource
ALLOW, DENY, ASK = PermissionBehavior.ALLOW, PermissionBehavior.DENY, PermissionBehavior.ASK


def write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def locations(tmp_path: Path, **extra: Any) -> SettingsLocations:
    return SettingsLocations(project_root=tmp_path / "proj", home=tmp_path / "home", managed_dir=tmp_path / "etc",
                             **extra)


def project_settings(tmp_path: Path) -> Path:
    return tmp_path / "proj" / ".tddagents" / "settings.json"


def rule(tool: str, behavior: PermissionBehavior, source: PermissionRuleSource, content: str | None = None
         ) -> PermissionRule:
    return PermissionRule(tool_name=tool, rule_behavior=behavior, rule_content=content, source=source)


# ── internalWrites.ts ────────────────────────────────────────────────────────

def test_an_internal_write_is_consumed_once_within_its_window(monkeypatch):
    clear_internal_writes()
    now = [100.0]
    monkeypatch.setattr("app.utils.settings.internal_writes.time.monotonic", lambda: now[0])
    mark_internal_write("/a")
    now[0] = 104.999
    assert consume_internal_write("/a", 5000) is True
    assert consume_internal_write("/a", 5000) is False
    mark_internal_write("/a")
    now[0] = 109.999
    assert consume_internal_write("/a", 5000) is False  # 5s and more: an external change
    assert consume_internal_write("/b", 5000) is False
    mark_internal_write("/c")
    clear_internal_writes()
    assert consume_internal_write("/c", 5000) is False


def test_update_settings_for_source_marks_its_write(tmp_path):
    clear_internal_writes()
    loc = locations(tmp_path)
    assert update_settings_for_source(S.PROJECT_SETTINGS, loc, {"x": 1}) is None
    assert consume_internal_write(str(project_settings(tmp_path).resolve()), cd.INTERNAL_WRITE_WINDOW_MS)


# ── getWatchTargets / getSourceForPath ───────────────────────────────────────

def test_constants_are_upstreams():
    assert (cd.FILE_STABILITY_THRESHOLD_MS, cd.FILE_STABILITY_POLL_INTERVAL_MS, cd.INTERNAL_WRITE_WINDOW_MS) == (
        1000, 500, 5000)
    assert cd.DELETION_GRACE_MS == 1700


def test_watch_targets_are_the_directories_holding_a_settings_file(tmp_path):
    flag = tmp_path / "flags" / "f.json"
    write(flag, {})
    write(project_settings(tmp_path), {})
    loc = locations(tmp_path, flag_settings_path=flag)
    detector = SettingsChangeDetector(loc)
    dirs, files, drop_in = detector.get_watch_targets()
    project_dir = str((tmp_path / "proj" / ".tddagents").resolve())
    # The project directory has a file: both its potential files are watched. No user file, no
    # policy file: those directories are not. The flag file is never watched.
    assert dirs == [project_dir] and drop_in is None
    assert files == {f"{project_dir}/settings.json", f"{project_dir}/settings.local.json"}
    write(tmp_path / "home" / ".tddagents" / "settings.json", {})
    (tmp_path / "etc" / "managed-settings.d").mkdir(parents=True)
    dirs, files, drop_in = detector.get_watch_targets()
    assert dirs == [str((tmp_path / "home" / ".tddagents").resolve()), project_dir,
                    str((tmp_path / "etc" / "managed-settings.d").resolve())]
    assert drop_in == dirs[-1] and len(files) == 3


def test_the_source_of_a_path(tmp_path):
    loc = locations(tmp_path, flag_settings_path=tmp_path / "f.json")
    detector = SettingsChangeDetector(loc)
    proj = str((tmp_path / "proj" / ".tddagents").resolve())
    assert detector.get_source_for_path(f"{proj}/settings.json") == S.PROJECT_SETTINGS
    assert detector.get_source_for_path(f"{proj}/settings.local.json") == S.LOCAL_SETTINGS
    assert detector.get_source_for_path(str((tmp_path / "home/.tddagents/settings.json").resolve())) == S.USER_SETTINGS
    assert detector.get_source_for_path(str((tmp_path / "etc/managed-settings.json").resolve())) == S.POLICY_SETTINGS
    drop_in = str((tmp_path / "etc" / "managed-settings.d").resolve())
    assert detector.get_source_for_path(f"{drop_in}/10-x.json") == S.POLICY_SETTINGS
    assert detector.get_source_for_path(str((tmp_path / "f.json").resolve())) == S.FLAG_SETTINGS
    assert detector.get_source_for_path(f"{proj}/other.json") is None


def test_config_change_sources():
    assert [setting_source_to_config_change_source(s) for s in (
        S.USER_SETTINGS, S.PROJECT_SETTINGS, S.LOCAL_SETTINGS, S.FLAG_SETTINGS, S.POLICY_SETTINGS)] == [
        "user_settings", "project_settings", "local_settings", "policy_settings", "policy_settings"]
    assert CONFIG_CHANGE_SOURCES == ("user_settings", "project_settings", "local_settings", "policy_settings",
                                     "skills")


# ── the poll: stability, add, change, delete ─────────────────────────────────

class Recorder:
    def __init__(self, detector: SettingsChangeDetector) -> None:
        self.seen: list[PermissionRuleSource] = []
        detector.subscribe(self.seen.append)


async def _started(loc: SettingsLocations, **kwargs: Any) -> SettingsChangeDetector:
    detector = SettingsChangeDetector(loc, poll_interval_ms=3_600_000, **kwargs)
    await detector.initialize()
    return detector


def test_a_change_counts_once_the_file_is_stable(tmp_path):
    write(project_settings(tmp_path), {})

    async def scenario() -> list[Any]:
        detector = await _started(locations(tmp_path), stability_threshold_ms=1000)
        recorder = Recorder(detector)
        write(project_settings(tmp_path), {"a": 1})
        detector.poll_once(now=10.0)
        detector.poll_once(now=10.5)
        await detector.settle()
        before = list(recorder.seen)
        write(project_settings(tmp_path), {"a": 22})  # still being written: the clock restarts
        detector.poll_once(now=10.9)
        detector.poll_once(now=11.8)
        await detector.settle()
        middle = list(recorder.seen)
        detector.poll_once(now=11.9)
        await detector.settle()
        detector.poll_once(now=20.0)  # nothing new
        await detector.settle()
        after = list(recorder.seen)
        await detector.dispose()
        return [before, middle, after]

    assert asyncio.run(scenario()) == [[], [], [S.PROJECT_SETTINGS]]


def test_a_file_created_beside_a_watched_one_is_added(tmp_path):
    write(project_settings(tmp_path), {})
    local = tmp_path / "proj" / ".tddagents" / "settings.local.json"

    async def scenario() -> list[Any]:
        detector = await _started(locations(tmp_path), stability_threshold_ms=0)
        recorder = Recorder(detector)
        write(local, {"permissions": {"deny": ["Bash"]}})
        detector.poll_once(now=1.0)
        detector.poll_once(now=1.0)
        await detector.settle()
        await detector.dispose()
        return recorder.seen

    assert asyncio.run(scenario()) == [S.LOCAL_SETTINGS]


def test_an_unwatched_directory_stays_unwatched(tmp_path):
    async def scenario() -> list[Any]:
        detector = await _started(locations(tmp_path), stability_threshold_ms=0)
        recorder = Recorder(detector)
        write(project_settings(tmp_path), {"a": 1})  # `.tddagents/` appeared after the start
        detector.poll_once(now=1.0)
        detector.poll_once(now=2.0)
        await detector.settle()
        await detector.dispose()
        return recorder.seen

    assert asyncio.run(scenario()) == []


def test_our_own_write_is_not_a_change(tmp_path):
    write(project_settings(tmp_path), {})
    clear_internal_writes()

    async def scenario() -> list[Any]:
        detector = await _started(locations(tmp_path), stability_threshold_ms=0)
        recorder = Recorder(detector)
        update_settings_for_source(S.PROJECT_SETTINGS, locations(tmp_path), {"x": 1})
        detector.poll_once(now=1.0)
        detector.poll_once(now=1.0)
        await detector.settle()
        write(project_settings(tmp_path), {"x": 2})  # the next, external, change is seen
        detector.poll_once(now=2.0)
        detector.poll_once(now=2.0)
        await detector.settle()
        await detector.dispose()
        return recorder.seen

    assert asyncio.run(scenario()) == [S.PROJECT_SETTINGS]


def test_a_deletion_waits_for_its_grace_and_a_recreation_cancels_it(tmp_path):
    path = project_settings(tmp_path)
    write(path, {})

    async def scenario() -> list[Any]:
        detector = await _started(locations(tmp_path), stability_threshold_ms=0, deletion_grace_ms=50)
        recorder = Recorder(detector)
        path.unlink()
        detector.poll_once(now=1.0)
        detector.poll_once(now=1.1)  # still gone: one pending deletion only
        await asyncio.sleep(0.15)
        await detector.settle()
        deleted = list(recorder.seen)
        path.unlink(missing_ok=True)
        write(path, {"back": True})
        detector.poll_once(now=2.0)
        detector.poll_once(now=2.0)
        await detector.settle()
        path.unlink()
        detector.poll_once(now=3.0)
        write(path, {"again": True})  # recreated within the grace: a change, not a deletion
        detector.poll_once(now=3.1)
        detector.poll_once(now=3.1)
        await asyncio.sleep(0.15)
        await detector.settle()
        await detector.dispose()
        return [deleted, recorder.seen]

    deleted, seen = asyncio.run(scenario())
    assert deleted == [S.PROJECT_SETTINGS]
    assert seen == [S.PROJECT_SETTINGS] * 3


def test_a_drop_in_file_is_a_policy_change(tmp_path):
    drop_in = tmp_path / "etc" / "managed-settings.d"
    drop_in.mkdir(parents=True)

    async def scenario() -> list[Any]:
        detector = await _started(locations(tmp_path), stability_threshold_ms=0)
        recorder = Recorder(detector)
        write(drop_in / "10-team.json", {"disableAllHooks": True})
        (drop_in / "notes.txt").write_text("ignored")
        detector.poll_once(now=1.0)
        detector.poll_once(now=1.0)
        await detector.settle()
        await detector.dispose()
        return recorder.seen

    assert asyncio.run(scenario()) == [S.POLICY_SETTINGS]


def test_the_poll_runs_on_its_own(tmp_path):
    write(project_settings(tmp_path), {})

    async def scenario() -> list[Any]:
        detector = SettingsChangeDetector(locations(tmp_path), poll_interval_ms=10, stability_threshold_ms=20)
        await detector.initialize()
        await detector.initialize()  # once
        recorder = Recorder(detector)
        write(project_settings(tmp_path), {"live": 1})
        deadline = time.monotonic() + 5
        while not recorder.seen and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await detector.dispose()
        await detector.initialize()  # never after dispose
        return [recorder.seen, detector._poll_task]

    seen, task = asyncio.run(scenario())
    assert seen == [S.PROJECT_SETTINGS] and task is None


def test_nothing_to_watch_starts_no_poll(tmp_path):
    async def scenario() -> Any:
        detector = SettingsChangeDetector(locations(tmp_path))
        await detector.initialize()
        task = detector._poll_task
        await detector.dispose()
        return detector.initialized, task

    assert asyncio.run(scenario()) == (True, None)


def test_a_failing_listener_does_not_starve_the_others(tmp_path):
    detector = SettingsChangeDetector(locations(tmp_path))
    seen: list[Any] = []

    def broken(source: Any) -> None:
        raise RuntimeError("boom")

    detector.subscribe(broken)
    unsubscribe = detector.subscribe(seen.append)
    detector.notify_change(S.FLAG_SETTINGS)
    unsubscribe()
    unsubscribe()
    detector.notify_change(S.FLAG_SETTINGS)
    assert seen == [S.FLAG_SETTINGS]


# ── ConfigChange hooks ───────────────────────────────────────────────────────

def _blocking_hooks(blocked: bool, calls: list[tuple[str, str]]) -> Any:
    async def hooks(source: str, path: str) -> list[HookOutsideReplResult]:
        calls.append((source, path))
        return [HookOutsideReplResult(command="c", succeeded=True, output="", blocked=blocked)]

    return hooks


@pytest.mark.parametrize("blocked, expected", [(True, []), (False, [S.PROJECT_SETTINGS])])
def test_a_blocking_config_change_hook_keeps_the_change_out(tmp_path, blocked, expected):
    write(project_settings(tmp_path), {})
    calls: list[tuple[str, str]] = []

    async def scenario() -> list[Any]:
        detector = await _started(locations(tmp_path), stability_threshold_ms=0,
                                  config_change_hooks=_blocking_hooks(blocked, calls))
        recorder = Recorder(detector)
        write(project_settings(tmp_path), {"a": 1})
        detector.poll_once(now=1.0)
        detector.poll_once(now=1.0)
        await detector.settle()
        await detector.dispose()
        return recorder.seen

    assert asyncio.run(scenario()) == expected
    assert calls == [("project_settings", str(project_settings(tmp_path).resolve()))]


def test_execute_config_change_hooks(tmp_path):
    hooks = {"ConfigChange": [{"matcher": "project_settings", "hooks": [
        {"type": "command", "command": "cat > \"$TDDAGENTS_PROJECT_DIR/in.json\"; exit 2"}]},
        {"matcher": "policy_settings", "hooks": [{"type": "command", "command": "exit 2"}]}]}
    write(tmp_path / "home" / ".tddagents" / "settings.json", {"hooks": hooks})
    dispatcher = build_hook_dispatcher(locations(tmp_path), session_id="s", cwd=str(tmp_path),
                                       project_dir=str(tmp_path), model="m")
    results = asyncio.run(execute_config_change_hooks(dispatcher, "project_settings", "/p/settings.json"))
    assert has_blocking_result(results)
    sent = json.loads((tmp_path / "in.json").read_text())
    assert (sent["hook_event_name"], sent["source"], sent["file_path"]) == (
        "ConfigChange", "project_settings", "/p/settings.json")
    # Policy settings: the hooks fire, but never block.
    policy = asyncio.run(execute_config_change_hooks(dispatcher, "policy_settings"))
    assert len(policy) == 1 and not has_blocking_result(policy)
    assert asyncio.run(execute_config_change_hooks(dispatcher, "user_settings")) == []
    assert asyncio.run(execute_config_change_hooks(None, "user_settings")) == []
    assert not has_blocking_result([])


# ── applySettingsChange ──────────────────────────────────────────────────────

def test_convert_rules_to_updates_groups_by_source_and_behavior():
    rules = [rule("Bash", ALLOW, S.USER_SETTINGS, "ls"), rule("Read", DENY, S.USER_SETTINGS),
             rule("Edit", ALLOW, S.USER_SETTINGS), rule("Bash", ALLOW, S.PROJECT_SETTINGS, "npm test")]
    assert convert_rules_to_updates(rules, "replaceRules") == [
        PermissionUpdate("replaceRules", S.USER_SETTINGS, rules=(("Bash", "ls"), ("Edit", None)), behavior=ALLOW),
        PermissionUpdate("replaceRules", S.USER_SETTINGS, rules=(("Read", None),), behavior=DENY),
        PermissionUpdate("replaceRules", S.PROJECT_SETTINGS, rules=(("Bash", "npm test"),), behavior=ALLOW),
    ]


def test_sync_replaces_the_file_rules_and_keeps_cli_and_session_rules(tmp_path):
    context = ToolPermissionContext(
        always_allow_rules=(rule("Bash", ALLOW, S.PROJECT_SETTINGS, "old"), rule("Bash", ALLOW, S.CLI_ARG, "cli")),
        always_deny_rules=(rule("Read", DENY, S.LOCAL_SETTINGS), rule("Edit", DENY, S.SESSION)),
        always_ask_rules=(rule("WebFetch", ASK, S.USER_SETTINGS),),
    )
    synced = sync_permission_rules_from_disk(context, [rule("Bash", ALLOW, S.PROJECT_SETTINGS, "new")],
                                             locations(tmp_path))
    assert synced.always_allow_rules == (rule("Bash", ALLOW, S.CLI_ARG, "cli"),
                                         rule("Bash", ALLOW, S.PROJECT_SETTINGS, "new"))
    assert synced.always_deny_rules == (rule("Edit", DENY, S.SESSION),)
    assert synced.always_ask_rules == ()


def test_sync_under_managed_only_clears_cli_and_session_rules_too(tmp_path):
    write(tmp_path / "etc" / "managed-settings.json", {"allowManagedPermissionRulesOnly": True})
    context = ToolPermissionContext(
        always_allow_rules=(rule("Bash", ALLOW, S.CLI_ARG, "cli"), rule("Bash", ALLOW, S.FLAG_SETTINGS, "f")),
        always_deny_rules=(rule("Edit", DENY, S.SESSION),),
    )
    policy = [rule("Read", DENY, S.POLICY_SETTINGS)]
    synced = sync_permission_rules_from_disk(context, policy, locations(tmp_path))
    assert synced.always_allow_rules == (rule("Bash", ALLOW, S.FLAG_SETTINGS, "f"),)
    assert synced.always_deny_rules == (rule("Read", DENY, S.POLICY_SETTINGS),)


def test_disabling_bypass_leaves_the_mode():
    bypass = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS, is_bypass_permissions_mode_available=True)
    assert create_disabled_bypass_permissions_context(bypass) == dataclasses.replace(
        bypass, mode=PermissionMode.DEFAULT, is_bypass_permissions_mode_available=False)
    plan = dataclasses.replace(bypass, mode=PermissionMode.PLAN)
    assert create_disabled_bypass_permissions_context(plan).mode == PermissionMode.PLAN


def _run_state(tmp_path: Path, **kwargs: Any) -> tuple[AppStateStore, SettingsLocations, HookDispatcher]:
    loc = locations(tmp_path)
    perm = build_tool_permission_context(loc.project_root, loc.home, managed_dir=loc.managed_dir,
                                         allowed_tools_cli=["Bash(make:*)"], **kwargs)
    store = AppStateStore(state=AppState(tool_permission_context=perm))
    dispatcher = build_hook_dispatcher(loc, session_id="s", cwd=str(tmp_path), project_dir=str(tmp_path), model="m")
    return store, loc, dispatcher


def test_apply_settings_change_reloads_rules_and_hooks_and_strips_dangerous_allows(tmp_path):
    write(project_settings(tmp_path), {"permissions": {"defaultMode": "acceptEdits", "deny": ["Bash(rm:*)"]}})
    store, loc, dispatcher = _run_state(tmp_path)
    assert dispatcher.snapshot.matchers_for("PostToolUse") == ()
    hook = {"matcher": "Edit", "hooks": [{"type": "command", "command": "ruff format"}]}
    write(project_settings(tmp_path), {
        "permissions": {"defaultMode": "plan", "allow": ["Bash(python:*)", "Bash(npm test)"], "ask": ["WebFetch"]},
        "hooks": {"PostToolUse": [hook]},
    })
    apply_settings_change(S.PROJECT_SETTINGS, store.update, loc, dispatcher)
    perm = store.get().tool_permission_context
    assert perm.mode == PermissionMode.ACCEPT_EDITS  # the mode is the session's, never reloaded
    assert [(r.tool_name, r.rule_content, r.source) for r in perm.always_allow_rules] == [
        ("Bash", "make:*", S.CLI_ARG), ("Bash", "npm test", S.PROJECT_SETTINGS)]
    assert perm.always_deny_rules == ()
    assert [r.tool_name for r in perm.always_ask_rules] == ["WebFetch"]
    assert perm.stripped_dangerous_rules == (rule("Bash", ALLOW, S.PROJECT_SETTINGS, "python:*"),)
    assert dispatcher.snapshot.matchers_for("PostToolUse") == (
        HookMatcher(matcher="Edit", hooks=(CommandHook(command="ruff format"),)),)
    # A second reload of the same file stashes the dangerous rule once.
    apply_settings_change(S.PROJECT_SETTINGS, store.update, loc, dispatcher)
    assert store.get().tool_permission_context.stripped_dangerous_rules == perm.stripped_dangerous_rules


def test_apply_settings_change_honours_disable_bypass(tmp_path):
    store, loc, dispatcher = _run_state(tmp_path, dangerously_skip_permissions=True)
    assert store.get().tool_permission_context.mode == PermissionMode.BYPASS_PERMISSIONS
    write(project_settings(tmp_path), {"permissions": {"disableBypassPermissionsMode": "disable"}})
    apply_settings_change(S.PROJECT_SETTINGS, store.update, loc, None)
    perm = store.get().tool_permission_context
    assert (perm.mode, perm.is_bypass_permissions_mode_available) == (PermissionMode.DEFAULT, False)


def test_a_dispatcher_without_locations_keeps_its_snapshot():
    snapshot = HooksConfigSnapshot(disable_all=True)
    dispatcher = HookDispatcher(snapshot=snapshot, session_id="s", cwd=".", project_dir=".")
    update_hooks_config_snapshot(dispatcher)
    assert dispatcher.snapshot is snapshot


def test_the_tdd_phase_tier_holds_after_a_reload(tmp_path):
    """An `Edit` / `WriteFile` allow written mid-session never lets RED write production code."""
    from app.cli.print import build_builtin_tools

    write(project_settings(tmp_path), {})
    loc = locations(tmp_path)
    perm = build_tool_permission_context(loc.project_root, loc.home, managed_dir=loc.managed_dir,
                                         working_dir=tmp_path)
    store = AppStateStore(state=AppState(tdd_plan=CURRENT_ITEM_PLAN, project_config=TEST_PROJECT_CONFIG,
                                         phase_ledger=PhaseLedger(phase=TddPhase.RED), tool_permission_context=perm))
    write(project_settings(tmp_path), {"permissions": {"allow": ["Edit", "WriteFile", "Bash(python:*)"]}})
    apply_settings_change(S.PROJECT_SETTINGS, store.update, loc)
    assert [r.tool_name for r in store.get().tool_permission_context.always_allow_rules] == ["Edit", "WriteFile"]
    ctx = tool_context_for(store, workspace=SimpleNamespace(root=str(tmp_path)))
    tools = {t.name: t for t in build_builtin_tools()}
    code = asyncio.run(has_permissions_to_use_tool(
        tools["WriteFile"], {"file_path": "calculator.py", "content": "x"}, ctx, "tu"))
    assert code.behavior == DENY and (code.decision_reason or {})["type"] == "phase_rule"
    test = asyncio.run(has_permissions_to_use_tool(
        tools["WriteFile"], {"file_path": "tests/test_calc.py", "content": "x"}, ctx, "tu"))
    assert test.behavior == ALLOW


# ── the inline flag settings (`apply_flag_settings`) ─────────────────────────

def test_inline_flag_settings_are_part_of_flag_settings(tmp_path):
    flag = tmp_path / "flag.json"
    write(flag, {"permissions": {"deny": ["Read"]}, "x": 1})
    loc = locations(tmp_path, flag_settings_path=flag)
    set_flag_settings_inline({"permissions": {"deny": ["Edit"]}, "y": 2})
    assert get_settings_for_source(S.FLAG_SETTINGS, loc) == {"permissions": {"deny": ["Read", "Edit"]}, "x": 1, "y": 2}
    no_file = locations(tmp_path)
    assert get_settings_for_source(S.FLAG_SETTINGS, no_file) == {"permissions": {"deny": ["Edit"]}, "y": 2}
    assert load_settings(no_file)["y"] == 2
    assert [(r.tool_name, r.source) for r in load_all_permission_rules_from_disk(no_file)] == [
        ("Edit", S.FLAG_SETTINGS)]
    # A document failing the schema this build checks is not applied, as `SettingsSchema().safeParse`.
    set_flag_settings_inline({"hooks": {"NoSuchEvent": []}})
    assert get_settings_for_source(S.FLAG_SETTINGS, no_file) is None and "hooks" not in load_settings(no_file)
    set_flag_settings_inline({})
    assert get_settings_for_source(S.FLAG_SETTINGS, no_file) is None


def test_apply_flag_settings_notifies_the_run(tmp_path):
    from app.cli.print import HeadlessStreaming

    loc = locations(tmp_path)
    store = AppStateStore(state=AppState())
    detector = SettingsChangeDetector(loc)
    detector.subscribe(lambda source: apply_settings_change(source, store.update, loc))
    responses: list[Any] = []
    fake = SimpleNamespace(run_=SimpleNamespace(settings_change_detector=detector, user_specified_model=None),
                           respond=lambda message, response=None: responses.append((message, response)))
    request = {"settings": {"permissions": {"deny": ["Bash(rm:*)"]}, "model": "claude-x", "z": None}}
    asyncio.run(HeadlessStreaming._control_apply_flag_settings(fake, {"id": 1}, request))  # type: ignore[arg-type]
    assert get_flag_settings_inline() == {"permissions": {"deny": ["Bash(rm:*)"]}, "model": "claude-x"}
    assert [(r.tool_name, r.rule_content, r.source) for r in store.get().tool_permission_context.always_deny_rules] == [
        ("Bash", "rm:*", S.FLAG_SETTINGS)]
    assert fake.run_.user_specified_model == "claude-x" and responses == [({"id": 1}, None)]
    asyncio.run(HeadlessStreaming._control_apply_flag_settings(
        fake, {"id": 2}, {"settings": {"permissions": None, "model": None}}))  # type: ignore[arg-type]
    assert get_flag_settings_inline() == {}
    # As upstream's `syncPermissionRulesFromDisk`: only the user, project and local groups are cleared
    # first, so a `flagSettings` group emptied mid-session keeps its rules (a replaced one is replaced).
    assert len(store.get().tool_permission_context.always_deny_rules) == 1
    assert fake.run_.user_specified_model is None


# ── the session: a settings file edited mid-run ──────────────────────────────

FINISHED = TddPlan.from_texts(["a"])
FINISHED = dataclasses.replace(FINISHED, items=(dataclasses.replace(FINISHED.items[0], status=ItemStatus.DONE),))


def _scripted() -> Any:
    from app.query.deps import get_production_deps

    async def model(state: Any, config: Any) -> AsyncIterator[Any]:
        raise AssertionError("no model call in this test")
        yield  # pragma: no cover

    return dataclasses.replace(get_production_deps(), call_model=model)


def test_a_settings_file_edited_during_the_session_reaches_the_run(tmp_path, monkeypatch):
    from app.cli.print import open_session_run
    from app.workspace.local import LocalWorkspace

    monkeypatch.setattr(cd, "FILE_STABILITY_POLL_INTERVAL_MS", 10)
    monkeypatch.setattr(cd, "FILE_STABILITY_THRESHOLD_MS", 20)
    write(project_settings(tmp_path), {})
    hook_log = tmp_path / "config-change.json"
    write(tmp_path / "home" / ".tddagents" / "settings.json", {"hooks": {"ConfigChange": [{"hooks": [
        {"type": "command", "command": f"cat >> {hook_log}"}]}]}})
    ws = LocalWorkspace(str(tmp_path / "ws"))
    perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, should_avoid_permission_prompts=True)

    async def wait_for(predicate: Any) -> None:
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert predicate()

    async def session() -> None:
        async with open_session_run(run_id="live", plan=FINISHED, workspace=ws, deps=_scripted(),
                                    permission_context=perm, project_root=tmp_path / "proj",
                                    home=tmp_path / "home", managed_dir=tmp_path / "etc") as run:
            detector = run.settings_change_detector
            assert detector is not None and detector._poll_task is not None
            dispatcher = run.ctx.hook_dispatcher
            write(project_settings(tmp_path), {
                "permissions": {"deny": ["Bash(rm:*)"], "allow": ["Bash(python:*)"]},
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}]},
            })
            await wait_for(lambda: run.store.get().tool_permission_context.always_deny_rules)
            context = run.store.get().tool_permission_context
            assert [(r.tool_name, r.rule_content) for r in context.always_deny_rules] == [("Bash", "rm:*")]
            assert context.always_allow_rules == () and len(context.stripped_dangerous_rules) == 1
            assert dispatcher.snapshot.has_hook_for_event("Stop")
            # The deny rule reaches the tool pool's filter at the next refresh.
            write(project_settings(tmp_path), {})
            await wait_for(lambda: not run.store.get().tool_permission_context.always_deny_rules)
            assert not dispatcher.snapshot.has_hook_for_event("Stop")
        assert detector.disposed and detector._poll_task is None

    asyncio.run(session())
    sent = [json.loads(line) for line in hook_log.read_text().splitlines()]
    assert [(e["hook_event_name"], e["source"]) for e in sent] == [("ConfigChange", "project_settings")] * 2
