"""
Full conversation compaction: summarize the history, then rebuild the context after it.

Ported from `reference/claude-code/src/services/compact/compact.ts` -> `compactConversation`,
`buildPostCompactMessages`, `truncateHeadForPTLRetry`, `stripImagesFromMessages`,
`createPostCompactFileAttachments`, `shouldExcludeFromPostCompactRestore`, the
`POST_COMPACT_*` and `ERROR_MESSAGE_*` constants; `src/utils/messages.ts` ->
`createCompactBoundaryMessage`, `isCompactBoundaryMessage`, `findLastCompactBoundaryIndex`,
`getMessagesAfterCompactBoundary`; `src/services/api/errors.ts` ->
`PROMPT_TOO_LONG_ERROR_MESSAGE`, `parsePromptTooLongTokenCounts`, `getPromptTooLongTokenGap`.

Upstream's full compaction **keeps no tail**: everything after the last boundary is
summarized, and the context is rebuilt from the summary plus attachments — the files read
most recently (re-read through `ReadFile`, as upstream re-reads through `FileReadTool`), then
what the caller injects through `post_compact_attachments`. That injection is where the
engine re-attaches the TDD state from the ledger and the last `RunTests` result: the summary
is never trusted for it. Then SessionStart(`compact`) hook output, and PreCompact /
PostCompact hooks around the whole thing.

The boundary is a `SystemMessage` tagged `additional_kwargs["compact_boundary"]` (upstream's
`compactMetadata`). Like upstream's `system` record it is history bookkeeping, not model
input: a request builder must drop it (`normalizeMessagesForAPI` drops system records).

*Divergences:* `FileState` carries no timestamp, so "most recently read" is the read-state's
insertion order, newest last; an attachment's share of the restore budget is the rough token
estimate of its messages, where upstream estimates its JSON record. Not ported: the forked
prompt-cache-sharing summary path and its streaming retry (GrowthBook, default off for the
retry; the summary request goes through the injected `CompactSummarizer`), plan / plan-mode /
invoked-skill / async-agent / deferred-tools attachments (no such subsystems yet; the deltas
arrive through `post_compact_attachments`), session-memory and partial compaction, analytics,
transcript re-append and cache-break notifications.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.services.compact.grouping import group_messages_by_api_round
from app.services.compact.prompt import (
    get_compact_prompt,
    get_compact_user_summary_message,
)
from app.utils.claudemd import LOCAL_MEMORY_FILE_NAME, MEMORY_FILE_NAME
from app.utils.messages import Message, wrap_in_system_reminder
from app.utils.tokens import (
    COMPACT_MAX_OUTPUT_TOKENS,
    get_max_output_tokens_for_model,
    get_token_count_from_usage,
    get_token_usage,
    rough_token_count_estimation,
    rough_token_count_estimation_for_messages,
    token_count_with_estimation,
)

logger = logging.getLogger("TDDOrchestrator.Compact")

#: `POST_COMPACT_MAX_FILES_TO_RESTORE`.
POST_COMPACT_MAX_FILES_TO_RESTORE = 5
#: `POST_COMPACT_TOKEN_BUDGET`: the restored files' combined budget.
POST_COMPACT_TOKEN_BUDGET = 50_000
#: `POST_COMPACT_MAX_TOKENS_PER_FILE`: a file over this becomes a reference, not content.
POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000

#: `MAX_PTL_RETRIES`: how often the summary request is retried with its head truncated.
MAX_PTL_RETRIES = 3
PTL_RETRY_MARKER = "[earlier conversation truncated for compaction retry]"

PROMPT_TOO_LONG_ERROR_MESSAGE = "Prompt is too long"
API_ERROR_MESSAGE_PREFIX = "API Error"

ERROR_MESSAGE_NOT_ENOUGH_MESSAGES = "Not enough messages to compact."
ERROR_MESSAGE_PROMPT_TOO_LONG = "Conversation too long. Press esc twice to go up a few messages and try again."
ERROR_MESSAGE_USER_ABORT = "API Error: Request was aborted."
ERROR_MESSAGE_INCOMPLETE_RESPONSE = "Compaction interrupted · This may be due to network issues — please try again."
ERROR_MESSAGE_NO_SUMMARY = "Failed to generate conversation summary - response did not contain valid text content"

BOUNDARY_KEY = "compact_boundary"
BOUNDARY_CONTENT = "Conversation compacted"

#: The tool post-compact restoration re-reads through.
READ_TOOL_NAME = "ReadFile"

CompactTrigger = Literal["auto", "manual"]


class CompactSummarizer(Protocol):
    """The summary request: the history to summarize, the request, and the run's context."""

    def __call__(
        self, messages: Sequence[Message], summary_request: Message, context: Any, *, max_output_tokens: int
    ) -> Awaitable[Message]: ...


class ForkCompactSummarizer(Protocol):
    """
    `streamCompactSummary`'s cache-sharing path (`runForkedAgent` with the main loop's
    `cacheSafeParams`): the messages exactly as the main loop sends them, then the request.
    """

    def __call__(self, messages: Sequence[Message], summary_request: Message, context: Any) -> Awaitable[Message]: ...


#: What the engine re-attaches after the boundary (TDD state, deltas), given the context.
PostCompactAttachments = Callable[[Any], Sequence[Message]]


@dataclass
class CompactionResult:
    """`CompactionResult` (compact.ts)."""

    boundary_marker: Message
    summary_messages: list[Message]
    attachments: list[Message]
    hook_results: list[Message]
    messages_to_keep: list[Message] | None = None
    user_display_message: str | None = None
    pre_compact_token_count: int | None = None
    post_compact_token_count: int | None = None
    true_post_compact_token_count: int | None = None
    compaction_usage: dict[str, Any] | None = field(default=None)


# ── boundary ──────────────────────────────────────────────────────────────────

def create_compact_boundary_message(trigger: CompactTrigger, pre_tokens: int) -> Message:
    """`createCompactBoundaryMessage`."""
    return SystemMessage(
        content=BOUNDARY_CONTENT,
        additional_kwargs={BOUNDARY_KEY: {"trigger": trigger, "preTokens": pre_tokens}},
    )


def is_compact_boundary_message(message: Any) -> bool:
    """`isCompactBoundaryMessage`."""
    kwargs = getattr(message, "additional_kwargs", None)
    return getattr(message, "type", None) == "system" and isinstance(kwargs, dict) and BOUNDARY_KEY in kwargs


def find_last_compact_boundary_index(messages: Sequence[Message]) -> int:
    """`findLastCompactBoundaryIndex`: -1 when there is none."""
    for i in range(len(messages) - 1, -1, -1):
        if is_compact_boundary_message(messages[i]):
            return i
    return -1


def get_messages_after_compact_boundary(messages: Sequence[Message]) -> list[Message]:
    """`getMessagesAfterCompactBoundary`: from the last boundary on (boundary included)."""
    index = find_last_compact_boundary_index(messages)
    return list(messages) if index == -1 else list(messages[index:])


def build_post_compact_messages(result: CompactionResult) -> list[Message]:
    """`buildPostCompactMessages`: boundary, summary, kept tail, attachments, hook output."""
    return [
        result.boundary_marker,
        *result.summary_messages,
        *(result.messages_to_keep or []),
        *result.attachments,
        *result.hook_results,
    ]


# ── the summary request ───────────────────────────────────────────────────────

def _text_of(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


async def _forked_summary(
    fork: ForkCompactSummarizer | None, messages: Sequence[Message], summary_request: Message, context: Any
) -> Message | None:
    """
    `streamCompactSummary`'s cache-sharing branch (`tengu_compact_cache_prefix`, default true
    for third-party builds): the fork's reply when it has text and is no API error, else `None`
    so the caller falls back to the regular path, as upstream does after logging.
    """
    if fork is None:
        return None
    try:
        reply = await fork(messages, summary_request, context)
    except Exception:  # upstream logs the error and falls back to the streaming path
        logger.exception("Compact cache sharing failed; falling back to the regular summary request")
        return None
    kwargs = getattr(reply, "additional_kwargs", None) or {}
    if _text_of(reply) and not kwargs.get("is_api_error_message"):
        return reply
    logger.warning("Compact cache sharing: no text in response, falling back")
    return None


def _strip_media(blocks: list[Any]) -> tuple[list[Any], bool]:
    changed = False
    out: list[Any] = []
    for block in blocks:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind in ("image", "image_url"):
            out.append({"type": "text", "text": "[image]"})
            changed = True
        elif kind == "document":
            out.append({"type": "text", "text": "[document]"})
            changed = True
        else:
            out.append(block)
    return out, changed


def strip_images_from_messages(messages: Sequence[Message]) -> list[Message]:
    """`stripImagesFromMessages`: media in user turns and tool results becomes a text stub."""
    out: list[Message] = []
    for message in messages:
        content = getattr(message, "content", None)
        if getattr(message, "type", None) in ("human", "tool") and isinstance(content, list):
            stripped, changed = _strip_media(content)
            if changed:
                out.append(message.model_copy(update={"content": stripped}))
                continue
        out.append(message)
    return out


_PTL_COUNTS = re.compile(r"prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)", re.IGNORECASE)


def _is_prompt_too_long_message(message: Any) -> bool:
    """`isPromptTooLongMessage`: an API-error message whose text starts with the PTL text."""
    kwargs = getattr(message, "additional_kwargs", None) or {}
    if kwargs.get("is_api_error_message") is not True:
        return False
    return _text_of(message).startswith(PROMPT_TOO_LONG_ERROR_MESSAGE)


def get_prompt_too_long_token_gap(message: Any) -> int | None:
    """`getPromptTooLongTokenGap`: tokens over the limit, from the error details."""
    details = (getattr(message, "additional_kwargs", None) or {}).get("error_details")
    if not _is_prompt_too_long_message(message) or not details:
        return None
    match = _PTL_COUNTS.search(str(details))
    if match is None:
        return None
    gap = int(match.group(1)) - int(match.group(2))
    return gap if gap > 0 else None


def _is_ptl_marker(message: Any) -> bool:
    kwargs = getattr(message, "additional_kwargs", None) or {}
    return (
        getattr(message, "type", None) == "human"
        and kwargs.get("is_meta") is True
        and getattr(message, "content", None) == PTL_RETRY_MARKER
    )


def truncate_head_for_ptl_retry(messages: Sequence[Message], ptl_response: Any) -> list[Message] | None:
    """
    `truncateHeadForPTLRetry`: drop the oldest API rounds — enough to cover the reported
    token gap, else a fifth of them — never all of them. `None` when nothing can go.
    """
    source = list(messages[1:]) if messages and _is_ptl_marker(messages[0]) else list(messages)
    groups = group_messages_by_api_round(source)
    if len(groups) < 2:
        return None
    gap = get_prompt_too_long_token_gap(ptl_response)
    if gap is not None:
        accumulated = 0
        drop = 0
        for drop, group in enumerate(groups, start=1):
            accumulated += rough_token_count_estimation_for_messages(group)
            if accumulated >= gap:
                break
    else:
        drop = max(1, int(len(groups) * 0.2))
    drop = min(drop, len(groups) - 1)
    if drop < 1:
        return None
    sliced = [m for group in groups[drop:] for m in group]
    if sliced and getattr(sliced[0], "type", None) == "ai":
        return [HumanMessage(content=PTL_RETRY_MARKER, additional_kwargs={"is_meta": True}), *sliced]
    return sliced


# ── post-compact file restoration ─────────────────────────────────────────────

#: `shouldExcludeFromPostCompactRestore`: the memory files `getMemoryPath` names that can be
#: read inside the workspace (the project file and the local one).
_EXCLUDED_FROM_RESTORE = frozenset({MEMORY_FILE_NAME, LOCAL_MEMORY_FILE_NAME})


def should_exclude_from_post_compact_restore(filename: str) -> bool:
    """`shouldExcludeFromPostCompactRestore` (no plan files here: no plan mode)."""
    return filename in _EXCLUDED_FROM_RESTORE


def _read_tool(context: Any) -> Any:
    for tool in getattr(context, "tools", ()) or ():
        if getattr(tool, "name", None) == READ_TOOL_NAME:
            return tool
    from app.tools.file_read_tool.file_read_tool import build_read_file_tool

    return build_read_file_tool()


async def _generate_file_attachment(filename: str, context: Any, index: int) -> list[Message] | None:
    """
    `generateFileAttachment` in `compact` mode: the file re-read as a `ReadFile` call and
    its result (`normalizeAttachmentForAPI`'s `file` case), or a reference note when it is
    over `POST_COMPACT_MAX_TOKENS_PER_FILE` (`compact_file_reference`). `None` when the
    read is refused or fails.
    """
    tool = _read_tool(context)
    args = {"file_path": filename}
    try:
        if not tool.validate_input(args, context).valid:
            return None
        result = await tool.call(args, context)
    except Exception:
        logger.debug("Post-compact restore of %s failed", filename, exc_info=True)
        return None
    if getattr(result, "is_error", False):
        return None
    content = str(result.content)
    if rough_token_count_estimation(content) > POST_COMPACT_MAX_TOKENS_PER_FILE:
        # Upstream's read fails before recording the file, so the model must read it again.
        context.read_file_state.pop(filename, None)
        note = (
            f"Note: {filename} was read before the last conversation was summarized, but the contents are too "
            f"large to include. Use {READ_TOOL_NAME} tool if you need to access it."
        )
        return [HumanMessage(content=wrap_in_system_reminder(note), additional_kwargs={"is_meta": True})]
    call_id = f"compact_restore_{index}"
    return [
        AIMessage(content="", tool_calls=[{"name": READ_TOOL_NAME, "args": args, "id": call_id}]),
        ToolMessage(content=content, tool_call_id=call_id),
    ]


async def create_post_compact_file_attachments(
    read_file_state: dict[str, Any], context: Any, max_files: int
) -> list[Message]:
    """`createPostCompactFileAttachments`: the newest files, within count and token budgets."""
    recent = [name for name in reversed(list(read_file_state)) if not should_exclude_from_post_compact_restore(name)]
    attachments: list[list[Message]] = []
    for index, filename in enumerate(recent[:max_files]):
        attachment = await _generate_file_attachment(filename, context, index)
        if attachment is not None:
            attachments.append(attachment)
    used = 0
    kept: list[Message] = []
    for attachment in attachments:
        tokens = rough_token_count_estimation_for_messages(attachment)
        if used + tokens <= POST_COMPACT_TOKEN_BUDGET:
            used += tokens
            kept.extend(attachment)
    return kept


# ── compactConversation ───────────────────────────────────────────────────────

def merge_hook_instructions(user_instructions: str | None, hook_instructions: str | None) -> str | None:
    """`mergeHookInstructions`."""
    if not hook_instructions:
        return user_instructions or None
    if not user_instructions:
        return hook_instructions
    return f"{user_instructions}\n\n{hook_instructions}"


async def compact_conversation(
    messages: Sequence[Message],
    context: Any,
    *,
    summarizer: CompactSummarizer,
    trigger: CompactTrigger,
    post_compact_attachments: PostCompactAttachments,
    model: str,
    custom_instructions: str | None = None,
    suppress_follow_up_questions: bool = True,
    transcript_path: str | None = None,
    fork_summarizer: ForkCompactSummarizer | None = None,
) -> CompactionResult:
    """`compactConversation`: summarize everything; rebuild the context after the boundary."""
    from app.utils.hooks.hooks import (
        dispatcher_of,
        execute_post_compact_hooks,
        execute_pre_compact_hooks,
    )
    from app.utils.session_start import process_session_start_hooks

    if not messages:
        raise ValueError(ERROR_MESSAGE_NOT_ENOUGH_MESSAGES)
    pre_compact_token_count = token_count_with_estimation(messages)
    dispatcher = dispatcher_of(context)
    cancel = getattr(context, "cancel", None)

    pre = await execute_pre_compact_hooks(dispatcher, trigger, custom_instructions, signal=cancel)
    instructions = merge_hook_instructions(custom_instructions, pre.new_custom_instructions)
    summary_request = HumanMessage(content=get_compact_prompt(instructions))

    max_output_tokens = min(COMPACT_MAX_OUTPUT_TOKENS, get_max_output_tokens_for_model(model))
    to_summarize = list(messages)
    attempts = 0
    while True:
        # `streamCompactSummary`: the cache-sharing fork first, the regular path on its failure.
        # Both get the PTL-truncated set (upstream threads it through `forkContextMessages`).
        response = await _forked_summary(fork_summarizer, to_summarize, summary_request, context)
        if response is None:
            prepared = strip_images_from_messages(get_messages_after_compact_boundary(to_summarize))
            response = await summarizer(prepared, summary_request, context, max_output_tokens=max_output_tokens)
        summary = _text_of(response)
        if not summary.startswith(PROMPT_TOO_LONG_ERROR_MESSAGE):
            break
        attempts += 1
        truncated = truncate_head_for_ptl_retry(to_summarize, response) if attempts <= MAX_PTL_RETRIES else None
        if truncated is None:
            raise ValueError(ERROR_MESSAGE_PROMPT_TOO_LONG)
        to_summarize = truncated

    if not summary:
        raise ValueError(ERROR_MESSAGE_NO_SUMMARY)
    if summary.startswith(API_ERROR_MESSAGE_PREFIX):
        raise ValueError(summary)

    pre_compact_read_state = dict(context.read_file_state)
    context.read_file_state.clear()
    attachments = await create_post_compact_file_attachments(
        pre_compact_read_state, context, POST_COMPACT_MAX_FILES_TO_RESTORE
    )
    attachments.extend(post_compact_attachments(context))

    session = await process_session_start_hooks(dispatcher, "compact", model=model)
    hook_messages = list(session.messages)

    boundary = create_compact_boundary_message(trigger, pre_compact_token_count)
    summary_messages: list[Message] = [
        HumanMessage(
            content=get_compact_user_summary_message(summary, suppress_follow_up_questions, transcript_path),
            additional_kwargs={"is_compact_summary": True},
        )
    ]
    usage = get_token_usage(response)
    post_compact_token_count = get_token_count_from_usage(usage) if usage is not None else 0
    true_post = rough_token_count_estimation_for_messages([boundary, *summary_messages, *attachments, *hook_messages])

    post_display = await execute_post_compact_hooks(dispatcher, trigger, summary, signal=cancel)
    display = "\n".join(part for part in (pre.user_display_message, post_display) if part)

    return CompactionResult(
        boundary_marker=boundary,
        summary_messages=summary_messages,
        attachments=attachments,
        hook_results=hook_messages,
        user_display_message=display or None,
        pre_compact_token_count=pre_compact_token_count,
        post_compact_token_count=post_compact_token_count,
        true_post_compact_token_count=true_post,
        compaction_usage=usage,
    )


__all__ = [
    "API_ERROR_MESSAGE_PREFIX",
    "BOUNDARY_KEY",
    "ERROR_MESSAGE_INCOMPLETE_RESPONSE",
    "ERROR_MESSAGE_NOT_ENOUGH_MESSAGES",
    "ERROR_MESSAGE_NO_SUMMARY",
    "ERROR_MESSAGE_PROMPT_TOO_LONG",
    "ERROR_MESSAGE_USER_ABORT",
    "MAX_PTL_RETRIES",
    "POST_COMPACT_MAX_FILES_TO_RESTORE",
    "POST_COMPACT_MAX_TOKENS_PER_FILE",
    "POST_COMPACT_TOKEN_BUDGET",
    "PROMPT_TOO_LONG_ERROR_MESSAGE",
    "PTL_RETRY_MARKER",
    "CompactSummarizer",
    "CompactTrigger",
    "CompactionResult",
    "PostCompactAttachments",
    "build_post_compact_messages",
    "compact_conversation",
    "create_compact_boundary_message",
    "create_post_compact_file_attachments",
    "find_last_compact_boundary_index",
    "get_messages_after_compact_boundary",
    "get_prompt_too_long_token_gap",
    "is_compact_boundary_message",
    "merge_hook_instructions",
    "should_exclude_from_post_compact_restore",
    "strip_images_from_messages",
    "truncate_head_for_ptl_retry",
]
