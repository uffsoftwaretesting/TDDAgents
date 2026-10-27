"""Phase 5: time-based microcompact (`microcompactMessages`), offline."""

from __future__ import annotations

from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services.compact.micro_compact import (
    COMPACTABLE_TOOLS,
    TIME_BASED_MC_CLEARED_MESSAGE,
    TIME_BASED_MC_CONFIG_DEFAULTS,
    MicrocompactResult,
    TimeBasedMCConfig,
    collect_compactable_tool_ids,
    evaluate_time_based_trigger,
    get_time_based_mc_config,
    microcompact_messages,
)

T0 = datetime(2026, 10, 22, 12, 0, tzinfo=UTC).timestamp()
ON = TimeBasedMCConfig(enabled=True, gap_threshold_minutes=60, keep_recent=2)


def ask(*calls: tuple[str, str], at: str | None = "2026-10-22T12:00:00Z") -> AIMessage:
    kwargs = {"timestamp": at} if at is not None else {}
    return AIMessage(content="", additional_kwargs=kwargs,
                     tool_calls=[{"name": name, "args": {}, "id": cid} for cid, name in calls])


def res(cid: str, text: str = "x" * 40) -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=cid)


def history() -> list:
    return [
        HumanMessage(content="go"),
        ask(("a", "ReadFile"), ("b", "RunTests")), res("a"), res("b"),
        ask(("c", "Bash")), res("c"),
        ask(("d", "Grep")), res("d"),
        ask(("e", "Edit")), res("e"),
    ]


def test_upstream_defaults_and_constants():
    assert TIME_BASED_MC_CLEARED_MESSAGE == "[Old tool result content cleared]"
    assert TIME_BASED_MC_CONFIG_DEFAULTS == TimeBasedMCConfig(enabled=False, gap_threshold_minutes=60, keep_recent=5)
    assert get_time_based_mc_config() == TIME_BASED_MC_CONFIG_DEFAULTS
    assert COMPACTABLE_TOOLS == frozenset(
        {"ReadFile", "Bash", "PowerShell", "Grep", "Glob", "WebSearch", "WebFetch", "Edit", "WriteFile"})


def test_the_default_config_is_a_no_op():
    messages = history()
    out = microcompact_messages(messages, is_main_thread=True, now=T0 + 10_000)
    assert out == MicrocompactResult(messages=messages, tokens_saved=0)
    assert out.messages is messages


def test_compactable_ids_in_encounter_order():
    assert collect_compactable_tool_ids(history()) == ["a", "c", "d", "e"]


def test_trigger_needs_main_thread_an_assistant_a_timestamp_and_a_gap():
    msgs = history()
    assert evaluate_time_based_trigger(msgs, is_main_thread=False, now=T0 + 7200, config=ON) is None
    assert evaluate_time_based_trigger([HumanMessage(content="x")], is_main_thread=True, now=T0 + 7200,
                                       config=ON) is None
    assert evaluate_time_based_trigger([ask(at=None)], is_main_thread=True, now=T0 + 7200, config=ON) is None
    assert evaluate_time_based_trigger([ask(at="not a date")], is_main_thread=True, now=T0 + 7200,
                                       config=ON) is None
    assert evaluate_time_based_trigger(msgs, is_main_thread=True, now=T0 + 3599, config=ON) is None
    off = TimeBasedMCConfig(enabled=False, gap_threshold_minutes=60, keep_recent=2)
    assert evaluate_time_based_trigger(msgs, is_main_thread=True, now=T0 + 7200, config=off) is None
    gap = evaluate_time_based_trigger(msgs, is_main_thread=True, now=T0 + 3600, config=ON)
    assert gap == (60.0, ON)


def test_the_trigger_reads_the_last_assistant_message():
    msgs = [ask(at="2026-10-22T10:00:00Z"), HumanMessage(content="x"), ask(at="2026-10-22T12:00:00+00:00")]
    assert evaluate_time_based_trigger(msgs, is_main_thread=True, now=T0 + 600, config=ON) is None


def test_old_compactable_results_are_cleared_and_the_recent_kept():
    out = microcompact_messages(history(), is_main_thread=True, now=T0 + 7200, config=ON)
    contents = {m.tool_call_id: m.content for m in out.messages if isinstance(m, ToolMessage)}
    assert contents == {"a": TIME_BASED_MC_CLEARED_MESSAGE, "b": "x" * 40, "c": TIME_BASED_MC_CLEARED_MESSAGE,
                        "d": "x" * 40, "e": "x" * 40}
    assert out.tokens_saved == 20


def test_keep_recent_is_at_least_one():
    zero = TimeBasedMCConfig(enabled=True, gap_threshold_minutes=60, keep_recent=0)
    out = microcompact_messages(history(), is_main_thread=True, now=T0 + 7200, config=zero)
    kept = [
        m.tool_call_id for m in out.messages
        if isinstance(m, ToolMessage) and m.content != TIME_BASED_MC_CLEARED_MESSAGE
    ]
    assert kept == ["b", "e"]


def test_nothing_to_clear_is_a_no_op():
    msgs = [ask(("a", "Bash")), res("a")]
    out = microcompact_messages(msgs, is_main_thread=True, now=T0 + 7200, config=ON)
    assert out.messages is msgs and out.tokens_saved == 0


def test_already_cleared_results_save_nothing_and_change_nothing():
    msgs = history()
    once = microcompact_messages(msgs, is_main_thread=True, now=T0 + 7200, config=ON)
    twice = microcompact_messages(once.messages, is_main_thread=True, now=T0 + 7200, config=ON)
    assert twice.messages is once.messages and twice.tokens_saved == 0


def test_block_content_is_estimated_by_text_and_media():
    blocks = ToolMessage(content=[{"type": "text", "text": "y" * 80}, {"type": "image", "source": {}},
                                  {"type": "other"}], tool_call_id="a")
    msgs = [ask(("a", "ReadFile")), blocks, ask(("b", "ReadFile")), res("b")]
    one = TimeBasedMCConfig(enabled=True, gap_threshold_minutes=60, keep_recent=1)
    out = microcompact_messages(msgs, is_main_thread=True, now=T0 + 7200, config=one)
    assert out.tokens_saved == 20 + 2000
    assert out.messages[1].content == TIME_BASED_MC_CLEARED_MESSAGE


def test_untouched_messages_keep_their_identity():
    msgs = history()
    out = microcompact_messages(msgs, is_main_thread=True, now=T0 + 7200, config=ON)
    assert out.messages[0] is msgs[0]
    assert out.messages[3] is msgs[3]  # b: not compactable
    assert out.messages[2] is not msgs[2]
