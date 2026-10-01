"""
Compaction systems: tool result budget, microcompaction, and auto/reactive compaction.

Ported from:
- `reference/claude-code/src/services/compact/compact.ts`
- `reference/claude-code/src/services/compact/autoCompact.ts`

Implements graduated context management (§2.5):
1. Cheap/lossless per-result size budgets.
2. Microcompaction of older completed tool outputs.
3. Summary generation + API-invariant head truncation (Part E2).
4. Reactive compact error recovery (PTL 413).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, Sequence

from langchain_core.messages import HumanMessage, ToolMessage

from app.loop.config import RunConfig
from app.loop.context.cleanup import notify_compaction
from app.loop.context.slicing import is_user_message, slice_messages_head
from app.loop.context.tokens import TokenCounter
from app.loop.messages import Message

if TYPE_CHECKING:
    from app.loop.deps import CompactionResult
    from app.loop.state import LoopState


DEFAULT_MAX_TOOL_RESULT_CHARS = 10_000


def apply_tool_result_budget(
    messages: Sequence[Message],
    max_chars_per_result: int = DEFAULT_MAX_TOOL_RESULT_CHARS,
) -> tuple[Message, ...]:
    """
    Truncate oversized individual tool results to prevent single calls from flooding context.

    Lossless for ordinary tools, caps outlier data.
    """
    result: list[Message] = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            content = str(getattr(msg, "content", ""))
            if len(content) > max_chars_per_result:
                truncated_text = (
                    content[:max_chars_per_result]
                    + f"\n\n... [Tool result truncated: was {len(content)} chars, capped to {max_chars_per_result}]"
                )
                call_id = getattr(msg, "tool_call_id", "")
                result.append(
                    ToolMessage(
                        content=truncated_text,
                        tool_call_id=call_id,
                        name=getattr(msg, "name", None),
                        status=getattr(msg, "status", None),
                    )
                )
                continue
        result.append(msg)
    return tuple(result)


def microcompact_tool_results(
    messages: Sequence[Message],
    keep_recent_turns: int = 2,
    min_chars_to_compact: int = 150,
) -> tuple[Message, ...]:
    """
    Microcompaction: replace older completed tool results with compact references.

    Preserves tool results for the most recent `keep_recent_turns` turns. Older results
    that succeeded and are longer than min_chars_to_compact are collapsed.
    """
    # Identify user turn boundaries
    user_turn_indices = [i for i, m in enumerate(messages) if is_user_message(m)]
    cutoff_index = 0
    if len(user_turn_indices) > keep_recent_turns:
        cutoff_index = user_turn_indices[-keep_recent_turns]

    result: list[Message] = []
    for idx, msg in enumerate(messages):
        if idx < cutoff_index and isinstance(msg, ToolMessage):
            content = str(getattr(msg, "content", ""))
            if len(content) > min_chars_to_compact:
                compact_notice = (
                    f"[Tool result microcompacted: {len(content)} chars output retained in earlier turn]"
                )
                call_id = getattr(msg, "tool_call_id", "")
                result.append(
                    ToolMessage(
                        content=compact_notice,
                        tool_call_id=call_id,
                        name=getattr(msg, "name", None),
                        status=getattr(msg, "status", None),
                    )
                )
                continue
        result.append(msg)

    return tuple(result)


def compact_conversation(
    messages: Sequence[Message],
    summary: str,
    keep_tail_count: int | None = 4,
    token_counter: TokenCounter | None = None,
    max_tokens: int | None = None,
) -> tuple[Message, ...]:
    """
    Compact conversation history by prepending a structured summary and retaining a safe tail.

    Enforces all three API invariants (§5 Part E2):
    - Truncated tail starts with a user message.
    - No split tool_use / tool_result pairs.
    - No orphaned thinking blocks.
    """
    effective_keep_count = None if (max_tokens is not None and token_counter is not None) else keep_tail_count
    tail = slice_messages_head(
        messages,
        keep_count=effective_keep_count,
        max_tokens=max_tokens,
        token_counter=token_counter,
    )

    summary_content = (
        f"<summary>\n{summary.strip()}\n</summary>\n\n"
        "(Conversation history before this point has been compacted to preserve context space.)"
    )
    summary_message = HumanMessage(content=summary_content)

    return (summary_message, *tail)


async def try_reactive_compact(
    state: LoopState,
    config: RunConfig,
    summarize_fn: Callable[[str], Awaitable[str]],
    token_counter: TokenCounter | None = None,
) -> CompactionResult:
    """
    Reactive compaction handler invoked when prompt-too-long (413) occurs in the loop.

    Generates a concise task and state summary, safely truncates the conversation head,
    and returns a CompactionResult with updated tracking.
    """
    # 1. Apply microcompaction first
    microcompacted = microcompact_tool_results(state.messages, keep_recent_turns=1)
    budgeted = apply_tool_result_budget(microcompacted, max_chars_per_result=5_000)

    # 2. Summarize
    prompt_for_summary = (
        "Write a structured, concise continuation summary wrapped in <summary></summary> tags "
        "covering task overview, current progress, modified files, key technical decisions, and next steps."
    )
    summary_text = await summarize_fn(prompt_for_summary)

    # 3. Compact conversation
    compacted_messages = compact_conversation(
        budgeted,
        summary=summary_text,
        keep_tail_count=2,
        token_counter=token_counter,
    )

    tracking = notify_compaction(
        turn_id=f"reactive_turn_{state.turn_count}",
        turn_counter=state.turn_count,
    )

    from app.loop.deps import CompactionResult

    return CompactionResult(
        compacted=True,
        messages=compacted_messages,
        tracking=tracking,
    )
