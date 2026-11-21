"""
`--settings <file-or-json>` and the sandbox flags (`app/main.py` `load_settings_from_flag`).

Ported from `reference/claude-code/src/main.tsx` `loadSettingsFromFlag`: a JSON string is written to a
content-hash temp file (`utils/tempfile.ts` `generateTempFilePath`), a path is resolved and must exist, and
the result is the session's flag settings file (`bootstrap/state.ts` `setFlagSettingsPath`). `--sandbox` /
`--no-sandbox` are TDDAgents' own (user decisions 2026-11-20): their fragment is merged over the `--settings`
document into one flag file, so the run, the final run and the skill read one source.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import pytest

import app.main as entry
from app.bootstrap.state import get_flag_settings_path
from app.utils.sandbox.sandbox_adapter import SandboxAdapter
from app.utils.settings.settings import SettingsLocations, load_settings
from app.utils.tempfile import generate_temp_file_path
from app.workspace.project import load_sandbox_profile
from tests.sandbox_doubles import RecordingRipgrep

OFF = {"sandbox": {"enabled": False}}
PROFILE = load_sandbox_profile()


@pytest.fixture(autouse=True)
def _temp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every temp settings file lands in this test's own directory."""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()


def read(path: str | None) -> Any:
    assert path is not None
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── generate_temp_file_path (utils/tempfile.ts) ─────────────────────────────────


def test_a_content_hash_gives_a_stable_path(tmp_path: Path) -> None:
    first = generate_temp_file_path("tddagents-settings", ".json", content_hash='{"a":1}')
    assert first == generate_temp_file_path("tddagents-settings", ".json", content_hash='{"a":1}')
    assert first != generate_temp_file_path("tddagents-settings", ".json", content_hash='{"a":2}')
    name = os.path.basename(first)
    assert name.startswith("tddagents-settings-") and name.endswith(".json")
    assert len(name) == len("tddagents-settings-") + 16 + len(".json")
    assert os.path.dirname(first) == str(tmp_path / "tmp")


def test_without_a_hash_each_path_is_new() -> None:
    assert generate_temp_file_path() != generate_temp_file_path()
    assert generate_temp_file_path().endswith(".md")


# ── --settings ──────────────────────────────────────────────────────────────────


def test_no_flag_gives_no_flag_settings() -> None:
    assert entry.load_settings_from_flag(None, None) is None


def test_a_path_is_resolved_and_kept(tmp_path: Path) -> None:
    file = tmp_path / "flags.json"
    file.write_text('{"model": "x"}')
    assert entry.load_settings_from_flag(str(file), None) == str(file.resolve())


def test_a_relative_path_resolves_against_the_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "f.json").write_text("{}")
    monkeypatch.chdir(tmp_path)
    assert entry.load_settings_from_flag("f.json", None) == str((tmp_path / "f.json").resolve())


def test_a_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(entry.FlagSettingsError, match="Settings file not found: .*nope.json"):
        entry.load_settings_from_flag(str(tmp_path / "nope.json"), None)


def test_a_json_string_is_written_to_a_hashed_temp_file() -> None:
    text = '{"sandbox": {"enabled": false}}'
    path = entry.load_settings_from_flag(f"  {text}  ", None)
    assert path == generate_temp_file_path("tddagents-settings", ".json", content_hash=text)
    assert Path(path).read_text(encoding="utf-8") == text


def test_the_same_json_gives_the_same_file() -> None:
    text = '{"model": "m"}'
    assert entry.load_settings_from_flag(text, None) == entry.load_settings_from_flag(text, None)


@pytest.mark.parametrize("bad", ["{not json}", '{"a": }', "{null}"])
def test_invalid_json_is_an_error(bad: str) -> None:
    with pytest.raises(entry.FlagSettingsError, match="Invalid JSON provided to --settings"):
        entry.load_settings_from_flag(bad, None)


def test_text_that_does_not_look_like_json_is_a_path(tmp_path: Path) -> None:
    with pytest.raises(entry.FlagSettingsError, match="Settings file not found"):
        entry.load_settings_from_flag("[1, 2]", None)


# ── the sandbox flags ───────────────────────────────────────────────────────────


def test_no_sandbox_alone_is_a_flag_file_turning_it_off() -> None:
    assert read(entry.load_settings_from_flag(None, OFF)) == OFF


def test_sandbox_alone_is_a_flag_file_holding_the_profile() -> None:
    assert read(entry.load_settings_from_flag(None, PROFILE)) == PROFILE


def test_the_flag_is_merged_over_a_settings_file(tmp_path: Path) -> None:
    file = tmp_path / "f.json"
    file.write_text(json.dumps({"model": "m", "sandbox": {"enabled": True, "failIfUnavailable": True}}))
    merged = read(entry.load_settings_from_flag(str(file), OFF))
    assert merged == {"model": "m", "sandbox": {"enabled": False, "failIfUnavailable": True}}
    assert json.loads(file.read_text())["sandbox"]["enabled"] is True  # the user's file is never written


def test_the_flag_is_merged_over_a_json_string() -> None:
    merged = read(entry.load_settings_from_flag('{"permissions": {"allow": ["Read"]}}', PROFILE))
    assert merged["permissions"] == {"allow": ["Read"]}
    assert merged["sandbox"] == PROFILE["sandbox"]


def test_a_merged_flag_file_is_stable() -> None:
    assert entry.load_settings_from_flag('{"a": 1}', OFF) == entry.load_settings_from_flag('{"a": 1}', OFF)
    assert entry.load_settings_from_flag('{"a": 1}', OFF) != entry.load_settings_from_flag('{"a": 2}', OFF)


def test_a_settings_file_that_is_not_an_object_cannot_take_a_sandbox_flag(tmp_path: Path) -> None:
    file = tmp_path / "f.json"
    file.write_text("[1]")
    with pytest.raises(entry.FlagSettingsError, match="must hold a JSON object"):
        entry.load_settings_from_flag(str(file), OFF)


def test_the_flags_fragments() -> None:
    assert entry.sandbox_flag_settings(entry.parse_args([])) is None
    assert entry.sandbox_flag_settings(entry.parse_args(["--no-sandbox"])) == OFF
    assert entry.sandbox_flag_settings(entry.parse_args(["--sandbox"])) == PROFILE


def test_the_two_sandbox_flags_exclude_each_other(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        entry.parse_args(["--sandbox", "--no-sandbox"])
    assert stop.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_the_flags_are_in_the_help_text() -> None:
    text = entry.build_parser().format_help()
    for flag in ("--sandbox", "--no-sandbox", "--settings"):
        assert flag in text
    assert "JSON string" in text


# ── main() ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[argparse.Namespace]:
    calls: list[argparse.Namespace] = []

    def run_entry(args: argparse.Namespace) -> int:
        calls.append(args)
        return 7

    monkeypatch.setattr(entry, "run_entry", run_entry)
    return calls


def test_main_hands_the_session_its_flag_file(seen: list[argparse.Namespace]) -> None:
    assert entry.main(["--no-sandbox"]) == 7
    assert read(seen[0].settings) == OFF
    assert get_flag_settings_path() == seen[0].settings
    assert entry.session_options(seen[0]).settings_path == seen[0].settings


def test_main_without_flags_has_no_flag_file(seen: list[argparse.Namespace]) -> None:
    entry.main([])
    assert seen[0].settings is None
    assert get_flag_settings_path() is None


def test_main_with_a_json_settings_string(seen: list[argparse.Namespace]) -> None:
    entry.main(["--settings", '{"sandbox":{"enabled":false}}'])
    assert read(seen[0].settings) == OFF


def test_main_reports_bad_settings_and_exits_1(seen: list[argparse.Namespace],
                                               capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main(["--settings", "{oops}"]) == 1
    assert "Error: Invalid JSON provided to --settings" in capsys.readouterr().err
    assert seen == []


def test_the_subcommands_take_no_flag_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entry, "run_plugin_command", lambda rest: 0)
    monkeypatch.setattr(entry, "run_doctor_command", lambda rest: 0)
    assert entry.main(["plugin", "list"]) == 0
    assert entry.main(["doctor"]) == 0
    assert get_flag_settings_path() is None


# ── what the session then reads ─────────────────────────────────────────────────


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "home" / "ws" / "project"
    project.mkdir(parents=True)
    return {"project": project, "home": tmp_path / "home", "managed": tmp_path / "etc"}


def locations(layout: dict[str, Path], flag: str | None) -> SettingsLocations:
    return SettingsLocations(project_root=layout["project"], home=layout["home"], managed_dir=layout["managed"],
                             flag_settings_path=Path(flag) if flag else None)


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def adapter(layout: dict[str, Path], flag: str | None, *, missing: bool = False) -> SandboxAdapter:
    return SandboxAdapter(
        locations(layout, flag), home=str(layout["home"]), platform="linux",
        which=lambda name: None if missing else f"/usr/bin/{name}", temp_dir="/tmp/tddagents-1000",
        run_ripgrep=RecordingRipgrep(), ripgrep_command="/opt/rg", probe=lambda: None)


def test_no_sandbox_turns_off_a_project_seeded_with_the_sandbox(layout: dict[str, Path]) -> None:
    write(layout["project"] / ".tddagents" / "settings.json", PROFILE)
    flag = entry.load_settings_from_flag(None, OFF)
    assert load_settings(locations(layout, flag))["sandbox"]["enabled"] is False
    off = adapter(layout, flag, missing=True)
    assert (off.is_sandbox_required(), off.get_sandbox_unavailable_reason()) == (False, None)


def test_sandbox_turns_it_on_and_requires_it(layout: dict[str, Path]) -> None:
    flag = entry.load_settings_from_flag(None, PROFILE)
    assert adapter(layout, flag).is_sandboxing_enabled() is True
    missing = adapter(layout, flag, missing=True)
    assert missing.is_sandbox_required() is True
    assert missing.get_sandbox_unavailable_reason() is not None


def test_a_managed_policy_still_wins(layout: dict[str, Path]) -> None:
    write(layout["managed"] / "managed-settings.json", {"sandbox": {"enabled": True}})
    flag = entry.load_settings_from_flag(None, OFF)
    assert load_settings(locations(layout, flag))["sandbox"]["enabled"] is True


def test_the_default_runs_without_the_sandbox(layout: dict[str, Path]) -> None:
    from app.workspace.project import prepare_project

    prepare_project(layout["project"])
    plain = adapter(layout, None, missing=True)
    assert "sandbox" not in load_settings(locations(layout, None))
    assert (plain.is_sandboxing_enabled(), plain.is_sandbox_required()) == (False, False)


# ── more edge cases (main.tsx loadSettingsFromFlag) ─────────────────────────────


def test_a_tilde_path_is_the_home_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "mine.json").write_text("{}")
    assert entry.load_settings_from_flag("~/mine.json", None) == str((tmp_path / "mine.json").resolve())


def test_a_directory_is_an_error_processing_settings(tmp_path: Path) -> None:
    with pytest.raises(entry.FlagSettingsError, match=r"^Error processing settings: "):
        entry.load_settings_from_flag(str(tmp_path), None)


def test_an_unreadable_file_is_an_error_processing_settings(tmp_path: Path) -> None:
    import os

    file = tmp_path / "secret.json"
    file.write_text("{}")
    file.chmod(0)
    try:
        if os.access(file, os.R_OK):  # running as root: permissions do not apply
            pytest.skip("the file stays readable")
        with pytest.raises(entry.FlagSettingsError, match=r"^Error processing settings: "):
            entry.load_settings_from_flag(str(file), None)
    finally:
        file.chmod(0o600)


def test_a_file_that_is_not_text_is_an_error_processing_settings(tmp_path: Path) -> None:
    file = tmp_path / "bin.json"
    file.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(entry.FlagSettingsError, match=r"^Error processing settings: "):
        entry.load_settings_from_flag(str(file), None)


def test_a_missing_file_says_not_found_as_upstream(tmp_path: Path) -> None:
    with pytest.raises(entry.FlagSettingsError, match=r"^Error: Settings file not found: "):
        entry.load_settings_from_flag(str(tmp_path / "gone.json"), None)


def test_json_with_newlines_and_spaces_is_kept_as_trimmed() -> None:
    text = '\n  {\n  "model": "m"\n}\n  '
    path = entry.load_settings_from_flag(text, None)
    assert Path(path).read_text() == text.strip()  # type: ignore[arg-type]


def test_a_file_whose_json_is_not_an_object_takes_no_sandbox_flag(tmp_path: Path) -> None:
    for content in ("[1]", "not json", '"text"', "3"):
        file = tmp_path / "f.json"
        file.write_text(content)
        with pytest.raises(entry.FlagSettingsError, match="must hold a JSON object"):
            entry.load_settings_from_flag(str(file), OFF)
        assert entry.load_settings_from_flag(str(file), None) == str(file.resolve())  # alone it is passed on


def test_arrays_merge_concatenated_and_deduplicated() -> None:
    """`merge_settings` (lodash `mergeWith` + `mergeArrays`): the profile's domains after the user's."""
    path = entry.load_settings_from_flag('{"sandbox": {"network": {"allowedDomains": ["a.example", "pypi.org"]}}}',
                                         PROFILE)
    domains = read(path)["sandbox"]["network"]["allowedDomains"]
    assert domains[:2] == ["a.example", "pypi.org"]
    assert domains.count("pypi.org") == 1
    assert set(PROFILE["sandbox"]["network"]["allowedDomains"]) <= set(domains)


def test_sandbox_over_a_settings_document_that_turns_it_off_turns_it_on() -> None:
    merged = read(entry.load_settings_from_flag('{"sandbox": {"enabled": false, "failIfUnavailable": false}}', PROFILE))
    assert (merged["sandbox"]["enabled"], merged["sandbox"]["failIfUnavailable"]) == (True, True)


def test_a_temp_folder_that_cannot_be_written_is_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path / "missing" / "dir"))
    with pytest.raises(entry.FlagSettingsError, match=r"^Error processing settings: "):
        entry.load_settings_from_flag('{"a": 1}', None)


def test_main_prints_upstreams_messages(seen: list[argparse.Namespace], tmp_path: Path,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main(["--settings", str(tmp_path / "gone.json")]) == 1
    assert capsys.readouterr().err.startswith("Error: Settings file not found: ")
    assert entry.main(["--settings", str(tmp_path)]) == 1
    assert capsys.readouterr().err.startswith("Error processing settings: ")
    assert seen == []


# ── integration: main() → the session's options and the final run ─────────────


def test_the_session_and_the_final_run_get_the_flag_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`main()` with the real `run_entry`: the develop path and the final run receive one settings path."""
    from tests.test_main_entry import DONE

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TDDAGENTS_COORDINATOR_MODE", "")
    monkeypatch.delenv("TDDAGENTS_COORDINATOR_MODE")
    seen_options: list[Any] = []
    final_paths: list[Any] = []

    class Workspace:
        root = tmp_path / "s" / "project"
        run_dir = tmp_path / "s"

    Workspace.root.mkdir(parents=True)
    monkeypatch.setattr("app.cli.print.session_workspace", lambda thread_id: Workspace())
    monkeypatch.setattr(entry, "session_exists", lambda thread_id, workspace: False)

    def develop(thread_id: str, request: Any, workspace: Any, options: Any, **kw: Any) -> Any:
        seen_options.append(options)
        return DONE

    monkeypatch.setattr(entry, "develop_interactive", develop)
    monkeypatch.setattr(entry, "develop_headless", develop)

    def final_run(artifacts: Any, workspace: Any, settings_path: Any = None) -> None:
        final_paths.append(settings_path)

    monkeypatch.setattr(entry, "write_final_test_run", final_run)
    assert entry.main(["--no-sandbox", "-p", "Build X"]) in (0, 1)
    [options] = seen_options
    assert read(options.settings_path) == OFF
    assert final_paths == [options.settings_path]


def test_a_resumed_thread_with_no_sandbox_gets_the_flag_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tests.test_main_entry import DONE

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TDDAGENTS_COORDINATOR_MODE", "")
    monkeypatch.delenv("TDDAGENTS_COORDINATOR_MODE")
    seen: list[tuple[Any, Any]] = []

    class Workspace:
        root = tmp_path / "s" / "project"
        run_dir = tmp_path / "s"

    Workspace.root.mkdir(parents=True)
    monkeypatch.setattr("app.cli.print.session_workspace", lambda thread_id: Workspace())
    monkeypatch.setattr(entry, "session_exists", lambda thread_id, workspace: True)
    monkeypatch.setattr(entry, "write_final_test_run", lambda *a, **k: None)

    def develop(thread_id: str, request: Any, workspace: Any, options: Any, prompt: Any = None, **kw: Any) -> Any:
        seen.append((thread_id, options.settings_path))
        return DONE

    monkeypatch.setattr(entry, "develop_interactive", develop)
    entry.main(["--thread-id", "tdd-abc", "--no-sandbox", "continue"])
    [(thread, path)] = seen
    assert thread == "tdd-abc" and read(path) == OFF
