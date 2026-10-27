"""Phase 6: resume (`conversationRecovery.ts` -> `deserializeMessagesWithInterruptDetection`,
`detectTurnInterruption`, `loadConversationForResume`; `messages.ts` -> the three filters)."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.utils.conversation_recovery import (
    CONTINUATION_MESSAGE,
    NO_RESPONSE_REQUESTED,
    DeserializeResult,
    TurnInterruptionState,
    deserialize_messages,
    deserialize_messages_with_interrupt_detection,
    filter_orphaned_thinking_only_messages,
    filter_unresolved_tool_uses,
    filter_whitespace_only_assistant_messages,
    load_conversation_for_resume,
)
from app.utils.session_storage import EntryStamp, build_entry


def call(cid: str, mid: str | None = None, content: str | list = "") -> AIMessage:
    return AIMessage(content=content, id=mid, tool_calls=[{"name": "ReadFile", "args": {}, "id": cid}])


def result(cid: str) -> ToolMessage:
    return ToolMessage(content="r", tool_call_id=cid)


THINK = {"type": "thinking", "thinking": "hmm", "signature": "s"}
REDACTED = {"type": "redacted_thinking", "data": "x"}


def test_the_ported_constants_are_upstreams():
    assert NO_RESPONSE_REQUESTED == "No response requested."
    assert CONTINUATION_MESSAGE == "Continue from where you left off."


class TestFilterUnresolvedToolUses:
    def test_nothing_unresolved_returns_the_same_list(self):
        messages = [HumanMessage(content="t"), call("c1"), result("c1")]
        assert filter_unresolved_tool_uses(messages) is messages

    def test_an_assistant_whose_calls_all_went_unanswered_is_dropped(self):
        h, a, r, dangling = HumanMessage(content="t"), call("c1"), result("c1"), call("c2")
        assert filter_unresolved_tool_uses([h, a, r, dangling]) == [h, a, r]

    def test_a_partly_answered_assistant_is_kept(self):
        a = AIMessage(content="", tool_calls=[
            {"name": "X", "args": {}, "id": "c1"}, {"name": "X", "args": {}, "id": "c2"}])
        messages = [a, result("c1")]
        assert filter_unresolved_tool_uses(messages) == messages

    def test_messages_without_tool_calls_survive(self):
        text, sysm, dangling = AIMessage(content="hi"), SystemMessage(content="s"), call("c9")
        assert filter_unresolved_tool_uses([text, sysm, dangling]) == [text, sysm]

    def test_tool_use_blocks_in_list_content_count(self):
        """Anthropic's list content carries the `tool_use` block itself."""
        block = {"type": "tool_use", "id": "c1", "name": "X", "input": {}}
        a = AIMessage(content=[block])
        assert filter_unresolved_tool_uses([a]) == []
        assert filter_unresolved_tool_uses([a, result("c1")]) == [a, result("c1")]


class TestFilterOrphanedThinkingOnly:
    def test_a_thinking_only_message_with_no_partner_is_dropped(self):
        orphan = AIMessage(content=[THINK, REDACTED], id="m1")
        keep = AIMessage(content="answer", id="m2")
        assert filter_orphaned_thinking_only_messages([orphan, keep]) == [keep]

    def test_a_thinking_block_whose_response_has_content_is_kept(self):
        thinking = AIMessage(content=[THINK], id="m1")
        tool = call("c1", mid="m1")
        assert filter_orphaned_thinking_only_messages([thinking, tool]) == [thinking, tool]

    def test_a_thinking_message_without_an_id_is_orphaned(self):
        thinking, text = AIMessage(content=[THINK]), AIMessage(content="x")
        assert filter_orphaned_thinking_only_messages([thinking, text]) == [text]

    def test_a_partner_without_an_id_does_not_rescue(self):
        thinking = AIMessage(content=[THINK], id="m1")
        assert filter_orphaned_thinking_only_messages([thinking, AIMessage(content="x")]) == [AIMessage(content="x")]

    def test_mixed_and_empty_and_non_assistant_messages_are_kept(self):
        mixed = AIMessage(content=[THINK, {"type": "text", "text": "a"}], id="m1")
        empty = AIMessage(content=[], id="m2")
        human = HumanMessage(content=[THINK])
        assert filter_orphaned_thinking_only_messages([mixed, empty, human]) == [mixed, empty, human]

    def test_string_content_is_not_thinking(self):
        a = AIMessage(content="plain", id="m1")
        assert filter_orphaned_thinking_only_messages([a]) == [a]


class TestFilterWhitespaceOnlyAssistant:
    def test_nothing_to_remove_returns_the_same_list(self):
        messages = [HumanMessage(content="a"), AIMessage(content="b")]
        assert filter_whitespace_only_assistant_messages(messages) is messages

    def test_a_whitespace_answer_is_dropped(self):
        h, blank = HumanMessage(content="a"), AIMessage(content="\n\n")
        assert filter_whitespace_only_assistant_messages([h, blank]) == [h]

    def test_whitespace_text_blocks_are_dropped_but_any_other_block_keeps_it(self):
        blank = AIMessage(content=[{"type": "text", "text": " "}, " "])
        thinking = AIMessage(content=[{"type": "text", "text": " "}, THINK])
        with_call = call("c1", content=" ")
        assert filter_whitespace_only_assistant_messages([blank, thinking, with_call]) == [thinking, with_call]

    def test_empty_content_is_left_to_other_passes(self):
        empty = AIMessage(content="")
        empty_list = AIMessage(content=[])
        assert filter_whitespace_only_assistant_messages([empty, empty_list, AIMessage(content=" ")]) == [
            empty, empty_list]

    def test_users_left_adjacent_are_merged_at_a_text_seam(self):
        a = HumanMessage(content="2 + 2", additional_kwargs={"uuid": "ua"})
        b = HumanMessage(content=[{"type": "text", "text": "3 + 3"}], additional_kwargs={"uuid": "ub"})
        (merged,) = filter_whitespace_only_assistant_messages([a, AIMessage(content=" "), b])
        assert isinstance(merged, HumanMessage)
        assert merged.content == [{"type": "text", "text": "2 + 2\n"}, {"type": "text", "text": "3 + 3"}]
        assert merged.additional_kwargs["uuid"] == "ua"

    def test_a_merge_into_a_meta_message_keeps_the_real_messages_uuid(self):
        a = HumanMessage(content="ctx", additional_kwargs={"uuid": "ua", "is_meta": True})
        b = HumanMessage(content="real", additional_kwargs={"uuid": "ub"})
        (merged,) = filter_whitespace_only_assistant_messages([a, AIMessage(content=" "), b])
        assert merged.additional_kwargs == {"uuid": "ub", "is_meta": True}

    def test_a_non_text_seam_gets_no_newline(self):
        image = {"type": "image_url", "image_url": {"url": "data:x"}}
        a = HumanMessage(content=[image])
        b = HumanMessage(content="after")
        (merged,) = filter_whitespace_only_assistant_messages([a, AIMessage(content=" "), b])
        assert merged.content == [image, {"type": "text", "text": "after"}]

    def test_tool_results_and_attachments_are_not_merged(self):
        r = result("c1")
        attachment = HumanMessage(content="x", additional_kwargs={"attachment": {"type": "tdd_state"}})
        h = HumanMessage(content="h")
        out = filter_whitespace_only_assistant_messages([r, AIMessage(content=" "), h, attachment])
        assert out == [r, h, attachment]


class TestInterruptionDetection:
    def test_an_empty_conversation_is_not_interrupted(self):
        out = deserialize_messages_with_interrupt_detection([])
        assert out == DeserializeResult(messages=[], turn_interruption_state=TurnInterruptionState("none", None))

    def test_a_finished_turn_is_not_interrupted(self):
        h, a = HumanMessage(content="t"), AIMessage(content="done")
        out = deserialize_messages_with_interrupt_detection([h, a])
        assert out.turn_interruption_state.kind == "none"
        assert out.messages == [h, a]

    def test_an_unanswered_prompt_is_an_interrupted_prompt_with_a_sentinel(self):
        h = HumanMessage(content="t")
        out = deserialize_messages_with_interrupt_detection([h])
        assert out.turn_interruption_state == TurnInterruptionState("interrupted_prompt", h)
        assert out.messages[0] is h
        sentinel = out.messages[1]
        assert isinstance(sentinel, AIMessage) and sentinel.content == NO_RESPONSE_REQUESTED

    def test_a_turn_cut_after_a_tool_result_gets_a_continuation(self):
        h, a, r = HumanMessage(content="t"), call("c1"), result("c1")
        out = deserialize_messages_with_interrupt_detection([h, a, r])
        state = out.turn_interruption_state
        assert state.kind == "interrupted_prompt"
        cont = out.messages[3]
        assert state.message is cont
        assert isinstance(cont, HumanMessage)
        assert (cont.content, cont.additional_kwargs) == (CONTINUATION_MESSAGE, {"is_meta": True})
        assert out.messages[4].content == NO_RESPONSE_REQUESTED
        assert len(out.messages) == 5

    def test_an_unresolved_tool_call_is_dropped_and_the_turn_continues(self):
        h, a, r, dangling = HumanMessage(content="t"), call("c1"), result("c1"), call("c2")
        out = deserialize_messages_with_interrupt_detection([h, a, r, dangling])
        assert out.messages[:3] == [h, a, r]
        assert out.messages[3].content == CONTINUATION_MESSAGE

    def test_a_trailing_attachment_is_an_interrupted_turn(self):
        h = HumanMessage(content="t")
        att = HumanMessage(content="x", additional_kwargs={"is_meta": True, "attachment": {"type": "tdd_state"}})
        out = deserialize_messages_with_interrupt_detection([h, att])
        assert out.messages[2].content == CONTINUATION_MESSAGE
        assert out.turn_interruption_state.message is out.messages[2]

    def test_meta_and_summary_users_are_not_interruptions(self):
        for flag in ("is_meta", "is_compact_summary"):
            m = HumanMessage(content="x", additional_kwargs={flag: True})
            out = deserialize_messages_with_interrupt_detection([m])
            assert out.turn_interruption_state == TurnInterruptionState("none", None)
            # Still a trailing user message, so the sentinel keeps the history API-valid.
            assert out.messages[-1].content == NO_RESPONSE_REQUESTED

    def test_system_records_and_api_errors_do_not_mask_an_interruption(self):
        h = HumanMessage(content="t")
        err = AIMessage(content="API Error", additional_kwargs={"is_api_error_message": True})
        sysm = SystemMessage(content="boundary-ish")
        out = deserialize_messages_with_interrupt_detection([h, err, sysm])
        assert out.turn_interruption_state == TurnInterruptionState("interrupted_prompt", h)
        # The sentinel goes right after the last non-system message (the error assistant):
        # it is not a user message, so none is inserted.
        assert out.messages == [h, err, sysm]

    def test_the_sentinel_goes_before_trailing_system_records(self):
        h, sysm = HumanMessage(content="t"), SystemMessage(content="s")
        out = deserialize_messages_with_interrupt_detection([h, sysm])
        assert [type(m) for m in out.messages] == [HumanMessage, AIMessage, SystemMessage]

    def test_only_system_records_are_not_interrupted(self):
        sysm = SystemMessage(content="s")
        out = deserialize_messages_with_interrupt_detection([sysm])
        assert out == DeserializeResult(messages=[sysm], turn_interruption_state=TurnInterruptionState("none", None))

    def test_deserialize_messages_returns_only_the_messages(self):
        h = HumanMessage(content="t")
        assert [m.content for m in deserialize_messages([h])] == ["t", NO_RESPONSE_REQUESTED]

    def test_all_three_filters_run(self):
        h = HumanMessage(content="t")
        orphan_thinking = AIMessage(content=[THINK], id="m9")
        blank = AIMessage(content="  ")
        done = AIMessage(content="done")
        out = deserialize_messages_with_interrupt_detection([h, orphan_thinking, blank, done, call("c7")])
        assert out.messages == [h, done]


STAMP = EntryStamp(session_id="s1", cwd="/w", version="v", git_branch=None)


def _write(path: Path, *messages) -> Path:
    lines, parent = [], None
    for i, m in enumerate(messages):
        m.additional_kwargs["uuid"] = f"u{i}"
        lines.append(json.dumps(build_entry(m, parent, False, STAMP, f"2026-10-22T00:00:{i:02d}.000Z")))
        parent = f"u{i}"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_load_conversation_for_resume_reads_the_chain_and_detects_the_interruption(tmp_path):
    path = _write(tmp_path / "s.jsonl", HumanMessage(content="task"), call("c1"), result("c1"))
    out = load_conversation_for_resume(path)
    assert out is not None
    assert [m.content for m in out.messages][-2:] == [CONTINUATION_MESSAGE, NO_RESPONSE_REQUESTED]
    assert out.messages[0].additional_kwargs["uuid"] == "u0"
    assert out.turn_interruption_state.kind == "interrupted_prompt"


def test_a_completed_conversation_resumes_as_is(tmp_path):
    path = _write(tmp_path / "s.jsonl", HumanMessage(content="task"), AIMessage(content="done"))
    out = load_conversation_for_resume(str(path))
    assert out is not None
    assert [m.content for m in out.messages] == ["task", "done"]
    assert out.turn_interruption_state.kind == "none"


def test_a_missing_transcript_is_not_found(tmp_path):
    assert load_conversation_for_resume(tmp_path / "absent.jsonl") is None
    assert load_conversation_for_resume(tmp_path) is None


# ── Phase 6 gate: mutation triage ───────────────────────────────────────────


def test_string_blocks_in_list_content_are_text():
    kept = AIMessage(content=["real words"])
    assert filter_whitespace_only_assistant_messages([HumanMessage(content="q"), kept]) == [
        HumanMessage(content="q"), kept]


def test_a_text_block_without_text_is_whitespace():
    empty = AIMessage(content=[{"type": "text"}])
    assert filter_whitespace_only_assistant_messages([HumanMessage(content="q"), empty]) == [HumanMessage(content="q")]


def test_a_thinking_partner_is_found_past_a_non_assistant():
    text = AIMessage(content="answer", id="m1")
    thinking = AIMessage(content=[THINK], id="m1")
    msgs = [HumanMessage(content="q"), text, thinking]
    assert filter_orphaned_thinking_only_messages(msgs) == msgs


def test_merging_keeps_every_block_of_the_left_message():
    a = HumanMessage(content=[{"type": "text", "text": "one"}, {"type": "text", "text": "two"}])
    b = HumanMessage(content="three")
    gap = AIMessage(content="  ")
    (merged,) = filter_whitespace_only_assistant_messages([a, gap, b])
    assert merged.content == [{"type": "text", "text": "one"}, {"type": "text", "text": "two\n"},
                              {"type": "text", "text": "three"}]


def test_a_seam_block_without_text_gets_only_the_newline():
    a = HumanMessage(content=[{"type": "text"}])
    (merged,) = filter_whitespace_only_assistant_messages([a, AIMessage(content=" "), HumanMessage(content="b")])
    assert merged.content[0] == {"type": "text", "text": "\n"}


def test_trailing_system_messages_are_skipped_one_by_one():
    """The last turn-relevant message is the assistant, not an earlier prompt."""
    msgs = [HumanMessage(content="p0"), HumanMessage(content="p1"), AIMessage(content="ok"),
            SystemMessage(content="s")]
    assert deserialize_messages_with_interrupt_detection(msgs).turn_interruption_state == TurnInterruptionState(
        kind="none", message=None)
    short = [HumanMessage(content="p0"), AIMessage(content="ok"), SystemMessage(content="s")]
    assert deserialize_messages_with_interrupt_detection(short).turn_interruption_state.kind == "none"


def test_remove_interrupted_message_drops_it_and_its_sentinel():
    from langchain_core.messages import AIMessage, HumanMessage

    from app.utils.conversation_recovery import remove_interrupted_message

    first, prompt, sentinel, later = (HumanMessage(content="a"), HumanMessage(content="b"),
                                      AIMessage(content="No response requested."), HumanMessage(content="c"))
    assert remove_interrupted_message([first, prompt, sentinel, later], prompt) == [first, later]
    # Matched by identity: an equal message elsewhere is not the interrupted one.
    twin = HumanMessage(content="b")
    assert remove_interrupted_message([first, twin], prompt) == [first, twin]
    # At the end, only what is there is removed.
    assert remove_interrupted_message([first, prompt], prompt) == [first]
    original = [first, prompt, sentinel]
    remove_interrupted_message(original, prompt)
    assert original == [first, prompt, sentinel]
