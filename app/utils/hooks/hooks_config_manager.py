"""
What `/hooks` knows about each hook event, and the configured hooks grouped for it.

Ported from `reference/claude-code/src/utils/hooks/hooksConfigManager.ts` -> `MatcherMetadata`,
`HookEventMetadata`, `getHookEventMetadata`, `groupHooksByEventAndMatcher`,
`getSortedMatchersForEvent`, `getHooksForMatcher`, `getMatcherMetadata`.

The event texts are upstream's, in its order, after the product substitutions: "Claude" ->
"TDDAgents", `CLAUDE.md` -> `TDDAGENTS.md`, `CLAUDE_ENV_FILE` -> `TDDAGENTS_ENV_FILE`
(`app/utils/session_environment.py`). `groupHooksByEventAndMatcher` takes the settings and session
hooks (`get_all_hooks`), then the registered ones: a plugin's matchers (those with a plugin root)
are listed as `pluginHook`; an SDK host's callback matchers are not (upstream shows them to its
internal users only).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.schemas.hooks import HookMatcher
from app.utils.hooks.hooks_settings import (
    PLUGIN_HOOK,
    IndividualHookConfig,
    get_all_hooks,
    sort_matchers_by_priority,
)
from app.utils.settings.settings import SettingsLocations


@dataclass(frozen=True, slots=True)
class MatcherMetadata:
    """`MatcherMetadata`: the hook input field a matcher is matched against, and its known values."""

    field_to_match: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HookEventMetadata:
    """`HookEventMetadata`."""

    summary: str
    description: str
    matcher_metadata: MatcherMetadata | None = None


_TOOL_NAME = "tool_name"


def get_hook_event_metadata(tool_names: Sequence[str]) -> dict[str, HookEventMetadata]:
    """`getHookEventMetadata`: every event, in upstream's order."""
    tools = MatcherMetadata(_TOOL_NAME, tuple(tool_names))
    return {
        "PreToolUse": HookEventMetadata(
            "Before tool execution",
            "Input to command is JSON of tool call arguments.\nExit code 0 - stdout/stderr not shown\n"
            "Exit code 2 - show stderr to model and block tool call\n"
            "Other exit codes - show stderr to user only but continue with tool call",
            tools),
        "PostToolUse": HookEventMetadata(
            "After tool execution",
            'Input to command is JSON with fields "inputs" (tool call arguments) and "response" (tool call '
            "response).\nExit code 0 - stdout shown in transcript mode (ctrl+o)\n"
            "Exit code 2 - show stderr to model immediately\nOther exit codes - show stderr to user only",
            tools),
        "PostToolUseFailure": HookEventMetadata(
            "After tool execution fails",
            "Input to command is JSON with tool_name, tool_input, tool_use_id, error, error_type, is_interrupt, "
            "and is_timeout.\nExit code 0 - stdout shown in transcript mode (ctrl+o)\n"
            "Exit code 2 - show stderr to model immediately\nOther exit codes - show stderr to user only",
            tools),
        "PermissionDenied": HookEventMetadata(
            "After auto mode classifier denies a tool call",
            "Input to command is JSON with tool_name, tool_input, tool_use_id, and reason.\n"
            'Return {"hookSpecificOutput":{"hookEventName":"PermissionDenied","retry":true}} to tell the model it '
            "may retry.\nExit code 0 - stdout shown in transcript mode (ctrl+o)\n"
            "Other exit codes - show stderr to user only",
            tools),
        "Notification": HookEventMetadata(
            "When notifications are sent",
            "Input to command is JSON with notification message and type.\nExit code 0 - stdout/stderr not shown\n"
            "Other exit codes - show stderr to user only",
            MatcherMetadata("notification_type", (
                "permission_prompt", "idle_prompt", "auth_success", "elicitation_dialog", "elicitation_complete",
                "elicitation_response"))),
        "UserPromptSubmit": HookEventMetadata(
            "When the user submits a prompt",
            "Input to command is JSON with original user prompt text.\nExit code 0 - stdout shown to TDDAgents\n"
            "Exit code 2 - block processing, erase original prompt, and show stderr to user only\n"
            "Other exit codes - show stderr to user only"),
        "SessionStart": HookEventMetadata(
            "When a new session is started",
            "Input to command is JSON with session start source.\nExit code 0 - stdout shown to TDDAgents\n"
            "Blocking errors are ignored\nOther exit codes - show stderr to user only",
            MatcherMetadata("source", ("startup", "resume", "clear", "compact"))),
        "Stop": HookEventMetadata(
            "Right before TDDAgents concludes its response",
            "Exit code 0 - stdout/stderr not shown\nExit code 2 - show stderr to model and continue conversation\n"
            "Other exit codes - show stderr to user only"),
        "StopFailure": HookEventMetadata(
            "When the turn ends due to an API error",
            "Fires instead of Stop when an API error (rate limit, auth failure, etc.) ended the turn. "
            "Fire-and-forget — hook output and exit codes are ignored.",
            MatcherMetadata("error", (
                "rate_limit", "authentication_failed", "billing_error", "invalid_request", "server_error",
                "max_output_tokens", "unknown"))),
        "SubagentStart": HookEventMetadata(
            "When a subagent (Agent tool call) is started",
            "Input to command is JSON with agent_id and agent_type.\nExit code 0 - stdout shown to subagent\n"
            "Blocking errors are ignored\nOther exit codes - show stderr to user only",
            MatcherMetadata("agent_type", ())),
        "SubagentStop": HookEventMetadata(
            "Right before a subagent (Agent tool call) concludes its response",
            "Input to command is JSON with agent_id, agent_type, and agent_transcript_path.\n"
            "Exit code 0 - stdout/stderr not shown\n"
            "Exit code 2 - show stderr to subagent and continue having it run\n"
            "Other exit codes - show stderr to user only",
            MatcherMetadata("agent_type", ())),
        "PreCompact": HookEventMetadata(
            "Before conversation compaction",
            "Input to command is JSON with compaction details.\n"
            "Exit code 0 - stdout appended as custom compact instructions\nExit code 2 - block compaction\n"
            "Other exit codes - show stderr to user only but continue with compaction",
            MatcherMetadata("trigger", ("manual", "auto"))),
        "PostCompact": HookEventMetadata(
            "After conversation compaction",
            "Input to command is JSON with compaction details and the summary.\nExit code 0 - stdout shown to user\n"
            "Other exit codes - show stderr to user only",
            MatcherMetadata("trigger", ("manual", "auto"))),
        "SessionEnd": HookEventMetadata(
            "When a session is ending",
            "Input to command is JSON with session end reason.\nExit code 0 - command completes successfully\n"
            "Other exit codes - show stderr to user only",
            MatcherMetadata("reason", ("clear", "logout", "prompt_input_exit", "other"))),
        "PermissionRequest": HookEventMetadata(
            "When a permission dialog is displayed",
            "Input to command is JSON with tool_name, tool_input, and tool_use_id.\n"
            "Output JSON with hookSpecificOutput containing decision to allow or deny.\n"
            "Exit code 0 - use hook decision if provided\nOther exit codes - show stderr to user only",
            tools),
        "Setup": HookEventMetadata(
            "Repo setup hooks for init and maintenance",
            "Input to command is JSON with trigger (init or maintenance).\nExit code 0 - stdout shown to TDDAgents\n"
            "Blocking errors are ignored\nOther exit codes - show stderr to user only",
            MatcherMetadata("trigger", ("init", "maintenance"))),
        "TeammateIdle": HookEventMetadata(
            "When a teammate is about to go idle",
            "Input to command is JSON with teammate_name and team_name.\nExit code 0 - stdout/stderr not shown\n"
            "Exit code 2 - show stderr to teammate and prevent idle (teammate continues working)\n"
            "Other exit codes - show stderr to user only"),
        "TaskCreated": HookEventMetadata(
            "When a task is being created",
            "Input to command is JSON with task_id, task_subject, task_description, teammate_name, and team_name.\n"
            "Exit code 0 - stdout/stderr not shown\nExit code 2 - show stderr to model and prevent task creation\n"
            "Other exit codes - show stderr to user only"),
        "TaskCompleted": HookEventMetadata(
            "When a task is being marked as completed",
            "Input to command is JSON with task_id, task_subject, task_description, teammate_name, and team_name.\n"
            "Exit code 0 - stdout/stderr not shown\nExit code 2 - show stderr to model and prevent task completion\n"
            "Other exit codes - show stderr to user only"),
        "Elicitation": HookEventMetadata(
            "When an MCP server requests user input (elicitation)",
            "Input to command is JSON with mcp_server_name, message, and requested_schema.\n"
            "Output JSON with hookSpecificOutput containing action (accept/decline/cancel) and optional content.\n"
            "Exit code 0 - use hook response if provided\nExit code 2 - deny the elicitation\n"
            "Other exit codes - show stderr to user only",
            MatcherMetadata("mcp_server_name", ())),
        "ElicitationResult": HookEventMetadata(
            "After a user responds to an MCP elicitation",
            "Input to command is JSON with mcp_server_name, action, content, mode, and elicitation_id.\n"
            "Output JSON with hookSpecificOutput containing optional action and content to override the response.\n"
            "Exit code 0 - use hook response if provided\nExit code 2 - block the response (action becomes decline)\n"
            "Other exit codes - show stderr to user only",
            MatcherMetadata("mcp_server_name", ())),
        "ConfigChange": HookEventMetadata(
            "When configuration files change during a session",
            "Input to command is JSON with source (user_settings, project_settings, local_settings, policy_settings, "
            "skills) and file_path.\nExit code 0 - allow the change\n"
            "Exit code 2 - block the change from being applied to the session\n"
            "Other exit codes - show stderr to user only",
            MatcherMetadata("source", (
                "user_settings", "project_settings", "local_settings", "policy_settings", "skills"))),
        "InstructionsLoaded": HookEventMetadata(
            "When an instruction file (TDDAGENTS.md or rule) is loaded",
            "Input to command is JSON with file_path, memory_type (User, Project, Local, Managed), load_reason "
            "(session_start, nested_traversal, path_glob_match, include, compact), globs (optional — the paths: "
            "frontmatter patterns that matched), trigger_file_path (optional — the file TDDAgents touched that "
            "caused the load), and parent_file_path (optional — the file that @-included this one).\n"
            "Exit code 0 - command completes successfully\nOther exit codes - show stderr to user only\n"
            "This hook is observability-only and does not support blocking.",
            MatcherMetadata("load_reason", (
                "session_start", "nested_traversal", "path_glob_match", "include", "compact"))),
        "WorktreeCreate": HookEventMetadata(
            "Create an isolated worktree for VCS-agnostic isolation",
            "Input to command is JSON with name (suggested worktree slug).\n"
            "Stdout should contain the absolute path to the created worktree directory.\n"
            "Exit code 0 - worktree created successfully\nOther exit codes - worktree creation failed"),
        "WorktreeRemove": HookEventMetadata(
            "Remove a previously created worktree",
            "Input to command is JSON with worktree_path (absolute path to worktree).\n"
            "Exit code 0 - worktree removed successfully\nOther exit codes - show stderr to user only"),
        "CwdChanged": HookEventMetadata(
            "After the working directory changes",
            "Input to command is JSON with old_cwd and new_cwd.\n"
            "TDDAGENTS_ENV_FILE is set — write bash exports there to apply env to subsequent BashTool commands.\n"
            "Hook output can include hookSpecificOutput.watchPaths (array of absolute paths) to register with the "
            "FileChanged watcher.\nExit code 0 - command completes successfully\n"
            "Other exit codes - show stderr to user only"),
        "FileChanged": HookEventMetadata(
            "When a watched file changes",
            "Input to command is JSON with file_path and event (change, add, unlink).\n"
            "TDDAGENTS_ENV_FILE is set — write bash exports there to apply env to subsequent BashTool commands.\n"
            'The matcher field specifies filenames to watch in the current directory (e.g. ".envrc|.env").\n'
            "Hook output can include hookSpecificOutput.watchPaths (array of absolute paths) to dynamically update "
            "the watch list.\nExit code 0 - command completes successfully\n"
            "Other exit codes - show stderr to user only"),
    }


#: The hooks of each event, by matcher (`''` for an event without matchers, and for "all").
HooksByEventAndMatcher = dict[str, dict[str, list[IndividualHookConfig]]]


def group_hooks_by_event_and_matcher(
    locations: SettingsLocations,
    tool_names: Sequence[str],
    session_matchers: Mapping[str, Sequence[HookMatcher]] | None = None,
    registered_matchers: Mapping[str, Sequence[HookMatcher]] | None = None,
) -> HooksByEventAndMatcher:
    """`groupHooksByEventAndMatcher`: settings and session hooks, then the plugins' registered ones."""
    metadata = get_hook_event_metadata(tool_names)
    grouped: HooksByEventAndMatcher = {event: {} for event in metadata}
    for hook in get_all_hooks(locations, session_matchers):
        event_group = grouped.get(hook.event)
        if event_group is None:
            continue
        key = (hook.matcher or "") if metadata[hook.event].matcher_metadata is not None else ""
        event_group.setdefault(key, []).append(hook)
    for event, matchers in (registered_matchers or {}).items():
        event_group = grouped.get(event)
        if event_group is None:
            continue
        for matcher in matchers:
            if matcher.plugin_root is None:
                continue  # an SDK host's callbacks: shown to upstream's internal users only
            hooks = event_group.setdefault(matcher.matcher or "", [])
            hooks.extend(IndividualHookConfig(event=event, config=hook, matcher=matcher.matcher, source=PLUGIN_HOOK,
                                              plugin_name=matcher.plugin_id)
                         for hook in matcher.hooks)
    return grouped


def get_sorted_matchers_for_event(hooks_by_event_and_matcher: HooksByEventAndMatcher, event: str) -> list[str]:
    """`getSortedMatchersForEvent`."""
    matchers = list(hooks_by_event_and_matcher.get(event) or {})
    return sort_matchers_by_priority(matchers, hooks_by_event_and_matcher, event)


def get_hooks_for_matcher(
    hooks_by_event_and_matcher: HooksByEventAndMatcher, event: str, matcher: str | None,
) -> list[IndividualHookConfig]:
    """`getHooksForMatcher`: an event without matchers keeps its hooks under `''`."""
    return list((hooks_by_event_and_matcher.get(event) or {}).get(matcher if matcher is not None else "") or [])


def get_matcher_metadata(event: str, tool_names: Sequence[str]) -> MatcherMetadata | None:
    """`getMatcherMetadata`."""
    return get_hook_event_metadata(tool_names)[event].matcher_metadata


__all__ = [
    "HookEventMetadata",
    "HooksByEventAndMatcher",
    "MatcherMetadata",
    "get_hook_event_metadata",
    "get_hooks_for_matcher",
    "get_matcher_metadata",
    "get_sorted_matchers_for_event",
    "group_hooks_by_event_and_matcher",
]
