"""
`--sandbox` (`app/main.py`): the opt-in to the sandbox, now that a run has none by default. It applies the
profile in `.tddagents/settings.sandbox.json` as command-line flag settings, mirrors `--no-sandbox` and
cannot be combined with it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

import app.main as entry
from app.bootstrap.state import get_cli_flag_settings
from app.utils.sandbox.sandbox_adapter import SandboxAdapter
from app.utils.settings.settings import SettingsLocations, get_inline_flag_settings, load_settings
from app.workspace.project import load_sandbox_profile
from tests.sandbox_doubles import RecordingRipgrep

PROFILE = load_sandbox_profile()


def write(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "home" / "ws" / "project"
    project.mkdir(parents=True)
    return {"project": project, "home": tmp_path / "home", "managed": tmp_path / "etc"}


def locations(layout: dict[str, Path]) -> SettingsLocations:
    return SettingsLocations(project_root=layout["project"], home=layout["home"], managed_dir=layout["managed"])


def adapter(layout: dict[str, Path], *, missing: bool = False, probe: str | None = None) -> SandboxAdapter:
    return SandboxAdapter(
        locations(layout), home=str(layout["home"]), platform="linux",
        which=lambda name: None if missing else f"/usr/bin/{name}", temp_dir="/tmp/tddagents-1000",
        run_ripgrep=RecordingRipgrep(), ripgrep_command="/opt/rg", probe=lambda: probe)


# ── parsing ─────────────────────────────────────────────────────────────────────


def test_the_flag_is_off_by_default() -> None:
    assert entry.parse_args([]).sandbox is False


def test_the_flag_takes_no_value() -> None:
    args = entry.parse_args(["--sandbox", "Build it"])
    assert args.sandbox is True
    assert args.prompt == "Build it"
    assert args.no_sandbox is False


@pytest.mark.parametrize("argv", [["--sandbox", "-p", "x"], ["-p", "x", "--sandbox"],
                                  ["--sandbox", "--thread-id", "t"]])
def test_the_flag_combines_with_the_other_flags(argv: list[str]) -> None:
    assert entry.parse_args(argv).sandbox is True


def test_the_two_flags_exclude_each_other(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        entry.parse_args(["--sandbox", "--no-sandbox"])
    assert stop.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_the_flag_is_in_the_help_text() -> None:
    assert "--sandbox" in entry.build_parser().format_help()


# ── the profile ─────────────────────────────────────────────────────────────────


def test_the_profile_is_the_sandbox_on_required_and_never_bypassed() -> None:
    sandbox = PROFILE["sandbox"]
    assert sandbox["enabled"] is True
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["filesystem"]["denyRead"] == ["~"]
    assert "pypi.org" in sandbox["network"]["allowedDomains"]


# ── main() ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any] | None]:
    calls: list[dict[str, Any] | None] = []

    def run_entry(args: argparse.Namespace) -> int:
        calls.append(get_cli_flag_settings())
        calls.append(get_inline_flag_settings())
        return 5

    monkeypatch.setattr(entry, "run_entry", run_entry)
    return calls


def test_main_applies_the_profile_before_the_session_opens(seen: list[dict[str, Any] | None]) -> None:
    assert entry.main(["--sandbox"]) == 5
    assert seen == [PROFILE, PROFILE]


def test_main_without_the_flag_applies_nothing(seen: list[dict[str, Any] | None]) -> None:
    entry.main([])
    assert seen == [None, None]


def test_main_with_no_sandbox_applies_only_the_off_switch(seen: list[dict[str, Any] | None]) -> None:
    entry.main(["--no-sandbox"])
    assert seen[0] == {"sandbox": {"enabled": False}}


def test_main_refuses_both_flags() -> None:
    with pytest.raises(SystemExit):
        entry.main(["--sandbox", "--no-sandbox"])
    assert get_cli_flag_settings() is None


def test_main_records_the_settings_file_for_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.bootstrap.state import get_flag_settings_path

    monkeypatch.setattr(entry, "run_entry", lambda args: 0)
    entry.main(["--settings", "f.json"])
    assert get_flag_settings_path() == "f.json"
    entry.main([])
    assert get_flag_settings_path() is None


# ── what the settings and the adapter then say ──────────────────────────────────


def test_the_profile_turns_the_sandbox_on_from_nothing(layout: dict[str, Path]) -> None:
    from app.bootstrap.state import set_cli_flag_settings

    assert load_settings(locations(layout)).get("sandbox") is None
    set_cli_flag_settings(PROFILE)
    assert load_settings(locations(layout))["sandbox"] == PROFILE["sandbox"]
    live = adapter(layout)
    assert live.is_sandbox_enabled_in_settings() is True
    assert live.is_sandbox_required() is True
    assert live.is_sandboxing_enabled() is True


def test_the_profile_wins_over_a_project_that_turned_the_sandbox_off(layout: dict[str, Path]) -> None:
    from app.bootstrap.state import set_cli_flag_settings

    write(layout["project"] / ".tddagents" / "settings.json", {"sandbox": {"enabled": False}})
    set_cli_flag_settings(PROFILE)
    assert load_settings(locations(layout))["sandbox"]["enabled"] is True


def test_a_managed_policy_that_turns_it_off_wins_over_the_profile(layout: dict[str, Path]) -> None:
    from app.bootstrap.state import set_cli_flag_settings

    write(layout["managed"] / "managed-settings.json", {"sandbox": {"enabled": False}})
    set_cli_flag_settings(PROFILE)
    assert load_settings(locations(layout))["sandbox"]["enabled"] is False


def test_the_profile_without_the_dependencies_refuses_to_open(layout: dict[str, Path]) -> None:
    from app.bootstrap.state import set_cli_flag_settings

    set_cli_flag_settings(PROFILE)
    live = adapter(layout, missing=True)
    assert live.is_sandbox_required() is True
    assert live.is_sandboxing_enabled() is False
    assert live.get_sandbox_unavailable_reason() is not None


def test_the_profile_with_a_failing_probe_refuses_to_open(layout: dict[str, Path]) -> None:
    from app.bootstrap.state import set_cli_flag_settings

    set_cli_flag_settings(PROFILE)
    live = adapter(layout, probe="bwrap cannot create a sandbox")
    assert live.is_sandbox_required() is True
    assert live.get_sandbox_unavailable_reason() is not None
