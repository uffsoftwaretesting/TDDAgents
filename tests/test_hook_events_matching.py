"""
Tests for Hook event vocabulary, match key extraction, pattern matching, and deduplication (Part H1).
"""

from __future__ import annotations

from app.hooks.events import HOOK_EVENTS, HookEvent, extract_match_key, matches_pattern
from app.hooks.matching import deduplicate_hooks, eval_if_condition
from app.hooks.schemas import CommandHook, HttpHook, PromptHook


class TestHookEventVocabulary:
    def test_all_expected_events_present(self):
        assert HookEvent.PRE_TOOL_USE.value == "PreToolUse"
        assert HookEvent.POST_TOOL_USE.value == "PostToolUse"
        assert HookEvent.POST_TOOL_USE_FAILURE.value == "PostToolUseFailure"
        assert HookEvent.STOP.value == "Stop"
        assert HookEvent.STOP_FAILURE.value == "StopFailure"
        assert HookEvent.SESSION_START.value == "SessionStart"
        assert HookEvent.SESSION_END.value == "SessionEnd"
        assert HookEvent.NOTIFICATION.value == "Notification"
        assert HookEvent.PRE_COMPACT.value == "PreCompact"
        assert HookEvent.POST_COMPACT.value == "PostCompact"
        assert HookEvent.PERMISSION_REQUEST.value == "PermissionRequest"
        assert HookEvent.PERMISSION_DENIED.value == "PermissionDenied"
        assert HookEvent.SUBAGENT_START.value == "SubagentStart"
        assert HookEvent.SUBAGENT_STOP.value == "SubagentStop"
        assert HookEvent.FILE_CHANGED.value == "FileChanged"

    def test_hook_events_tuple_contains_strings(self):
        assert len(HOOK_EVENTS) >= 25
        assert all(isinstance(e, str) for e in HOOK_EVENTS)
        assert "PreToolUse" in HOOK_EVENTS
        assert "PostToolUse" in HOOK_EVENTS
        assert "Stop" in HOOK_EVENTS


class TestMatchKeyExtraction:
    def test_tool_events_extract_tool_name(self):
        for evt in (
            HookEvent.PRE_TOOL_USE,
            HookEvent.POST_TOOL_USE,
            HookEvent.POST_TOOL_USE_FAILURE,
            HookEvent.PERMISSION_REQUEST,
            HookEvent.PERMISSION_DENIED,
        ):
            assert extract_match_key(evt, {"tool_name": "Bash"}) == "Bash"
            assert extract_match_key(evt, {}) is None

    def test_session_start_and_config_change_extract_source(self):
        assert extract_match_key(HookEvent.SESSION_START, {"source": "startup"}) == "startup"
        assert extract_match_key(HookEvent.CONFIG_CHANGE, {"source": "settings"}) == "settings"

    def test_setup_and_compact_extract_trigger(self):
        assert extract_match_key(HookEvent.SETUP, {"trigger": "init"}) == "init"
        assert extract_match_key(HookEvent.PRE_COMPACT, {"trigger": "threshold"}) == "threshold"
        assert extract_match_key(HookEvent.POST_COMPACT, {"trigger": "auto"}) == "auto"

    def test_notification_extracts_type(self):
        assert extract_match_key(HookEvent.NOTIFICATION, {"notification_type": "alert"}) == "alert"

    def test_session_end_extracts_reason(self):
        assert extract_match_key(HookEvent.SESSION_END, {"reason": "prompt_input_exit"}) == "prompt_input_exit"

    def test_stop_failure_extracts_error(self):
        assert extract_match_key(HookEvent.STOP_FAILURE, {"error": "timeout"}) == "timeout"

    def test_subagent_events_extract_agent_type(self):
        assert extract_match_key(HookEvent.SUBAGENT_START, {"agent_type": "developer"}) == "developer"
        assert extract_match_key(HookEvent.SUBAGENT_STOP, {"agent_type": "refactorer"}) == "refactorer"

    def test_elicitation_extracts_server_name(self):
        assert extract_match_key(HookEvent.ELICITATION, {"mcp_server_name": "db"}) == "db"
        assert extract_match_key(HookEvent.ELICITATION_RESULT, {"mcp_server_name": "api"}) == "api"

    def test_instructions_loaded_extracts_load_reason(self):
        assert extract_match_key(HookEvent.INSTRUCTIONS_LOADED, {"load_reason": "workspace"}) == "workspace"

    def test_file_changed_extracts_basename(self):
        assert extract_match_key(HookEvent.FILE_CHANGED, {"file_path": "/path/to/main.py"}) == "main.py"
        assert extract_match_key(HookEvent.FILE_CHANGED, {}) is None

    def test_unkeyed_events_return_none(self):
        assert extract_match_key(HookEvent.STOP, {"some": "data"}) is None
        assert extract_match_key(HookEvent.TEAMMATE_IDLE, {}) is None
        assert extract_match_key(HookEvent.TASK_CREATED, {}) is None
        assert extract_match_key(HookEvent.TASK_COMPLETED, {}) is None


class TestPatternMatching:
    def test_wildcards_and_none(self):
        assert matches_pattern("Bash", None) is True
        assert matches_pattern("Bash", "*") is True
        assert matches_pattern(None, None) is True
        assert matches_pattern(None, "*") is True
        assert matches_pattern(None, "Bash") is False

    def test_exact_matches(self):
        assert matches_pattern("Bash", "Bash") is True
        assert matches_pattern("Grep", "Bash") is False

    def test_pipe_separated_matches(self):
        assert matches_pattern("Bash", "Bash|Grep") is True
        assert matches_pattern("Grep", "Bash|Grep") is True
        assert matches_pattern("RunCode", "Bash|Grep") is False
        assert matches_pattern("RunCode", "Bash | RunCode | Grep") is True

    def test_regex_matching(self):
        assert matches_pattern("TestClass", r"^Test.*") is True
        assert matches_pattern("ProductionClass", r"^Test.*") is False
        assert matches_pattern("invalid[regex", "[") is False


class TestIfConditionEvaluation:
    def test_empty_or_none_is_true(self):
        assert eval_if_condition(None, "Bash") is True
        assert eval_if_condition("", "Bash") is True
        assert eval_if_condition("   ", "Bash") is True

    def test_no_tool_name_returns_false(self):
        assert eval_if_condition("Bash(git *)", None) is False

    def test_command_matching(self):
        assert eval_if_condition("Bash(git *)", "Bash", command="git status") is True
        assert eval_if_condition("Bash(git *)", "Bash", command="npm test") is False
        assert eval_if_condition("Bash(git *)", "Grep", command="git status") is False

    def test_command_from_tool_input(self):
        assert eval_if_condition("Bash(pip *)", "Bash", tool_input={"command": "pip install pytest"}) is True
        assert eval_if_condition("Bash(pip *)", "Bash", tool_input={"command": "cargo build"}) is False

    def test_file_path_from_tool_input(self):
        assert eval_if_condition("Read(*.py)", "Read", tool_input={"file_path": "test.py"}) is True
        assert eval_if_condition("Read(*.py)", "Read", tool_input={"path": "test.js"}) is False


class TestHookDeduplication:
    def test_empty_sequence(self):
        assert deduplicate_hooks(()) == ()

    def test_command_hooks_deduplicated_by_source_and_command(self):
        h1 = CommandHook(command="echo hi", source="user")
        h2 = CommandHook(command="echo hi", source="user")
        h3 = CommandHook(command="echo hi", source="project")
        h4 = CommandHook(command="echo other", source="user")

        deduped = deduplicate_hooks([h1, h2, h3, h4])
        # h1 and h2 collapse to h2; h3 and h4 remain
        assert len(deduped) == 3
        assert h2 in deduped
        assert h3 in deduped
        assert h4 in deduped

    def test_different_if_conditions_do_not_dedup(self):
        h1 = CommandHook(command="echo hi", if_condition="Bash(git *)", source="repo")
        h2 = CommandHook(command="echo hi", if_condition="Bash(pip *)", source="repo")
        assert len(deduplicate_hooks([h1, h2])) == 2

    def test_prompt_and_http_hooks_deduplicated(self):
        p1 = PromptHook(prompt="Check tests", source="user")
        p2 = PromptHook(prompt="Check tests", source="user")
        u1 = HttpHook(url="https://api.example.com", source="user")
        u2 = HttpHook(url="https://api.example.com", source="user")

        deduped = deduplicate_hooks([p1, p2, u1, u2])
        assert len(deduped) == 2
