"""
Tests for Part E7 & E8: Compaction and post-compact cleanup (app/loop/context/compact.py, cleanup.py).
"""

import asyncio
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.loop.config import build_run_config
from app.loop.context import AppStateStore, tool_context_for
from app.loop.context.cache import SessionLatches
from app.loop.context.cleanup import invalidate_context_caches
from app.loop.context.compact import (
    apply_tool_result_budget,
    compact_conversation,
    microcompact_tool_results,
    try_reactive_compact,
)
from app.loop.context.slicing import validate_api_invariants
from app.loop.prompts.sections import PromptSectionCache
from app.loop.state import initial_loop_state


def test_apply_tool_result_budget() -> None:
    big_content = "X" * 20_000
    m1 = HumanMessage(content="run")
    m2 = AIMessage(content="", tool_calls=[{"id": "c1", "name": "Bash", "args": {}}])
    m3 = ToolMessage(content=big_content, tool_call_id="c1", name="Bash", status="success")

    messages = (m1, m2, m3)
    budgeted = apply_tool_result_budget(messages, max_chars_per_result=5_000)

    assert len(budgeted) == 3
    tool_msg = budgeted[2]
    assert isinstance(tool_msg, ToolMessage)
    expected_content = ("X" * 5_000) + "\n\n... [Tool result truncated: was 20000 chars, capped to 5000]"
    assert tool_msg.content == expected_content
    assert tool_msg.name == "Bash"
    assert tool_msg.status == "success"
    assert tool_msg.tool_call_id == "c1"

    # Tool message within budget is NOT truncated
    short_msg = ToolMessage(content="short content", tool_call_id="c1")
    not_budgeted = apply_tool_result_budget((short_msg,), max_chars_per_result=5_000)
    assert not_budgeted[0].content == "short content"

    # Exact boundary (len == 5000) is NOT truncated
    exact_msg = ToolMessage(content="X" * 5_000, tool_call_id="c1")
    exact_budgeted = apply_tool_result_budget((exact_msg,), max_chars_per_result=5_000)
    assert exact_budgeted[0].content == "X" * 5_000

    # Missing tool_call_id defaults to empty string
    no_id_msg = ToolMessage(content="X" * 20, tool_call_id="", name="T", status="error")
    budgeted_no_id = apply_tool_result_budget((no_id_msg,), max_chars_per_result=5)
    assert isinstance(budgeted_no_id[0], ToolMessage)
    assert budgeted_no_id[0].tool_call_id == ""
    assert budgeted_no_id[0].status == "error"
    assert budgeted_no_id[0].name == "T"

    # Non-string or None content
    from typing import Any, cast
    tm_none = ToolMessage(content=cast(Any, None), tool_call_id="c1")
    res_none = apply_tool_result_budget((tm_none,), max_chars_per_result=10)
    assert res_none[0].content == "None"


def test_microcompact_tool_results() -> None:
    # Older tool result should be compressed, recent one preserved
    m1 = HumanMessage(content="turn 1")
    m2 = AIMessage(content="", tool_calls=[{"id": "c1", "name": "Bash", "args": {}}])
    m3 = ToolMessage(content="old verbose output " * 50, tool_call_id="c1", name="Bash", status="error")
    m4 = AIMessage(content="turn 1 finished")

    m5 = HumanMessage(content="turn 2")
    m6 = AIMessage(content="", tool_calls=[{"id": "c2", "name": "Bash", "args": {}}])
    m7 = ToolMessage(content="recent output", tool_call_id="c2")

    messages = (m1, m2, m3, m4, m5, m6, m7)
    microcompacted = microcompact_tool_results(messages, keep_recent_turns=1)

    expected_notice = "[Tool result microcompacted: 950 chars output retained in earlier turn]"
    assert isinstance(microcompacted[2], ToolMessage)
    assert microcompacted[2].content == expected_notice
    assert microcompacted[2].name == "Bash"
    assert microcompacted[2].status == "error"
    assert microcompacted[2].tool_call_id == "c1"
    assert microcompacted[6].content == "recent output"

    # Tool message of exactly 150 chars (min_chars_to_compact) is NOT microcompacted
    m3_150 = ToolMessage(content="Y" * 150, tool_call_id="c1")
    res_150 = microcompact_tool_results((m1, m2, m3_150, m4, m5, m6, m7), keep_recent_turns=1)
    assert res_150[2].content == "Y" * 150

    # Tool message of 151 chars IS microcompacted

    m3_151 = ToolMessage(content="Y" * 151, tool_call_id="c1")
    res_151 = microcompact_tool_results((m1, m2, m3_151, m4, m5, m6, m7), keep_recent_turns=1)
    assert res_151[2].content == "[Tool result microcompacted: 151 chars output retained in earlier turn]"

    # Default parameters: keep_recent_turns=2, min_chars_to_compact=150
    # Turn 1 tool result (151 chars) is microcompacted, Turn 2 tool result (151 chars) is retained
    m8 = HumanMessage(content="turn 3")
    m9 = AIMessage(content="turn 3 done")
    m7_151 = ToolMessage(content="Z" * 151, tool_call_id="c2")
    all_turns = (m1, m2, m3_151, m4, m5, m6, m7_151, m8, m9)
    res_default = microcompact_tool_results(all_turns)
    assert res_default[2].content == "[Tool result microcompacted: 151 chars output retained in earlier turn]"
    assert res_default[6].content == "Z" * 151

    # No user messages at all: cutoff_index=0, no microcompaction
    solo_tool = (ToolMessage(content="W" * 300, tool_call_id="c0"),)
    microcompacted_none = microcompact_tool_results(solo_tool)
    assert len(microcompacted_none) == 1
    assert microcompacted_none[0].content == "W" * 300

    # Single user turn with fewer turns than keep_recent_turns: cutoff_index=0
    single_turn = (HumanMessage(content="single"), ToolMessage(content="W" * 300, tool_call_id="c0"))
    res_single = microcompact_tool_results(single_turn, keep_recent_turns=2)
    assert res_single[1].content == "W" * 300


def test_compact_conversation_structure_and_invariants() -> None:

    m1 = HumanMessage(content="Old user task 1")
    m2 = AIMessage(content="Old response 1")
    m3 = HumanMessage(content="Old user task 2")
    m4 = AIMessage(content="Old response 2")
    m5 = HumanMessage(content="New user task")
    m6 = AIMessage(content="New response")

    messages = (m1, m2, m3, m4, m5, m6)
    summary_text = "Task was to build feature X. Decisions: used architecture Y."

    # Using default keep_tail_count=4: keeps m3, m4, m5, m6
    compacted_default = compact_conversation(messages, summary=summary_text)
    assert len(compacted_default) == 5  # summary + 4 tail messages
    notice = "(Conversation history before this point has been compacted to preserve context space.)"
    assert notice in compacted_default[0].content
    assert compacted_default[0].content == f"<summary>\n{summary_text.strip()}\n</summary>\n\n{notice}"

    # Explicit keep_tail_count=2
    compacted = compact_conversation(messages, summary=summary_text, keep_tail_count=2)
    assert len(compacted) == 3
    assert isinstance(compacted[0], HumanMessage)
    assert "<summary>" in compacted[0].content
    assert summary_text in compacted[0].content
    assert compacted[1].content == "New user task"
    assert compacted[2].content == "New response"

    # API invariants must strictly hold
    valid, err = validate_api_invariants(compacted)
    assert valid is True, err

    # With max_tokens and token_counter
    from app.loop.context.tokens import TokenCounter
    counter = TokenCounter()
    budget = counter.count_messages((m5, m6)) + 5
    compacted_tokens = compact_conversation(
        messages,
        summary=summary_text,
        keep_tail_count=6,
        max_tokens=budget,
        token_counter=counter,
    )
    assert len(compacted_tokens) == 3  # summary + 2 tail messages
    assert compacted_tokens[1].content == "New user task"


def test_try_reactive_compact() -> None:
    async def _run() -> None:
        store = AppStateStore()
        tc = tool_context_for(store)
        # Setup conversation with tool messages to verify microcompact and tool budget in try_reactive_compact
        m1 = HumanMessage(content="Old prompt")
        m2 = AIMessage(content="", tool_calls=[{"id": "c1", "name": "Bash", "args": {}}])
        m3 = ToolMessage(content="old tool output " * 20, tool_call_id="c1", name="Bash")
        m4 = AIMessage(content="Old answer")
        m5 = HumanMessage(content="Middle prompt")
        m6 = AIMessage(content="", tool_calls=[{"id": "c2", "name": "Bash", "args": {}}])
        m7 = ToolMessage(content="T" * 6_000, tool_call_id="c2", name="Bash")
        m8 = AIMessage(content="Middle answer")
        m9 = HumanMessage(content="Latest prompt")
        m10 = AIMessage(content="Latest answer")

        state = initial_loop_state((m1, m2, m3, m4, m5, m6, m7, m8, m9, m10), tc)
        config = build_run_config(run_id="test_run", postgres_checkpointing=False)

        captured_prompts: list[str] = []

        async def fake_summarize(prompt: str) -> str:
            captured_prompts.append(prompt)
            return "Summary of old work"

        from app.loop.context.tokens import TokenCounter
        counter = TokenCounter()

        # Attempt reactive compact
        res = await try_reactive_compact(state, config, summarize_fn=fake_summarize, token_counter=counter)
        assert res.compacted is True
        assert len(res.messages) == 3
        assert "<summary>" in res.messages[0].content
        assert res.messages[1].content == "Latest prompt"
        assert res.messages[2].content == "Latest answer"
        assert res.tracking is not None
        assert res.tracking.compacted is True
        assert res.tracking.turn_id == "reactive_turn_1"
        assert res.tracking.turn_counter == 1

        # Check prompt sent to summarize_fn
        assert len(captured_prompts) == 1
        assert captured_prompts[0] == (
            "Write a structured, concise continuation summary wrapped in <summary></summary> tags "
            "covering task overview, current progress, modified files, key technical decisions, and next steps."
        )

    asyncio.run(_run())


def test_notify_compaction_defaults_and_explicit() -> None:

    from app.loop.context.cleanup import notify_compaction

    t1 = notify_compaction()
    assert t1.compacted is True
    assert t1.turn_id == "compaction"
    assert t1.turn_counter == 0

    t2 = notify_compaction(turn_id="turn_5", turn_counter=5)
    assert t2.compacted is True
    assert t2.turn_id == "turn_5"
    assert t2.turn_counter == 5


def test_invalidate_context_caches_avoids_two_layer_trap() -> None:
    section_cache = PromptSectionCache()
    session_latches = SessionLatches()

    section_cache.set("identity", "cached identity")
    session_latches.latch("tool_pool_hash", "hash_abc")

    # Invalidate named point
    invalidate_context_caches(section_cache=section_cache, session_latches=session_latches)

    assert not section_cache.has("identity")
    assert not session_latches.has("tool_pool_hash")
    assert not session_latches.has("tool_pool_hash")
