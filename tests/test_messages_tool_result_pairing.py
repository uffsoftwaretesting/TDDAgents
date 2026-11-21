"""
Tool results reach the provider next to their tool calls, whatever the history holds between them.

A live run (2026-11-20) called `Skill` and `Bash` in one response. The stored history was
`AI(Skill, Bash)`, `Tool(Skill)`, the skill's meta `Human`, `Tool(Bash)`, so the provider got one user
message `[tool_result, text, tool_result]` and returned 400 ("`tool_use` ids were found without
`tool_result` blocks immediately after"). Upstream folds the user side into one message and hoists every
`tool_result` first (`reference/claude-code/src/utils/messages.ts` `normalizeMessagesForAPI`,
`mergeUserMessages`, `hoistToolResults`), then repairs any pairing still broken
(`ensureToolResultPairing`, run by `services/api/claude.ts`).
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.utils.messages import (
    SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
    TOOL_USE_INTERRUPTED,
    ORPHANED_TOOL_RESULT_REMOVED,
    ensure_tool_result_pairing,
    hoist_tool_results,
    normalize_messages_for_api,
)


def call(cid: str, name: str = "Bash") -> dict[str, Any]:
    return {"id": cid, "name": name, "args": {"command": cid}, "type": "tool_call"}


def kinds(messages: list[Any]) -> list[str]:
    out = []
    for m in messages:
        if isinstance(m, ToolMessage):
            out.append(f"Tool({m.tool_call_id})")
        elif isinstance(m, AIMessage):
            out.append("AI(" + ",".join(str(c["id"]) for c in m.tool_calls) + ")")
        else:
            out.append(f"Human({m.content})")
    return out


META = HumanMessage(content="skill prompt", additional_kwargs={"is_meta": True})


# ── the failed run's shape ──────────────────────────────────────────────────────


def live_run_history() -> list[Any]:
    """The transcript order of loop-ab2be2cd…: one response, split into one message per block."""
    return [
        HumanMessage(content="go"),
        AIMessage(content="", tool_calls=[call("s", "Skill")], id="r1"),
        AIMessage(content="", tool_calls=[call("b")], id="r1"),
        ToolMessage(content="Launching skill: tdd-project-config", tool_call_id="s"),
        META,
        ToolMessage(content="Exit code 1", tool_call_id="b"),
    ]


def test_the_failed_run_reaches_the_provider_with_both_results_first() -> None:
    assert kinds(normalize_messages_for_api(live_run_history())) == [
        "Human(go)", "AI(s,b)", "Tool(s)", "Tool(b)", "Human(skill prompt)"]


def test_the_anthropic_payload_has_both_tool_results_before_the_text() -> None:
    from langchain_anthropic.chat_models import _format_messages

    _system, payload = _format_messages(normalize_messages_for_api(live_run_history()))
    assistant, user = payload[1], payload[2]
    assert [b["type"] for b in assistant["content"] if b["type"] == "tool_use"] == ["tool_use", "tool_use"]
    types = [b["type"] for b in user["content"]]
    assert types[:2] == ["tool_result", "tool_result"]
    assert "text" in types[2:]


def test_without_the_fix_the_payload_is_the_rejected_one() -> None:
    """Guards the test above: the stored order really is the one the provider rejects."""
    from langchain_anthropic.chat_models import _format_messages

    from app.utils.messages import merge_split_assistant_messages

    _system, payload = _format_messages(merge_split_assistant_messages(live_run_history()))
    assert [b["type"] for b in payload[2]["content"]] == ["tool_result", "text", "tool_result"]


def test_the_stored_history_is_never_changed() -> None:
    history = live_run_history()
    snapshot = kinds(history)
    normalize_messages_for_api(history)
    assert kinds(history) == snapshot


# ── hoist_tool_results ──────────────────────────────────────────────────────────


def test_results_move_before_the_messages_between_them_in_their_order() -> None:
    history = [
        AIMessage(content="", tool_calls=[call("a"), call("b"), call("c")]),
        ToolMessage(content="A", tool_call_id="a"),
        META,
        ToolMessage(content="B", tool_call_id="b"),
        HumanMessage(content="attachment", additional_kwargs={"is_meta": True}),
        ToolMessage(content="C", tool_call_id="c"),
    ]
    assert kinds(hoist_tool_results(history)) == [
        "AI(a,b,c)", "Tool(a)", "Tool(b)", "Tool(c)", "Human(skill prompt)", "Human(attachment)"]


def test_a_meta_message_after_the_last_result_stays_put() -> None:
    history = [AIMessage(content="", tool_calls=[call("a")]), ToolMessage(content="A", tool_call_id="a"), META]
    assert kinds(hoist_tool_results(history)) == ["AI(a)", "Tool(a)", "Human(skill prompt)"]


def test_the_window_ends_at_the_next_assistant() -> None:
    history = [
        AIMessage(content="", tool_calls=[call("a")]),
        META,
        ToolMessage(content="A", tool_call_id="a"),
        AIMessage(content="", tool_calls=[call("b")]),
        ToolMessage(content="B", tool_call_id="b"),
    ]
    assert kinds(hoist_tool_results(history)) == [
        "AI(a)", "Tool(a)", "Human(skill prompt)", "AI(b)", "Tool(b)"]


def test_an_assistant_without_tool_calls_and_plain_turns_are_untouched() -> None:
    history = [HumanMessage(content="q"), AIMessage(content="answer"), HumanMessage(content="next")]
    assert kinds(hoist_tool_results(history)) == kinds(history)


def test_a_result_for_another_call_is_not_pulled_forward() -> None:
    history = [AIMessage(content="", tool_calls=[call("a")]), META, ToolMessage(content="X", tool_call_id="x")]
    assert kinds(hoist_tool_results(history)) == ["AI(a)", "Human(skill prompt)", "Tool(x)"]


# ── ensure_tool_result_pairing ──────────────────────────────────────────────────


def test_a_valid_history_is_returned_as_is() -> None:
    history = [HumanMessage(content="go"), AIMessage(content="", tool_calls=[call("a")]),
               ToolMessage(content="A", tool_call_id="a")]
    assert ensure_tool_result_pairing(history) == history


def test_a_missing_result_gets_a_synthetic_error_result_first() -> None:
    history = [AIMessage(content="", tool_calls=[call("a"), call("b")]), ToolMessage(content="B", tool_call_id="b")]
    repaired = ensure_tool_result_pairing(history)
    assert kinds(repaired) == ["AI(a,b)", "Tool(a)", "Tool(b)"]
    synthetic = repaired[1]
    assert isinstance(synthetic, ToolMessage)
    assert synthetic.content == SYNTHETIC_TOOL_RESULT_PLACEHOLDER
    assert synthetic.status == "error"


def test_an_assistant_with_no_following_results_gets_them_all() -> None:
    history = [AIMessage(content="", tool_calls=[call("a")]), HumanMessage(content="next")]
    assert kinds(ensure_tool_result_pairing(history)) == ["AI(a)", "Tool(a)", "Human(next)"]


def test_an_assistant_at_the_end_gets_its_results() -> None:
    assert kinds(ensure_tool_result_pairing([AIMessage(content="", tool_calls=[call("a")])])) == ["AI(a)", "Tool(a)"]


def test_an_orphaned_result_after_an_assistant_is_dropped() -> None:
    history = [AIMessage(content="", tool_calls=[call("a")]), ToolMessage(content="A", tool_call_id="a"),
               ToolMessage(content="Z", tool_call_id="z")]
    assert kinds(ensure_tool_result_pairing(history)) == ["AI(a)", "Tool(a)"]


def test_a_duplicate_result_is_dropped() -> None:
    history = [AIMessage(content="", tool_calls=[call("a")]), ToolMessage(content="A", tool_call_id="a"),
               ToolMessage(content="A again", tool_call_id="a")]
    repaired = ensure_tool_result_pairing(history)
    assert kinds(repaired) == ["AI(a)", "Tool(a)"]
    assert repaired[1].content == "A"


def test_a_result_with_no_assistant_before_it_is_dropped() -> None:
    history = [HumanMessage(content="go"), ToolMessage(content="Z", tool_call_id="z"), HumanMessage(content="more")]
    assert kinds(ensure_tool_result_pairing(history)) == ["Human(go)", "Human(more)"]


def test_a_history_that_starts_with_an_orphaned_result_keeps_a_user_first() -> None:
    history = [ToolMessage(content="Z", tool_call_id="z"), AIMessage(content="hi")]
    repaired = ensure_tool_result_pairing(history)
    assert isinstance(repaired[0], HumanMessage)
    assert repaired[0].content == ORPHANED_TOOL_RESULT_REMOVED
    assert isinstance(repaired[1], AIMessage)


def test_a_tool_call_repeated_by_a_later_assistant_is_dropped() -> None:
    history = [
        AIMessage(content="", tool_calls=[call("a")], id="r1"),
        ToolMessage(content="A", tool_call_id="a"),
        AIMessage(content=[{"type": "text", "text": "again"},
                           {"type": "tool_use", "id": "a", "name": "Bash", "input": {}}],
                  tool_calls=[call("a")], id="r2"),
        ToolMessage(content="A2", tool_call_id="a"),
    ]
    repaired = ensure_tool_result_pairing(history)
    assert kinds(repaired) == ["AI(a)", "Tool(a)", "AI()"]
    assert repaired[2].content == [{"type": "text", "text": "again"}]


def test_an_assistant_left_empty_says_its_tool_use_was_interrupted() -> None:
    history = [
        AIMessage(content="", tool_calls=[call("a")]), ToolMessage(content="A", tool_call_id="a"),
        AIMessage(content="", tool_calls=[call("a")]),
    ]
    repaired = ensure_tool_result_pairing(history)
    interrupted = repaired[2]
    assert isinstance(interrupted, AIMessage)
    assert interrupted.content == TOOL_USE_INTERRUPTED
    assert interrupted.tool_calls == []


def test_system_messages_pass_through() -> None:
    history = [SystemMessage(content="s"), HumanMessage(content="go")]
    assert ensure_tool_result_pairing(history) == history


# ── the request and /context ────────────────────────────────────────────────────


def test_the_request_and_the_context_analysis_share_the_normalization() -> None:
    from app.utils.analyze_context import normalize_messages_for_api as analysis_view

    assert kinds(analysis_view(live_run_history())) == kinds(normalize_messages_for_api(live_run_history()))


def test_the_context_analysis_counts_without_the_pairing_repair() -> None:
    """`analyzeContext.ts` counts `normalizeMessagesForAPI` alone; only the request repairs the pairing."""
    from app.utils.analyze_context import normalize_messages_for_api as analysis_view

    orphan = [HumanMessage(content="go"), ToolMessage(content="Z", tool_call_id="z")]
    assert kinds(analysis_view(orphan)) == ["Human(go)", "Tool(z)"]


def test_the_request_is_normalized_then_repaired(monkeypatch: Any) -> None:
    from types import SimpleNamespace

    import app.services.api.claude as claude

    monkeypatch.setattr(claude, "request_system_texts", lambda state, config: ["system"])
    monkeypatch.setattr(claude, "request_user_context", lambda state, config: {})
    history = (*live_run_history(), AIMessage(content="", tool_calls=[call("late")]))
    state: Any = SimpleNamespace(messages=history)
    request = claude.build_request_messages(state, SimpleNamespace())  # type: ignore[arg-type]
    assert kinds(request[1:]) == [
        "Human(go)", "AI(s,b)", "Tool(s)", "Tool(b)", "Human(skill prompt)", "AI(late)", "Tool(late)"]


# ── more edge cases ─────────────────────────────────────────────────────────────


def test_two_batches_each_keep_their_results_next_to_their_calls() -> None:
    history = [
        HumanMessage(content="go"),
        AIMessage(content="", tool_calls=[call("a"), call("b")], id="r1"),
        ToolMessage(content="A", tool_call_id="a"), META, ToolMessage(content="B", tool_call_id="b"),
        AIMessage(content="", tool_calls=[call("c"), call("d")], id="r2"),
        ToolMessage(content="C", tool_call_id="c"),
        HumanMessage(content="attachment", additional_kwargs={"is_meta": True}),
        ToolMessage(content="D", tool_call_id="d"),
    ]
    assert kinds(normalize_messages_for_api(history)) == [
        "Human(go)", "AI(a,b)", "Tool(a)", "Tool(b)", "Human(skill prompt)",
        "AI(c,d)", "Tool(c)", "Tool(d)", "Human(attachment)"]


def test_a_result_answering_an_earlier_turn_is_dropped_by_the_repair() -> None:
    history = [
        AIMessage(content="", tool_calls=[call("a")]), ToolMessage(content="A", tool_call_id="a"),
        AIMessage(content="", tool_calls=[call("b")]), ToolMessage(content="late A", tool_call_id="a"),
        ToolMessage(content="B", tool_call_id="b"),
    ]
    repaired = ensure_tool_result_pairing(normalize_messages_for_api(history))
    assert kinds(repaired) == ["AI(a)", "Tool(a)", "AI(b)", "Tool(b)"]


def test_a_duplicate_id_inside_one_assistant_keeps_the_first_call() -> None:
    history = [AIMessage(content="", tool_calls=[call("a"), call("a")]), ToolMessage(content="A", tool_call_id="a")]
    repaired = ensure_tool_result_pairing(history)
    assert kinds(repaired) == ["AI(a)", "Tool(a)"]


def test_a_tool_use_block_without_a_tool_call_entry_is_left_to_the_adapter() -> None:
    """Only `tool_calls` declare calls here; a stray content block is not invented a result."""
    block = {"type": "tool_use", "id": "x", "name": "Bash", "input": {}}
    history = [AIMessage(content=[block]), HumanMessage(content="next")]
    assert kinds(ensure_tool_result_pairing(history)) == ["AI()", "Human(next)"]


def test_a_signal_attachment_and_a_compact_boundary_around_a_batch() -> None:
    from app.services.compact.compact import is_compact_boundary_message
    from app.utils.messages import ATTACHMENT_KEY, is_signal_attachment_message

    signal = HumanMessage(content="", additional_kwargs={"is_meta": True, ATTACHMENT_KEY: {"type": "max_turns"}})
    history = [AIMessage(content="", tool_calls=[call("a"), call("b")]), ToolMessage(content="A", tool_call_id="a"),
               signal, ToolMessage(content="B", tool_call_id="b")]
    assert is_signal_attachment_message(signal) and not is_compact_boundary_message(signal)
    assert kinds(normalize_messages_for_api(history))[:3] == ["AI(a,b)", "Tool(a)", "Tool(b)"]


def test_the_openai_media_move_keeps_results_adjacent_after_the_hoist() -> None:
    from app.utils.messages import move_tool_result_media_to_user

    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    history = [AIMessage(content="", tool_calls=[call("a"), call("b")]),
               ToolMessage(content=[{"type": "text", "text": "pic"}, image], tool_call_id="a"),
               META, ToolMessage(content="B", tool_call_id="b")]
    moved = move_tool_result_media_to_user(ensure_tool_result_pairing(normalize_messages_for_api(history)))
    assert [type(m).__name__ for m in moved[:3]] == ["AIMessage", "ToolMessage", "ToolMessage"]
    assert isinstance(moved[3], HumanMessage) and moved[3].content == [image]


def test_an_empty_history_and_a_history_of_text() -> None:
    assert normalize_messages_for_api([]) == [] and ensure_tool_result_pairing([]) == []
    texts = [HumanMessage(content="a"), AIMessage(content="b")]
    assert ensure_tool_result_pairing(normalize_messages_for_api(texts)) == texts


# ── integration: a real session where the model calls Skill and Bash together ───


def test_a_session_with_a_parallel_skill_and_bash_sends_a_valid_request(python_workspace: Any, tmp_path: Any) -> None:
    """The live run's shape, end to end: the second request is valid for the Anthropic API."""
    import asyncio

    from langchain_anthropic.chat_models import _format_messages

    from app.services.api.claude import build_request_messages
    from app.tdd.plan import TddPlan
    from tests.test_repl import Model, session, until

    both = AIMessage(content="", tool_calls=[
        {"name": "Skill", "args": {"skill": "tdd-project-config"}, "id": "s1", "type": "tool_call"},
        {"name": "Bash", "args": {"command": "ls"}, "id": "b1", "type": "tool_call"}], id="resp-1")
    model = Model([[both], [AIMessage(content="done")]])
    seen: dict[str, Any] = {}

    async def script(repl: Any, io: Any) -> None:
        await until(lambda: len(model.seen) >= 2)
        seen["request"] = build_request_messages(model.seen[1], repl.run.config)
        await until(lambda: not repl.busy)
        io.lines.put_nowait("/exit")

    asyncio.run(session(python_workspace, tmp_path, model, TddPlan(), script))
    stored = [m for m in model.seen[1].messages if isinstance(m, (AIMessage, ToolMessage)) or
              (isinstance(m, HumanMessage) and m.additional_kwargs.get("is_meta"))]
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "s1" for m in stored)
    _system, payload = _format_messages(seen["request"])
    for index, message in enumerate(payload):
        if message["role"] != "assistant" or not isinstance(message["content"], list):
            continue
        uses = [b["id"] for b in message["content"] if b.get("type") == "tool_use"]
        if not uses:
            continue
        following = payload[index + 1]["content"]
        results = [b for b in following if isinstance(b, dict) and b.get("type") == "tool_result"]
        assert [b["tool_use_id"] for b in results] == uses
        assert following[:len(uses)] == results  # every result before any text


# ── resume: a transcript stored in the broken order ─────────────────────────────


def test_a_transcript_stored_in_the_broken_order_resumes_into_a_valid_request(tmp_path: Any) -> None:
    from langchain_anthropic.chat_models import _format_messages

    from app.utils.conversation_recovery import load_conversation_for_resume
    from app.utils.session_storage import TranscriptStore

    store = TranscriptStore("loop-resume-broken", str(tmp_path))
    store.record_transcript(live_run_history())
    resumed = load_conversation_for_resume(store.path)
    assert resumed is not None
    loaded = list(resumed.messages)
    request = ensure_tool_result_pairing(normalize_messages_for_api(loaded))
    _system, payload = _format_messages(request)
    uses = [b["id"] for b in payload[1]["content"] if b.get("type") == "tool_use"]
    blocks = payload[2]["content"]
    assert sorted(uses) == ["b", "s"]
    assert sorted(b["tool_use_id"] for b in blocks[:2]) == ["b", "s"]  # both results first, any order
    assert all(b["type"] == "tool_result" for b in blocks[:2])
    assert all(b["type"] != "tool_result" for b in blocks[2:])
