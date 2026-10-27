"""
`getAttachments` / `getAttachmentMessages`: the per-turn context the loop persists.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getAttachments`, `maybe`,
`getAttachmentMessages`.

Each source runs through `maybe`: a source that fails is logged and contributes nothing, so
one broken source never costs the turn its other attachments. The order is upstream's —
user-input sources (only when there is input), then the all-thread sources, then the
main-thread ones — with TDDAgents' `tdd_state` last among the all-thread sources.
`TDDAGENTS_DISABLE_ATTACHMENTS` / `TDDAGENTS_SIMPLE` (upstream's `CLAUDE_CODE_*`) leave only
the queued commands.

Sources not ported, each because this build has no subsystem for it: `at_mentioned_files`,
`mcp_resources`, `agent_mentions`, `skill_discovery`, `dynamic_skill` (no skill discovery or
nested skill dirs), `deferred_tools_delta` (no deferred tools), `ultrathink_effort` (no reasoning-effort
control), `companion_intro`, `plan_mode`/`plan_mode_exit` (no plan mode in a headless run),
`auto_mode_exit` (no mode transition sets its flag), `teammate_mailbox`, `team_context`,
`critical_system_reminder` (no context field carries one),
`compaction_reminder` and `context_efficiency` (feature-gated off), `ide_selection`,
`ide_opened_file`, `output_style`, `diagnostics`, `lsp_diagnostics`, `unified_tasks`,
`budget_usd` (no budget or cost tracker until Phase 6), `output_token_usage`
(`TOKEN_BUDGET`, off), `verify_plan_reminder` (ant-only). The upstream 1 s computation
timeout is not ported: every source here is local and bounded.

What an attachment is, and the messages the model sees for it.

Ported from:
- `reference/claude-code/src/utils/attachments.ts` -> the `Attachment` union, `createAttachmentMessage`
- `reference/claude-code/src/utils/messages.ts` -> `normalizeAttachmentForAPI` (the cases for
  the sources this build ports), `wrapCommandText`, `wrapMessagesInSystemReminder`,
  `getAutoModeInstructions`

An attachment is a typed record of per-turn context. It is persisted into the conversation as
one or more meta user messages, each wrapped in `<system-reminder>` and carrying the
attachment itself under `additional_kwargs["attachment"]` (the key `app/utils/messages.py`
uses), so later turns — the cadence counters, delta diffs, the TDD-state dedup — can read what
was already said from the history itself, exactly as upstream scans `type: 'attachment'`
messages.

The `agent_pending_messages` source (Phase 8).

Ported from `reference/claude-code/src/utils/attachments.ts` ->
`getAgentPendingMessageAttachments`: a background worker drains the messages `SendMessage`
queued on its task (`queuePendingMessage`) at its next tool round, each becoming a meta
`queued_command` from the coordinator. The main thread has no agent id and drains nothing.

The `changed_files` source: files the agent read that changed under it.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getChangedFiles` and
`src/tools/FileEditTool/utils.ts` -> `getSnippetForTwoFileDiff` (8 lines of context,
`DIFF_SNIPPET_MAX_BYTES`).

For every file in `read_file_state` that was read whole: if it is gone, it is forgotten; if it
differs from what was read, the new content is recorded (upstream re-reads it through
`FileReadTool.call`, which updates the cache) and an `edited_text_file` attachment carries
the changed hunks with line numbers. *Divergence:* the `Workspace` protocol exposes no mtime
(see `utils/file_state_cache.py`), so "changed" is decided by content, the fallback upstream's own
read-before-write check uses. A Read-denied path is skipped, as upstream skips it.

The `date_change` source.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getDateChangeAttachments` and
`src/constants/common.ts` -> `getLocalISODate`. Upstream keeps the last emitted date in
session state (`getLastEmittedDate`); a loop run is a session here, so it is kept per
`run_id`. The first sight of a date only records it — the run's opening context already
states today's date.

The `agent_listing_delta` and `mcp_instructions_delta` sources.

Ported from `reference/claude-code/src/utils/attachments.ts` ->
`getAgentListingDeltaAttachment`, `getMcpInstructionsDeltaAttachment`;
`src/utils/mcpInstructionsDelta.ts` -> `getMcpInstructionsDelta`,
`isMcpInstructionsDeltaEnabled`; `src/tools/AgentTool/prompt.ts` -> `formatAgentLine`,
`getToolsDescription`, `shouldInjectAgentListInMessages`.

Both diff what the conversation already announced (by scanning its attachments) against what
is available now, and say only what changed. **Both are off unless their env gate is set**, as
upstream's external build has them (`tengu_agent_list_attach` / `tengu_basalt_3kr` default
false): `TDDAGENTS_AGENT_LIST_IN_MESSAGES`, `TDDAGENTS_MCP_INSTR_DELTA` (upstream's
`CLAUDE_CODE_*`, renamed). The agent roster and MCP clients are read from the context
(`agent_definitions`, `mcp_clients`); a context without them announces nothing.

Not ported: the MCP-requirement and permission-deny filters on the roster (the Agent tool that
enforces them is Phase 7), and the client-side Chrome instruction (no such client).

The `nested_memory` source: instructions that apply to a file the agent just read.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getNestedMemoryAttachments`,
`getNestedMemoryAttachmentsForFile`, `getDirectoriesToProcess`, `memoryFilesToAttachments`, and
`src/utils/claudemd.ts` -> `getManagedAndUserConditionalRules`,
`getMemoryFilesForNestedDirectory`, `getConditionalRulesForCwdLevelDirectory`,
`processConditionedMdRules`.

When `ReadFile` reads a file (upstream adds it to `nestedMemoryAttachmentTriggers`), the next
attachment pass attaches, for that file: managed and user conditional rules whose `paths:`
match it; then, for each directory between the workspace root and the file, that directory's
`TDDAGENTS.md`, `.tddagents/TDDAGENTS.md`, `TDDAGENTS.local.md` and `.tddagents/rules/*.md`
(unconditional, then conditional ones that match); then the root's conditional rules. A file
already attached this run (`loadedNestedMemoryPaths`) or already read is not attached again,
and an attached one is recorded in `read_file_state` as if read.

Paths are workspace-relative: the workspace root is upstream's original cwd, so the
"cwd-level" directories are the root alone (memory above the workspace is host memory — user
and managed — which `get_memory_files` already loads).

The `queued_commands` and `async_hook_responses` sources.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getQueuedCommandAttachments`,
`getAsyncHookResponseAttachments`. Queued commands live in the run's `CommandQueue`
(`ToolContext.command_queue`), fed by background tasks and `asyncRewake` hooks; finished
background hooks' responses live in the run's `AsyncHookRegistry`. Task notifications are
`later` priority, so they are delivered as a turn of their own by the runner, not here.

The `todo_reminders` and `auto_mode` sources.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `TODO_REMINDER_CONFIG`,
`getTodoReminderTurnCounts`, `getTodoReminderAttachments`, `AUTO_MODE_ATTACHMENT_CONFIG`,
`getAutoModeAttachmentTurnCount`, `countAutoModeAttachmentsSinceLastExit`,
`getAutoModeAttachments`.

* **todo** — when the `TodoWrite` tool is in the pool and neither a `TodoWrite` call nor a
  reminder happened in the last 10 assistant turns (thinking-only messages do not count), the
  current list is attached with a gentle reminder.
* **auto mode** — while the mode is `auto`: a reminder on the first turn, then again after 5
  human turns; every 5th reminder since the last exit is the full one, the rest are sparse.

Not ported: `task_reminder` (Todo v2 tools are absent), the Brief-tool exemption (no Brief
tool), `plan` mode with auto active, and `auto_mode_exit` — its trigger is upstream's
`transitionPermissionMode` flag, and no mode transition in this build sets one.

The `skill_listing` source (Phase 8).

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getSkillListingAttachments`
(`sentSkillNames`). Each agent is told about each skill once: the first listing carries every
skill the model may invoke, later ones only skills that appeared since (a conditional skill
activated by a file path). Nothing is listed unless the context's pool holds the Skill tool.

The registry comes from the context (`skill_registry`), and its `sent_skill_names` is upstream's
module-scope map made per run. The budget follows the run's model window
(`getContextWindowForModel(mainLoopModel)`), here `SkillRegistry.model`; unset means the
default budget, which is what an unknown model's 200k window gives anyway.

Not ported: MCP skill commands (`getMcpSkillCommands`), the skill-search filter
(`EXPERIMENTAL_SKILL_SEARCH`), `suppressNextSkillListing` (resume wiring is Phase 9), and the
`NODE_ENV === 'test'` short-circuit.

The `token_usage` source.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getTokenUsageAttachment` and
`src/utils/tokens.ts` -> `getTokenUsage`, `getTokenCountFromUsage`,
`tokenCountFromLastAPIResponse`. Off unless `TDDAGENTS_ENABLE_TOKEN_USAGE_ATTACHMENT` is set
(upstream's `CLAUDE_CODE_ENABLE_TOKEN_USAGE_ATTACHMENT`), and main-thread only.

The usage is the provider's own count on the last assistant message (LangChain's
`usage_metadata`: input, output, and the cache read/creation details upstream adds in).
"""

from __future__ import annotations

import datetime
import difflib
import inspect
import logging
import os
import posixpath
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pathspec
from langchain_core.messages import HumanMessage

from app.tasks.local_agent_task.local_agent_task import drain_pending_messages
from app.tools.skill_tool.prompt import SKILL_TOOL_NAME, format_commands_within_budget
from app.types.permissions import PermissionMode
from app.utils.claudemd import (
    CONFIG_DIR_NAME,
    LOCAL_MEMORY_FILE_NAME,
    MANAGED_DIR,
    MEMORY_FILE_NAME,
    HostSource,
    MemoryFileInfo,
    MemorySource,
    WorkspaceSource,
    process_md_rules,
    process_memory_file,
)
from app.utils.file_state_cache import FileState
from app.utils.message_queue_manager import QueuedCommand, is_slash_command
from app.utils.messages import (
    ATTACHMENT_KEY,
    Message,
    tool_calls_in,
    wrap_in_system_reminder,
)
from app.utils.tokens import get_context_window_for_model
from app.workspace.base import WorkspaceError, WorkspaceNotFound, normalize_path

logger = logging.getLogger("TDDOrchestrator.Attachments")

#: A source: `(messages, context)` -> its attachments, or an awaitable of them.
Source = Callable[[Sequence[Message], Any], Any]

_TRUTHY = ("1", "true", "yes", "on")


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


async def maybe(label: str, source: Source, messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`maybe`: a source's attachments, or none if it fails."""
    try:
        result = source(messages, context)
        if inspect.isawaitable(result):
            result = await result
        return list(result)
    except Exception:
        logger.exception("Attachment error in %s", label)
        return []


async def get_attachments(
    context: Any, messages: Sequence[Message], *, user_input: str | None = None
) -> list[Attachment]:
    """`getAttachments`. `user_input` is the prompt being submitted; between turns it is `None`."""
    # Imported here: TDDAgents' own source builds its attachment from this module's types.
    from app.tdd.tdd_state_attachment import get_tdd_state_attachments

    if _env_truthy("TDDAGENTS_DISABLE_ATTACHMENTS") or _env_truthy("TDDAGENTS_SIMPLE"):
        return await maybe("queued_commands", get_queued_command_attachments, messages, context)
    is_main_thread = not getattr(context, "agent_id", None)
    all_thread: list[tuple[str, Source]] = [
        ("queued_commands", get_queued_command_attachments),
        ("date_change", get_date_change_attachments),
        ("agent_listing_delta", get_agent_listing_delta_attachment),
        ("mcp_instructions_delta", get_mcp_instructions_delta_attachment),
        ("changed_files", get_changed_files),
        ("nested_memory", get_nested_memory_source),
        ("skill_listing", get_skill_listing_attachments),
        ("auto_mode", get_auto_mode_attachments),
        ("todo_reminders", get_todo_reminder_attachments),
        ("agent_pending_messages", get_agent_pending_message_attachments),
        ("tdd_state", get_tdd_state_attachments),
    ]
    main_thread: list[tuple[str, Source]] = (
        [
            ("async_hook_responses", get_async_hook_response_attachments),
            ("token_usage", get_token_usage_attachment),
        ]
        if is_main_thread
        else []
    )
    out: list[Attachment] = []
    for label, source in [*all_thread, *main_thread]:
        out.extend(await maybe(label, source, messages, context))
    return out


async def get_attachment_messages(
    context: Any, messages: Sequence[Message], *, user_input: str | None = None
) -> list[Message]:
    """`getAttachmentMessages`: every attachment as its meta messages, in order."""
    out: list[Message] = []
    for attachment in await get_attachments(context, messages, user_input=user_input):
        out.extend(create_attachment_messages(attachment))
    return out


@dataclass(frozen=True, slots=True)
class Attachment:
    """One attachment: its `type` and the fields upstream's union member carries."""

    type: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, **dict(self.data)}


#: `getAutoModeFullInstructions`.
AUTO_MODE_FULL_INSTRUCTIONS = """## Auto Mode Active

Auto mode is active. The user chose continuous, autonomous execution. You should:

1. **Execute immediately** — Start implementing right away. Make reasonable assumptions and proceed on low-risk work.
2. **Minimize interruptions** — Prefer making reasonable assumptions over asking questions for routine decisions.
3. **Prefer action over planning** — Do not enter plan mode unless the user explicitly asks. When in doubt, start coding.
4. **Expect course corrections** — The user may provide suggestions or course corrections at any point; treat those as normal input.
5. **Do not take overly destructive actions** — Auto mode is not a license to destroy. Anything that deletes data or modifies shared or production systems still needs explicit user confirmation. If you reach such a decision point, ask and wait, or course correct to a safer method instead.
6. **Avoid data exfiltration** — Post even routine messages to chat platforms or work tickets only if the user has directed you to. You must not share secrets (e.g. credentials, internal documentation) unless the user has explicitly authorized both that specific secret and its destination."""  # noqa: E501

#: `getAutoModeSparseInstructions`.
AUTO_MODE_SPARSE_INSTRUCTIONS = (
    "Auto mode still active (see full instructions earlier in conversation). Execute autonomously, "
    "minimize interruptions, prefer action over planning."
)

TODO_REMINDER_TEXT = (
    "The TodoWrite tool hasn't been used recently. If you're working on tasks that would benefit from "
    "tracking progress, consider using the TodoWrite tool to track progress. Also consider cleaning up the "
    "todo list if has become stale and no longer matches what you are working on. Only use it if it's "
    "relevant to the current work. This is just a gentle reminder - ignore if not applicable. Make sure "
    "that you NEVER mention this reminder to the user\n"
)


def wrap_command_text(raw: str, origin_kind: str | None) -> str:
    """`wrapCommandText`, for the origins a queued command can carry here."""
    if origin_kind == "task-notification":
        return f"A background agent completed a task:\n{raw}"
    if origin_kind == "coordinator":
        return (
            f"The coordinator sent a message while you were working:\n{raw}\n\n"
            "Address this before completing your current task."
        )
    return (
        f"The user sent a new message while you were working:\n{raw}\n\n"
        "IMPORTANT: After completing your current task, you MUST address the user's message above. "
        "Do not ignore it."
    )


def _agent_listing(data: Mapping[str, Any]) -> list[str]:
    parts: list[str] = []
    added = list(data.get("addedLines") or [])
    removed = list(data.get("removedTypes") or [])
    if added:
        header = (
            "Available agent types for the Agent tool:"
            if data.get("isInitial")
            else "New agent types are now available for the Agent tool:"
        )
        parts.append(header + "\n" + "\n".join(added))
    if removed:
        parts.append("The following agent types are no longer available:\n" + "\n".join(f"- {t}" for t in removed))
    if data.get("isInitial") and data.get("showConcurrencyNote"):
        parts.append(
            "Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a "
            "single message with multiple tool uses."
        )
    return ["\n\n".join(parts)]


def _mcp_instructions(data: Mapping[str, Any]) -> list[str]:
    parts: list[str] = []
    blocks = list(data.get("addedBlocks") or [])
    removed = list(data.get("removedNames") or [])
    if blocks:
        parts.append(
            "# MCP Server Instructions\n\nThe following MCP servers have provided instructions for how to use "
            "their tools and resources:\n\n" + "\n\n".join(blocks)
        )
    if removed:
        parts.append(
            "The following MCP servers have disconnected. Their instructions above no longer apply:\n"
            + "\n".join(removed)
        )
    return ["\n\n".join(parts)]


def _todo_reminder(data: Mapping[str, Any]) -> list[str]:
    items = list(data.get("content") or [])
    message = TODO_REMINDER_TEXT
    if items:
        rendered = "\n".join(f"{i + 1}. [{item.get('status')}] {item.get('content')}" for i, item in enumerate(items))
        message += f"\n\nHere are the existing contents of your todo list:\n\n[{rendered}]"
    return [message]


def _async_hook_response(data: Mapping[str, Any]) -> list[str]:
    response = data.get("response") or {}
    texts: list[str] = []
    system_message = response.get("systemMessage")
    if isinstance(system_message, str) and system_message:
        texts.append(system_message)
    specific = response.get("hookSpecificOutput")
    if isinstance(specific, Mapping):
        context = specific.get("additionalContext")
        if isinstance(context, str) and context:
            texts.append(context)
    return texts


#: The TDD-state attachment's heading (TDDAgents' own source).
TDD_STATE_HEADER = "TDD phase ledger (written only by RunTests from observed test runs):"


def tdd_state_lines(data: Mapping[str, Any]) -> list[str]:
    """The ledger part of a TDD-state attachment."""
    return [
        TDD_STATE_HEADER,
        f"Current Phase: {data.get('phase')}",
        f"Red Confirmed: {data.get('red_confirmed')}",
        f"Green Passed: {data.get('green_passed')}",
    ]


def _tdd_state(data: Mapping[str, Any]) -> list[str]:
    return ["\n".join(tdd_state_lines(data))]


def normalize_attachment_for_api(attachment: Attachment) -> list[str]:
    """
    `normalizeAttachmentForAPI`: the text of each message the model sees for `attachment`
    (before the `<system-reminder>` wrap). An unknown type is not shown.
    """
    data = attachment.data
    kind = attachment.type
    if kind == "edited_text_file":
        return [
            (
                f"Note: {data['filename']} was modified, either by the user or by a linter. This change was "
                "intentional, so make sure to take it into account as you proceed (ie. don't revert it unless the "
                "user asks you to). Don't tell the user this, since they are already aware. Here are the relevant "
                f"changes (shown with line numbers):\n{data['snippet']}"
            )
        ]
    if kind == "nested_memory":
        return [f"Contents of {data['path']}:\n\n{data['content']}"]
    if kind == "queued_command":
        # An explicit origin wins; task notifications predate origin and fall back to the mode.
        explicit = data.get("origin")
        origin = (
            str(explicit.get("kind")) if isinstance(explicit, Mapping)
            else "task-notification" if data.get("commandMode") == "task-notification" else None
        )
        return [wrap_command_text(str(data.get("prompt", "")), origin)]
    if kind == "date_change":
        return [
            (
                f"The date has changed. Today's date is now {data['newDate']}. DO NOT mention this to the user "
                "explicitly because they are already aware."
            )
        ]
    if kind == "todo_reminder":
        return _todo_reminder(data)
    if kind == "auto_mode":
        return [AUTO_MODE_SPARSE_INSTRUCTIONS if data.get("reminderType") == "sparse" else AUTO_MODE_FULL_INSTRUCTIONS]
    if kind == "agent_listing_delta":
        return _agent_listing(data)
    if kind == "mcp_instructions_delta":
        return _mcp_instructions(data)
    if kind == "token_usage":
        return [f"Token usage: {data['used']}/{data['total']}; {data['remaining']} remaining"]
    if kind == "async_hook_response":
        return _async_hook_response(data)
    if kind == "tdd_state":
        return _tdd_state(data)
    if kind == "skill_listing":
        content = data.get("content")
        return [f"The following skills are available for use with the Skill tool:\n\n{content}"] if content else []
    return []


def create_attachment_messages(attachment: Attachment) -> list[Message]:
    """`createAttachmentMessage` + `normalizeAttachmentForAPI`: the meta messages, wrapped."""
    record = attachment.as_dict()
    return [
        HumanMessage(content=wrap_in_system_reminder(text), additional_kwargs={"is_meta": True, ATTACHMENT_KEY: record})
        for text in normalize_attachment_for_api(attachment)
    ]


def attachment_of(message: Any) -> Mapping[str, Any] | None:
    """The attachment a message carries, or `None` (upstream: `message.type === 'attachment'`)."""
    kwargs = getattr(message, "additional_kwargs", None)
    if not isinstance(kwargs, Mapping):
        return None
    attachment = kwargs.get(ATTACHMENT_KEY)
    return attachment if isinstance(attachment, Mapping) else None


def get_agent_pending_message_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """
    `getAgentPendingMessageAttachments`. Upstream drains through `setAppStateForTasks ??
    setAppState`; a background worker's `set_app_state` here already lets task writes reach the
    root store (`tools/agent_tool/run_agent.py`), so it is that one setter.
    """
    agent_id = getattr(context, "agent_id", None)
    if not agent_id:
        return []
    drained = drain_pending_messages(agent_id, context.get_app_state, context.set_app_state)
    return [
        Attachment("queued_command", {"prompt": msg, "origin": {"kind": "coordinator"}, "isMeta": True})
        for msg in drained
    ]


DIFF_SNIPPET_MAX_BYTES = 8192
#: `structuredPatch(..., { context: 8 })` in `getSnippetForTwoFileDiff`.
SNIPPET_CONTEXT_LINES = 8


def _number(lines: list[str], start_line: int) -> str:
    """`addLineNumbers` with the compact `N\\t` prefix this build's ReadFile uses."""
    if not lines:
        return ""
    return "\n".join(f"{start_line + i}\t{line}" for i, line in enumerate(lines))


def get_snippet_for_two_file_diff(before: str, after: str) -> str:
    """`getSnippetForTwoFileDiff`: each hunk's surviving lines, numbered from the old start."""
    a = before.splitlines()
    b = after.splitlines()
    hunks: list[str] = []
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(SNIPPET_CONTEXT_LINES):
        start = group[0][1] + 1
        kept: list[str] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                kept.extend(a[i1:i2])
            elif tag in ("replace", "insert"):
                kept.extend(b[j1:j2])
        hunks.append(_number(kept, start))
    if not hunks:
        return ""
    full = "\n...\n".join(hunks)
    if len(full) <= DIFF_SNIPPET_MAX_BYTES:
        return full
    # `lastIndexOf('\n', DIFF_SNIPPET_MAX_BYTES + 1)` searches up to and including that index.
    cutoff = full.rfind("\n", 0, DIFF_SNIPPET_MAX_BYTES + 1)
    kept_text = full[:cutoff] if cutoff > 0 else full[:DIFF_SNIPPET_MAX_BYTES]
    remaining = full.count("\n", len(kept_text)) + 1
    return f"{kept_text}\n\n... [{remaining} lines truncated] ..."


def _read_denied(path: str, context: Any) -> bool:
    try:
        from app.utils.permissions.filesystem import (
            matching_rule_for_input,
            permission_cwd,
        )
        from app.utils.permissions.permissions import extract_permission_context

        perm = extract_permission_context(context)
        return matching_rule_for_input(path, perm, "read", "deny", permission_cwd(context)) is not None
    except Exception:  # noqa: BLE001 - a context without permissions denies nothing
        return False


def get_changed_files(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getChangedFiles`."""
    workspace = getattr(context, "workspace", None)
    state: dict[str, FileState] = getattr(context, "read_file_state", {})
    if workspace is None or not state:
        return []
    out: list[Attachment] = []
    for path, seen in list(state.items()):
        if seen.offset is not None or seen.limit is not None:
            continue
        if _read_denied(path, context):
            continue
        try:
            current = workspace.read_file(path)
        except WorkspaceNotFound:
            state.pop(path, None)
            continue
        except (WorkspaceError, OSError, UnicodeDecodeError):
            continue
        if current == seen.content:
            continue
        state[path] = FileState(content=current)
        snippet = get_snippet_for_two_file_diff(seen.content, current)
        if snippet == "":
            continue
        out.append(Attachment("edited_text_file", {"filename": path, "snippet": snippet}))
    return out


OVERRIDE_DATE_ENV = "TDDAGENTS_OVERRIDE_DATE"

_last_emitted: dict[str, str] = {}


def get_local_iso_date() -> str:
    """`getLocalISODate`: today in local time, `YYYY-MM-DD`, unless overridden."""
    override = os.environ.get(OVERRIDE_DATE_ENV)
    if override:
        return override
    return datetime.date.today().isoformat()


def reset_last_emitted_dates() -> None:
    """Forget every run's last emitted date (tests)."""
    _last_emitted.clear()


def get_date_change_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getDateChangeAttachments`."""
    key = str(getattr(context, "run_id", "") or "")
    current = get_local_iso_date()
    last = _last_emitted.get(key)
    if last is None:
        _last_emitted[key] = current
        return []
    if current == last:
        return []
    _last_emitted[key] = current
    return [Attachment("date_change", {"newDate": current})]


AGENT_TOOL_NAME = "Agent"
AGENT_LIST_ENV = "TDDAGENTS_AGENT_LIST_IN_MESSAGES"
MCP_INSTR_DELTA_ENV = "TDDAGENTS_MCP_INSTR_DELTA"


def get_tools_description(agent: Any) -> str:
    """`getToolsDescription`."""
    tools = list(getattr(agent, "tools", None) or ())
    denied = list(getattr(agent, "disallowed_tools", None) or ())
    if tools and denied:
        effective = [t for t in tools if t not in set(denied)]
        return ", ".join(effective) if effective else "None"
    if tools:
        return ", ".join(tools)
    if denied:
        return "All tools except " + ", ".join(denied)
    return "All tools"


def format_agent_line(agent: Any) -> str:
    """`formatAgentLine` (the definition's `description` is upstream's `whenToUse`)."""
    return f"- {agent.name}: {agent.description} (Tools: {get_tools_description(agent)})"


def agent_definitions_for(context: Any) -> Sequence[Any]:
    return tuple(getattr(context, "agent_definitions", ()) or ())


def mcp_clients_for(context: Any) -> Sequence[Any]:
    return tuple(getattr(context, "mcp_clients", ()) or ())


def _has_agent_tool(context: Any) -> bool:
    return any(
        getattr(t, "name", None) == AGENT_TOOL_NAME or AGENT_TOOL_NAME in getattr(t, "aliases", ())
        for t in getattr(context, "tools", ()) or ()
    )


def get_agent_listing_delta_attachment(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getAgentListingDeltaAttachment`."""
    if not _env_truthy(AGENT_LIST_ENV) or not _has_agent_tool(context):
        return []
    agents = list(agent_definitions_for(context))
    announced: set[str] = set()
    for message in messages:
        attachment = attachment_of(message)
        if not attachment or attachment.get("type") != "agent_listing_delta":
            continue
        announced.update(attachment.get("addedTypes") or ())
        announced.difference_update(attachment.get("removedTypes") or ())
    current = {a.name for a in agents}
    added = sorted((a for a in agents if a.name not in announced), key=lambda a: a.name)
    removed = sorted(t for t in announced if t not in current)
    if not added and not removed:
        return []
    return [Attachment("agent_listing_delta", {
        "addedTypes": [a.name for a in added],
        "addedLines": [format_agent_line(a) for a in added],
        "removedTypes": removed,
        "isInitial": len(announced) == 0,
        "showConcurrencyNote": True,
    })]


def _field(client: Any, name: str) -> Any:
    return client.get(name) if isinstance(client, Mapping) else getattr(client, name, None)


def is_mcp_instructions_delta_enabled() -> bool:
    """`isMcpInstructionsDeltaEnabled`: the env opt-in (GrowthBook's gate is off externally)."""
    return _env_truthy(MCP_INSTR_DELTA_ENV)


def get_mcp_instructions_delta_attachment(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getMcpInstructionsDeltaAttachment` / `getMcpInstructionsDelta`."""
    if not is_mcp_instructions_delta_enabled():
        return []
    announced: set[str] = set()
    for message in messages:
        attachment = attachment_of(message)
        if not attachment or attachment.get("type") != "mcp_instructions_delta":
            continue
        announced.update(attachment.get("addedNames") or ())
        announced.difference_update(attachment.get("removedNames") or ())
    connected = [c for c in mcp_clients_for(context) if _field(c, "type") == "connected"]
    connected_names = {str(_field(c, "name")) for c in connected}
    blocks = {
        str(_field(c, "name")): f"## {_field(c, 'name')}\n{_field(c, 'instructions')}"
        for c in connected
        if _field(c, "instructions")
    }
    added = sorted((name, block) for name, block in blocks.items() if name not in announced)
    removed = sorted(name for name in announced if name not in connected_names)
    if not added and not removed:
        return []
    return [Attachment("mcp_instructions_delta", {
        "addedNames": [name for name, _ in added],
        "addedBlocks": [block for _, block in added],
        "removedNames": removed,
    })]


RULES_SUBDIR = f"{CONFIG_DIR_NAME}/rules"


@dataclass
class NestedMemoryState:
    """`nestedMemoryAttachmentTriggers` and `loadedNestedMemoryPaths`, both mutated in place."""

    triggers: set[str] = field(default_factory=set)
    loaded_paths: set[str] = field(default_factory=set)


def get_directories_to_process(target_path: str) -> tuple[list[str], list[str]]:
    """`getDirectoriesToProcess`, workspace-relative: (nested dirs root-first, cwd-level dirs)."""
    parent = posixpath.dirname(target_path)
    nested: list[str] = []
    while parent not in ("", "."):
        nested.append(parent)
        parent = posixpath.dirname(parent)
    nested.reverse()
    return nested, [""]


def _matches(globs: tuple[str, ...] | None, target_path: str, base_dir: str) -> bool:
    if not globs:
        return False
    if base_dir:
        if not target_path.startswith(base_dir + "/"):
            return False
        relative = target_path[len(base_dir) + 1:]
    else:
        relative = target_path
    if not relative or relative.startswith(".."):
        return False
    return pathspec.GitIgnoreSpec.from_lines(globs).match_file(relative)


def process_conditioned_md_rules(
    source: MemorySource,
    target_path: str,
    rules_dir: str,
    type: Any,
    processed: set[str],
    include_external: bool,
    base_dir: str,
) -> list[MemoryFileInfo]:
    """`processConditionedMdRules`: conditional rules whose globs match the target."""
    rules = process_md_rules(source, rules_dir, type, processed, include_external, conditional_rule=True)
    return [rule for rule in rules if _matches(rule.globs, target_path, base_dir)]


def _join(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def _nested_directory_files(
    workspace: WorkspaceSource, directory: str, target_path: str, processed: set[str]
) -> list[MemoryFileInfo]:
    """`getMemoryFilesForNestedDirectory`."""
    result: list[MemoryFileInfo] = []
    result += process_memory_file(workspace, _join(directory, MEMORY_FILE_NAME), "Project", processed, False)
    result += process_memory_file(
        workspace, _join(directory, f"{CONFIG_DIR_NAME}/{MEMORY_FILE_NAME}"), "Project", processed, False
    )
    result += process_memory_file(workspace, _join(directory, LOCAL_MEMORY_FILE_NAME), "Local", processed, False)
    rules_dir = _join(directory, RULES_SUBDIR)
    unconditional_processed = set(processed)
    result += process_md_rules(workspace, rules_dir, "Project", unconditional_processed, False)
    result += process_conditioned_md_rules(workspace, target_path, rules_dir, "Project", processed, False, directory)
    processed |= unconditional_processed
    return result


def _to_attachments(files: list[MemoryFileInfo], context: Any, state: NestedMemoryState) -> list[Attachment]:
    """`memoryFilesToAttachments`."""
    out: list[Attachment] = []
    read_state: dict[str, FileState] = context.read_file_state
    for info in files:
        if info.path in state.loaded_paths or info.path in read_state:
            continue
        out.append(Attachment("nested_memory", {"path": info.path, "content": info.content, "displayPath": info.path}))
        state.loaded_paths.add(info.path)
        differs = info.content_differs_from_disk
        read_state[info.path] = FileState(
            content=(info.raw_content or info.content) if differs else info.content, is_partial_view=differs
        )
    return out


async def get_nested_memory_attachments(
    context: Any,
    state: NestedMemoryState,
    *,
    home: str | None = None,
    managed_dir: str = MANAGED_DIR,
) -> list[Attachment]:
    """`getNestedMemoryAttachments`: one pass over the pending triggers, which it clears."""
    workspace = getattr(context, "workspace", None)
    if not state.triggers:
        return []
    triggers = sorted(state.triggers)
    state.triggers.clear()
    if workspace is None:
        return []
    ws_source = WorkspaceSource(workspace)
    host = HostSource()
    user_rules = os.path.join(home if home is not None else os.path.expanduser("~"), CONFIG_DIR_NAME, "rules")
    managed_rules = os.path.join(managed_dir, CONFIG_DIR_NAME, "rules")
    out: list[Attachment] = []
    for raw in triggers:
        try:
            target = normalize_path(raw)
        except WorkspaceError:
            continue
        processed: set[str] = set()
        files = process_conditioned_md_rules(host, target, managed_rules, "Managed", processed, False, "")
        files += process_conditioned_md_rules(host, target, user_rules, "User", processed, True, "")
        out += _to_attachments(files, context, state)
        nested, cwd_level = get_directories_to_process(target)
        for directory in nested:
            out += _to_attachments(_nested_directory_files(ws_source, directory, target, processed), context, state)
        for directory in cwd_level:
            rules = process_conditioned_md_rules(
                ws_source, target, _join(directory, RULES_SUBDIR), "Project", processed, False, directory
            )
            out += _to_attachments(rules, context, state)
    return out


def nested_memory_state_of(context: Any) -> NestedMemoryState | None:
    """
    The context's `nested_memory_attachment_triggers` / `loaded_nested_memory_paths`, as a
    live view; `None` when the context carries no triggers (nothing has asked for them).
    """
    triggers = getattr(context, "nested_memory_attachment_triggers", None)
    if not isinstance(triggers, set):
        return None
    loaded = getattr(context, "loaded_nested_memory_paths", None)
    return NestedMemoryState(triggers=triggers, loaded_paths=loaded if isinstance(loaded, set) else set())


async def get_nested_memory_source(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """The pipeline's `nested_memory` entry."""
    state = nested_memory_state_of(context)
    if state is None:
        return []
    return await get_nested_memory_attachments(context, state)


INLINE_NOTIFICATION_MODES = frozenset({"prompt", "task-notification"})


def _registry(context: Any) -> Any:
    return getattr(getattr(context, "hook_dispatcher", None), "registry", None)


def get_queued_command_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """
    `getQueuedCommandAttachments` over `query.ts`'s snapshot: the commands at `next` priority or
    above (no Sleep tool here, so never `later`), minus slash commands, scoped by agent — the main
    thread takes what is addressed to no agent, a worker only its own task notifications. Only
    `INLINE_NOTIFICATION_MODES` become attachments, and only those are removed from the queue.
    """
    queue = getattr(context, "command_queue", None)
    if queue is None:
        return []
    agent_id = getattr(context, "agent_id", None)

    def addressed(command: QueuedCommand) -> bool:
        if is_slash_command(command):
            return False
        if agent_id is None:
            return command.agent_id is None
        return command.mode == "task-notification" and command.agent_id == agent_id

    consumed = [c for c in queue.get_commands_by_max_priority("next")
                if addressed(c) and c.mode in INLINE_NOTIFICATION_MODES]
    queue.remove(consumed)
    return [
        Attachment("queued_command", {"prompt": c.value, "source_uuid": c.uuid, "commandMode": c.mode})
        for c in consumed
    ]


async def get_async_hook_response_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getAsyncHookResponseAttachments`."""
    registry = _registry(context)
    if registry is None:
        return []
    out: list[Attachment] = []
    for response in await registry.check_for_responses():
        record = response.attachment()
        record.pop("type", None)
        out.append(Attachment("async_hook_response", record))
    return out


TODO_REMINDER_CONFIG = SimpleNamespace(TURNS_SINCE_WRITE=10, TURNS_BETWEEN_REMINDERS=10)
#: `AUTO_MODE_ATTACHMENT_CONFIG`.
AUTO_MODE_ATTACHMENT_CONFIG = SimpleNamespace(TURNS_BETWEEN_ATTACHMENTS=5, FULL_REMINDER_EVERY_N_ATTACHMENTS=5)

TODO_WRITE_TOOL_NAME = "TodoWrite"


def _is_assistant(message: Any) -> bool:
    return getattr(message, "type", None) == "ai"


def is_thinking_message(message: Any) -> bool:
    """`isThinkingMessage`: an assistant message whose content is only thinking blocks."""
    content = getattr(message, "content", None)
    if not isinstance(content, list) or not content:
        return False
    return all(
        isinstance(block, Mapping) and block.get("type") in ("thinking", "redacted_thinking") for block in content
    )


def get_todo_reminder_turn_counts(messages: Sequence[Message]) -> tuple[int, int]:
    """`getTodoReminderTurnCounts`: assistant turns since the last TodoWrite / reminder."""
    last_write = -1
    last_reminder = -1
    since_write = 0
    since_reminder = 0
    for i in range(len(messages) - 1, -1, -1):
        message = messages[i]
        if _is_assistant(message):
            if is_thinking_message(message):
                continue
            if last_write == -1 and any(call.get("name") == TODO_WRITE_TOOL_NAME for call in tool_calls_in(message)):
                last_write = i
            if last_write == -1:
                since_write += 1
            if last_reminder == -1:
                since_reminder += 1
        elif last_reminder == -1 and (attachment_of(message) or {}).get("type") == "todo_reminder":
            last_reminder = i
        if last_write != -1 and last_reminder != -1:
            break
    return since_write, since_reminder


def _has_tool(context: Any, name: str) -> bool:
    for tool in getattr(context, "tools", ()) or ():
        if getattr(tool, "name", None) == name or name in getattr(tool, "aliases", ()):
            return True
    return False


def get_todo_reminder_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getTodoReminderAttachments`."""
    if not _has_tool(context, TODO_WRITE_TOOL_NAME) or not messages:
        return []
    since_write, since_reminder = get_todo_reminder_turn_counts(messages)
    if (
        since_write < TODO_REMINDER_CONFIG.TURNS_SINCE_WRITE
        or since_reminder < TODO_REMINDER_CONFIG.TURNS_BETWEEN_REMINDERS
    ):
        return []
    from app.tools.todo_write_tool.todo_write_tool import read_todos

    todos = [item.to_dict() for item in read_todos(context)]
    return [Attachment("todo_reminder", {"content": todos, "itemCount": len(todos)})]


def _is_human_turn(message: Any) -> bool:
    if getattr(message, "type", None) != "human":
        return False
    kwargs = getattr(message, "additional_kwargs", None) or {}
    return not kwargs.get("is_meta")


def get_auto_mode_attachment_turn_count(messages: Sequence[Message]) -> tuple[int, bool]:
    """`getAutoModeAttachmentTurnCount`: human turns since the last reminder, and whether one exists."""
    turns = 0
    for i in range(len(messages) - 1, -1, -1):
        message = messages[i]
        kind = (attachment_of(message) or {}).get("type")
        if kind == "auto_mode":
            return turns, True
        if kind == "auto_mode_exit":
            return turns, False
        if _is_human_turn(message):
            turns += 1
    return turns, False


def count_auto_mode_attachments_since_last_exit(messages: Sequence[Message]) -> int:
    """`countAutoModeAttachmentsSinceLastExit`."""
    count = 0
    for i in range(len(messages) - 1, -1, -1):
        kind = (attachment_of(messages[i]) or {}).get("type")
        if kind == "auto_mode_exit":
            break
        if kind == "auto_mode":
            count += 1
    return count


def _mode(context: Any) -> Any:
    try:
        return context.get_app_state().tool_permission_context.mode
    except Exception:  # noqa: BLE001 - a context without permissions is not in auto mode
        return None


def get_auto_mode_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getAutoModeAttachments`."""
    if _mode(context) != PermissionMode.AUTO:
        return []
    if messages:
        turns, found = get_auto_mode_attachment_turn_count(messages)
        if found and turns < AUTO_MODE_ATTACHMENT_CONFIG.TURNS_BETWEEN_ATTACHMENTS:
            return []
    count = count_auto_mode_attachments_since_last_exit(messages) + 1
    reminder = "full" if count % AUTO_MODE_ATTACHMENT_CONFIG.FULL_REMINDER_EVERY_N_ATTACHMENTS == 1 else "sparse"
    return [Attachment("auto_mode", {"reminderType": reminder})]


def _has_skill_tool(context: Any) -> bool:
    return any(t.name == SKILL_TOOL_NAME for t in context.tools)


def get_skill_listing_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getSkillListingAttachments`."""
    registry = context.skill_registry
    if registry is None or not _has_skill_tool(context):
        return []
    commands = registry.get_skill_tool_commands()
    # Upstream keys by `agentId ?? ''`; the main thread's key here is its `None` agent id.
    sent = registry.sent_skill_names.setdefault(context.agent_id, set())
    new_skills = [cmd for cmd in commands if cmd.name not in sent]
    if not new_skills:
        return []
    is_initial = not sent
    sent.update(cmd.name for cmd in new_skills)
    logger.debug("Sending %d skills via attachment (%s, %d total sent)",
                 len(new_skills), "initial" if is_initial else "dynamic", len(sent))
    window = get_context_window_for_model(registry.model) if registry.model else None
    content = format_commands_within_budget(new_skills, window)
    return [Attachment("skill_listing", {"content": content, "skillCount": len(new_skills), "isInitial": is_initial})]


TOKEN_USAGE_ENV = "TDDAGENTS_ENABLE_TOKEN_USAGE_ATTACHMENT"


def token_count_from_usage(usage: Mapping[str, Any]) -> int:
    """`getTokenCountFromUsage`: input + cache creation + cache read + output."""
    details = usage.get("input_token_details") or {}
    return (
        int(usage.get("input_tokens") or 0)
        + int(details.get("cache_creation") or 0)
        + int(details.get("cache_read") or 0)
        + int(usage.get("output_tokens") or 0)
    )


def token_count_from_last_api_response(messages: Sequence[Message]) -> int:
    """`tokenCountFromLastAPIResponse`: the last assistant message carrying usage, else 0."""
    for message in reversed(list(messages)):
        if getattr(message, "type", None) != "ai":
            continue
        usage = getattr(message, "usage_metadata", None)
        if isinstance(usage, Mapping):
            return token_count_from_usage(usage)
    return 0


def effective_context_window(model: str) -> int:
    """`getEffectiveContextWindowSize`, from the token module (Phase 5, F1)."""
    import app.utils.tokens as tokens

    size: int = tokens.get_effective_context_window_size(model)
    return size


def get_token_usage_attachment(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getTokenUsageAttachment`."""
    if os.environ.get(TOKEN_USAGE_ENV, "").strip().lower() not in ("1", "true", "yes", "on"):
        return []
    from app.config.config import Config

    window = effective_context_window(Config.MODEL)
    used = token_count_from_last_api_response(messages)
    return [Attachment("token_usage", {"used": used, "total": window, "remaining": window - used})]


__all__ = [
    "AGENT_LIST_ENV",
    "AUTO_MODE_ATTACHMENT_CONFIG",
    "AUTO_MODE_FULL_INSTRUCTIONS",
    "AUTO_MODE_SPARSE_INSTRUCTIONS",
    "Attachment",
    "DIFF_SNIPPET_MAX_BYTES",
    "MCP_INSTR_DELTA_ENV",
    "NestedMemoryState",
    "OVERRIDE_DATE_ENV",
    "TDD_STATE_HEADER",
    "TODO_REMINDER_CONFIG",
    "TODO_REMINDER_TEXT",
    "TOKEN_USAGE_ENV",
    "agent_definitions_for",
    "attachment_of",
    "count_auto_mode_attachments_since_last_exit",
    "create_attachment_messages",
    "effective_context_window",
    "format_agent_line",
    "get_agent_listing_delta_attachment",
    "get_agent_pending_message_attachments",
    "get_async_hook_response_attachments",
    "get_attachment_messages",
    "get_attachments",
    "get_auto_mode_attachment_turn_count",
    "get_auto_mode_attachments",
    "get_changed_files",
    "get_date_change_attachments",
    "get_directories_to_process",
    "get_local_iso_date",
    "get_mcp_instructions_delta_attachment",
    "get_nested_memory_attachments",
    "get_nested_memory_source",
    "get_queued_command_attachments",
    "get_skill_listing_attachments",
    "get_snippet_for_two_file_diff",
    "get_todo_reminder_attachments",
    "get_todo_reminder_turn_counts",
    "get_token_usage_attachment",
    "get_tools_description",
    "is_mcp_instructions_delta_enabled",
    "is_thinking_message",
    "maybe",
    "mcp_clients_for",
    "nested_memory_state_of",
    "normalize_attachment_for_api",
    "process_conditioned_md_rules",
    "reset_last_emitted_dates",
    "tdd_state_lines",
    "token_count_from_last_api_response",
    "token_count_from_usage",
    "wrap_command_text",
]
