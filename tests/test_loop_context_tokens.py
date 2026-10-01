"""
Tests for Part E1: Token counting substrate (app/loop/context/tokens.py).
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from app.loop.context.tokens import (
    TokenCounter,
    TokenWarningState,
    calculate_token_warning_state,
    count_messages_tokens,
    estimate_block_tokens,
    estimate_message_tokens,
    estimate_string_tokens,
)


def test_estimate_string_tokens_empty() -> None:
    assert estimate_string_tokens("") == 0
    assert estimate_string_tokens(None) == 0


def test_estimate_string_tokens_ceil() -> None:
    # 1 to 4 chars -> 1 token
    assert estimate_string_tokens("a") == 1
    assert estimate_string_tokens("abcd") == 1
    # 5 chars -> 2 tokens
    assert estimate_string_tokens("abcde") == 2
    # 8 chars -> 2 tokens
    assert estimate_string_tokens("abcdefgh") == 2
    # 9 chars -> 3 tokens
    assert estimate_string_tokens("abcdefghi") == 3


def test_estimate_block_tokens_text() -> None:
    # text 11 chars -> 3 tokens
    assert estimate_block_tokens("hello world") == 3
    assert estimate_block_tokens({"type": "text", "text": "hello world"}) == 3
    assert estimate_block_tokens({"type": "text", "text": ""}) == 0
    assert estimate_block_tokens({"type": "text"}) == 0


def test_estimate_block_tokens_thinking() -> None:
    assert estimate_block_tokens({"type": "thinking", "thinking": "Let's think"}) == 3
    assert estimate_block_tokens({"type": "thinking", "thinking": ""}) == 0
    assert estimate_block_tokens({"type": "thinking"}) == 0


def test_estimate_block_tokens_tool_use() -> None:
    # name = "ReadFile" (8 chars -> 2 tokens)
    # inp = {"path": "test.txt"} -> json is '{"path": "test.txt"}' (20 chars -> 5 tokens)
    # total = 7
    block = {"type": "tool_use", "name": "ReadFile", "input": {"path": "test.txt"}}
    assert estimate_block_tokens(block) == 7

    # string input
    block2 = {"type": "tool_use", "name": "Bash", "input": "ls -la"}
    # Bash = 4 chars (1 token), "ls -la" = 6 chars (2 tokens) -> 3
    assert estimate_block_tokens(block2) == 3

    # missing name and input
    assert estimate_block_tokens({"type": "tool_use"}) == 0


def test_estimate_block_tokens_tool_result() -> None:
    block = {"type": "tool_result", "content": "result data"}
    # "result data" is 11 chars -> 3 tokens
    assert estimate_block_tokens(block) == 3

    # dict content
    block_dict = {"type": "tool_result", "content": {"status": "ok"}}
    # '{"status": "ok"}' is 16 chars -> 4 tokens
    assert estimate_block_tokens(block_dict) == 4

    # missing content
    assert estimate_block_tokens({"type": "tool_result"}) == 0


def test_estimate_block_tokens_fallback_and_non_dict() -> None:
    unknown_block = {"type": "unknown_type", "data": 123}
    assert estimate_block_tokens(unknown_block) > 0

    assert estimate_block_tokens(12345) == estimate_string_tokens("12345")


def test_estimate_message_tokens_human() -> None:
    msg = HumanMessage(content="Hello world")
    tokens = estimate_message_tokens(msg)
    # content is 11 chars -> 3 tokens + 4 message overhead = 7
    assert tokens == 7


def test_estimate_message_tokens_content_blocks() -> None:
    msg = HumanMessage(content=[
        {"type": "text", "text": "part 1"},
        {"type": "text", "text": "part 2"},
    ])
    # part 1 = 6 chars (2 tks), part 2 = 6 chars (2 tks) + 4 overhead = 8
    assert estimate_message_tokens(msg) == 8


def test_estimate_message_tokens_ai_with_tool_calls_and_thinking() -> None:
    msg = AIMessage(
        content="I will read.",
        tool_calls=[
            {"id": "call_1", "name": "ReadFile", "args": {"path": "main.py"}},
            {"id": "call_2", "name": "Bash", "args": {"command": "echo hi"}},
        ],
        additional_kwargs={"thinking": "Reasoning step"},
    )
    tokens = estimate_message_tokens(msg)
    assert tokens == 25


def test_estimate_message_tokens_tool_message() -> None:
    msg = ToolMessage(content="file content line 1\nline 2", tool_call_id="call_1")
    tokens = estimate_message_tokens(msg)
    assert tokens == estimate_string_tokens("file content line 1\nline 2") + 4


def test_count_messages_tokens() -> None:
    m1 = HumanMessage(content="Hello")
    m2 = AIMessage(content="Hi there")
    total = count_messages_tokens((m1, m2))
    assert total == estimate_message_tokens(m1) + estimate_message_tokens(m2)
    assert count_messages_tokens(()) == 0


def test_calculate_token_warning_state() -> None:
    # Model limit <= 0
    zero_state = calculate_token_warning_state(50, 0)
    assert zero_state.is_warning is False
    assert zero_state.is_blocking_limit is False
    assert zero_state.tokens == 50
    assert zero_state.model_limit == 0

    negative_state = calculate_token_warning_state(50, -100)
    assert negative_state.is_warning is False
    assert negative_state.is_blocking_limit is False
    assert negative_state.tokens == 50
    assert negative_state.model_limit == -100

    # Boundary model_limit == 1 (kills model_limit <= 1 mutant)
    limit_one = calculate_token_warning_state(1, 1)
    assert limit_one.tokens == 1
    assert limit_one.model_limit == 1
    assert limit_one.is_warning is True
    assert limit_one.is_blocking_limit is True

    # Using defaults: 100 limit, 80 tokens -> warning True, blocking False
    state_default = calculate_token_warning_state(80, 100)
    assert state_default.is_warning is True
    assert state_default.is_blocking_limit is False

    # 100 limit, 80 tokens -> warning True, blocking False
    state = calculate_token_warning_state(80, 100, warning_threshold=0.8, blocking_threshold=0.95)
    assert state.is_warning is True
    assert state.is_blocking_limit is False

    # 79 tokens -> warning False, blocking False
    state2 = calculate_token_warning_state(79, 100, warning_threshold=0.8, blocking_threshold=0.95)
    assert state2.is_warning is False
    assert state2.is_blocking_limit is False

    # 95 tokens with defaults -> warning True, blocking True
    state_default_blocking = calculate_token_warning_state(95, 100)
    assert state_default_blocking.is_warning is True
    assert state_default_blocking.is_blocking_limit is True

    # 95 tokens -> warning True, blocking True
    state3 = calculate_token_warning_state(95, 100, warning_threshold=0.8, blocking_threshold=0.95)
    assert state3.is_warning is True
    assert state3.is_blocking_limit is True


def test_token_counter_class() -> None:
    counter = TokenCounter()
    assert counter.count_string("hello world") == 3
    m = HumanMessage(content="test")
    assert counter.count_message(m) == estimate_message_tokens(m)
    assert counter.count_messages((m,)) == estimate_message_tokens(m)

    # Defaults
    warning = counter.warning_state(95, 100)
    assert isinstance(warning, TokenWarningState)
    assert warning.is_warning is True
    assert warning.is_blocking_limit is True
    assert warning.tokens == 95
    assert warning.model_limit == 100

    # Custom thresholds (verifies blocking_threshold forwarded properly)
    custom_warn = counter.warning_state(85, 100, warning_threshold=0.6, blocking_threshold=0.84)
    assert custom_warn.is_warning is True
    assert custom_warn.is_blocking_limit is True


def test_estimate_message_tokens_edge_cases() -> None:
    from typing import Any, cast

    # Tool call missing name via duck typing
    class DuckNoName:
        content = ""
        tool_calls = [{"id": "call_1", "args": {}}]

    # 4 (envelope) + 0 (name) + 1 (args={}) = 5
    assert estimate_message_tokens(cast(Any, DuckNoName())) == 5

    # Tool call missing args
    msg_no_args = AIMessage(content="", tool_calls=[{"id": "call_1", "name": "Run", "args": {}}])
    assert estimate_message_tokens(msg_no_args) == 6

    # Tool call with non-serializable args object fallback to str()
    class DuckCustomArgs:
        content = ""
        tool_calls = [{"id": "call_1", "name": "Run", "args": object()}]

    assert estimate_message_tokens(cast(Any, DuckCustomArgs())) > 6

    # Message duck-type missing content and additional_kwargs attributes
    class BareMessage:
        pass

    assert estimate_message_tokens(cast(Any, BareMessage())) == 4
