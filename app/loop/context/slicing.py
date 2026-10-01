"""
API-invariant slicing for message history truncation.

Enforces the three hard API invariants specified in §5 Part E2:
1. Never split a tool_use / tool_result pair across truncation boundaries.
2. Never orphan a thinking block.
3. Never leave an assistant message first after head truncation.
"""

from __future__ import annotations

from typing import Sequence

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.loop.context.tokens import TokenCounter
from app.loop.messages import Message, tool_calls_in


def is_user_message(message: Message) -> bool:
    """Check if message represents a user turn."""
    if isinstance(message, HumanMessage):
        return True
    msg_type = getattr(message, "type", None)
    return msg_type in ("human", "user")


def is_assistant_message(message: Message) -> bool:
    """Check if message represents an assistant turn."""
    if isinstance(message, AIMessage):
        return True
    msg_type = getattr(message, "type", None)
    return msg_type in ("ai", "assistant")


def has_thinking_block(message: Message) -> bool:
    """Check if message contains a thinking block or thinking content."""
    additional = getattr(message, "additional_kwargs", None)
    if isinstance(additional, dict) and "thinking" in additional:
        return True
    content = getattr(message, "content", None)
    if isinstance(content, (list, tuple)):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "thinking":
                return True
    return False


def validate_api_invariants(messages: Sequence[Message]) -> tuple[bool, str | None]:
    """
    Validate that a sequence of messages obeys the three hard API invariants.

    Returns:
        (True, None) if valid, or (False, error_description) if any invariant is violated.
    """
    if not messages:
        return True, None

    # Invariant 3: First message must not be an assistant message (must be user message)
    if not is_user_message(messages[0]):
        return (
            False,
            "Invariant 3 violated: first message after truncation must be a user message, "
            f"found {messages[0].__class__.__name__}.",
        )

    # Invariant 2: Thinking blocks must not be orphaned outside assistant messages
    for idx, msg in enumerate(messages):
        if not is_assistant_message(msg) and has_thinking_block(msg):
            return (
                False,
                f"Invariant 2 violated: message at index {idx} ({msg.__class__.__name__}) "
                "contains an orphaned thinking block outside an assistant message.",
            )

    # Invariant 1: Never split tool_use / tool_result pairs
    # Every ToolMessage must have a matching preceding AIMessage tool_call.
    # Every AIMessage tool_call must be fulfilled by a matching ToolMessage.
    pending_tool_calls: set[str] = set()

    for idx, msg in enumerate(messages):
        if is_assistant_message(msg):
            # If there were already pending tool calls from a previous assistant message,
            # that means they were never answered before this new turn!
            if pending_tool_calls:
                return (
                    False,
                    f"Invariant 1 violated: preceding tool calls {pending_tool_calls} "
                    f"were left unfulfilled before assistant message at index {idx}.",
                )
            for call in tool_calls_in(msg):
                call_id = call.get("id")
                if call_id:
                    pending_tool_calls.add(call_id)

        elif isinstance(msg, ToolMessage):
            call_id = getattr(msg, "tool_call_id", None)
            if not call_id:
                return (
                    False,
                    f"Invariant 1 violated: ToolMessage at index {idx} has no tool_call_id.",
                )
            if call_id not in pending_tool_calls:
                return (
                    False,
                    f"Invariant 1 violated: ToolMessage at index {idx} with tool_call_id '{call_id}' "
                    "has no preceding unfulfilled tool_use in this slice.",
                )
            pending_tool_calls.remove(call_id)

        elif is_user_message(msg):
            if pending_tool_calls:
                return (
                    False,
                    f"Invariant 1 violated: tool_use {pending_tool_calls} left dangling "
                    f"without tool_result before user message at index {idx}.",
                )

    if pending_tool_calls:
        return (
            False,
            f"Invariant 1 violated: unfulfilled tool_use calls {pending_tool_calls} "
            "at the end of message sequence.",
        )

    return True, None


def find_safe_truncation_index(
    messages: Sequence[Message],
    target_index: int,
    direction: str = "forward",
) -> int:
    """
    Find the closest index to `target_index` where slicing `messages[idx:]` satisfies
    all three API invariants.

    Args:
        messages: Full message history.
        target_index: Desired cutoff index for head truncation.
        direction: "forward" (advance toward newer messages) or "backward" (retreat toward older).

    Returns:
        Safe cutoff index in range [0, len(messages)].
    """
    n = len(messages)
    if n == 0 or target_index <= 0:
        if n == 0:
            return 0
        valid, _ = validate_api_invariants(messages)
        if valid:
            return 0

    if target_index >= n:
        return n

    if direction == "backward":
        # Search backward from target_index down to 0
        for idx in range(target_index, -1, -1):
            if idx == 0:
                valid, _ = validate_api_invariants(messages)
                if valid:
                    return 0
            elif is_user_message(messages[idx]):
                valid, _ = validate_api_invariants(messages[idx:])
                if valid:
                    return idx
        # Fallback to forward search if backward yielded no safe cut
        return find_safe_truncation_index(messages, target_index, direction="forward")

    # Default direction == "forward"
    # Search forward from target_index up to n
    for idx in range(target_index, n):
        if is_user_message(messages[idx]):
            valid, _ = validate_api_invariants(messages[idx:])
            if valid:
                return idx

    # If no subsequent safe user message is found, slice to empty (n)
    return n


def slice_messages_head(
    messages: Sequence[Message],
    keep_count: int | None = None,
    max_tokens: int | None = None,
    token_counter: TokenCounter | None = None,
) -> tuple[Message, ...]:
    """
    Safely slice messages from the head (discarding oldest messages) while preserving API invariants.

    Args:
        messages: Input message sequence.
        keep_count: Approximate number of messages to keep from the tail.
        max_tokens: Target maximum tokens to keep in the retained tail.
        token_counter: TokenCounter instance for measuring token budget.

    Returns:
        Truncated message sequence starting with a valid user message and unbroken tool/thinking pairs.
    """
    if not messages:
        return ()

    target_idx = 0

    if keep_count is not None:
        target_idx = max(0, len(messages) - keep_count)

    elif max_tokens is not None and token_counter is not None:
        # Find index where messages[idx:] <= max_tokens
        curr_tokens = token_counter.count_messages(messages)
        if curr_tokens <= max_tokens:
            target_idx = 0
        else:
            for idx in range(len(messages)):
                tail_tokens = token_counter.count_messages(messages[idx:])
                if tail_tokens <= max_tokens:
                    target_idx = idx
                    break
            else:
                target_idx = len(messages)

    safe_idx = find_safe_truncation_index(messages, target_idx, direction="forward")
    return tuple(messages[safe_idx:])
