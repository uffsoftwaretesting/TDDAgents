"""
Unit tests for I4: Context forking and incomplete tool call filtering.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.loop.agents.fork import (
    FORK_BOILERPLATE_TAG,
    FORK_DIRECTIVE_PREFIX,
    FORK_PLACEHOLDER_RESULT,
    build_child_message,
    build_forked_messages,
    build_worktree_notice,
    filter_incomplete_tool_calls,
    is_in_fork_child,
)


def test_filter_incomplete_tool_calls_all_complete() -> None:
    ai_msg = AIMessage(
        content="I will run tools",
        tool_calls=[{"name": "ReadFile", "args": {"path": "a.py"}, "id": "call_1"}],
    )
    tool_msg = ToolMessage(content="file contents", tool_call_id="call_1")
    human_msg = HumanMessage(content="Next step")

    messages = [human_msg, ai_msg, tool_msg]
    filtered = filter_incomplete_tool_calls(messages)
    assert len(filtered) == 3
    assert filtered == messages


def test_filter_incomplete_tool_calls_drops_orphaned_call() -> None:
    human_msg = HumanMessage(content="Start")
    ai_complete = AIMessage(
        content="First call",
        tool_calls=[{"name": "ReadFile", "args": {}, "id": "call_1"}],
    )
    tool_1 = ToolMessage(content="res1", tool_call_id="call_1")

    # Orphaned AI message without subsequent tool result
    ai_orphaned = AIMessage(
        content="Dangling call",
        tool_calls=[{"name": "WriteFile", "args": {}, "id": "call_2"}],
    )

    messages = [human_msg, ai_complete, tool_1, ai_orphaned]
    filtered = filter_incomplete_tool_calls(messages)
    assert len(filtered) == 3
    assert ai_orphaned not in filtered
    assert filtered == [human_msg, ai_complete, tool_1]


def test_filter_incomplete_tool_calls_partial_completion_drops_assistant() -> None:
    ai_multi = AIMessage(
        content="Two calls",
        tool_calls=[
            {"name": "ReadFile", "args": {}, "id": "call_a"},
            {"name": "WriteFile", "args": {}, "id": "call_b"},
        ],
    )
    tool_a = ToolMessage(content="res_a", tool_call_id="call_a")
    # call_b has no result!

    messages = [ai_multi, tool_a]
    filtered = filter_incomplete_tool_calls(messages)
    # ai_multi should be dropped because call_b has no result
    assert ai_multi not in filtered
    assert filtered == [tool_a]


def test_filter_incomplete_tool_calls_content_blocks() -> None:
    ai_block = AIMessage(
        content=[
            {"type": "text", "text": "calling tool"},
            {"type": "tool_use", "id": "tc_99", "name": "Grep", "input": {}},
        ]
    )
    human_block = HumanMessage(
        content=[
            {"type": "tool_result", "tool_use_id": "tc_99", "content": "matched lines"}
        ]
    )
    messages = [ai_block, human_block]
    filtered = filter_incomplete_tool_calls(messages)
    assert len(filtered) == 2


def test_build_child_message_format() -> None:
    msg = build_child_message("Investigate test failures")
    assert f"<{FORK_BOILERPLATE_TAG}>" in msg
    assert f"</{FORK_BOILERPLATE_TAG}>" in msg
    assert FORK_DIRECTIVE_PREFIX in msg
    assert "Investigate test failures" in msg
    assert "Your response MUST begin with \"Scope:\"" in msg


def test_is_in_fork_child_detection() -> None:
    normal_history = [
        HumanMessage(content="Hello"),
        AIMessage(content="Hi there"),
    ]
    assert is_in_fork_child(normal_history) is False

    fork_child_history = [
        HumanMessage(content="Hello"),
        HumanMessage(content=build_child_message("Do work")),
    ]
    assert is_in_fork_child(fork_child_history) is True

    # Content block detection
    block_history = [
        HumanMessage(content=[{"type": "text", "text": f"prefix <{FORK_BOILERPLATE_TAG}> suffix"}]),
    ]
    assert is_in_fork_child(block_history) is True


def test_build_forked_messages_structure() -> None:
    parent_history = [HumanMessage(content="Initial task")]
    assistant_msg = AIMessage(
        content="I will run two tools",
        tool_calls=[
            {"name": "ReadFile", "args": {"path": "a.txt"}, "id": "call_1"},
            {"name": "ReadFile", "args": {"path": "b.txt"}, "id": "call_2"},
        ],
    )

    forked = build_forked_messages("Check file differences", assistant_msg, parent_history)
    # Expected: parent_history (1) + assistant_msg (1) + 2 placeholder tool results + 1 child directive = 5
    assert len(forked) == 5
    assert forked[0].content == "Initial task"
    assert forked[1] == assistant_msg
    assert isinstance(forked[2], ToolMessage)
    assert forked[2].content == FORK_PLACEHOLDER_RESULT
    assert forked[2].tool_call_id == "call_1"
    assert isinstance(forked[3], ToolMessage)
    assert forked[3].content == FORK_PLACEHOLDER_RESULT
    assert forked[3].tool_call_id == "call_2"
    assert isinstance(forked[4], HumanMessage)
    assert "Check file differences" in str(forked[4].content)


def test_build_worktree_notice() -> None:
    notice = build_worktree_notice("/home/user/repo", "/tmp/worktree-123")
    assert "/home/user/repo" in notice
    assert "/tmp/worktree-123" in notice
    assert "isolated git worktree" in notice
