"""
Three-tier hook settings discovery and merge.

Every test passes an explicit `home`, so a developer's real `~/.tddagents/settings.json`
can never leak into a test run.
"""

from __future__ import annotations

import json

import pytest

from app.hooks.config import (
    DEFAULT_HOOK_TIMEOUT,
    KNOWN_EVENTS,
    HookSettings,
    load_hook_settings,
    settings_paths,
)


def write_settings(directory, filename, document):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def hooks_doc(event="PreToolUse", matcher="Bash", command="echo hi", **extra):
    hook = {"type": "command", "command": command, **extra}
    return {"hooks": {event: [{"matcher": matcher, "hooks": [hook]}]}}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return root


@pytest.fixture
def home(tmp_path):
    directory = tmp_path / "home"
    directory.mkdir()
    return directory


class TestSettingsPaths:
    def test_three_scopes_in_merge_order(self, project, home):
        paths = settings_paths(project, home)
        assert len(paths) == 3
        assert paths[0] == home / ".tddagents" / "settings.json"
        assert paths[1] == project / ".tddagents" / "settings.json"
        assert paths[2] == project / ".tddagents" / "settings.local.json"


class TestLoading:
    def test_no_files_yields_empty_settings(self, project, home):
        settings = load_hook_settings(project, home)
        assert settings.is_empty
        assert settings.sources == ()
        assert settings.matchers_for("PreToolUse") == ()

    def test_loads_a_project_hook(self, project, home):
        write_settings(project / ".tddagents", "settings.json", hooks_doc())
        settings = load_hook_settings(project, home)

        matchers = settings.matchers_for("PreToolUse")
        assert len(matchers) == 1
        assert matchers[0].matcher == "Bash"
        assert matchers[0].hooks[0].command == "echo hi"
        assert matchers[0].hooks[0].timeout == DEFAULT_HOOK_TIMEOUT

    def test_optional_fields_are_read(self, project, home):
        write_settings(
            project / ".tddagents",
            "settings.json",
            hooks_doc(timeout=5, **{"if": "Bash(pip *)", "statusMessage": "auditing"}),
        )
        hook = load_hook_settings(project, home).matchers_for("PreToolUse")[0].hooks[0]
        assert hook.timeout == 5.0
        assert hook.if_condition == "Bash(pip *)"
        assert hook.status_message == "auditing"

    def test_source_is_recorded(self, project, home):
        path = write_settings(project / ".tddagents", "settings.json", hooks_doc())
        assert load_hook_settings(project, home).sources == (str(path),)


class TestMergeOrder:
    def test_all_three_scopes_accumulate(self, project, home):
        write_settings(home / ".tddagents", "settings.json", hooks_doc(command="user"))
        write_settings(project / ".tddagents", "settings.json", hooks_doc(command="project"))
        write_settings(project / ".tddagents", "settings.local.json", hooks_doc(command="local"))

        matchers = load_hook_settings(project, home).matchers_for("PreToolUse")
        assert [m.hooks[0].command for m in matchers] == ["user", "project", "local"]

    def test_a_local_file_cannot_remove_a_project_hook(self, project, home):
        """Later scopes append. A personal file can add a veto, never silence one."""
        write_settings(project / ".tddagents", "settings.json", hooks_doc(command="gate"))
        write_settings(project / ".tddagents", "settings.local.json", {"hooks": {}})

        matchers = load_hook_settings(project, home).matchers_for("PreToolUse")
        assert [m.hooks[0].command for m in matchers] == ["gate"]

    def test_events_are_kept_separate(self, project, home):
        write_settings(
            project / ".tddagents",
            "settings.json",
            {
                "hooks": {
                    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "a"}]}],
                    "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "b"}]}],
                }
            },
        )
        settings = load_hook_settings(project, home)
        assert settings.matchers_for("PreToolUse")[0].hooks[0].command == "a"
        assert settings.matchers_for("PostToolUse")[0].hooks[0].command == "b"


class TestDefensiveParsing:
    """A typo in one file must not take hook dispatch down for the other two."""

    def test_malformed_json_is_skipped(self, project, home):
        (project / ".tddagents").mkdir(parents=True)
        (project / ".tddagents" / "settings.json").write_text("{not json", encoding="utf-8")
        write_settings(project / ".tddagents", "settings.local.json", hooks_doc(command="survivor"))

        matchers = load_hook_settings(project, home).matchers_for("PreToolUse")
        assert [m.hooks[0].command for m in matchers] == ["survivor"]

    def test_non_object_top_level_is_skipped(self, project, home):
        write_settings(project / ".tddagents", "settings.json", ["not", "an", "object"])
        assert load_hook_settings(project, home).is_empty

    def test_missing_hooks_key_is_fine(self, project, home):
        write_settings(project / ".tddagents", "settings.json", {"other": 1})
        assert load_hook_settings(project, home).is_empty

    def test_unsupported_event_is_ignored(self, project, home):
        write_settings(project / ".tddagents", "settings.json", hooks_doc(event="SessionStart"))
        assert load_hook_settings(project, home).is_empty

    def test_unsupported_hook_type_is_ignored(self, project, home):
        write_settings(
            project / ".tddagents",
            "settings.json",
            {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "http", "url": "x"}]}]}},
        )
        assert load_hook_settings(project, home).is_empty

    def test_hook_without_a_command_is_ignored(self, project, home):
        write_settings(
            project / ".tddagents",
            "settings.json",
            {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command"}]}]}},
        )
        assert load_hook_settings(project, home).is_empty

    def test_blank_command_is_ignored(self, project, home):
        write_settings(project / ".tddagents", "settings.json", hooks_doc(command="   "))
        assert load_hook_settings(project, home).is_empty

    @pytest.mark.parametrize("bad", [0, -5, "soon", None, True])
    def test_invalid_timeout_falls_back_to_the_default(self, project, home, bad):
        write_settings(project / ".tddagents", "settings.json", hooks_doc(timeout=bad))
        hook = load_hook_settings(project, home).matchers_for("PreToolUse")[0].hooks[0]
        assert hook.timeout == DEFAULT_HOOK_TIMEOUT

    def test_non_list_event_is_ignored(self, project, home):
        write_settings(project / ".tddagents", "settings.json", {"hooks": {"PreToolUse": {}}})
        assert load_hook_settings(project, home).is_empty

    def test_non_object_matcher_is_ignored(self, project, home):
        write_settings(project / ".tddagents", "settings.json", {"hooks": {"PreToolUse": ["x"]}})
        assert load_hook_settings(project, home).is_empty

    def test_matcher_with_no_valid_hooks_is_dropped(self, project, home):
        write_settings(
            project / ".tddagents",
            "settings.json",
            {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]}},
        )
        assert load_hook_settings(project, home).is_empty

    def test_absent_matcher_means_every_tool(self, project, home):
        write_settings(
            project / ".tddagents",
            "settings.json",
            {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "x"}]}]}},
        )
        assert load_hook_settings(project, home).matchers_for("PreToolUse")[0].matcher is None

    def test_type_defaults_to_command(self, project, home):
        write_settings(
            project / ".tddagents",
            "settings.json",
            {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"command": "x"}]}]}},
        )
        assert load_hook_settings(project, home).matchers_for("PreToolUse")[0].hooks[0].command == "x"


class TestDescribe:
    """Hooks are host-side processes; a run's log has to record which ones were live."""

    def test_empty_settings_say_so(self):
        assert HookSettings().describe() == "no hook settings found"

    def test_describes_counts_and_sources(self, project, home):
        path = write_settings(project / ".tddagents", "settings.json", hooks_doc())
        description = load_hook_settings(project, home).describe()
        assert "PreToolUse=1" in description
        assert "PostToolUse=0" in description
        assert str(path) in description

    def test_known_events_are_exactly_the_two_tool_events(self):
        assert KNOWN_EVENTS == ("PreToolUse", "PostToolUse")


class TestHookCommandFields:
    def test_source_records_the_originating_file(self, project, home):
        """Which file a hook came from is what makes a surprising veto diagnosable."""
        path = write_settings(project / ".tddagents", "settings.json", hooks_doc())
        hook = load_hook_settings(project, home).matchers_for("PreToolUse")[0].hooks[0]
        assert hook.source == str(path)

    def test_source_distinguishes_the_three_scopes(self, project, home):
        user = write_settings(home / ".tddagents", "settings.json", hooks_doc(command="u"))
        shared = write_settings(project / ".tddagents", "settings.json", hooks_doc(command="p"))
        local = write_settings(project / ".tddagents", "settings.local.json", hooks_doc(command="l"))

        matchers = load_hook_settings(project, home).matchers_for("PreToolUse")
        assert [m.hooks[0].source for m in matchers] == [str(user), str(shared), str(local)]

    def test_label_prefers_the_status_message(self):
        from app.hooks.config import HookCommand

        assert HookCommand(command="run.sh", status_message="auditing").label == "auditing"

    def test_label_falls_back_to_the_command(self):
        from app.hooks.config import HookCommand

        assert HookCommand(command="run.sh").label == "run.sh"

    def test_label_falls_back_when_the_status_message_is_blank(self):
        from app.hooks.config import HookCommand

        assert HookCommand(command="run.sh", status_message="").label == "run.sh"


class TestFieldNormalization:
    def test_empty_if_condition_becomes_none(self, project, home):
        """An empty `if` is no condition at all, not a condition that matches nothing."""
        write_settings(project / ".tddagents", "settings.json", hooks_doc(**{"if": ""}))
        hook = load_hook_settings(project, home).matchers_for("PreToolUse")[0].hooks[0]
        assert hook.if_condition is None

    def test_non_string_if_condition_becomes_none(self, project, home):
        write_settings(project / ".tddagents", "settings.json", hooks_doc(**{"if": 42}))
        hook = load_hook_settings(project, home).matchers_for("PreToolUse")[0].hooks[0]
        assert hook.if_condition is None

    def test_empty_matcher_becomes_none(self, project, home):
        """`"matcher": ""` must mean "every tool", the same as omitting it."""
        write_settings(project / ".tddagents", "settings.json", hooks_doc(matcher=""))
        assert load_hook_settings(project, home).matchers_for("PreToolUse")[0].matcher is None

    def test_non_string_matcher_becomes_none(self, project, home):
        write_settings(project / ".tddagents", "settings.json", hooks_doc(matcher=7))
        assert load_hook_settings(project, home).matchers_for("PreToolUse")[0].matcher is None

    def test_non_string_status_message_becomes_empty(self, project, home):
        write_settings(project / ".tddagents", "settings.json", hooks_doc(statusMessage=99))
        hook = load_hook_settings(project, home).matchers_for("PreToolUse")[0].hooks[0]
        assert hook.status_message == ""

    def test_a_one_second_timeout_is_valid(self, project, home):
        """The boundary is `<= 0`; a 1s timeout is short but legitimate."""
        write_settings(project / ".tddagents", "settings.json", hooks_doc(timeout=1))
        hook = load_hook_settings(project, home).matchers_for("PreToolUse")[0].hooks[0]
        assert hook.timeout == 1.0

    def test_a_fractional_timeout_is_kept(self, project, home):
        write_settings(project / ".tddagents", "settings.json", hooks_doc(timeout=0.5))
        hook = load_hook_settings(project, home).matchers_for("PreToolUse")[0].hooks[0]
        assert hook.timeout == 0.5

    def test_missing_timeout_uses_the_default(self, project, home):
        write_settings(project / ".tddagents", "settings.json", hooks_doc())
        hook = load_hook_settings(project, home).matchers_for("PreToolUse")[0].hooks[0]
        assert hook.timeout == DEFAULT_HOOK_TIMEOUT


class TestBadEntriesDoNotStopParsing:
    """One malformed entry must cost that entry, not the ones that follow it."""

    def test_an_invalid_hook_does_not_drop_later_hooks(self, project, home):
        write_settings(
            project / ".tddagents",
            "settings.json",
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "http"}, {"type": "command", "command": "survivor"}],
                        }
                    ]
                }
            },
        )
        hooks = load_hook_settings(project, home).matchers_for("PreToolUse")[0].hooks
        assert [h.command for h in hooks] == ["survivor"]

    def test_an_invalid_matcher_does_not_drop_later_matchers(self, project, home):
        write_settings(
            project / ".tddagents",
            "settings.json",
            {
                "hooks": {
                    "PreToolUse": [
                        "not an object",
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "survivor"}]},
                    ]
                }
            },
        )
        matchers = load_hook_settings(project, home).matchers_for("PreToolUse")
        assert [m.hooks[0].command for m in matchers] == ["survivor"]

    def test_an_unsupported_event_does_not_drop_a_later_event(self, project, home):
        write_settings(
            project / ".tddagents",
            "settings.json",
            {
                "hooks": {
                    "SessionStart": [{"hooks": [{"command": "ignored"}]}],
                    "PostToolUse": [{"hooks": [{"command": "survivor"}]}],
                }
            },
        )
        settings = load_hook_settings(project, home)
        assert settings.matchers_for("PostToolUse")[0].hooks[0].command == "survivor"

    def test_a_matcher_with_only_bad_hooks_does_not_drop_a_later_matcher(self, project, home):
        write_settings(
            project / ".tddagents",
            "settings.json",
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command"}]},
                        {"matcher": "Grep", "hooks": [{"type": "command", "command": "survivor"}]},
                    ]
                }
            },
        )
        matchers = load_hook_settings(project, home).matchers_for("PreToolUse")
        assert [m.matcher for m in matchers] == ["Grep"]
