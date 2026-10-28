"""
One model response is split into one message per content block (Phase 1). Upstream stamps every
block with the response's `message.id` and `normalizeMessagesForAPI` merges blocks of the same
id back into one assistant message before sending (`mergeAssistantMessages`). Without it, two
parallel tool calls reached OpenAI as two assistant messages followed by two tool results, which
the API rejects (the Phase 13 live run's `model_error`).
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from app.services.api.claude import frame_content_blocks
from app.utils.messages import merge_split_assistant_messages


def call(cid: str, name: str = "Bash") -> dict:
    return {"id": cid, "name": name, "args": {"command": cid}, "type": "tool_call"}


def test_blocks_of_one_response_are_merged_across_their_tool_results():
    history = [
        HumanMessage(content="go"),
        AIMessage(content="", tool_calls=[call("a")], id="r1"),
        AIMessage(content="", tool_calls=[call("b")], id="r1"),
        ToolMessage(content="A", tool_call_id="a"),
        ToolMessage(content="B", tool_call_id="b"),
    ]
    merged: list = merge_split_assistant_messages(history)
    assert [type(m).__name__ for m in merged] == ["HumanMessage", "AIMessage", "ToolMessage", "ToolMessage"]
    assert [c["id"] for c in merged[1].tool_calls] == ["a", "b"]
    # The stored history is never changed.
    assert len(history) == 5 and [c["id"] for c in history[1].tool_calls] == ["a"]  # type: ignore[attr-defined]


def test_text_and_a_tool_call_of_one_response_become_one_message():
    merged: list = merge_split_assistant_messages([
        AIMessage(content="Let me run it.", id="r1"),
        AIMessage(content="", tool_calls=[call("a")], id="r1"),
        ToolMessage(content="A", tool_call_id="a"),
    ])
    assert len(merged) == 2
    assert merged[0].content == "Let me run it." and [c["id"] for c in merged[0].tool_calls] == ["a"]


def test_list_contents_are_concatenated():
    merged = merge_split_assistant_messages([
        AIMessage(content=[{"type": "text", "text": "x"}], id="r"),
        AIMessage(content=[{"type": "text", "text": "y"}], id="r"),
    ])
    assert merged[0].content == [{"type": "text", "text": "x"}, {"type": "text", "text": "y"}]
    mixed = merge_split_assistant_messages([AIMessage(content="x", id="r"), AIMessage(content=[{"type": "text",
                                                                                                "text": "y"}], id="r")])
    assert mixed[0].content == [{"type": "text", "text": "x"}, {"type": "text", "text": "y"}]


def test_different_or_missing_ids_and_a_human_turn_in_between_are_not_merged():
    for history in (
        [AIMessage(content="1", id="r1"), AIMessage(content="2", id="r2")],
        [AIMessage(content="1"), AIMessage(content="2")],
        [AIMessage(content="1", id="r1"), HumanMessage(content="u"), AIMessage(content="2", id="r1")],
    ):
        assert len(merge_split_assistant_messages(history)) == len(history)


def test_every_block_of_one_stream_carries_the_same_response_id():
    async def stream():
        yield AIMessageChunk(content="", tool_call_chunks=[{"id": "a", "name": "Bash", "args": "{}", "index": 0}])
        yield AIMessageChunk(content="", tool_call_chunks=[{"id": "b", "name": "Bash", "args": "{}", "index": 1}],
                             id="lc_run--late")

    async def collect() -> list:
        return [m async for m in frame_content_blocks(stream())]

    first, second = asyncio.run(collect())
    assert first.id and first.id == second.id
    again, _ = asyncio.run(collect())
    assert again.id != first.id  # one id per response
