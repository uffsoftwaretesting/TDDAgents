"""
The hook dispatcher, exercised against real shell processes.

Hooks are an OS-level contract — JSON on stdin, a verdict in the exit code — so mocking
`subprocess.run` would test the mock rather than the contract. Every hook below is a real
command that really runs.
"""

from __future__ import annotations

import json

import pytest

from app.hooks.config import HookCommand, HookMatcher, HookSettings
from app.hooks.dispatcher import BLOCKING_EXIT_CODE, HookDispatcher, HookEvent


def settings_with(*hooks: HookCommand, event: str = "PreToolUse", matcher: str | None = None):
    return HookSettings(events={event: (HookMatcher(matcher=matcher, hooks=tuple(hooks)),)})


def hook(command: str, **kwargs) -> HookCommand:
    return HookCommand(command=command, **kwargs)


def script_hook(tmp_path, body: str, name: str = "hook.sh", **kwargs) -> HookCommand:
    """
    A hook whose *command* shares no text with its *output*.

    This matters more than it looks. The blocked-reason fallback embeds `hook.label`,
    which is the command string — so an inline `echo 'X' >&2; exit 2` makes
    `"X" in reason` true even when the dispatcher drops stderr entirely. Mutation testing
    caught exactly that false positive. Putting the body in a script keeps the assertion
    honest, and is how a real hook is written anyway.
    """
    path = tmp_path / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return HookCommand(command=str(path), **kwargs)


def dispatch(dispatcher, event=HookEvent.PRE_TOOL_USE, tool_name="Bash", **kwargs):
    payload = {"tool_input": {"command": "pip install x"}, "command": "pip install x"}
    payload.update(kwargs)
    return dispatcher.run(event, tool_name=tool_name, **payload)


class TestExitCodeContract:
    def test_exit_zero_proceeds(self):
        outcome = dispatch(HookDispatcher(settings_with(hook("exit 0"))))
        assert outcome.denied is False
        assert outcome.stop_continuation is False

    def test_exit_two_vetoes_pre_tool_use(self, tmp_path):
        dispatcher = HookDispatcher(
            settings_with(script_hook(tmp_path, "echo 'no pip installs' >&2; exit 2"))
        )
        outcome = dispatch(dispatcher)
        assert outcome.denied is True
        assert "no pip installs" in outcome.reason

    def test_the_reason_comes_from_stderr_not_from_the_command(self, tmp_path):
        """stderr is the channel; the command name must not leak in as the reason."""
        dispatcher = HookDispatcher(
            settings_with(script_hook(tmp_path, "echo 'the real reason' >&2; exit 2"))
        )
        assert dispatch(dispatcher).reason == "the real reason"

    def test_exit_two_falls_back_to_stdout_when_stderr_is_empty(self, tmp_path):
        dispatcher = HookDispatcher(
            settings_with(script_hook(tmp_path, "echo 'reason on stdout'; exit 2"))
        )
        assert dispatch(dispatcher).reason == "reason on stdout"

    def test_stderr_wins_over_stdout(self, tmp_path):
        dispatcher = HookDispatcher(
            settings_with(script_hook(tmp_path, "echo OUT; echo ERR >&2; exit 2"))
        )
        assert dispatch(dispatcher).reason == "ERR"

    def test_exit_two_with_no_output_still_gives_a_reason(self):
        assert dispatch(HookDispatcher(settings_with(hook("exit 2")))).reason != ""

    def test_other_exit_codes_are_logged_and_ignored(self):
        """A broken hook must not break the pipeline."""
        dispatcher = HookDispatcher(settings_with(hook("echo boom >&2; exit 42")))
        outcome = dispatch(dispatcher)
        assert outcome.denied is False
        assert outcome.stop_continuation is False

    def test_a_missing_executable_is_not_fatal(self):
        dispatcher = HookDispatcher(settings_with(hook("definitely-not-a-real-command-xyz")))
        assert dispatch(dispatcher).denied is False

    def test_timeout_is_survivable(self):
        dispatcher = HookDispatcher(settings_with(hook("sleep 5", timeout=0.2)))
        outcome = dispatch(dispatcher)
        assert outcome.denied is False


class TestPostToolUseSemantics:
    """The side effect already landed. Exit 2 flags it; it never pretends it did not happen."""

    def test_exit_two_does_not_deny(self, tmp_path):
        dispatcher = HookDispatcher(
            settings_with(
                script_hook(tmp_path, "echo 'coverage dropped' >&2; exit 2"), event="PostToolUse"
            )
        )
        outcome = dispatch(dispatcher, event=HookEvent.POST_TOOL_USE)
        assert outcome.denied is False

    def test_exit_two_becomes_feedback_and_stops_continuation(self, tmp_path):
        dispatcher = HookDispatcher(
            settings_with(
                script_hook(tmp_path, "echo 'coverage dropped' >&2; exit 2"), event="PostToolUse"
            )
        )
        outcome = dispatch(dispatcher, event=HookEvent.POST_TOOL_USE)
        assert outcome.additional_context == "coverage dropped"
        assert outcome.stop_continuation is True


class TestPayload:
    def _echo_payload_hook(self, tmp_path):
        capture = tmp_path / "payload.json"
        return capture, hook(f"cat > {capture}")

    def test_payload_reaches_the_hook_on_stdin(self, tmp_path):
        capture, echo = self._echo_payload_hook(tmp_path)
        dispatcher = HookDispatcher(settings_with(echo), project_root=tmp_path)
        dispatch(dispatcher)

        payload = json.loads(capture.read_text())
        assert payload["hook_event_name"] == "PreToolUse"
        assert payload["tool_name"] == "Bash"
        assert payload["tool_input"] == {"command": "pip install x"}

    def test_context_fields_are_included(self, tmp_path):
        capture, echo = self._echo_payload_hook(tmp_path)
        dispatcher = HookDispatcher(settings_with(echo), project_root=tmp_path)
        dispatch(
            dispatcher,
            ctx_fields={
                "session_id": "s1",
                "cwd": "/work",
                "permission_mode": "full",
                "agent_id": "a1",
                "agent_type": "developer",
            },
        )

        payload = json.loads(capture.read_text())
        assert payload["session_id"] == "s1"
        assert payload["permission_mode"] == "full"
        assert payload["agent_type"] == "developer"

    def test_tool_response_only_appears_on_post(self, tmp_path):
        capture, echo = self._echo_payload_hook(tmp_path)
        pre = HookDispatcher(settings_with(echo), project_root=tmp_path)
        dispatch(pre)
        assert "tool_response" not in json.loads(capture.read_text())

        post = HookDispatcher(settings_with(echo, event="PostToolUse"), project_root=tmp_path)
        dispatch(post, event=HookEvent.POST_TOOL_USE, tool_response="the output")
        assert json.loads(capture.read_text())["tool_response"] == "the output"


class TestMatching:
    def test_matcher_selects_by_tool_name(self):
        dispatcher = HookDispatcher(settings_with(hook("exit 2"), matcher="Grep"))
        assert dispatch(dispatcher, tool_name="Bash").denied is False
        assert dispatch(dispatcher, tool_name="Grep").denied is True

    @pytest.mark.parametrize("matcher", [None, "*"])
    def test_wildcard_matchers_catch_everything(self, matcher):
        dispatcher = HookDispatcher(settings_with(hook("exit 2"), matcher=matcher))
        assert dispatch(dispatcher, tool_name="AnythingAtAll").denied is True

    def test_alternation_matcher(self):
        dispatcher = HookDispatcher(settings_with(hook("exit 2"), matcher="Bash|RunCode"))
        assert dispatch(dispatcher, tool_name="Bash").denied is True
        assert dispatch(dispatcher, tool_name="RunCode").denied is True
        assert dispatch(dispatcher, tool_name="Grep").denied is False

    def test_if_condition_filters_before_spawning(self):
        dispatcher = HookDispatcher(
            settings_with(hook("exit 2", if_condition="Bash(pip *)"))
        )
        assert dispatch(dispatcher, command="pip install x").denied is True
        assert dispatch(dispatcher, command="ls -la").denied is False

    def test_if_condition_on_a_different_tool_never_fires(self):
        dispatcher = HookDispatcher(
            settings_with(hook("exit 2", if_condition="Grep(x)"), matcher=None)
        )
        assert dispatch(dispatcher, tool_name="Bash", command="pip install x").denied is False


class TestMultipleHooks:
    def test_all_matching_hooks_run(self, tmp_path):
        log = tmp_path / "log"
        dispatcher = HookDispatcher(
            settings_with(hook(f"echo a >> {log}"), hook(f"echo b >> {log}")),
            project_root=tmp_path,
        )
        dispatch(dispatcher)
        assert log.read_text().split() == ["a", "b"]

    def test_first_denial_short_circuits(self, tmp_path):
        log = tmp_path / "log"
        dispatcher = HookDispatcher(
            settings_with(hook("exit 2"), hook(f"echo ran >> {log}")),
            project_root=tmp_path,
        )
        assert dispatch(dispatcher).denied is True
        assert not log.exists()

    def test_context_from_several_hooks_accumulates(self):
        dispatcher = HookDispatcher(
            settings_with(
                hook('echo \'{"additionalContext": "first"}\''),
                hook('echo \'{"additionalContext": "second"}\''),
            )
        )
        outcome = dispatch(dispatcher)
        assert "first" in outcome.additional_context
        assert "second" in outcome.additional_context


class TestJsonProtocol:
    def test_deny_decision(self):
        document = {"permissionDecision": "deny", "permissionDecisionReason": "policy says no"}
        dispatcher = HookDispatcher(settings_with(hook(f"echo '{json.dumps(document)}'")))
        outcome = dispatch(dispatcher)
        assert outcome.denied is True
        assert outcome.reason == "policy says no"

    def test_deny_without_a_reason_still_gives_one(self):
        dispatcher = HookDispatcher(settings_with(hook('echo \'{"permissionDecision": "deny"}\'')))
        assert dispatch(dispatcher).reason != ""

    def test_allow_decision_overrides_a_blocking_exit_code(self):
        document = {"permissionDecision": "allow"}
        dispatcher = HookDispatcher(
            settings_with(hook(f"echo '{json.dumps(document)}'; exit {BLOCKING_EXIT_CODE}"))
        )
        assert dispatch(dispatcher).denied is False

    def test_updated_input_on_pre_tool_use(self):
        document = {"updatedInput": {"command": "pip install --dry-run x"}}
        dispatcher = HookDispatcher(settings_with(hook(f"echo '{json.dumps(document)}'")))
        assert dispatch(dispatcher).updated_input == {"command": "pip install --dry-run x"}

    def test_updated_input_is_ignored_on_post_tool_use(self):
        """The call already ran; rewriting its input afterwards would mean nothing."""
        document = {"updatedInput": {"command": "x"}}
        dispatcher = HookDispatcher(
            settings_with(hook(f"echo '{json.dumps(document)}'"), event="PostToolUse")
        )
        assert dispatch(dispatcher, event=HookEvent.POST_TOOL_USE).updated_input is None

    def test_continue_false_stops_continuation_on_post(self):
        dispatcher = HookDispatcher(
            settings_with(hook('echo \'{"continue": false}\''), event="PostToolUse")
        )
        assert dispatch(dispatcher, event=HookEvent.POST_TOOL_USE).stop_continuation is True

    def test_continue_false_is_ignored_on_pre(self):
        dispatcher = HookDispatcher(settings_with(hook('echo \'{"continue": false}\'')))
        assert dispatch(dispatcher).stop_continuation is False

    def test_ask_is_not_honored_as_a_decision(self):
        """There is no interactive prompt to route `ask` to; guessing would be worse."""
        dispatcher = HookDispatcher(
            settings_with(hook('echo \'{"permissionDecision": "ask"}\''))
        )
        outcome = dispatch(dispatcher)
        assert outcome.denied is False

    def test_additional_context_is_carried(self):
        dispatcher = HookDispatcher(
            settings_with(hook('echo \'{"additionalContext": "mind the lint rules"}\''))
        )
        assert dispatch(dispatcher).additional_context == "mind the lint rules"


class TestGracefulDegradation:
    def test_plain_text_stdout_is_not_an_error(self):
        dispatcher = HookDispatcher(settings_with(hook("echo just a log line")))
        outcome = dispatch(dispatcher)
        assert outcome.denied is False
        assert outcome.additional_context == ""

    def test_malformed_json_degrades_to_text(self):
        dispatcher = HookDispatcher(settings_with(hook("echo '{not json'")))
        assert dispatch(dispatcher).denied is False

    def test_json_array_stdout_is_ignored(self):
        dispatcher = HookDispatcher(settings_with(hook("echo '[1,2,3]'")))
        assert dispatch(dispatcher).denied is False

    def test_empty_stdout_is_fine(self):
        assert dispatch(HookDispatcher(settings_with(hook("true")))).denied is False

    def test_unknown_decision_value_is_ignored(self):
        dispatcher = HookDispatcher(
            settings_with(hook('echo \'{"permissionDecision": "maybe"}\''))
        )
        assert dispatch(dispatcher).denied is False


class TestNoConfiguration:
    def test_no_hooks_configured_is_a_clean_pass(self):
        outcome = dispatch(HookDispatcher(HookSettings()))
        assert outcome.denied is False
        assert outcome.additional_context == ""
        assert outcome.updated_input is None

    def test_from_project_reads_the_filesystem(self, tmp_path):
        directory = tmp_path / ".tddagents"
        directory.mkdir()
        (directory / "settings.json").write_text(
            json.dumps(
                {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "exit 2"}]}]}}
            ),
            encoding="utf-8",
        )
        dispatcher = HookDispatcher.from_project(tmp_path, home=tmp_path / "nonexistent-home")
        assert dispatch(dispatcher).denied is True


class TestJsonEdgeCases:
    """
    Guards written as `isinstance(x, str) and x` reject both a wrong type and an empty
    string. Swapping in `or` still handles `None`, so only these two cases separate them.
    """

    def test_non_string_additional_context_is_ignored(self):
        dispatcher = HookDispatcher(settings_with(hook('echo \'{"additionalContext": 123}\'')))
        assert dispatch(dispatcher).additional_context == ""

    def test_empty_additional_context_is_ignored(self):
        dispatcher = HookDispatcher(settings_with(hook('echo \'{"additionalContext": ""}\'')))
        assert dispatch(dispatcher).additional_context == ""

    def test_empty_deny_reason_falls_back_to_the_default(self, tmp_path):
        body = """echo '{"permissionDecision": "deny", "permissionDecisionReason": ""}'"""
        dispatcher = HookDispatcher(settings_with(script_hook(tmp_path, body)))
        outcome = dispatch(dispatcher)
        assert outcome.denied is True
        assert outcome.reason != ""

    def test_non_string_deny_reason_falls_back_to_the_default(self, tmp_path):
        body = """echo '{"permissionDecision": "deny", "permissionDecisionReason": 7}'"""
        dispatcher = HookDispatcher(settings_with(script_hook(tmp_path, body)))
        assert dispatch(dispatcher).reason != ""

    def test_non_object_updated_input_is_ignored(self):
        dispatcher = HookDispatcher(settings_with(hook('echo \'{"updatedInput": "nope"}\'')))
        assert dispatch(dispatcher).updated_input is None

    def test_continue_true_does_not_stop_continuation(self):
        dispatcher = HookDispatcher(
            settings_with(hook('echo \'{"continue": true}\''), event="PostToolUse")
        )
        assert dispatch(dispatcher, event=HookEvent.POST_TOOL_USE).stop_continuation is False

    def test_json_on_a_non_blocking_error_exit_is_not_applied(self, tmp_path):
        """A hook that crashed is not a hook whose verdict should be trusted."""
        body = """echo '{"permissionDecision": "deny"}'; exit 42"""
        dispatcher = HookDispatcher(settings_with(script_hook(tmp_path, body)))
        assert dispatch(dispatcher).denied is False


class TestMatcherParsing:
    def test_alternation_tolerates_whitespace(self):
        dispatcher = HookDispatcher(settings_with(hook("exit 2"), matcher="Bash | RunCode"))
        assert dispatch(dispatcher, tool_name="RunCode").denied is True

    def test_empty_alternation_segments_are_dropped(self):
        dispatcher = HookDispatcher(settings_with(hook("exit 2"), matcher="Bash||Grep"))
        assert dispatch(dispatcher, tool_name="Grep").denied is True
        assert dispatch(dispatcher, tool_name="Other").denied is False

    def test_matching_is_exact_not_a_prefix(self):
        dispatcher = HookDispatcher(settings_with(hook("exit 2"), matcher="Bash"))
        assert dispatch(dispatcher, tool_name="BashOutput").denied is False


class TestExecutionEnvironment:
    def test_the_hook_runs_from_the_project_root(self, tmp_path):
        capture = tmp_path / "cwd.txt"
        dispatcher = HookDispatcher(settings_with(hook(f"pwd > {capture}")), project_root=tmp_path)
        dispatch(dispatcher)
        assert capture.read_text().strip() == str(tmp_path)

    def test_stdin_is_valid_json(self, tmp_path):
        capture = tmp_path / "payload.json"
        dispatcher = HookDispatcher(settings_with(hook(f"cat > {capture}")), project_root=tmp_path)
        dispatch(dispatcher)
        assert isinstance(json.loads(capture.read_text()), dict)

    def test_a_per_hook_timeout_is_honored(self, tmp_path):
        """The configured timeout, not a global one — a slow hook must not stall a run."""
        marker = tmp_path / "finished"
        dispatcher = HookDispatcher(
            settings_with(script_hook(tmp_path, f"sleep 5; touch {marker}", timeout=0.2))
        )
        dispatch(dispatcher)
        assert not marker.exists()


class TestFilteringSkipsRatherThanStops:
    """
    A non-matching hook must be skipped, not treated as the end of the list. `break` where
    `continue` belongs silently disables every hook configured after the first miss.
    """

    def test_a_non_matching_matcher_does_not_hide_a_later_one(self, tmp_path):
        log = tmp_path / "log"
        settings = HookSettings(
            events={
                "PreToolUse": (
                    HookMatcher(matcher="Grep", hooks=(hook("echo skipped"),)),
                    HookMatcher(matcher="Bash", hooks=(hook(f"echo ran > {log}"),)),
                )
            }
        )
        dispatch(HookDispatcher(settings, project_root=tmp_path), tool_name="Bash")
        assert log.exists()

    def test_a_filtered_if_condition_does_not_hide_a_later_hook(self, tmp_path):
        log = tmp_path / "log"
        settings = settings_with(
            hook("echo skipped", if_condition="Bash(npm *)"),
            hook(f"echo ran > {log}"),
        )
        dispatch(HookDispatcher(settings, project_root=tmp_path), command="pip install x")
        assert log.exists()

    def test_several_matchers_all_contribute(self, tmp_path):
        log = tmp_path / "log"
        settings = HookSettings(
            events={
                "PreToolUse": (
                    HookMatcher(matcher="Bash", hooks=(hook(f"echo a >> {log}"),)),
                    HookMatcher(matcher=None, hooks=(hook(f"echo b >> {log}"),)),
                )
            }
        )
        dispatch(HookDispatcher(settings, project_root=tmp_path), tool_name="Bash")
        assert log.read_text().split() == ["a", "b"]


class TestContextJoining:
    def test_two_contexts_are_joined_with_exactly_one_newline(self):
        dispatcher = HookDispatcher(
            settings_with(
                hook('echo \'{"additionalContext": "first"}\''),
                hook('echo \'{"additionalContext": "second"}\''),
            )
        )
        assert dispatch(dispatcher).additional_context == "first\nsecond"

    def test_a_silent_hook_contributes_nothing_to_the_join(self):
        dispatcher = HookDispatcher(
            settings_with(
                hook('echo \'{"additionalContext": "first"}\''),
                hook("true"),
                hook('echo \'{"additionalContext": "second"}\''),
            )
        )
        assert dispatch(dispatcher).additional_context == "first\nsecond"

    def test_a_single_context_is_not_padded(self):
        dispatcher = HookDispatcher(
            settings_with(hook('echo \'{"additionalContext": "only"}\''))
        )
        assert dispatch(dispatcher).additional_context == "only"


class TestOutputDecoding:
    def test_invalid_utf8_output_does_not_crash_the_dispatcher(self, tmp_path):
        """
        A hook is an arbitrary process and may emit binary garbage. Decoding must replace
        the bad bytes rather than raise, or one stray `printf` takes down the run.
        """
        dispatcher = HookDispatcher(
            settings_with(script_hook(tmp_path, r"printf '\xff\xfe invalid' >&2; exit 2"))
        )
        outcome = dispatch(dispatcher)
        assert outcome.denied is True
        assert "invalid" in outcome.reason

    def test_utf8_output_survives_intact(self, tmp_path):
        dispatcher = HookDispatcher(
            settings_with(script_hook(tmp_path, "echo 'café — naïve' >&2; exit 2"))
        )
        assert dispatch(dispatcher).reason == "café — naïve"
