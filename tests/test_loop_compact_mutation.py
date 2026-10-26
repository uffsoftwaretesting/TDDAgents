"""
Phase 5 mutation triage: the compaction package (`app/loop/compact/*`) and its production
bindings in `app/loop/factory.py`.

Each test pins a behaviour a surviving mutant changed without any existing assertion
noticing: exact outputs where the older tests only checked a count or a prefix, the
boundaries of every threshold, and the `getattr(..., default)` fallbacks that keep these
helpers total over objects missing an attribute.
"""

from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import app.loop.compact.auto_compact as ac
import app.loop.compact.compact as cc
import app.loop.compact.post_compact_cleanup as pcc
import app.loop.compact.summarizer as summ
import app.loop.factory as factory
from app.loop.compact.auto_compact import (
    AutoCompactOutcome,
    auto_compact_if_needed,
    calculate_token_warning_state,
    get_auto_compact_threshold,
    is_auto_compact_enabled,
)
from app.loop.compact.compact import (
    ERROR_MESSAGE_NO_SUMMARY,
    POST_COMPACT_MAX_TOKENS_PER_FILE,
    PROMPT_TOO_LONG_ERROR_MESSAGE,
    PTL_RETRY_MARKER,
    compact_conversation,
    create_compact_boundary_message,
    create_post_compact_file_attachments,
    find_last_compact_boundary_index,
    get_prompt_too_long_token_gap,
    is_compact_boundary_message,
    merge_hook_instructions,
    strip_images_from_messages,
    truncate_head_for_ptl_retry,
)
from app.loop.compact.grouping import group_messages_by_api_round
from app.loop.compact.micro_compact import (
    TIME_BASED_MC_CLEARED_MESSAGE,
    TimeBasedMCConfig,
    calculate_tool_result_tokens,
    collect_compactable_tool_ids,
    evaluate_time_based_trigger,
    microcompact_messages,
)
from app.loop.compact.prompt import (
    COMPACT_FORBIDDEN_TOOLS,
    _read,
    format_compact_summary,
    get_compact_prompt,
    get_compact_user_summary_message,
)
from app.loop.compact.summarizer import build_compact_summarizer
from app.loop.config import build_run_config
from app.loop.context.app_state import AppStateStore
from app.loop.context.file_state import FileState
from app.loop.context.tokens import (
    MEDIA_BLOCK_TOKENS,
    rough_token_count_estimation_for_messages,
)
from app.loop.context.tool_context import tool_context_for
from app.loop.prompts.loader import render_prompt
from app.loop.state import CompactionTracking, initial_loop_state
from tests.conftest import FakeWorkspace
from tests.hook_doubles import ScriptedDispatcher

MODEL = "o4-mini"  # 200k window, 32k max output -> 180k effective, 167k autocompact threshold
CONFIG = build_run_config("r1", postgres_checkpointing=False)
_ENV = ("TDDAGENTS_DISABLE_COMPACT", "TDDAGENTS_DISABLE_AUTO_COMPACT", "TDDAGENTS_AUTOCOMPACT_PCT_OVERRIDE",
        "TDDAGENTS_BLOCKING_LIMIT_OVERRIDE")


def _bare(**attrs: Any) -> Any:
    """A record with only the given attributes, typed loosely so it can stand where a message does."""
    return SimpleNamespace(**attrs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _usage_message(tokens: int) -> AIMessage:
    return AIMessage(content="ok", id="a1", usage_metadata={"input_tokens": tokens, "output_tokens": 0,
                                                            "total_tokens": tokens})


BIG = [HumanMessage(content="q"), _usage_message(170_000)]


class _Compact:
    """Stands in for `compact_conversation`: records the call, returns a marker."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, messages: Any, context: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return _bare(marker="result")


# ── auto_compact ──────────────────────────────────────────────────────────────

def test_a_blocking_override_must_be_positive(clean_env):
    clean_env.setenv("TDDAGENTS_BLOCKING_LIMIT_OVERRIDE", "0")
    # Zero is no override: the limit stays 3k under the 180k effective window.
    assert calculate_token_warning_state(176_999, MODEL).is_at_blocking_limit is False
    clean_env.setenv("TDDAGENTS_BLOCKING_LIMIT_OVERRIDE", "1")
    assert calculate_token_warning_state(1, MODEL).is_at_blocking_limit is True
    assert calculate_token_warning_state(0, MODEL).is_at_blocking_limit is False


@pytest.mark.parametrize("value", ["yes", " ON ", "on", "YES"])
def test_every_truthy_spelling_disables_compaction(clean_env, value):
    clean_env.setenv("TDDAGENTS_DISABLE_COMPACT", value)
    assert is_auto_compact_enabled() is False


def test_the_disable_switch_holds_even_if_the_threshold_check_would_compact(clean_env):
    """`autoCompactIfNeeded` checks `DISABLE_COMPACT` itself, before `shouldAutoCompact`."""
    compact = _Compact()
    clean_env.setattr(ac, "compact_conversation", compact)
    clean_env.setattr(ac, "should_auto_compact", lambda *a, **k: True)
    clean_env.setattr(ac, "run_post_compact_cleanup", lambda *a, **k: None)
    clean_env.setenv("TDDAGENTS_DISABLE_COMPACT", "1")
    out = _run(auto_compact_if_needed(BIG, tool_context_for(AppStateStore()), model=MODEL, tracking=None,
                                      summarizer=None, post_compact_attachments=lambda c: ()))  # type: ignore[arg-type]
    assert out == AutoCompactOutcome(False)
    assert compact.calls == []


def test_the_query_source_and_snip_reach_the_threshold_check(clean_env):
    compact = _Compact()
    clean_env.setattr(ac, "compact_conversation", compact)
    clean_env.setattr(ac, "run_post_compact_cleanup", lambda *a, **k: None)
    ctx = tool_context_for(AppStateStore())

    def go(**kw: Any) -> AutoCompactOutcome:
        outcome: AutoCompactOutcome = _run(auto_compact_if_needed(
            BIG, ctx, model=MODEL, tracking=None, summarizer=None,  # type: ignore[arg-type]
            post_compact_attachments=lambda c: (), **kw))
        return outcome

    assert go(query_source="compact") == AutoCompactOutcome(False)
    assert go(snip_tokens_freed=10_000) == AutoCompactOutcome(False)
    assert compact.calls == []
    assert go(query_source="sdk").was_compacted is True


def test_a_workers_autocompaction_cleans_up_as_a_worker(clean_env):
    clean_env.setattr(ac, "compact_conversation", _Compact())
    calls: list[Any] = []
    clean_env.setattr(pcc, "invalidate_context_caches", lambda **kw: calls.append(kw))
    clean_env.setattr(pcc, "get_session_context", lambda run_id, ws: _bare(section_cache="sections"))
    # A context with no agent id but a worker's query source: only the source says "worker".
    ctx = tool_context_for(AppStateStore(), run_id="r1")
    _run(auto_compact_if_needed(BIG, ctx, model=MODEL, tracking=None, summarizer=None,  # type: ignore[arg-type]
                                post_compact_attachments=lambda c: (), query_source="agent:x"))
    assert calls == [{"section_cache": "sections"}]


def test_a_one_percent_override_is_honoured(clean_env):
    clean_env.setenv("TDDAGENTS_AUTOCOMPACT_PCT_OVERRIDE", "1")
    assert get_auto_compact_threshold(MODEL) == 1_800


# ── compact.py: boundary and PTL helpers ──────────────────────────────────────

def test_boundary_helpers_are_total_over_bare_objects():
    assert is_compact_boundary_message(_bare()) is False
    assert is_compact_boundary_message(_bare(type="system")) is False
    assert is_compact_boundary_message(_bare(additional_kwargs={"compact_boundary": {}})) is False
    boundary = create_compact_boundary_message("auto", 1)
    assert find_last_compact_boundary_index([HumanMessage(content="a"), boundary]) == 1
    assert find_last_compact_boundary_index([boundary]) == 0


def test_the_ptl_gap_helpers_are_total_and_exact():
    assert get_prompt_too_long_token_gap(_bare(content=PROMPT_TOO_LONG_ERROR_MESSAGE)) is None
    assert cc._is_prompt_too_long_message(_bare(content=PROMPT_TOO_LONG_ERROR_MESSAGE)) is False
    one = AIMessage(content=PROMPT_TOO_LONG_ERROR_MESSAGE, additional_kwargs={
        "is_api_error_message": True, "error_details": "prompt is too long: 3 tokens > 2"})
    assert get_prompt_too_long_token_gap(one) == 1


def test_the_retry_marker_is_recognised_exactly():
    marker = HumanMessage(content=PTL_RETRY_MARKER, additional_kwargs={"is_meta": True})
    assert cc._is_ptl_marker(marker) is True
    assert cc._is_ptl_marker(_bare()) is False
    assert cc._is_ptl_marker(HumanMessage(content=PTL_RETRY_MARKER)) is False  # not meta
    assert cc._is_ptl_marker(HumanMessage(content="other", additional_kwargs={"is_meta": True})) is False
    assert cc._is_ptl_marker(AIMessage(content=PTL_RETRY_MARKER, additional_kwargs={"is_meta": True})) is False
    assert cc._is_ptl_marker(HumanMessage(content=PTL_RETRY_MARKER, additional_kwargs={"is_meta": False})) is False


def _round(i: int, size: int = 400) -> list[Any]:
    return [AIMessage(content="a" * size, id=f"m{i}"), HumanMessage(content="u" * size)]


def _marker() -> HumanMessage:
    return HumanMessage(content=PTL_RETRY_MARKER, additional_kwargs={"is_meta": True})


def _ptl(gap_tokens: int) -> AIMessage:
    return AIMessage(content=PROMPT_TOO_LONG_ERROR_MESSAGE, additional_kwargs={
        "is_api_error_message": True, "error_details": f"prompt is too long: {gap_tokens + 100} tokens > 100"})


def test_a_retry_strips_the_old_marker_before_regrouping():
    rounds = [m for i in range(5) for m in _round(i)]
    out = truncate_head_for_ptl_retry([_marker(), *rounds], AIMessage(content="err"))
    # Five rounds once the marker is gone: drop one (a fifth), re-mark the assistant-first tail.
    assert out == [_marker(), *rounds[2:]]


def test_a_non_meta_message_with_the_marker_text_is_an_ordinary_group():
    first = HumanMessage(content=PTL_RETRY_MARKER)
    rounds = [m for i in range(4) for m in _round(i)]
    # Five groups (the leading human is its own): a fifth drops exactly that leading group.
    assert truncate_head_for_ptl_retry([first, *rounds], AIMessage(content="err")) == [_marker(), *rounds]


def test_the_gap_decides_how_many_rounds_go():
    lead = HumanMessage(content="q")
    rounds = [m for i in range(4) for m in _round(i)]
    msgs = [lead, *rounds]
    groups = group_messages_by_api_round(msgs)
    t0 = rough_token_count_estimation_for_messages(groups[0])
    # Exactly the first group's tokens: one group covers the gap.
    assert truncate_head_for_ptl_retry(msgs, _ptl(t0)) == [_marker(), *rounds]
    # One token more needs the second group as well.
    assert truncate_head_for_ptl_retry(msgs, _ptl(t0 + 1)) == [_marker(), *rounds[2:]]


def test_the_gap_accumulates_across_rounds_and_never_drops_everything():
    rounds = [m for i in range(5) for m in _round(i)]
    per_round = rough_token_count_estimation_for_messages(_round(0))
    assert truncate_head_for_ptl_retry(rounds, _ptl(2 * per_round)) == [_marker(), *rounds[4:]]
    # A gap larger than the whole history still keeps the last round.
    assert truncate_head_for_ptl_retry(rounds, _ptl(1_000_000)) == [_marker(), *rounds[8:]]


def test_without_a_gap_a_small_history_drops_one_round():
    rounds = [m for i in range(2) for m in _round(i)]
    assert truncate_head_for_ptl_retry(rounds, AIMessage(content="err")) == [_marker(), *rounds[2:]]


# ── compact.py: media stripping and summary text ──────────────────────────────

def test_media_becomes_exact_text_stubs_in_user_turns_and_tool_results():
    human = HumanMessage(content=[{"type": "image_url", "image_url": {"url": "x"}}, {"type": "document"}, "raw"])
    tool = ToolMessage(content=[{"type": "image", "source": {}}], tool_call_id="c1")
    after = HumanMessage(content=[{"type": "image", "source": {}}])
    out = strip_images_from_messages([human, tool, after])
    assert out[0].content == [{"type": "text", "text": "[image]"}, {"type": "text", "text": "[document]"}, "raw"]
    assert out[1].content == [{"type": "text", "text": "[image]"}]
    assert out[2].content == [{"type": "text", "text": "[image]"}]


def test_messages_without_media_or_outside_user_turns_are_untouched():
    plain = HumanMessage(content=[{"type": "text", "text": "hi"}])
    ai = AIMessage(content=[{"type": "image", "source": {}}])
    bare = _bare()
    out = strip_images_from_messages([plain, ai, bare, _bare(type="human")])
    assert out[0] is plain and out[1] is ai and out[2] is bare


class FakeSummarizer:
    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, messages: Any, summary_request: Any, context: Any, *, max_output_tokens: int) -> Any:
        self.calls.append({"messages": list(messages)})
        return self.replies.pop(0)


def _ctx(files: dict[str, str] | None = None, **kw: Any) -> Any:
    return tool_context_for(AppStateStore(), workspace=FakeWorkspace(files=files or {}), run_id="r1", **kw)


def _compact(reply: Any, ctx: Any = None, **kw: Any) -> Any:
    return _run(compact_conversation([HumanMessage(content="q"), AIMessage(content="a", id="a1")],
                                     ctx if ctx is not None else _ctx(), summarizer=FakeSummarizer(reply),
                                     trigger="auto", post_compact_attachments=lambda c: (), model="m", **kw))


def test_a_block_summary_joins_only_the_text_blocks():
    reply = AIMessage(content=[{"type": "text", "text": "one"}, {"type": "image"}, "loose",
                               {"type": "text"}, {"type": "text", "text": "two"}])
    result = _compact(reply, suppress_follow_up_questions=False)
    assert result.summary_messages[0].content.endswith("\n\none\n\ntwo")


@pytest.mark.parametrize("reply", [_bare(content=None), _bare()])
def test_a_reply_without_text_is_no_summary(reply):
    with pytest.raises(ValueError, match=f"^{ERROR_MESSAGE_NO_SUMMARY}$"):
        _compact(reply)


# ── compact.py: post-compact file restoration ─────────────────────────────────

class FakeRead:
    """A `ReadFile` stand-in whose output differs from the real tool's numbered lines."""

    name = "ReadFile"

    def __init__(self, content: str = "fake body", *, valid: bool = True, raises: bool = False,
                 result: Any = None) -> None:
        self.content = content
        self.valid = valid
        self.raises = raises
        self.result = result
        self.seen: list[Any] = []

    def validate_input(self, args: Any, context: Any) -> Any:
        self.seen.append(("validate", args, context))
        return _bare(valid=self.valid)

    async def call(self, args: Any, context: Any) -> Any:
        self.seen.append(("call", args, context))
        if self.raises:
            raise RuntimeError("read failed")
        if self.result is not None:
            return self.result
        context.read_file_state[args["file_path"]] = FileState(content=self.content)
        return _bare(content=self.content, is_error=False)


def test_restoration_reads_through_the_contexts_read_tool():
    read = FakeRead()
    ctx = _ctx(tools=(_bare(), _bare(name="Bash"), read))
    out = _run(cc._generate_file_attachment("a.py", ctx, 3))
    call_id = "compact_restore_3"
    assert out == [AIMessage(content="", tool_calls=[{"name": "ReadFile", "args": {"file_path": "a.py"},
                                                      "id": call_id}]),
                   ToolMessage(content="fake body", tool_call_id=call_id)]
    assert read.seen == [("validate", {"file_path": "a.py"}, ctx), ("call", {"file_path": "a.py"}, ctx)]


def test_the_read_tool_falls_back_to_the_built_in_one():
    assert cc._read_tool(_bare()).name == "ReadFile"
    assert cc._read_tool(_bare(tools=None)).name == "ReadFile"


@pytest.mark.parametrize("read", [
    FakeRead(valid=False),
    FakeRead(raises=True),
    FakeRead(result=_bare(content="denied", is_error=True)),
])
def test_a_refused_or_failed_read_restores_nothing(read):
    assert _run(cc._generate_file_attachment("a.py", _ctx(tools=(read,)), 0)) is None


def test_a_result_without_an_error_flag_is_a_success():
    read = FakeRead(result=_bare(content="body"))
    out = _run(cc._generate_file_attachment("a.py", _ctx(tools=(read,)), 0))
    assert out is not None and out[1].content == "body"


def test_the_per_file_limit_is_inclusive_and_an_oversized_file_is_forgotten():
    exact = "x" * (POST_COMPACT_MAX_TOKENS_PER_FILE * 4)
    out = _run(cc._generate_file_attachment("a.py", _ctx(tools=(FakeRead(exact),)), 0))
    assert out is not None and out[1].content == exact
    ctx = _ctx(tools=(FakeRead(exact + "xxxx"),))
    ctx.read_file_state["keep.py"] = FileState(content="k")
    (note,) = _run(cc._generate_file_attachment("a.py", ctx, 0))
    assert isinstance(note, HumanMessage)
    assert set(ctx.read_file_state) == {"keep.py"}


def test_restored_calls_are_numbered_by_position():
    files = {"a.py": "a\n", "b.py": "b\n"}
    ctx = _ctx(files)
    out = _run(create_post_compact_file_attachments({k: FileState(content=v) for k, v in files.items()}, ctx, 5))
    assert [m.tool_calls[0]["id"] for m in out if isinstance(m, AIMessage)] == [
        "compact_restore_0", "compact_restore_1"]


def _three_equal_attachments(monkeypatch: pytest.MonkeyPatch, budget_in_files: int) -> list[str]:
    files = {f"{c}.py": c * 40 for c in "abc"}
    ctx = _ctx(tools=(FakeRead("x" * 40),))
    one = _run(cc._generate_file_attachment("a.py", _ctx(tools=(FakeRead("x" * 40),)), 0))
    budget = budget_in_files * rough_token_count_estimation_for_messages(one)
    monkeypatch.setattr(cc, "POST_COMPACT_TOKEN_BUDGET", budget)
    out = _run(create_post_compact_file_attachments({k: FileState(content=v) for k, v in files.items()}, ctx, 5))
    return [m.tool_calls[0]["args"]["file_path"] for m in out if isinstance(m, AIMessage)]


def test_the_total_budget_is_inclusive(monkeypatch):
    assert _three_equal_attachments(monkeypatch, 1) == ["c.py"]


def test_the_total_budget_accumulates(monkeypatch):
    assert _three_equal_attachments(monkeypatch, 2) == ["c.py", "b.py"]


def test_hook_instructions_merge():
    assert merge_hook_instructions("user", None) == "user"
    assert merge_hook_instructions("", None) is None
    assert merge_hook_instructions(None, "hook") == "hook"
    assert merge_hook_instructions("user", "hook") == "user\n\nhook"


# ── compact_conversation ──────────────────────────────────────────────────────

class RecordingDispatcher(ScriptedDispatcher):
    def __init__(self) -> None:
        super().__init__()
        self.kwargs: list[tuple[str, dict[str, Any]]] = []

    async def execute_hooks_outside_repl(self, hook_input: Any, **kwargs: Any) -> Any:
        self.kwargs.append((str(hook_input["hook_event_name"]), kwargs))
        return await super().execute_hooks_outside_repl(hook_input, **kwargs)


def test_the_compact_hooks_get_the_trigger_the_model_and_the_cancel_signal():
    dispatcher = RecordingDispatcher()
    ctx = _ctx(hook_dispatcher=dispatcher)
    _run(compact_conversation([HumanMessage(content="q")], ctx, summarizer=FakeSummarizer(AIMessage(content="s")),
                              trigger="manual", post_compact_attachments=lambda c: (), model="the-model"))
    assert dispatcher.payload("PreCompact")["trigger"] == "manual"
    assert dispatcher.payload("PostCompact")["trigger"] == "manual"
    assert dispatcher.payload("SessionStart")["model"] == "the-model"
    signals = [(e, kw["signal"]) for e, kw in dispatcher.kwargs]
    assert signals == [("PreCompact", ctx.cancel), ("PostCompact", ctx.cancel)]


def test_a_context_without_a_cancel_token_still_compacts():
    ctx = _bare(read_file_state={})
    result = _compact(AIMessage(content="s"), ctx)
    assert result.summary_messages[0].additional_kwargs == {"is_compact_summary": True}


def test_a_reply_without_usage_counts_zero_post_compact_tokens():
    assert _compact(AIMessage(content="s")).post_compact_token_count == 0


def test_only_the_newest_files_are_restored():
    files = {f"f{i}.py": f"{i}\n" for i in range(7)}
    ctx = _ctx(files)
    ctx.read_file_state.update({k: FileState(content=v) for k, v in files.items()})
    result = _compact(AIMessage(content="s"), ctx)
    assert sum(isinstance(m, AIMessage) for m in result.attachments) == 5


def test_the_ptl_retry_uses_the_reported_gap():
    lead = HumanMessage(content="q")
    rounds = [m for i in range(4) for m in _round(i)]
    history = [lead, *rounds]
    t0 = rough_token_count_estimation_for_messages([lead])
    summarizer = FakeSummarizer(_ptl(t0 + 1), AIMessage(content="ok"))
    _run(compact_conversation(history, _ctx(), summarizer=summarizer, trigger="auto",
                              post_compact_attachments=lambda c: (), model="m"))
    # The gap covers the lead and the first round; without it a fifth (one group) would go.
    assert summarizer.calls[1]["messages"] == [_marker(), *rounds[2:]]


# ── grouping ──────────────────────────────────────────────────────────────────

def test_grouping_keeps_every_message_and_is_total():
    human = HumanMessage(content="first")
    a1, a1b = AIMessage(content="x", id="1"), AIMessage(content="y", id="1")
    assert group_messages_by_api_round([human, a1, a1b]) == [[human], [a1, a1b]]
    bare = _bare()
    assert group_messages_by_api_round([bare]) == [[bare]]
    no_id_1, no_id_2 = _bare(type="ai"), _bare(type="ai")
    assert group_messages_by_api_round([no_id_1, no_id_2]) == [[no_id_1], [no_id_2]]
    empty_id = _bare(type="ai", id="")
    assert group_messages_by_api_round([human, empty_id]) == [[human], [empty_id]]


# ── micro_compact ─────────────────────────────────────────────────────────────

ON = TimeBasedMCConfig(enabled=True, gap_threshold_minutes=60, keep_recent=1)
OLD = "2000-01-01T00:00:00+00:00"
OLD_TS = 946_684_800.0


def _ask(*calls: tuple[str | None, str]) -> AIMessage:
    return AIMessage(content="", additional_kwargs={"timestamp": OLD}, tool_calls=[
        {"name": name, "args": {}, "id": cid} for cid, name in calls])


def test_the_trigger_uses_the_given_clock_not_the_wall_clock():
    msgs = [_ask(("a", "Bash")), ToolMessage(content="x" * 40, tool_call_id="a"),
            _ask(("b", "Bash")), ToolMessage(content="y" * 40, tool_call_id="b")]
    out = microcompact_messages(msgs, is_main_thread=True, now=OLD_TS + 60, config=ON)
    assert out.messages is msgs and out.tokens_saved == 0


def test_tool_shaped_records_without_a_call_id_are_never_cleared():
    bare_tool = _bare(type="tool", content="keep me")
    typeless = _bare(content="typeless")
    msgs = [AIMessage(content="", additional_kwargs={"timestamp": OLD}, tool_calls=[
                {"name": "Bash", "args": {}, "id": None}, {"name": "Bash", "args": {}, "id": "XXXX"},
                {"name": "Bash", "args": {}, "id": "old"}]),
            ToolMessage(content="x" * 40, tool_call_id="old"), bare_tool, typeless,
            _ask(("new", "Bash")), ToolMessage(content="y", tool_call_id="new")]
    out = microcompact_messages(msgs, is_main_thread=True, now=OLD_TS + 7200, config=ON)
    assert out.tokens_saved == 10
    assert out.messages[1].content == TIME_BASED_MC_CLEARED_MESSAGE
    assert out.messages[2] is bare_tool and out.messages[3] is typeless


def test_the_micro_helpers_are_total_over_bare_objects():
    assert collect_compactable_tool_ids([_bare(), _bare(type="ai")]) == []
    assert evaluate_time_based_trigger([_bare(type="ai")], is_main_thread=True, now=OLD_TS,
                                       config=ON) is None
    assert evaluate_time_based_trigger([_ask(), _bare()], is_main_thread=True, now=OLD_TS + 7200,
                                       config=ON) == (120.0, ON)


def test_tool_result_tokens_exactly():
    assert calculate_tool_result_tokens("") == 0
    assert calculate_tool_result_tokens([]) == 0
    assert calculate_tool_result_tokens([{"type": "text", "text": "abcd"}, {"type": "text", "text": "efgh"}]) == 2
    assert calculate_tool_result_tokens([{"type": "text"}]) == 0
    assert calculate_tool_result_tokens(["loose", {"type": "text", "text": "abcd"}]) == 1
    assert calculate_tool_result_tokens([{"type": "other"}]) == 0
    assert calculate_tool_result_tokens([{"type": "image_url"}, {"type": "document"}]) == 2 * MEDIA_BLOCK_TOKENS


# ── prompt ────────────────────────────────────────────────────────────────────

def test_the_prompt_is_joined_exactly():
    preamble = render_prompt(_read("no_tools_preamble.md"), {"TOOL_LIST": ", ".join(COMPACT_FORBIDDEN_TOOLS)})
    head = f"{preamble}\n\n{_read('base_compact_prompt.md')}\n\n{_read('tdd_compact_sections.md')}"
    assert get_compact_prompt() == f"{head}\n\n{_read('no_tools_trailer.md')}"
    assert get_compact_prompt("Mind it.") == (
        f"{head}\n\nAdditional Instructions:\nMind it.\n\n{_read('no_tools_trailer.md')}")


def test_only_the_first_analysis_and_summary_blocks_are_rewritten():
    raw = "<analysis>a</analysis>x<analysis>b</analysis><summary>s1</summary><summary>s2</summary>"
    assert format_compact_summary(raw) == "x<analysis>b</analysis>Summary:\ns1<summary>s2</summary>"


def test_the_preserved_note_is_appended_not_substituted():
    kept = get_compact_user_summary_message("x", False, None, True)
    assert kept.startswith("This session is being continued from a previous conversation")
    assert kept.endswith("\n\nRecent messages are preserved verbatim.")


# ── post_compact_cleanup ──────────────────────────────────────────────────────

def test_cleanup_is_total_over_bare_contexts(monkeypatch):
    calls: list[Any] = []
    monkeypatch.setattr(pcc, "invalidate_context_caches", lambda **kw: calls.append(kw))
    seen: list[Any] = []

    def session_for(run_id: str, ws: Any) -> Any:
        seen.append((run_id, ws))
        return _bare(section_cache="s")

    monkeypatch.setattr(pcc, "get_session_context", session_for)
    pcc.run_post_compact_cleanup(_bare())  # no run id: nothing to clean
    pcc.run_post_compact_cleanup(_bare(run_id="r1"))  # no agent id: the main thread
    pcc.run_post_compact_cleanup(_bare(run_id="r1", agent_id="w"))  # a worker without a workspace
    assert calls == [{"run_id": "r1"}, {"section_cache": "s"}]
    assert seen == [("r1", None)]


# ── summarizer ────────────────────────────────────────────────────────────────

def test_the_summarizer_forwards_typeless_records(monkeypatch):
    sent: list[Any] = []

    class Chat:
        async def ainvoke(self, messages: Any) -> Any:
            sent.append(messages)
            return AIMessage(content="s")

    monkeypatch.setattr(summ, "get_chat_model", lambda provider, **kw: Chat())
    bare = _bare(content="x")
    summarize = build_compact_summarizer("p", "m")
    _run(summarize([bare, SystemMessage(content="sys")], HumanMessage(content="r"), None, max_output_tokens=1))
    assert sent[0][1:] == [bare, HumanMessage(content="r")]


# ── factory ───────────────────────────────────────────────────────────────────

def test_the_query_source_names_the_worker():
    assert factory.query_source_of(_bare()) == "sdk"
    assert factory.query_source_of(_bare(agent_id="w1", agent_type="tester")) == "agent:tester"
    assert factory.query_source_of(_bare(agent_id="w1")) == "agent:None"
    assert factory.query_source_of(_bare(agent_type="tester")) == "sdk"


def _worker_state(**kw: Any) -> Any:
    ctx = tool_context_for(AppStateStore(), agent_id="w1", agent_type="tester", **kw)
    return initial_loop_state((HumanMessage(content="task"),), ctx)


def test_a_workers_autocompaction_reports_its_query_source(monkeypatch):
    seen: dict[str, Any] = {}

    async def fake(messages: Any, context: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return AutoCompactOutcome(was_compacted=False)

    monkeypatch.setattr(factory, "auto_compact_if_needed", fake)
    monkeypatch.setattr(factory, "build_compact_summarizer", lambda: "summarizer")
    state = _worker_state()
    _run(factory.production_autocompact(state.messages, state, CONFIG))
    assert seen["query_source"] == "agent:tester"


def test_reactive_compaction_summarizes_this_run_and_a_failure_keeps_the_tracking(monkeypatch):
    seen: list[Any] = []

    async def boom(messages: Any, context: Any, **kwargs: Any) -> Any:
        seen.append((messages, context))
        raise RuntimeError("no")

    monkeypatch.setattr(factory, "compact_conversation", boom)
    monkeypatch.setattr(factory, "build_compact_summarizer", lambda: "summarizer")
    tracking = CompactionTracking(compacted=True, turn_id="t", turn_counter=4, consecutive_failures=0)
    state = dataclasses.replace(_worker_state(), compaction_tracking=tracking)
    out = _run(factory.production_compact(state, CONFIG))
    assert seen == [(state.messages, state.tool_context)]
    assert out.tracking is tracking


def test_microcompaction_runs_as_main_thread_only_without_an_agent(monkeypatch):
    seen: list[Any] = []

    def fake(messages: Any, *, is_main_thread: Any) -> Any:
        seen.append(is_main_thread)
        return _bare(messages=messages)

    monkeypatch.setattr(factory, "microcompact_messages", fake)
    main = initial_loop_state((HumanMessage(content="task"),), tool_context_for(AppStateStore()))
    bare = dataclasses.replace(main, tool_context=_bare())
    for state in (main, bare, _worker_state()):
        _run(factory.production_microcompact(state.messages, state, CONFIG))
    assert seen == [True, True, False]


# ── second round ──────────────────────────────────────────────────────────────

def test_a_human_meta_record_without_content_is_not_the_marker():
    assert cc._is_ptl_marker(_bare(type="human", additional_kwargs={"is_meta": True})) is False


def test_a_stripped_marker_takes_only_itself_with_it():
    rounds = [m for i in range(5) for m in _round(i)]
    per_round = rough_token_count_estimation_for_messages(_round(0))
    # The gap is exactly the first round: dropping one more message would regroup and drop two.
    assert truncate_head_for_ptl_retry([_marker(), *rounds], _ptl(per_round)) == [_marker(), *rounds[2:]]


def test_an_oversized_file_the_read_did_not_record_is_still_a_reference():
    read = FakeRead(result=_bare(content="x" * (POST_COMPACT_MAX_TOKENS_PER_FILE * 4 + 4), is_error=False))
    (note,) = _run(cc._generate_file_attachment("a.py", _ctx(tools=(read,)), 0))
    assert isinstance(note, HumanMessage)


def test_the_summary_requests_usage_is_reported():
    usage = {"input_tokens": 50, "output_tokens": 7, "total_tokens": 57}
    result = _compact(AIMessage(content="s", usage_metadata=usage))
    assert result.compaction_usage is not None
    assert result.compaction_usage["output_tokens"] == 7
