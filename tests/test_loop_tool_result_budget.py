"""Phase 5: the per-message aggregate tool-result budget (`applyToolResultBudget`), offline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import app.loop.tools.result_storage as rs
from app.loop.context.tool_context import ContentReplacementState
from app.loop.tools.result_storage import (
    MAX_TOOL_RESULTS_PER_MESSAGE_CHARS,
    PERSISTED_OUTPUT_TAG,
    apply_tool_result_budget,
    enforce_tool_result_budget,
    get_per_message_budget_limit,
    provision_content_replacement_state,
    reconstruct_content_replacement_state,
)


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    return tmp_path / "cfg"


def ask(*calls: tuple[str, str], msg_id: str | None = None) -> AIMessage:
    return AIMessage(content="", id=msg_id,
                     tool_calls=[{"name": name, "args": {}, "id": cid} for cid, name in calls])


def result(cid: str, size: int, char: str = "x") -> ToolMessage:
    return ToolMessage(content=char * size, tool_call_id=cid)


def apply(messages: list[Any], state: ContentReplacementState | None, **kw: Any) -> list[Any]:
    return apply_tool_result_budget(messages, state, run_id="run-1", working_directory="/ws", **kw)


def test_upstream_limit():
    assert MAX_TOOL_RESULTS_PER_MESSAGE_CHARS == 200_000
    assert get_per_message_budget_limit() == 200_000


def test_no_state_is_the_feature_off():
    messages = [ask(("a", "Bash")), result("a", 300_000)]
    out = apply(messages, None)
    assert out == messages
    assert all(x is y for x, y in zip(out, messages))


def test_provisioning_follows_the_external_build_default():
    assert provision_content_replacement_state() is None
    fresh = provision_content_replacement_state(enabled=True)
    assert fresh == ContentReplacementState()
    msgs = [ask(("a", "Bash")), result("a", 10)]
    rebuilt = provision_content_replacement_state(msgs, [], enabled=True)
    assert rebuilt == ContentReplacementState(seen_ids={"a"}, replacements={})


def test_under_budget_nothing_changes_but_everything_is_seen():
    state = ContentReplacementState()
    messages = [ask(("a", "Bash"), ("b", "Grep")), result("a", 100_000), result("b", 100_000)]
    out = apply(messages, state)
    assert [m.content for m in out] == [m.content for m in messages]
    assert state.seen_ids == {"a", "b"}
    assert state.replacements == {}


def test_over_budget_the_largest_fresh_results_are_persisted(config_dir):
    state = ContentReplacementState()
    messages = [ask(("a", "Bash"), ("b", "Grep"), ("c", "Glob")),
                result("a", 150_000, "a"), result("b", 80_000, "b"), result("c", 10_000, "c")]
    out = apply(messages, state)
    assert out[1].content.startswith(PERSISTED_OUTPUT_TAG)
    assert out[2].content == "b" * 80_000 and out[3].content == "c" * 10_000
    assert out[1].tool_call_id == "a"
    assert set(state.replacements) == {"a"}
    assert state.seen_ids == {"a", "b", "c"}
    saved = next(config_dir.rglob("a.txt"))
    assert saved.read_text() == "a" * 150_000
    assert "Output too large (146.5KB)" in out[1].content
    assert str(saved) in out[1].content


def test_several_results_are_replaced_until_the_group_fits():
    state = ContentReplacementState()
    messages = [ask(("a", "Bash"), ("b", "Bash"), ("c", "Bash")),
                result("a", 120_000), result("b", 110_000), result("c", 100_000)]
    out = apply(messages, state)
    replaced = [m.tool_call_id for m in out[1:] if m.content.startswith(PERSISTED_OUTPUT_TAG)]
    assert replaced == ["a", "b"]  # 330K -> drop a (210K) -> drop b (100K <= 200K)


def test_exactly_at_the_limit_is_within_budget():
    state = ContentReplacementState()
    messages = [ask(("a", "Bash"), ("b", "Bash")), result("a", 100_000), result("b", 100_000)]
    out = apply(messages, state)
    assert not any(m.content.startswith(PERSISTED_OUTPUT_TAG) for m in out[1:])


def test_decisions_replay_byte_identically_on_the_next_turn():
    state = ContentReplacementState()
    first = [ask(("a", "Bash")), result("a", 250_000)]
    out1 = apply(first, state)
    preview = out1[1].content
    # Next turn: the original (unreplaced) message is still in history, plus a new turn.
    second = [*first, HumanMessage(content="next"), ask(("b", "Bash")), result("b", 10)]
    out2 = apply(second, state)
    assert out2[1].content == preview
    assert out2[4].content == "x" * 10


def test_a_seen_result_is_never_replaced_later():
    state = ContentReplacementState()
    messages = [ask(("a", "Bash")), result("a", 150_000)]
    apply(messages, state)
    assert state.seen_ids == {"a"}
    # The same group grows past the budget with a later result: only the fresh one may go.
    grown = [ask(("a", "Bash"), ("b", "Bash")), result("a", 150_000), result("b", 100_000)]
    out = apply(grown, state)
    assert out[1].content == "x" * 150_000
    assert out[2].content.startswith(PERSISTED_OUTPUT_TAG)


def test_frozen_results_alone_over_budget_are_accepted():
    state = ContentReplacementState(seen_ids={"a"})
    messages = [ask(("a", "Bash"), ("b", "Bash")), result("a", 250_000), result("b", 10)]
    out = apply(messages, state)
    # b is fresh but replacing it cannot bring the group under budget; the selection still
    # walks it (largest-first) because the remaining total stays above the limit.
    assert out[1].content == "x" * 250_000
    assert out[2].content.startswith(PERSISTED_OUTPUT_TAG)


def test_groups_are_split_by_assistant_messages():
    state = ContentReplacementState()
    messages = [ask(("a", "Bash"), msg_id="m1"), result("a", 150_000),
                ask(("b", "Bash"), msg_id="m2"), result("b", 150_000)]
    out = apply(messages, state)
    assert not any(str(m.content).startswith(PERSISTED_OUTPUT_TAG) for m in out if isinstance(m, ToolMessage))


def test_records_of_one_response_do_not_split_a_group():
    state = ContentReplacementState()
    messages = [ask(("a", "Bash"), msg_id="m1"), ask(("b", "Bash"), msg_id="m1"),
                result("a", 150_000), result("b", 150_000)]
    out = apply(messages, state)
    assert out[2].content.startswith(PERSISTED_OUTPUT_TAG)


def test_assistant_messages_without_an_id_each_start_a_group():
    state = ContentReplacementState()
    messages = [ask(("a", "Bash")), result("a", 150_000), ask(("b", "Bash")), result("b", 150_000)]
    out = apply(messages, state)
    assert not any(str(m.content).startswith(PERSISTED_OUTPUT_TAG) for m in out if isinstance(m, ToolMessage))


def test_skipped_tools_are_seen_but_never_replaced():
    state = ContentReplacementState()
    messages = [ask(("a", "ReadFile"), ("b", "Bash")), result("a", 300_000), result("b", 10)]
    out = apply(messages, state, skip_tool_names=frozenset({"ReadFile"}))
    assert out[1].content == "x" * 300_000
    assert state.seen_ids == {"a", "b"}


def test_non_candidates_are_ignored():
    state = ContentReplacementState()
    messages = [
        ask(("a", "Bash"), ("b", "Bash"), ("c", "Bash"), ("d", "Bash")),
        ToolMessage(content=PERSISTED_OUTPUT_TAG + "y" * 300_000, tool_call_id="a"),
        ToolMessage(content=[{"type": "image", "source": {}}, {"type": "text", "text": "y" * 300_000}],
                    tool_call_id="b"),
        ToolMessage(content="", tool_call_id="c"),
        result("d", 10),
    ]
    out = apply(messages, state)
    assert out == messages
    assert state.seen_ids == {"d"}


def test_block_content_counts_only_text():
    state = ContentReplacementState()
    blocks = ToolMessage(content=[{"type": "text", "text": "x" * 150_000}, {"type": "other", "v": "y" * 999_999},
                                  {"type": "text", "text": "z" * 60_000}], tool_call_id="a")
    out = apply([ask(("a", "Bash")), blocks], state)
    assert isinstance(out[1].content, str) and out[1].content.startswith(PERSISTED_OUTPUT_TAG)


def test_a_failed_persist_leaves_the_result_and_marks_it_seen(monkeypatch):
    monkeypatch.setattr(rs, "persist_tool_result", lambda *a, **k: None)
    state = ContentReplacementState()
    messages = [ask(("a", "Bash")), result("a", 250_000)]
    out = apply(messages, state)
    assert out[1].content == "x" * 250_000
    assert state.seen_ids == {"a"} and state.replacements == {}


def test_enforce_reports_newly_replaced_only():
    state = ContentReplacementState()
    messages = [ask(("a", "Bash")), result("a", 250_000)]
    out, newly = enforce_tool_result_budget(messages, state, run_id="r", working_directory="/ws")
    assert [r.tool_use_id for r in newly] == ["a"]
    assert newly[0].replacement == out[1].content
    out2, newly2 = enforce_tool_result_budget(messages, state, run_id="r", working_directory="/ws")
    assert newly2 == [] and out2[1].content == out[1].content


def test_reconstruct_freezes_everything_and_restores_records():
    msgs = [ask(("a", "Bash"), ("b", "Bash")), result("a", 10), result("b", 10)]
    rec = [rs.ToolResultReplacementRecord(tool_use_id="a", replacement="P"),
           rs.ToolResultReplacementRecord(tool_use_id="zzz", replacement="Q")]
    state = reconstruct_content_replacement_state(msgs, rec)
    assert state == ContentReplacementState(seen_ids={"a", "b"}, replacements={"a": "P"})


def test_untouched_messages_are_passed_by_reference():
    state = ContentReplacementState()
    human = HumanMessage(content="hi")
    messages = [human, ask(("a", "Bash")), result("a", 250_000)]
    out = apply(messages, state)
    assert out[0] is human and out[1] is messages[1]
    assert isinstance(Path(rs.config_home()), Path)
