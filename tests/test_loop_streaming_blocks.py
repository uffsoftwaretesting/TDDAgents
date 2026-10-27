"""
Content-block framing: `app/services/api/claude.py`, the port of upstream's
`content_block_start` / `_delta` / `_stop` handling in `claude.ts`.

The property under test is the one eager dispatch depends on: a tool call is a whole,
parseable call the moment its own block closes, and never before.
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.services.api.claude import (
    TEXT_BLOCK,
    BlockAccumulator,
    frame_content_blocks,
    split_chunk_into_blocks,
)
from app.utils.messages import tool_calls_in


async def _aiter(items):
    for item in items:
        yield item


def frame(items):
    async def go():
        return [m async for m in frame_content_blocks(_aiter(items))]
    return asyncio.run(go())


def call_chunk(index, name=None, args="", cid=None):
    return AIMessageChunk(
        content="", tool_call_chunks=[{"name": name, "args": args, "id": cid, "index": index}]
    )


# ── split_chunk_into_blocks ─────────────────────────────────────────────────

def test_a_text_only_chunk_is_one_text_block():
    chunk = AIMessageChunk(content="hello")
    assert split_chunk_into_blocks(chunk) == [(TEXT_BLOCK, chunk)]


def test_a_tool_chunk_is_keyed_by_its_index():
    [(key, part)] = split_chunk_into_blocks(call_chunk(3, "ReadFile", "{}", "c1"))
    assert key == ("tool", 3)
    assert part.content == ""


def test_a_mixed_chunk_splits_text_first_then_each_call():
    chunk = AIMessageChunk(content="thinking", tool_call_chunks=[
        {"name": "A", "args": "{}", "id": "c1", "index": 0},
        {"name": "B", "args": "{}", "id": "c2", "index": 1},
    ])
    keys = [key for key, _ in split_chunk_into_blocks(chunk)]
    assert keys == [TEXT_BLOCK, ("tool", 0), ("tool", 1)]


def test_a_call_with_no_index_falls_back_to_zero():
    [(key, _)] = split_chunk_into_blocks(
        AIMessageChunk(content="", tool_call_chunks=[{"name": "A", "args": "{}", "id": "c", "index": None}])
    )
    assert key == ("tool", 0)


def test_a_non_dict_tool_chunk_entry_is_ignored():
    chunk = AIMessageChunk(content="x")
    object.__setattr__(chunk, "tool_call_chunks", ["junk"])
    assert split_chunk_into_blocks(chunk) == [(TEXT_BLOCK, chunk)]


# ── framing ─────────────────────────────────────────────────────────────────

def test_an_empty_stream_produces_nothing():
    assert frame([]) == []


def test_text_arrives_as_one_message_at_the_end_of_its_block():
    out = frame([AIMessageChunk(content="a"), AIMessageChunk(content="b")])
    assert [m.content for m in out] == ["ab"]
    assert isinstance(out[0], AIMessage) and not isinstance(out[0], AIMessageChunk)


def test_a_tool_call_is_only_emitted_once_its_arguments_are_complete():
    out = frame([
        call_chunk(0, "WriteFile", "", "c1"),
        call_chunk(0, None, '{"file_path": "a.py"'),
        call_chunk(0, None, ', "content": "x"}'),
    ])
    assert len(out) == 1
    assert tool_calls_in(out[0]) == (
        {"name": "WriteFile", "args": {"file_path": "a.py", "content": "x"},
         "id": "c1", "type": "tool_call"},
    )


def test_the_first_call_closes_when_the_second_opens():
    out = frame([
        call_chunk(0, "A", '{"x": 1}', "c1"),
        call_chunk(1, "B", '{"y": 2}', "c2"),
    ])
    assert [tool_calls_in(m)[0]["id"] for m in out] == ["c1", "c2"]
    assert [tool_calls_in(m)[0]["args"] for m in out] == [{"x": 1}, {"y": 2}]


def test_text_then_a_call_is_two_messages_in_order():
    out = frame([AIMessageChunk(content="I will read it."), call_chunk(0, "ReadFile", '{"p": "a"}', "c1")])
    assert [m.content for m in out] == ["I will read it.", ""]
    assert tool_calls_in(out[0]) == ()
    assert tool_calls_in(out[1])[0]["id"] == "c1"


def test_a_whole_message_mid_stream_flushes_the_open_block_first():
    whole = AIMessage(content="whole")
    out = frame([AIMessageChunk(content="part-1"), whole, AIMessageChunk(content="part-2")])
    assert [m.content for m in out] == ["part-1", "whole", "part-2"]
    assert out[1] is whole


def test_a_non_message_item_is_ignored():
    out = frame(["noise", AIMessageChunk(content="x"), 42, None])
    assert [m.content for m in out] == ["x"]


def test_a_human_message_in_the_stream_passes_through():
    human = HumanMessage(content="interjection")
    assert frame([human]) == [human]


def test_metadata_only_chunks_attach_to_the_open_block():
    out = frame([
        AIMessageChunk(content="answer"),
        AIMessageChunk(content="", response_metadata={"finish_reason": "stop"}),
    ])
    assert len(out) == 1
    assert out[0].response_metadata["finish_reason"] == "stop"


def test_metadata_before_any_block_opens_nothing():
    out = frame([AIMessageChunk(content="", response_metadata={"finish_reason": "stop"})])
    assert out == []


def test_metadata_after_a_tool_block_does_not_reopen_text():
    out = frame([
        call_chunk(0, "A", '{"x": 1}', "c1"),
        AIMessageChunk(content="", response_metadata={"finish_reason": "tool_calls"}),
    ])
    assert len(out) == 1
    assert tool_calls_in(out[0])[0]["id"] == "c1"


# ── the accumulator directly ────────────────────────────────────────────────

def test_flush_on_a_fresh_accumulator_yields_nothing():
    assert list(BlockAccumulator().flush()) == []


def test_flush_is_idempotent():
    acc = BlockAccumulator()
    list(acc.add(AIMessageChunk(content="x")))
    assert [m.content for m in acc.flush()] == ["x"]
    assert list(acc.flush()) == []
