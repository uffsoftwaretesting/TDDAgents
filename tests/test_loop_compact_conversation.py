"""Phase 5 F2: `compactConversation` and its helpers (`services/compact/compact.ts`)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.loop.compact import compact as cc
from app.loop.compact.compact import (
    ERROR_MESSAGE_INCOMPLETE_RESPONSE,
    ERROR_MESSAGE_NOT_ENOUGH_MESSAGES,
    ERROR_MESSAGE_PROMPT_TOO_LONG,
    ERROR_MESSAGE_USER_ABORT,
    MAX_PTL_RETRIES,
    POST_COMPACT_MAX_FILES_TO_RESTORE,
    POST_COMPACT_MAX_TOKENS_PER_FILE,
    POST_COMPACT_TOKEN_BUDGET,
    PROMPT_TOO_LONG_ERROR_MESSAGE,
    PTL_RETRY_MARKER,
    CompactionResult,
    build_post_compact_messages,
    compact_conversation,
    create_compact_boundary_message,
    create_post_compact_file_attachments,
    get_messages_after_compact_boundary,
    get_prompt_too_long_token_gap,
    is_compact_boundary_message,
    truncate_head_for_ptl_retry,
)
from app.loop.compact.grouping import group_messages_by_api_round
from app.loop.compact.prompt import get_compact_prompt
from app.loop.context import AppStateStore, tool_context_for
from app.loop.context.file_state import FileState
from tests.conftest import FakeWorkspace
from tests.hook_doubles import ScriptedDispatcher


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_constants():
    assert (POST_COMPACT_MAX_FILES_TO_RESTORE, POST_COMPACT_TOKEN_BUDGET, POST_COMPACT_MAX_TOKENS_PER_FILE) == (
        5, 50_000, 5_000)
    assert MAX_PTL_RETRIES == 3
    assert PTL_RETRY_MARKER == "[earlier conversation truncated for compaction retry]"
    assert ERROR_MESSAGE_NOT_ENOUGH_MESSAGES == "Not enough messages to compact."
    assert ERROR_MESSAGE_PROMPT_TOO_LONG == (
        "Conversation too long. Press esc twice to go up a few messages and try again.")
    assert ERROR_MESSAGE_USER_ABORT == "API Error: Request was aborted."
    assert ERROR_MESSAGE_INCOMPLETE_RESPONSE == (
        "Compaction interrupted · This may be due to network issues — please try again.")
    assert PROMPT_TOO_LONG_ERROR_MESSAGE == "Prompt is too long"


# ── boundary ──────────────────────────────────────────────────────────────────

def test_boundary_message_and_slicing():
    boundary = create_compact_boundary_message("auto", 1234)
    assert isinstance(boundary, SystemMessage)
    assert boundary.content == "Conversation compacted"
    assert boundary.additional_kwargs["compact_boundary"] == {"trigger": "auto", "preTokens": 1234}
    assert is_compact_boundary_message(boundary) is True
    assert is_compact_boundary_message(SystemMessage(content="Conversation compacted")) is False
    assert is_compact_boundary_message(HumanMessage(content="x")) is False
    old = [HumanMessage(content="a"), AIMessage(content="b")]
    second = create_compact_boundary_message("manual", 9)
    history = [*old, boundary, HumanMessage(content="s1"), second, HumanMessage(content="s2")]
    assert get_messages_after_compact_boundary(history) == [second, HumanMessage(content="s2")]
    assert get_messages_after_compact_boundary(old) == old


def test_build_post_compact_messages_order():
    boundary = create_compact_boundary_message("auto", 1)
    result = CompactionResult(
        boundary_marker=boundary,
        summary_messages=[HumanMessage(content="sum")],
        attachments=[HumanMessage(content="att")],
        hook_results=[HumanMessage(content="hook")],
        messages_to_keep=[HumanMessage(content="kept")],
    )
    assert [m.content for m in build_post_compact_messages(result)] == [
        "Conversation compacted", "sum", "kept", "att", "hook"]
    no_keep = CompactionResult(boundary_marker=boundary, summary_messages=[], attachments=[], hook_results=[])
    assert build_post_compact_messages(no_keep) == [boundary]


# ── PTL retry ─────────────────────────────────────────────────────────────────

def _round(i: int, size: int = 400) -> list[Any]:
    return [AIMessage(content="a" * size, id=f"m{i}"), HumanMessage(content="u" * size)]


def test_grouping_by_api_round():
    msgs = [HumanMessage(content="first"), AIMessage(content="x", id="1"), AIMessage(content="y", id="1"),
            HumanMessage(content="r"), AIMessage(content="z", id="2"), AIMessage(content="w")]
    groups = group_messages_by_api_round(msgs)
    # Upstream: an assistant with a new id closes the current group, the leading user turn too.
    assert [len(g) for g in groups] == [1, 3, 1, 1]
    assert group_messages_by_api_round([]) == []


def test_ptl_token_gap():
    ptl = AIMessage(content=f"{PROMPT_TOO_LONG_ERROR_MESSAGE}: 210000 tokens > 200000 maximum",
                    additional_kwargs={"is_api_error_message": True,
                                       "error_details": "prompt is too long: 210000 tokens > 200000 maximum"})
    assert get_prompt_too_long_token_gap(ptl) == 10_000
    assert get_prompt_too_long_token_gap(AIMessage(content=ptl.content, additional_kwargs={
        "is_api_error_message": True})) is None
    assert get_prompt_too_long_token_gap(AIMessage(content="other", additional_kwargs={
        "is_api_error_message": True, "error_details": "prompt is too long: 1 tokens > 2"})) is None
    assert get_prompt_too_long_token_gap(AIMessage(content=ptl.content, additional_kwargs={
        "error_details": "prompt is too long: 3 tokens > 2"})) is None
    assert get_prompt_too_long_token_gap(AIMessage(content=ptl.content, additional_kwargs={
        "is_api_error_message": True, "error_details": "prompt is too long: 2 tokens > 2"})) is None


def test_truncate_head_drops_a_fifth_without_a_gap():
    msgs = [HumanMessage(content="q")] + [m for i in range(10) for m in _round(i)]
    out = truncate_head_for_ptl_retry(msgs, AIMessage(content="err"))
    assert out is not None
    # 11 groups (the leading user message is its own): drop floor(11*0.2)=2.
    groups = group_messages_by_api_round(msgs)
    assert len(groups) == 11
    assert out[0] == HumanMessage(content=PTL_RETRY_MARKER, additional_kwargs={"is_meta": True})
    assert out[1:] == [m for g in groups[2:] for m in g]


def test_truncate_head_with_a_gap_and_marker_stripping():
    msgs = [HumanMessage(content="q")] + [m for i in range(4) for m in _round(i)]
    ptl = AIMessage(content="x", additional_kwargs={
        "is_api_error_message": True, "error_details": "prompt is too long: 250 tokens > 50"})
    ptl = AIMessage(content=f"{PROMPT_TOO_LONG_ERROR_MESSAGE}", additional_kwargs=ptl.additional_kwargs)
    out = truncate_head_for_ptl_retry(msgs, ptl)
    assert out is not None and out[0].content == PTL_RETRY_MARKER
    # A second retry strips the earlier marker before regrouping.
    again = truncate_head_for_ptl_retry(out, AIMessage(content="err"))
    assert again is not None and sum(1 for m in again if m.content == PTL_RETRY_MARKER) == 1
    assert truncate_head_for_ptl_retry([HumanMessage(content="only")], AIMessage(content="e")) is None


def test_truncate_head_keeps_a_user_first_tail_unmarked():
    msgs = [AIMessage(content="a", id="1"), HumanMessage(content="u1"), AIMessage(content="b", id="2"),
            HumanMessage(content="u2")]
    # Every group starts with an assistant message; dropping one leaves an assistant first.
    out = truncate_head_for_ptl_retry(msgs, AIMessage(content="e"))
    assert out == [HumanMessage(content=PTL_RETRY_MARKER, additional_kwargs={"is_meta": True}),
                   AIMessage(content="b", id="2"), HumanMessage(content="u2")]


# ── post-compact file restoration ─────────────────────────────────────────────

def _ctx(files: dict[str, str] | None = None, **kw: Any) -> Any:
    return tool_context_for(AppStateStore(), workspace=FakeWorkspace(files=files or {}), run_id="r1", **kw)


def test_files_are_restored_newest_first_up_to_the_cap():
    files = {f"f{i}.py": f"print({i})\n" for i in range(7)}
    ctx = _ctx(files)
    state = {name: FileState(content=body) for name, body in files.items()}
    out = _run(create_post_compact_file_attachments(state, ctx, POST_COMPACT_MAX_FILES_TO_RESTORE))
    calls = [m for m in out if isinstance(m, AIMessage)]
    results = [m for m in out if isinstance(m, ToolMessage)]
    assert [c.tool_calls[0]["args"] for c in calls] == [{"file_path": f"f{i}.py"} for i in (6, 5, 4, 3, 2)]
    assert all(c.tool_calls[0]["name"] == "ReadFile" for c in calls)
    assert [r.tool_call_id for r in results] == [c.tool_calls[0]["id"] for c in calls]
    assert results[0].content == "1\tprint(6)"
    # Restoring re-registers what was read, as upstream's FileReadTool.call does.
    assert set(ctx.read_file_state) == {f"f{i}.py" for i in (6, 5, 4, 3, 2)}


def test_memory_files_are_never_restored_and_missing_files_are_skipped():
    files = {"TDDAGENTS.md": "rules", ".tddagents/TDDAGENTS.md": "r2", "TDDAGENTS.local.md": "r3", "a.py": "x\n"}
    ctx = _ctx(files)
    state = {name: FileState(content=b) for name, b in files.items()}
    state["gone.py"] = FileState(content="old")
    out = _run(create_post_compact_file_attachments(state, ctx, 5))
    # Upstream excludes the memory files `getMemoryPath` names (the project and local files);
    # a nested `.tddagents/TDDAGENTS.md` is an ordinary file to it.
    assert [m.tool_calls[0]["args"]["file_path"] for m in out if isinstance(m, AIMessage)] == [
        "a.py", ".tddagents/TDDAGENTS.md"]


def test_a_file_over_the_per_file_budget_becomes_a_reference():
    big = "\n".join("x = 1" * 10 for _ in range(500))  # well over 5000 rough tokens
    ctx = _ctx({"big.py": big})
    out = _run(create_post_compact_file_attachments({"big.py": FileState(content=big)}, ctx, 5))
    (note,) = out
    assert isinstance(note, HumanMessage) and note.additional_kwargs["is_meta"] is True
    assert note.content == (
        "<system-reminder>\nNote: big.py was read before the last conversation was summarized, but the contents "
        "are too large to include. Use ReadFile tool if you need to access it.\n</system-reminder>")


def test_the_total_budget_drops_the_files_that_do_not_fit(monkeypatch):
    monkeypatch.setattr(cc, "POST_COMPACT_TOKEN_BUDGET", 60)
    files = {"a.py": "a" * 150, "b.py": "b" * 150}
    ctx = _ctx(files)
    out = _run(create_post_compact_file_attachments({k: FileState(content=v) for k, v in files.items()}, ctx, 5))
    assert [m.tool_calls[0]["args"]["file_path"] for m in out if isinstance(m, AIMessage)] == ["b.py"]


# ── compact_conversation ──────────────────────────────────────────────────────

class FakeSummarizer:
    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, messages, summary_request, context, *, max_output_tokens):
        self.calls.append({"messages": list(messages), "request": summary_request, "context": context,
                           "max_output_tokens": max_output_tokens})
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _history() -> list[Any]:
    return [HumanMessage(content="build the calculator"), AIMessage(content="ok", id="a1",
            usage_metadata={"input_tokens": 900, "output_tokens": 100, "total_tokens": 1000})]


def test_compact_conversation_happy_path():
    summarizer = FakeSummarizer(AIMessage(content="<analysis>a</analysis><summary>did things</summary>",
                                          usage_metadata={"input_tokens": 50, "output_tokens": 7,
                                                          "total_tokens": 57}))
    ctx = _ctx({"calc.py": "x = 1\n"})
    ctx.read_file_state["calc.py"] = FileState(content="x = 1\n")
    injected = [HumanMessage(content="tdd state", additional_kwargs={"is_meta": True})]
    seen_ctx: list[Any] = []

    def attachments(c: Any) -> list[Any]:
        seen_ctx.append(c)
        return injected

    result = _run(compact_conversation(_history(), ctx, summarizer=summarizer, trigger="auto",
                                       post_compact_attachments=attachments, model="o4-mini",
                                       transcript_path="/t.jsonl"))
    call = summarizer.calls[0]
    assert call["messages"] == _history()
    assert call["request"] == HumanMessage(content=get_compact_prompt())
    assert call["max_output_tokens"] == 20_000 and call["context"] is ctx
    assert result.boundary_marker.additional_kwargs["compact_boundary"] == {"trigger": "auto", "preTokens": 1000}
    (summary,) = result.summary_messages
    assert summary.content.startswith("This session is being continued from a previous conversation")
    assert "Summary:\ndid things" in summary.content
    assert "read the full transcript at: /t.jsonl" in summary.content
    assert summary.content.endswith("Pick up the last task as if the break never happened.")
    assert summary.additional_kwargs == {"is_compact_summary": True}
    # Files first, then the injected (TDD state) attachments, read after the cache was cleared.
    assert isinstance(result.attachments[0], AIMessage)
    assert result.attachments[-1] is injected[0]
    assert seen_ctx == [ctx]
    assert result.hook_results == []
    assert result.messages_to_keep is None
    assert (result.pre_compact_token_count, result.post_compact_token_count) == (1000, 57)
    assert result.true_post_compact_token_count is not None and result.true_post_compact_token_count > 0


def test_the_follow_up_suffix_is_optional():
    summarizer = FakeSummarizer(AIMessage(content="plain summary"))
    result = _run(compact_conversation(_history(), _ctx(), summarizer=summarizer, trigger="manual",
                                       post_compact_attachments=lambda c: (), model="o4-mini",
                                       suppress_follow_up_questions=False))
    assert result.summary_messages[0].content.endswith("plain summary")
    assert result.boundary_marker.additional_kwargs["compact_boundary"]["trigger"] == "manual"


def test_the_summarized_history_starts_after_the_last_boundary_without_images():
    boundary = create_compact_boundary_message("auto", 5)
    image_msg = HumanMessage(content=[{"type": "image", "source": {"data": "x"}}, {"type": "text", "text": "hi"}])
    history = [HumanMessage(content="old"), boundary, image_msg]
    summarizer = FakeSummarizer(AIMessage(content="s"))
    _run(compact_conversation(history, _ctx(), summarizer=summarizer, trigger="auto",
                              post_compact_attachments=lambda c: (), model="o4-mini"))
    sent = summarizer.calls[0]["messages"]
    assert sent[0] is boundary
    assert sent[1].content == [{"type": "text", "text": "[image]"}, {"type": "text", "text": "hi"}]


def test_errors():
    with pytest.raises(ValueError, match="^Not enough messages to compact.$"):
        _run(compact_conversation([], _ctx(), summarizer=FakeSummarizer(), trigger="auto",
                                  post_compact_attachments=lambda c: (), model="m"))
    with pytest.raises(ValueError, match="^Failed to generate conversation summary - response did not contain "
                                         "valid text content$"):
        _run(compact_conversation(_history(), _ctx(), summarizer=FakeSummarizer(AIMessage(content="")),
                                  trigger="auto", post_compact_attachments=lambda c: (), model="m"))
    with pytest.raises(ValueError, match="^API Error: 500 boom$"):
        failing = FakeSummarizer(AIMessage(content="API Error: 500 boom"))
        _run(compact_conversation(_history(), _ctx(), summarizer=failing,
                                  trigger="auto", post_compact_attachments=lambda c: (), model="m"))


def test_prompt_too_long_retries_then_gives_up():
    history = [HumanMessage(content="q")] + [m for i in range(10) for m in _round(i)]
    ptl = AIMessage(content=f"{PROMPT_TOO_LONG_ERROR_MESSAGE}: too many")
    summarizer = FakeSummarizer(ptl, AIMessage(content="ok summary"))
    result = _run(compact_conversation(history, _ctx(), summarizer=summarizer, trigger="auto",
                                       post_compact_attachments=lambda c: (), model="m"))
    assert len(summarizer.calls) == 2
    assert summarizer.calls[1]["messages"][0].content == PTL_RETRY_MARKER
    assert "ok summary" in result.summary_messages[0].content
    stubborn = FakeSummarizer(*([ptl] * (MAX_PTL_RETRIES + 1)))
    with pytest.raises(ValueError, match="^Conversation too long. Press esc twice"):
        _run(compact_conversation(history, _ctx(), summarizer=stubborn, trigger="auto",
                                  post_compact_attachments=lambda c: (), model="m"))
    assert len(stubborn.calls) == MAX_PTL_RETRIES + 1


def test_hooks_shape_the_compaction():
    from app.loop.hooks.execute import HookOutsideReplResult
    from app.loop.hooks.messages import HookAttachment
    from app.loop.hooks.output import AggregatedHookResult

    dispatcher = ScriptedDispatcher(
        script={"SessionStart": [AggregatedHookResult(attachment=HookAttachment(
            type="hook_success", hook_name="SessionStart:compact", tool_use_id="t", hook_event="SessionStart",
            content="restored"))]},
        outside={"PreCompact": [HookOutsideReplResult(command="pre.sh", succeeded=True, output="Keep diffs.",
                                                      blocked=False)],
                 "PostCompact": [HookOutsideReplResult(command="post.sh", succeeded=True, output="",
                                                       blocked=False)]},
    )
    summarizer = FakeSummarizer(AIMessage(content="s"))
    result = _run(compact_conversation(_history(), _ctx(hook_dispatcher=dispatcher), summarizer=summarizer,
                                       trigger="auto", post_compact_attachments=lambda c: (), model="m",
                                       custom_instructions="Mind the tests."))
    request = summarizer.calls[0]["request"].content
    assert "\n\nAdditional Instructions:\nMind the tests.\n\nKeep diffs.\n\nREMINDER" in request
    assert [e for e, _ in dispatcher.seen] == ["PreCompact", "SessionStart", "PostCompact"]
    assert dispatcher.payload("PreCompact")["custom_instructions"] == "Mind the tests."
    assert dispatcher.payload("PostCompact")["compact_summary"] == "s"
    assert dispatcher.payload("SessionStart")["source"] == "compact"
    assert [m.content for m in result.hook_results] == [
        "<system-reminder>\nSessionStart:compact hook success: restored\n</system-reminder>"]
    assert result.user_display_message == (
        "PreCompact [pre.sh] completed successfully: Keep diffs.\nPostCompact [post.sh] completed successfully")
