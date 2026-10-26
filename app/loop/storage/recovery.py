"""
Resuming a conversation from its transcript (Phase 6).

Ported from `reference/claude-code/src/utils/conversationRecovery.ts` ->
`deserializeMessagesWithInterruptDetection`, `detectTurnInterruption` and
`loadConversationForResume` (the `.jsonl` path branch), and from
`reference/claude-code/src/utils/messages.ts` -> `filterUnresolvedToolUses`,
`filterOrphanedThinkingOnlyMessages`, `filterWhitespaceOnlyAssistantMessages` and
`mergeUserMessages`.

What a resume gets back is the chain the loop can continue from:

1. assistant messages whose every tool call went unanswered are dropped (the process died
   between the call and its result), as are orphaned thinking-only and whitespace-only
   assistant messages, which the API would reject;
2. the end of the chain says whether the run was interrupted. A turn cut short after a tool
   result or an attachment gets upstream's meta continuation ("Continue from where you left
   off."), so both interruption kinds reach the caller as `interrupted_prompt`;
3. a trailing user message gets the `NO_RESPONSE_REQUESTED` assistant sentinel, so the
   history is API-valid even if the caller takes no resume action.

**TDD.** Resume restores messages, never the ledger. The phase ledger is written only by
`RunTests`; a resumed run starts from whatever ledger its caller seeds, and the TDD-state
attachment is not in the transcript (the external build does not log attachments), so
nothing here can claim an observed RED or GREEN.

Mapping onto LangChain messages: a `ToolMessage` is upstream's tool-result user message, an
`AIMessage`'s blocks are its content (a string is one text block) plus its `tool_calls`, and
a message carrying an attachment record is upstream's `attachment` type even though it is a
`HumanMessage`.

*Divergences, recorded:* adjacent user messages left by the whitespace filter are merged only
when both are plain `HumanMessage`s — a `ToolMessage` is a message of its own in LangChain,
and the provider adapters group consecutive tool results themselves.

Not ported: legacy attachment-type migration and `permissionMode` stripping (no legacy
transcripts or per-message modes here), skill-state restore (no skills subsystem state),
brief mode's terminal tool results (no such tool: `_is_terminal_tool_result` is the hook
point), `checkResumeConsistency` (analytics only), and the session-id / most-recent lookup.
SessionStart(`resume`) hooks need the run's dispatcher, so the caller runs them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeGuard

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.loop.hooks.messages import ATTACHMENT_KEY
from app.loop.messages import Message
from app.loop.storage.entries import UUID_KEY, entry_type_of
from app.loop.storage.loader import load_messages_from_jsonl_path

#: `messages.ts` -> `NO_RESPONSE_REQUESTED`.
NO_RESPONSE_REQUESTED = "No response requested."

#: `deserializeMessagesWithInterruptDetection`'s continuation for a mid-turn interruption.
CONTINUATION_MESSAGE = "Continue from where you left off."

_THINKING = ("thinking", "redacted_thinking")


@dataclass(frozen=True)
class TurnInterruptionState:
    """`TurnInterruptionState`: `message` is set for `interrupted_prompt` only."""

    kind: Literal["none", "interrupted_prompt"]
    message: Message | None


@dataclass(frozen=True)
class DeserializeResult:
    messages: list[Message]
    turn_interruption_state: TurnInterruptionState


_NONE = TurnInterruptionState(kind="none", message=None)


def _flag(message: Message, key: str) -> bool:
    return message.additional_kwargs.get(key) is True


def _kind(message: Message) -> str | None:
    """Upstream's `type`, with an attachment-carrying message as `attachment`."""
    if isinstance(message.additional_kwargs.get(ATTACHMENT_KEY), Mapping):
        return "attachment"
    return entry_type_of(message)


def _assistant_blocks(message: AIMessage) -> list[dict[str, Any]]:
    """
    An assistant's content blocks: its content, then a `tool_use` block per tool call.

    A provider that also keeps the call in `content` yields it twice; every reader asks
    only about block types or the set of ids, so the duplicate changes no decision.
    """
    content = message.content
    if isinstance(content, str):
        blocks: list[dict[str, Any]] = [{"type": "text", "text": content}] if content else []
    else:
        blocks = [{"type": "text", "text": b} if isinstance(b, str) else dict(b) for b in content]
    blocks.extend({"type": "tool_use", "id": call["id"]} for call in message.tool_calls)
    return blocks


def _tool_use_ids(message: AIMessage) -> list[str]:
    return [str(b.get("id")) for b in _assistant_blocks(message) if b.get("type") == "tool_use"]


def filter_unresolved_tool_uses(messages: list[Message]) -> list[Message]:
    """`filterUnresolvedToolUses`: drop an assistant only if *all* its tool calls are unanswered."""
    tool_use_ids: set[str] = set()
    tool_result_ids: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage):
            tool_use_ids.update(_tool_use_ids(message))
        elif isinstance(message, ToolMessage):
            tool_result_ids.add(message.tool_call_id)
    unresolved = tool_use_ids - tool_result_ids
    if not unresolved:
        return messages

    def keep(message: Message) -> bool:
        if not isinstance(message, AIMessage):
            return True
        ids = _tool_use_ids(message)
        return not ids or not all(i in unresolved for i in ids)

    return [m for m in messages if keep(m)]


def filter_orphaned_thinking_only_messages(messages: list[Message]) -> list[Message]:
    """`filterOrphanedThinkingOnlyMessages`: thinking-only blocks with no same-id partner go."""
    ids_with_content: set[str] = set()
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        if any(b.get("type") not in _THINKING for b in _assistant_blocks(message)) and message.id:
            ids_with_content.add(message.id)

    def keep(message: Message) -> bool:
        if not isinstance(message, AIMessage):
            return True
        blocks = _assistant_blocks(message)
        if not blocks or not all(b.get("type") in _THINKING for b in blocks):
            return True
        return bool(message.id) and message.id in ids_with_content

    return [m for m in messages if keep(m)]


def _has_only_whitespace_text(blocks: list[dict[str, Any]]) -> bool:
    """`hasOnlyWhitespaceTextContent`."""
    if not blocks:
        return False
    return all(b.get("type") == "text" and not str(b.get("text") or "").strip() for b in blocks)


def _user_blocks(content: Any) -> list[Any]:
    """`normalizeUserTextContent`."""
    return [{"type": "text", "text": content}] if isinstance(content, str) else list(content)


def _is_text_block(block: Any) -> TypeGuard[dict[str, Any]]:
    return isinstance(block, dict) and block.get("type") == "text"


def _merge_user_messages(a: Message, b: Message) -> Message:
    """`mergeUserMessages` + `joinTextAtSeam`: a `\\n` on a's side of a text-text seam."""
    left, right = _user_blocks(a.content), _user_blocks(b.content)
    last, first = (left[-1] if left else None), (right[0] if right else None)
    if _is_text_block(last) and _is_text_block(first):
        left = [*left[:-1], {**last, "text": f"{last.get('text', '')}\n"}]
    kwargs = dict(a.additional_kwargs)
    uuid = (b if _flag(a, "is_meta") else a).additional_kwargs.get(UUID_KEY)
    if uuid is not None:
        kwargs[UUID_KEY] = uuid
    return HumanMessage(content=[*left, *right], additional_kwargs=kwargs)


def _is_plain_user(message: Any) -> bool:
    return isinstance(message, HumanMessage) and _kind(message) == "user"


def filter_whitespace_only_assistant_messages(messages: list[Message]) -> list[Message]:
    """`filterWhitespaceOnlyAssistantMessages`, then merge the users it leaves adjacent."""
    filtered = [
        m for m in messages
        if not isinstance(m, AIMessage) or not _has_only_whitespace_text(_assistant_blocks(m))
    ]
    if len(filtered) == len(messages):
        return messages
    merged: list[Message] = []
    for message in filtered:
        if merged and _is_plain_user(message) and _is_plain_user(merged[-1]):
            merged[-1] = _merge_user_messages(merged[-1], message)
        else:
            merged.append(message)
    return merged


def _is_terminal_tool_result(message: Message, messages: Sequence[Message], index: int) -> bool:
    """`isTerminalToolResult`: upstream's brief-mode tools end a turn; this build has none."""
    return False


def _detect_turn_interruption(messages: list[Message]) -> TurnInterruptionState | None:
    """`detectTurnInterruption`: a final state, or `None` for upstream's `interrupted_turn`."""
    index = len(messages) - 1
    while index >= 0:
        candidate = messages[index]
        if _kind(candidate) != "system" and not (candidate.type == "ai" and _flag(candidate, "is_api_error_message")):
            break
        index -= 1
    if index < 0:
        return _NONE
    last = messages[index]
    kind = _kind(last)
    if kind == "attachment":
        return None
    if kind != "user":
        return _NONE
    if _flag(last, "is_meta") or _flag(last, "is_compact_summary"):
        return _NONE
    if last.type == "tool":
        return _NONE if _is_terminal_tool_result(last, messages, index) else None
    return TurnInterruptionState(kind="interrupted_prompt", message=last)


def deserialize_messages_with_interrupt_detection(messages: Sequence[Message]) -> DeserializeResult:
    """`deserializeMessagesWithInterruptDetection`."""
    filtered = list(filter_whitespace_only_assistant_messages(
        filter_orphaned_thinking_only_messages(filter_unresolved_tool_uses(list(messages)))
    ))
    detected = _detect_turn_interruption(filtered)
    if detected is not None:
        state = detected
    else:
        continuation = HumanMessage(content=CONTINUATION_MESSAGE, additional_kwargs={"is_meta": True})
        filtered.append(continuation)
        state = TurnInterruptionState(kind="interrupted_prompt", message=continuation)

    last_relevant = len(filtered) - 1
    while last_relevant >= 0 and _kind(filtered[last_relevant]) == "system":
        last_relevant -= 1
    if last_relevant >= 0 and _kind(filtered[last_relevant]) == "user":
        filtered.insert(last_relevant + 1, AIMessage(content=NO_RESPONSE_REQUESTED))
    return DeserializeResult(messages=filtered, turn_interruption_state=state)


def deserialize_messages(messages: Sequence[Message]) -> list[Message]:
    """`deserializeMessages`."""
    return deserialize_messages_with_interrupt_detection(messages).messages


def remove_interrupted_message(messages: Sequence[Message], interrupted: Message) -> list[Message]:
    """
    `print.ts` -> `removeInterruptedMessage`: drop the interrupted user message and the
    sentinel that immediately follows it, so the caller can submit it again and the model
    sees it exactly once. Matched by identity: the continuation message has no uuid.
    """
    result = list(messages)
    for index, message in enumerate(result):
        if message is interrupted:
            del result[index:index + 2]
            break
    return result


def load_conversation_for_resume(path: str | Path) -> DeserializeResult | None:
    """
    `loadConversationForResume(undefined, path)`: the main chain of a transcript file, ready
    for the loop. `None` when there is no such file (upstream's "not found").
    """
    if not Path(path).is_file():
        return None
    return deserialize_messages_with_interrupt_detection(load_messages_from_jsonl_path(path))


__all__ = [
    "CONTINUATION_MESSAGE",
    "NO_RESPONSE_REQUESTED",
    "DeserializeResult",
    "TurnInterruptionState",
    "deserialize_messages",
    "deserialize_messages_with_interrupt_detection",
    "filter_orphaned_thinking_only_messages",
    "filter_unresolved_tool_uses",
    "filter_whitespace_only_assistant_messages",
    "load_conversation_for_resume",
    "remove_interrupted_message",
]
