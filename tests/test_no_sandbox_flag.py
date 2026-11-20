"""
`--no-sandbox` (`app/main.py`): the flag's parsing, the command-line settings holder
(`app/bootstrap/state.py`), how it merges beneath the SDK's inline flag settings and over every file
source (`app/utils/settings/settings.py`), and what the sandbox adapter then reports.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

import app.main as entry
from app.bootstrap.state import (
    get_cli_flag_settings,
    get_flag_settings_inline,
    set_cli_flag_settings,
    set_flag_settings_inline,
)
from app.utils.sandbox.sandbox_adapter import SandboxAdapter
from app.utils.settings.settings import SettingsLocations, get_inline_flag_settings, load_settings
from tests.sandbox_doubles import RecordingRipgrep

OFF = {"sandbox": {"enabled": False}}
SHIPPED = {"sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False}}


def write(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "home" / "ws" / "project"
    project.mkdir(parents=True)
    return {"project": project, "home": tmp_path / "home", "managed": tmp_path / "etc",
            "flag": tmp_path / "flag.json"}


def locations(layout: dict[str, Path], *, flag: bool = False) -> SettingsLocations:
    return SettingsLocations(project_root=layout["project"], home=layout["home"], managed_dir=layout["managed"],
                             flag_settings_path=layout["flag"] if flag else None)


def shipped_project(layout: dict[str, Path]) -> None:
    write(layout["project"] / ".tddagents" / "settings.json", SHIPPED)


def sandbox_adapter(layout: dict[str, Path], *, flag: bool = False) -> SandboxAdapter:
    return SandboxAdapter(
        locations(layout, flag=flag), home=str(layout["home"]), platform="linux",
        which=lambda name: f"/usr/bin/{name}", temp_dir="/tmp/tddagents-1000",
        run_ripgrep=RecordingRipgrep(), ripgrep_command="/opt/rg", probe=lambda: None)


# ── parsing ─────────────────────────────────────────────────────────────────────


def test_the_flag_is_off_by_default() -> None:
    assert entry.parse_args([]).no_sandbox is False


def test_the_flag_takes_no_value() -> None:
    assert entry.parse_args(["--no-sandbox"]).no_sandbox is True


@pytest.mark.parametrize("argv", [
    ["-p", "--no-sandbox", "Build it"],
    ["--no-sandbox", "-p", "Build it"],
    ["Build it", "-p", "--no-sandbox"],
])
def test_the_flag_combines_with_print_in_any_position(argv: list[str]) -> None:
    args = entry.parse_args(argv)
    assert args.no_sandbox is True
    assert args.print is True


def test_the_flag_leaves_the_request_alone() -> None:
    args = entry.parse_args(["--no-sandbox", "--thread-id", "tdd-abc", "next message"])
    assert args.no_sandbox is True
    assert args.thread_id == "tdd-abc"
    assert args.settings is None
    assert args.prompt == "next message"


def test_the_flag_is_independent_of_settings_and_permission_flags() -> None:
    args = entry.parse_args(["--no-sandbox", "--settings", "f.json", "--permission-mode", "plan"])
    assert (args.no_sandbox, args.settings, args.permission_mode) == (True, "f.json", "plan")
    assert entry.parse_args(["--settings", "f.json"]).no_sandbox is False


def test_the_flag_does_not_change_the_session_options() -> None:
    assert entry.session_options(entry.parse_args(["--no-sandbox"])) == entry.session_options(entry.parse_args([]))


def test_the_flag_is_in_the_help_text() -> None:
    help_text = entry.build_parser().format_help()
    assert "--no-sandbox" in help_text
    assert "without the sandbox" in help_text


# ── the command-line settings holder ────────────────────────────────────────────


def test_the_holder_starts_empty() -> None:
    assert get_cli_flag_settings() is None


def test_the_holder_stores_and_clears() -> None:
    set_cli_flag_settings(OFF)
    assert get_cli_flag_settings() == OFF
    set_cli_flag_settings(None)
    assert get_cli_flag_settings() is None


def test_the_holder_keeps_an_empty_document_distinct_from_none() -> None:
    set_cli_flag_settings({})
    assert get_cli_flag_settings() == {}


def test_the_holder_stores_a_copy_and_returns_a_copy() -> None:
    source = {"sandbox": {"enabled": False}, "model": "x"}
    set_cli_flag_settings(source)
    source["model"] = "changed"
    assert get_cli_flag_settings() == {"sandbox": {"enabled": False}, "model": "x"}
    returned = get_cli_flag_settings()
    assert returned is not None
    returned["model"] = "mutated"
    assert get_cli_flag_settings() == {"sandbox": {"enabled": False}, "model": "x"}


def test_the_holder_is_separate_from_the_sdk_inline_settings() -> None:
    set_cli_flag_settings(OFF)
    assert get_flag_settings_inline() is None
    set_flag_settings_inline({"model": "m"})
    assert get_cli_flag_settings() == OFF
    set_flag_settings_inline(None)
    assert get_cli_flag_settings() == OFF


# ── the merge the settings readers see ──────────────────────────────────────────


def test_no_flag_and_no_inline_settings_give_nothing() -> None:
    assert get_inline_flag_settings() is None


def test_the_flag_alone() -> None:
    set_cli_flag_settings(OFF)
    assert get_inline_flag_settings() == OFF


def test_the_inline_settings_alone_are_unchanged() -> None:
    set_flag_settings_inline({"model": "m"})
    assert get_inline_flag_settings() == {"model": "m"}


def test_the_flag_and_inline_settings_merge_deeply() -> None:
    set_cli_flag_settings(OFF)
    set_flag_settings_inline({"sandbox": {"failIfUnavailable": False}, "model": "m"})
    assert get_inline_flag_settings() == {"sandbox": {"enabled": False, "failIfUnavailable": False}, "model": "m"}


def test_the_sdk_hosts_settings_win_over_the_flag() -> None:
    set_cli_flag_settings(OFF)
    set_flag_settings_inline({"sandbox": {"enabled": True}})
    assert get_inline_flag_settings() == {"sandbox": {"enabled": True}}


def test_a_headless_session_opening_does_not_lose_the_flag() -> None:
    set_cli_flag_settings(OFF)
    set_flag_settings_inline({"model": "m"})
    set_flag_settings_inline(None)  # `HeadlessStreaming.__init__`
    assert get_inline_flag_settings() == OFF


def test_an_sdk_null_cleared_key_leaves_the_flag() -> None:
    set_cli_flag_settings(OFF)
    set_flag_settings_inline({})
    assert get_inline_flag_settings() == OFF


def test_a_document_the_schema_rejects_is_ignored() -> None:
    set_cli_flag_settings({"sandbox": {"enabled": "maybe"}})
    assert get_inline_flag_settings() is None


# ── what the settings sources load ──────────────────────────────────────────────


def test_the_shipped_project_settings_keep_the_sandbox_on_without_the_flag(layout: dict[str, Path]) -> None:
    shipped_project(layout)
    assert load_settings(locations(layout))["sandbox"]["enabled"] is True


def test_the_flag_turns_the_projects_sandbox_off(layout: dict[str, Path]) -> None:
    shipped_project(layout)
    set_cli_flag_settings(OFF)
    sandbox = load_settings(locations(layout))["sandbox"]
    assert sandbox["enabled"] is False


def test_the_flag_changes_nothing_else_in_the_sandbox_section(layout: dict[str, Path]) -> None:
    shipped_project(layout)
    set_cli_flag_settings(OFF)
    sandbox = load_settings(locations(layout))["sandbox"]
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False


def test_the_flag_wins_over_user_and_project_files(layout: dict[str, Path]) -> None:
    write(layout["home"] / ".tddagents" / "settings.json", {"sandbox": {"enabled": True}})
    shipped_project(layout)
    write(layout["project"] / ".tddagents" / "settings.local.json", {"sandbox": {"enabled": True}})
    set_cli_flag_settings(OFF)
    assert load_settings(locations(layout))["sandbox"]["enabled"] is False


def test_the_flag_wins_over_the_settings_file_flag(layout: dict[str, Path]) -> None:
    write(layout["flag"], {"sandbox": {"enabled": True}})
    set_cli_flag_settings(OFF)
    assert load_settings(locations(layout, flag=True))["sandbox"]["enabled"] is False


def test_the_flag_leaves_other_settings_files_content(layout: dict[str, Path]) -> None:
    write(layout["project"] / ".tddagents" / "settings.json",
          {"permissions": {"defaultMode": "acceptEdits"}, **SHIPPED})
    set_cli_flag_settings(OFF)
    assert load_settings(locations(layout))["permissions"] == {"defaultMode": "acceptEdits"}


def test_the_flag_cannot_override_a_managed_policy(layout: dict[str, Path]) -> None:
    write(layout["managed"] / "managed-settings.json", {"sandbox": {"enabled": True}})
    shipped_project(layout)
    set_cli_flag_settings(OFF)
    assert load_settings(locations(layout))["sandbox"]["enabled"] is True


def test_without_any_file_the_flag_alone_is_the_sandbox_setting(layout: dict[str, Path]) -> None:
    set_cli_flag_settings(OFF)
    assert load_settings(locations(layout))["sandbox"] == {"enabled": False}


# ── what the sandbox adapter reports ────────────────────────────────────────────


def test_the_adapter_is_on_and_required_with_the_shipped_settings(layout: dict[str, Path]) -> None:
    shipped_project(layout)
    adapter = sandbox_adapter(layout)
    assert adapter.is_sandbox_enabled_in_settings() is True
    assert adapter.is_sandbox_required() is True
    assert adapter.is_sandboxing_enabled() is True


def test_the_flag_disables_and_unrequires_the_sandbox(layout: dict[str, Path]) -> None:
    shipped_project(layout)
    set_cli_flag_settings(OFF)
    adapter = sandbox_adapter(layout)
    assert adapter.is_sandbox_enabled_in_settings() is False
    assert adapter.is_sandbox_required() is False
    assert adapter.is_sandboxing_enabled() is False
    assert adapter.get_sandbox_unavailable_reason() is None


def test_the_flag_removes_the_requirement_even_without_the_dependencies(layout: dict[str, Path]) -> None:
    shipped_project(layout)
    set_cli_flag_settings(OFF)
    adapter = SandboxAdapter(
        locations(layout), home=str(layout["home"]), platform="linux",
        which=lambda name: None, temp_dir="/tmp/tddagents-1000",
        run_ripgrep=RecordingRipgrep(), ripgrep_command="/opt/rg", probe=lambda: "bwrap cannot run")
    assert adapter.is_sandbox_required() is False
    assert adapter.is_sandboxing_enabled() is False
    assert adapter.get_sandbox_unavailable_reason() is None


def test_without_the_flag_missing_dependencies_make_the_session_unavailable(layout: dict[str, Path]) -> None:
    shipped_project(layout)
    adapter = SandboxAdapter(
        locations(layout), home=str(layout["home"]), platform="linux",
        which=lambda name: None, temp_dir="/tmp/tddagents-1000",
        run_ripgrep=RecordingRipgrep(), ripgrep_command="/opt/rg", probe=lambda: None)
    assert adapter.is_sandbox_required() is True
    assert adapter.is_sandboxing_enabled() is False
    assert adapter.get_sandbox_unavailable_reason() is not None


def test_the_adapter_reads_the_flag_live(layout: dict[str, Path]) -> None:
    shipped_project(layout)
    adapter = sandbox_adapter(layout)
    assert adapter.is_sandboxing_enabled() is True
    set_cli_flag_settings(OFF)
    assert adapter.is_sandboxing_enabled() is False
    set_cli_flag_settings(None)
    assert adapter.is_sandboxing_enabled() is True


# ── main() ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any] | None]:
    """The CLI holder and the merged settings as `run_entry` sees them; the entry returns exit code 7."""
    calls: list[dict[str, Any] | None] = []

    def run_entry(args: argparse.Namespace) -> int:
        calls.append(get_cli_flag_settings())
        calls.append(get_inline_flag_settings())
        return 7

    monkeypatch.setattr(entry, "run_entry", run_entry)
    return calls


def test_main_applies_the_flag_before_the_session_opens(seen: list[dict[str, Any] | None]) -> None:
    assert entry.main(["--no-sandbox"]) == 7
    assert seen == [OFF, OFF]


def test_main_without_the_flag_applies_nothing(seen: list[dict[str, Any] | None]) -> None:
    assert entry.main([]) == 7
    assert seen == [None, None]


def test_main_applies_the_flag_for_a_headless_request(seen: list[dict[str, Any] | None]) -> None:
    assert entry.main(["-p", "--no-sandbox", "Build it"]) == 7
    assert seen[0] == OFF


def test_main_applies_the_flag_when_continuing_a_thread(seen: list[dict[str, Any] | None]) -> None:
    assert entry.main(["--thread-id", "tdd-abc", "--no-sandbox"]) == 7
    assert seen[0] == OFF


def test_main_writes_only_the_sandbox_switch(seen: list[dict[str, Any] | None]) -> None:
    entry.main(["--no-sandbox", "--permission-mode", "plan"])
    assert seen[0] == {"sandbox": {"enabled": False}}


def test_the_plugin_and_doctor_subcommands_never_apply_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entry, "run_plugin_command", lambda rest: 0)
    monkeypatch.setattr(entry, "run_doctor_command", lambda rest: 0)
    assert entry.main(["plugin", "list"]) == 0
    assert entry.main(["doctor"]) == 0
    assert get_cli_flag_settings() is None
