"""
`/hooks` (`reference/claude-code/src/commands/hooks/`, `components/hooks/HooksConfigMenu.tsx` and its
modes, `utils/hooks/hooksConfigManager.ts`, `utils/hooks/hooksSettings.ts`): the hooks listed by
event, matcher and source, the menu's views, and the reload on open.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.commands.commands import COMMANDS, HELP_TEXT, parse_slash_command
from app.commands.hooks.hooks import DESCRIPTION, call, menu_context
from app.components.hooks.hooks_config_menu import (
    DISMISSED,
    HooksMenuContext,
    render_disabled,
    run_hooks_config_menu,
)
from app.components.hooks.view_hook_mode import (
    get_content_field_label,
    get_content_field_value,
    render_hook_details,
    round_box,
)
from app.schemas.hooks import AgentHook, CallbackHook, CommandHook, FunctionHook, HookMatcher, HttpHook, PromptHook
from app.utils.hooks.hooks import HookDispatcher
from app.utils.hooks.hooks_config_manager import (
    get_hook_event_metadata,
    get_hooks_for_matcher,
    get_matcher_metadata,
    get_sorted_matchers_for_event,
    group_hooks_by_event_and_matcher,
)
from app.utils.hooks.hooks_config_snapshot import HooksConfigSnapshot
from app.utils.hooks.hooks_settings import (
    IndividualHookConfig,
    get_all_hooks,
    get_hooks_for_event,
    hook_source_description_display_string,
    hook_source_header_display_string,
    hook_source_inline_display_string,
    sort_matchers_by_priority,
)
from app.utils.hooks.hook_events import HOOK_EVENTS
from app.utils.settings.settings import SettingsLocations
from tests.dialog_fakes import ESC, FakeDialog

REPO = Path(__file__).resolve().parent.parent
HOOKS_CONFIG_MANAGER_TS = REPO / "reference" / "claude-code" / "src" / "utils" / "hooks" / "hooksConfigManager.ts"
needs_reference = pytest.mark.skipif(not HOOKS_CONFIG_MANAGER_TS.is_file(),
                                     reason="the claude-code reference snapshot is not checked out")


def _locations(tmp_path: Path) -> SettingsLocations:
    return SettingsLocations(project_root=tmp_path / "project", home=tmp_path / "home",
                             managed_dir=tmp_path / "managed")


def _write(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings), encoding="utf-8")


def _hooks(event: str, matcher: str | None, *commands: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"hooks": [{"type": "command", "command": c} for c in commands]}
    if matcher is not None:
        entry["matcher"] = matcher
    return {"hooks": {event: [entry]}}


def _user(loc: SettingsLocations) -> Path:
    return loc.user_config_dir / "settings.json"


def _project(loc: SettingsLocations) -> Path:
    return loc.project_root / ".tddagents" / "settings.json"


def _local(loc: SettingsLocations) -> Path:
    return loc.project_root / ".tddagents" / "settings.local.json"


# ── upstream's event texts ───────────────────────────────────────────────────

def _ts_string(source: str, start: int) -> tuple[str, int]:
    """The single-quoted literal starting at `start` (escapes resolved), and the index after it."""
    assert source[start] == "'"
    out: list[str] = []
    i = start + 1
    while source[i] != "'":
        if source[i] == "\\":
            out.append({"n": "\n"}.get(source[i + 1], source[i + 1]))
            i += 2
            continue
        out.append(source[i])
        i += 1
    return "".join(out), i + 1


def _upstream_metadata() -> dict[str, tuple[str, str, bool]]:
    source = HOOKS_CONFIG_MANAGER_TS.read_text(encoding="utf-8")
    body = source[source.index("return {", source.index("getHookEventMetadata")):source.index("toolNames => toolNames")]
    events: dict[str, tuple[str, str, bool]] = {}
    starts = [(m.group(1), m.end()) for m in re.finditer(r"^      (\w+): \{", body, re.MULTILINE)]
    for index, (name, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(body)
        block = body[start:end]
        summary, _ = _ts_string(block, re.search(r"summary:\s*'", block).end() - 1)  # type: ignore[union-attr]
        description, _ = _ts_string(block, re.search(r"description:\s*'", block).end() - 1)  # type: ignore[union-attr]
        events[name] = (summary, description, "matcherMetadata" in block)
    return events


def _substituted(text: str) -> str:
    return (text.replace("CLAUDE_ENV_FILE", "TDDAGENTS_ENV_FILE").replace("CLAUDE.md", "TDDAGENTS.md")
            .replace("Claude", "TDDAgents"))


@needs_reference
def test_the_event_texts_are_upstreams_after_the_product_substitutions() -> None:
    ours = get_hook_event_metadata(["Bash"])
    upstream = _upstream_metadata()
    assert list(ours) == list(upstream)
    for name, (summary, description, has_matcher) in upstream.items():
        assert ours[name].summary == _substituted(summary), name
        assert ours[name].description == _substituted(description), name
        assert (ours[name].matcher_metadata is not None) == has_matcher, name


def test_every_hook_event_has_metadata_and_tools_feed_the_tool_matchers() -> None:
    metadata = get_hook_event_metadata(["Bash", "Edit"])
    assert set(metadata) == set(HOOK_EVENTS)
    pre = metadata["PreToolUse"].matcher_metadata
    assert pre is not None and pre.field_to_match == "tool_name" and pre.values == ("Bash", "Edit")
    assert metadata["Stop"].matcher_metadata is None
    assert get_matcher_metadata("SessionStart", []) == metadata["SessionStart"].matcher_metadata
    assert get_matcher_metadata("UserPromptSubmit", []) is None
    assert all("Claude" not in m.summary + m.description for m in metadata.values())


# ── the hooks, by source ─────────────────────────────────────────────────────

def test_get_all_hooks_lists_the_editable_settings_then_the_session(tmp_path: Path) -> None:
    loc = _locations(tmp_path)
    _write(_user(loc), _hooks("PreToolUse", "Bash", "user-cmd"))
    _write(_project(loc), _hooks("Stop", None, "project-cmd"))
    _write(_local(loc), _hooks("PreToolUse", "Edit", "local-cmd"))
    _write(loc.managed_dir / "managed-settings.json", _hooks("PreToolUse", "Bash", "policy-cmd"))
    session = {"Stop": [HookMatcher(hooks=(CommandHook(command="session-cmd"),
                                           FunctionHook(callback=lambda _m: True, error_message="x", id="f")))]}
    hooks = get_all_hooks(loc, session)
    assert [(h.event, h.matcher, h.source, getattr(h.config, "command", None)) for h in hooks] == [
        ("PreToolUse", "Bash", "userSettings", "user-cmd"),
        ("Stop", None, "projectSettings", "project-cmd"),
        ("PreToolUse", "Edit", "localSettings", "local-cmd"),
        ("Stop", None, "sessionHook", "session-cmd"),
    ]
    assert [h.source for h in get_hooks_for_event(loc, "Stop", session)] == ["projectSettings", "sessionHook"]


def test_managed_hooks_only_hides_every_settings_hook(tmp_path: Path) -> None:
    loc = _locations(tmp_path)
    _write(_user(loc), _hooks("PreToolUse", "Bash", "user-cmd"))
    _write(loc.managed_dir / "managed-settings.json", {"allowManagedHooksOnly": True, **_hooks("Stop", None, "p")})
    session = {"Stop": [HookMatcher(hooks=(CommandHook(command="session-cmd"),))]}
    assert [h.source for h in get_all_hooks(loc, session)] == ["sessionHook"]


def test_a_file_two_sources_share_is_listed_once(tmp_path: Path) -> None:
    loc = SettingsLocations(project_root=tmp_path, home=tmp_path, managed_dir=tmp_path / "managed")
    _write(_user(loc), _hooks("Stop", None, "shared"))
    assert [h.source for h in get_all_hooks(loc)] == ["userSettings"]


def test_an_invalid_settings_file_lists_nothing(tmp_path: Path) -> None:
    loc = _locations(tmp_path)
    _write(_project(loc), {"hooks": {"Stop": [{"hooks": [{"type": "command"}]}]}})
    assert get_all_hooks(loc) == []


def test_grouping_adds_plugin_hooks_and_keys_matcherless_events_under_the_empty_matcher(tmp_path: Path) -> None:
    loc = _locations(tmp_path)
    _write(_project(loc), {"hooks": {
        "Stop": [{"matcher": "ignored", "hooks": [{"type": "command", "command": "stop-cmd"}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "bash-cmd"}]},
                       {"hooks": [{"type": "command", "command": "all-cmd"}]}],
    }})
    registered = {
        "PreToolUse": [
            HookMatcher(hooks=(CommandHook(command="plugin-cmd"),), matcher="Bash", plugin_root="/p",
                        plugin_name="fmt", plugin_id="fmt@market"),
            HookMatcher(hooks=(CallbackHook(callback=lambda *a: None),), matcher="Bash"),  # type: ignore[arg-type]
        ],
        "NotAnEvent": [HookMatcher(hooks=(CommandHook(command="x"),), plugin_root="/p")],
    }
    grouped = group_hooks_by_event_and_matcher(loc, ["Bash"], {}, registered)
    assert set(grouped) == set(HOOK_EVENTS)
    assert [h.config.command for h in grouped["Stop"][""]] == ["stop-cmd"]  # type: ignore[union-attr]
    bash = grouped["PreToolUse"]["Bash"]
    assert [(h.source, h.plugin_name) for h in bash] == [("projectSettings", None), ("pluginHook", "fmt@market")]
    assert get_hooks_for_matcher(grouped, "PreToolUse", None) == grouped["PreToolUse"][""]
    assert get_hooks_for_matcher(grouped, "Stop", "nope") == []
    assert get_sorted_matchers_for_event(grouped, "PreToolUse") == ["", "Bash"]


def _config(source: str, matcher: str = "m") -> IndividualHookConfig:
    return IndividualHookConfig(event="PreToolUse", config=CommandHook(command="c"), source=source, matcher=matcher)


def test_matchers_sort_by_their_highest_priority_source_then_by_name() -> None:
    grouped = {"PreToolUse": {
        "user": [_config("userSettings")], "local": [_config("localSettings")],
        "plugin": [_config("pluginHook")], "project": [_config("projectSettings")],
        "b": [_config("userSettings")], "A": [_config("userSettings")], "a": [_config("userSettings")],
        "mixed": [_config("pluginHook"), _config("localSettings")],
    }}
    ordered = sort_matchers_by_priority(list(grouped["PreToolUse"]), grouped, "PreToolUse")
    assert ordered == ["local", "mixed", "project", "a", "A", "b", "user", "plugin"]
    # A session hook has no priority: upstream's comparator yields NaN, which keeps the pair's order.
    session = {"PreToolUse": {"z": [_config("userSettings")], "s": [_config("sessionHook")]}}
    assert sort_matchers_by_priority(["z", "s"], session, "PreToolUse") == ["z", "s"]


def test_the_source_display_strings() -> None:
    assert hook_source_description_display_string("userSettings") == "User settings (~/.tddagents/settings.json)"
    assert hook_source_description_display_string("pluginHook") == (
        "Plugin hooks (~/.tddagents/plugins/*/hooks/hooks.json)")
    assert hook_source_description_display_string("odd") == "odd"
    assert hook_source_header_display_string("localSettings") == "Local Settings"
    assert hook_source_header_display_string("sessionHook") == "Session Hooks"
    assert hook_source_inline_display_string("projectSettings") == "Project"
    assert hook_source_inline_display_string("builtinHook") == "Built-in"


# ── the views ────────────────────────────────────────────────────────────────

def test_the_detail_view_shows_the_real_value_never_the_status_message() -> None:
    hook = IndividualHookConfig(event="PreToolUse", matcher=None, source="pluginHook", plugin_name="fmt@m",
                                config=CommandHook(command="ruff format", status_message="Formatting"))
    lines = render_hook_details(hook, event_supports_matcher=True)
    assert lines[:7] == ["Hook details", "", "Event: PreToolUse", "Matcher: (all)", "Type: command",
                         "Source: Plugin hooks (~/.tddagents/plugins/*/hooks/hooks.json)", "Plugin: fmt@m"]
    assert "│ ruff format │" in lines and "Status message: Formatting" in lines
    assert lines[-1] == "To modify or remove this hook, edit settings.json directly or ask TDDAgents to help."
    plain = render_hook_details(IndividualHookConfig(event="Stop", config=PromptHook(prompt="p"), source="x"), False)
    assert not any(line.startswith(("Matcher:", "Plugin:", "Status message:")) for line in plain)
    assert [get_content_field_label(c) for c in (CommandHook("c"), PromptHook("p"), AgentHook("a"), HttpHook("u"))] \
        == ["Command", "Prompt", "Prompt", "URL"]
    assert [get_content_field_value(c) for c in (CommandHook("c"), PromptHook("p"), AgentHook("a"), HttpHook("u"))] \
        == ["c", "p", "a", "u"]
    assert round_box("ab\nc") == ["╭────╮", "│ ab │", "│ c  │", "╰────╯"]


def test_the_disabled_screen() -> None:
    assert render_disabled(1, by_policy=False) == [
        "Hook Configuration - Disabled", "",
        "All hooks are currently disabled. You have 1 configured hook that is not running.",
        "", "When hooks are disabled:", "· No hook commands will execute",
        "· Tool operations will proceed without hook validation",
        "", 'To re-enable hooks, remove "disableAllHooks" from settings.json or ask TDDAgents.',
    ]
    managed = render_disabled(2, by_policy=True)
    assert managed[2] == ("All hooks are currently disabled by a managed settings file. You have 2 configured hooks "
                          "that are not running.")
    assert "To re-enable" not in "\n".join(managed)


def _event_number(name: str) -> str:
    return str(list(get_hook_event_metadata([])).index(name) + 1)


def test_the_menu_drills_from_event_to_matcher_to_hook_and_back(tmp_path: Path) -> None:
    loc = _locations(tmp_path)
    _write(_project(loc), {"hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "echo pre"}, {"type": "http", "url": "https://h.example/x"}]}],
        "Stop": [{"hooks": [{"type": "prompt", "prompt": "check it"}]}],
    }})
    io = FakeDialog(_event_number("PreToolUse"), "1", "2", "", ESC, ESC,
                    _event_number("Stop"), "1", "", ESC, ESC)
    result = asyncio.run(run_hooks_config_menu(io, HooksMenuContext(locations=loc, tool_names=("Bash",))))
    assert result == DISMISSED
    first = io.asked[0]
    assert first.startswith("Hooks\n3 hooks configured\n\nℹ This menu is read-only. To add or modify hooks, edit "
                            "settings.json directly or ask TDDAgents.")
    assert f"  {_event_number('PreToolUse')}. PreToolUse (2) — Before tool execution" in first
    assert f"  {_event_number('Notification')}. Notification — When notifications are sent" in first
    assert io.asked[1].startswith("PreToolUse - Matchers\nInput to command is JSON of tool call arguments.")
    assert "  1. [Project] Bash — 2 hooks" in io.asked[1]
    assert io.asked[2].startswith("PreToolUse - Matcher: Bash\n")
    assert "  1. [command] echo pre — Project Settings" in io.asked[2]
    assert "  2. [http] https://h.example/x — Project Settings" in io.asked[2]
    assert "URL:" in io.asked[3] and "Matcher: Bash" in io.asked[3]
    # Esc from the hook list goes back to the matchers, then to the events.
    assert io.asked[4].startswith("PreToolUse - Matcher: Bash") and io.asked[5].startswith("PreToolUse - Matchers")
    assert io.asked[6].startswith("Hooks\n")
    # An event without matchers goes straight to its hooks, and back to the events.
    assert io.asked[7].startswith("Stop\nExit code 0 - stdout/stderr not shown")
    assert "  1. [prompt] check it — Project Settings" in io.asked[7]
    assert "Matcher:" not in io.asked[8]
    assert io.asked[9].startswith("Stop\n") and io.asked[10].startswith("Hooks\n")


def test_an_event_without_hooks_says_how_to_add_one(tmp_path: Path) -> None:
    io = FakeDialog(_event_number("PreToolUse"), "", _event_number("Stop"), "", ESC)
    asyncio.run(run_hooks_config_menu(io, HooksMenuContext(locations=_locations(tmp_path))))
    assert io.asked[0].startswith("Hooks\n0 hooks configured\n")
    for text in (io.asked[1], io.asked[3]):
        assert "No hooks configured for this event.\nTo add hooks, edit settings.json directly or ask TDDAgents." \
            in text
    assert io.asked[1].startswith("PreToolUse - Matchers") and io.asked[3].startswith("Stop\n")


def test_the_policy_notice_and_the_disabled_screen(tmp_path: Path) -> None:
    loc = _locations(tmp_path)
    _write(loc.managed_dir / "managed-settings.json", {"allowManagedHooksOnly": True})
    io = FakeDialog(ESC)
    asyncio.run(run_hooks_config_menu(io, HooksMenuContext(locations=loc)))
    assert "ℹ Hooks Restricted by Policy\nOnly hooks from managed settings can run. User-defined hooks from " \
           "~/.tddagents/settings.json, .tddagents/settings.json, and .tddagents/settings.local.json are blocked." \
           in io.asked[0]

    _write(_user(loc), {"disableAllHooks": True, **_hooks("Stop", None, "a", "b")})
    io = FakeDialog("1", ESC)
    assert asyncio.run(run_hooks_config_menu(io, HooksMenuContext(locations=loc))) == DISMISSED
    assert io.asked[0].startswith("Hook Configuration - Disabled")
    assert io.out[-1] == "Esc to close"  # a line is not Esc: the screen stays
    _write(loc.managed_dir / "managed-settings.json", {"disableAllHooks": True})
    io = FakeDialog(ESC)
    asyncio.run(run_hooks_config_menu(io, HooksMenuContext(locations=loc)))
    assert "disabled by a managed settings file. You have 2 configured hooks" in io.asked[0]


# ── the command ──────────────────────────────────────────────────────────────

def test_hooks_is_an_immediate_local_command() -> None:
    assert "hooks" in COMMANDS and DESCRIPTION == "View hook configurations for tool events"
    command = parse_slash_command("/hooks")
    assert command is not None and command.immediate
    assert "  /hooks                        view hook configurations for tool events" in HELP_TEXT


def test_opening_hooks_reloads_the_runs_hooks_snapshot(tmp_path: Path) -> None:
    loc = _locations(tmp_path)
    dispatcher = HookDispatcher(snapshot=HooksConfigSnapshot(), session_id="s", cwd=str(tmp_path),
                                project_dir=str(tmp_path), settings_locations=loc)
    dispatcher.session_matchers["Stop"] = [HookMatcher(hooks=(CommandHook(command="from-a-skill"),))]
    _write(_project(loc), _hooks("Stop", None, "written-mid-session"))
    tools = [type("T", (), {"name": "Bash"})()]
    context = menu_context(loc, tools, dispatcher)
    assert context.tool_names == ("Bash",) and list(context.session_matchers) == ["Stop"]
    io = FakeDialog(_event_number("Stop"), ESC, ESC)
    assert asyncio.run(call(io, context, dispatcher)) == DISMISSED
    stop = dispatcher.snapshot.matchers_for("Stop")
    assert [getattr(h, "command", None) for m in stop for h in m.hooks] == ["written-mid-session"]
    assert "  1. [command] written-mid-session — Project Settings" in io.asked[1]
    assert "  2. [command] from-a-skill — Session Hooks" in io.asked[1]
    # Without a dispatcher nothing is reloaded, and the menu still opens.
    assert asyncio.run(call(FakeDialog(ESC), menu_context(loc, None, None))) == DISMISSED
