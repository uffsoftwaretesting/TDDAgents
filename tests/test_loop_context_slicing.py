"""
Tests for Part E2: API-invariant slicing (app/loop/context/slicing.py).
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.loop.context.slicing import (
    find_safe_truncation_index,
    slice_messages_head,
    validate_api_invariants,
)
from app.loop.context.tokens import TokenCounter


def test_validate_api_invariants_valid() -> None:
    msgs = (
        HumanMessage(content="Hello"),
        AIMessage(content="Hi"),
        HumanMessage(content="Do tool"),
        AIMessage(content="", tool_calls=[{"id": "call_1", "name": "ReadFile", "args": {}}]),
        ToolMessage(content="file data", tool_call_id="call_1"),
        AIMessage(content="Done"),
    )
    valid, err = validate_api_invariants(msgs)
    assert valid is True
    assert err is None


def test_validate_api_invariants_empty() -> None:
    valid, err = validate_api_invariants(())
    assert valid is True
    assert err is None


def test_invariant_1_never_orphan_tool_result() -> None:
    # ToolMessage without preceding AIMessage with matching tool_calls
    msgs = (
        HumanMessage(content="Hello"),
        ToolMessage(content="orphan result", tool_call_id="call_99"),
    )
    valid, err = validate_api_invariants(msgs)
    assert valid is False
    assert err is not None
    assert (
        "Invariant 1 violated: ToolMessage at index 1 with tool_call_id 'call_99' "
        "has no preceding unfulfilled tool_use in this slice."
    ) in err


def test_invariant_1_never_leave_dangling_tool_use() -> None:
    # AIMessage with tool_calls but missing ToolMessage
    msgs = (
        HumanMessage(content="Hello"),
        AIMessage(content="", tool_calls=[{"id": "call_1", "name": "ReadFile", "args": {}}]),
        HumanMessage(content="Next user prompt"),
    )
    valid, err = validate_api_invariants(msgs)
    assert valid is False
    assert err is not None
    assert (
        "Invariant 1 violated: tool_use {'call_1'} left dangling without tool_result "
        "before user message at index 2."
    ) in err


def test_invariant_2_never_orphan_thinking_block() -> None:
    # Thinking block outside AIMessage or in an invalid message
    msg = HumanMessage(content="Hello", additional_kwargs={"thinking": "orphaned thinking"})
    valid, err = validate_api_invariants((msg,))
    assert valid is False
    assert err is not None
    assert (
        "Invariant 2 violated: message at index 0 (HumanMessage) contains an "
        "orphaned thinking block outside an assistant message."
    ) in err


def test_invariant_3_never_leave_assistant_first() -> None:

    # First message is AIMessage
    msgs = (
        AIMessage(content="I am first"),
        HumanMessage(content="Hi"),
    )
    valid, err = validate_api_invariants(msgs)
    assert valid is False
    assert err is not None
    assert "Invariant 3 violated: first message after truncation must be a user message, found AIMessage." in err


def test_find_safe_truncation_index_basic() -> None:
    msgs = (
        HumanMessage(content="Turn 1 User"),
        AIMessage(content="", tool_calls=[{"id": "call_1", "name": "ReadFile", "args": {}}]),
        ToolMessage(content="data 1", tool_call_id="call_1"),
        AIMessage(content="Turn 1 Done"),
        HumanMessage(content="Turn 2 User"),
        AIMessage(content="Turn 2 Done"),
    )

    # Cut at 0 -> 0 (valid)
    assert find_safe_truncation_index(msgs, 0) == 0

    # Cut at 1 (AIMessage tool_use) -> must advance to 4 (Turn 2 User)
    assert find_safe_truncation_index(msgs, 1) == 4

    # Cut at 2 (ToolMessage) -> must advance to 4
    assert find_safe_truncation_index(msgs, 2) == 4

    # Cut at 3 (AIMessage) -> must advance to 4
    assert find_safe_truncation_index(msgs, 3) == 4

    # Cut at 4 (HumanMessage) -> 4
    assert find_safe_truncation_index(msgs, 4) == 4

    # Cut at 5 (AIMessage) -> no subsequent user message, advances to 6 (empty)
    assert find_safe_truncation_index(msgs, 5) == 6


def test_find_safe_truncation_index_backward() -> None:
    msgs = (
        HumanMessage(content="Turn 1 User"),
        AIMessage(content="", tool_calls=[{"id": "call_1", "name": "ReadFile", "args": {}}]),
        ToolMessage(content="data 1", tool_call_id="call_1"),
        AIMessage(content="Turn 1 Done"),
        HumanMessage(content="Turn 2 User"),
        AIMessage(content="Turn 2 Done"),
    )
    # Cut backward from 2 (ToolMessage) -> retreats to 0
    assert find_safe_truncation_index(msgs, 2, direction="backward") == 0
    # Cut backward from 5 (AIMessage) -> retreats to 4
    assert find_safe_truncation_index(msgs, 5, direction="backward") == 4

    # Backward search fallback to forward search when backward search yields no valid cut
    msgs_no_backward = (
        AIMessage(content="initial ai"),
        HumanMessage(content="user turn"),
    )
    assert find_safe_truncation_index(msgs_no_backward, 0, direction="backward") == 1


def test_slice_messages_head() -> None:
    msgs = (
        HumanMessage(content="Turn 1 User " + "x" * 100),
        AIMessage(content="Turn 1 AI " + "x" * 100),
        HumanMessage(content="Turn 2 User"),
        AIMessage(content="Turn 2 AI"),
    )
    counter = TokenCounter()
    total_tokens = counter.count_messages(msgs)

    # Slice to keep within a budget smaller than total tokens
    # Should safely drop Turn 1 and keep Turn 2
    budget = counter.count_messages(msgs[2:]) + 5
    assert total_tokens > budget
    sliced = slice_messages_head(msgs, max_tokens=budget, token_counter=counter)

    assert len(sliced) == 2
    assert sliced[0].content == "Turn 2 User"
    valid, err = validate_api_invariants(sliced)
    assert valid is True, err

    # Exact token equality: curr_tokens == max_tokens keeps all
    sliced_exact = slice_messages_head(msgs, max_tokens=total_tokens, token_counter=counter)
    assert len(sliced_exact) == 4

    # Exact tail token equality: tail_tokens == max_tokens
    exact_tail_budget = counter.count_messages(msgs[2:])
    sliced_tail = slice_messages_head(msgs, max_tokens=exact_tail_budget, token_counter=counter)
    assert len(sliced_tail) == 2
    assert sliced_tail[0].content == "Turn 2 User"


def test_slice_messages_head_with_keep_count() -> None:
    msgs = (
        HumanMessage(content="Turn 1 User"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "Run", "args": {}}]),
        ToolMessage(content="ok", tool_call_id="c1"),
        HumanMessage(content="Turn 2 User"),
        AIMessage(content="Turn 2 Done"),
    )
    # Asking to keep last 3 messages (ToolMessage, Turn 2 User, Turn 2 Done)
    # Invariant requires starting with HumanMessage and not orphaning ToolMessage!
    sliced = slice_messages_head(msgs, keep_count=3)
    assert len(sliced) == 2
    assert sliced[0].content == "Turn 2 User"
    valid, err = validate_api_invariants(sliced)
    assert valid is True, err

    # keep_count=0 returns empty tuple
    assert slice_messages_head(msgs, keep_count=0) == ()
    # keep_count exceeding length returns full sequence
    assert slice_messages_head(msgs, keep_count=100) == msgs


def test_duck_type_and_thinking_blocks() -> None:

    from app.loop.context.slicing import (
        has_thinking_block,
        is_assistant_message,
        is_user_message,
    )

    from langchain_core.messages import BaseMessage

    class CustomTypeMessage(BaseMessage):
        type: str

    assert is_user_message(CustomTypeMessage(content="hi", type="human")) is True
    assert is_user_message(CustomTypeMessage(content="hi", type="user")) is True
    assert is_user_message(CustomTypeMessage(content="hi", type="other")) is False

    assert is_assistant_message(CustomTypeMessage(content="hi", type="ai")) is True
    assert is_assistant_message(CustomTypeMessage(content="hi", type="assistant")) is True
    assert is_assistant_message(CustomTypeMessage(content="hi", type="other")) is False

    from typing import Any, cast
    raw_obj = cast(Any, object())
    assert is_user_message(raw_obj) is False
    assert is_assistant_message(raw_obj) is False
    assert has_thinking_block(raw_obj) is False

    # Thinking in content blocks
    t_msg = HumanMessage(content=[{"type": "thinking", "text": "secret"}])
    assert has_thinking_block(t_msg) is True

    plain_msg = HumanMessage(content=[{"type": "text", "text": "hello"}])
    assert has_thinking_block(plain_msg) is False

    str_msg = HumanMessage(content="just text")
    assert has_thinking_block(str_msg) is False


def test_validate_api_invariants_detailed_failures() -> None:
    # ToolMessage missing tool_call_id
    msgs_no_id = (
        HumanMessage(content="Hi"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "f", "args": {}}]),
        ToolMessage(content="res", tool_call_id=""),
    )
    v1, err1 = validate_api_invariants(msgs_no_id)
    assert v1 is False
    assert err1 is not None and "has no tool_call_id" in err1

    # Unfulfilled before assistant message
    msgs_unfulfilled_assistant = (
        HumanMessage(content="Hi"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "f", "args": {}}]),
        AIMessage(content="Second AI message without tool result"),
    )
    v2, err2 = validate_api_invariants(msgs_unfulfilled_assistant)
    assert v2 is False
    assert err2 is not None and "were left unfulfilled before assistant message" in err2

    # Unfulfilled at end of conversation
    msgs_unfulfilled_end = (
        HumanMessage(content="Hi"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "f", "args": {}}]),
    )
    v3, err3 = validate_api_invariants(msgs_unfulfilled_end)
    assert v3 is False
    assert err3 is not None and "unfulfilled tool_use calls" in err3 and "at the end of message sequence" in err3

    # Orphaned thinking in content blocks of HumanMessage
    msgs_orphaned = (
        HumanMessage(content=[{"type": "thinking", "text": "secret"}]),
    )
    v4, err4 = validate_api_invariants(msgs_orphaned)
    assert v4 is False
    assert err4 is not None and "orphaned thinking block" in err4


def test_find_safe_truncation_index_clamping_and_no_user() -> None:
    msgs = (
        HumanMessage(content="Turn 1"),
        AIMessage(content="Turn 1 AI"),
    )
    # Negative clamp
    assert find_safe_truncation_index(msgs, -10) == 0
    # Over-length clamp
    assert find_safe_truncation_index(msgs, 100) == 2

    # No user message anywhere
    msgs_no_user = (
        AIMessage(content="AI 1"),
        AIMessage(content="AI 2"),
    )
    assert find_safe_truncation_index(msgs_no_user, 0) == 2


def test_slice_messages_head_empty_and_budget_noop() -> None:
    assert slice_messages_head(()) == ()

    msgs = (
        HumanMessage(content="Hello"),
        AIMessage(content="World"),
    )
    counter = TokenCounter()
    total = counter.count_messages(msgs)
    # Passing ample budget returns unchanged messages
    assert slice_messages_head(msgs, max_tokens=total + 100, token_counter=counter) == msgs
