"""
Phase 5 gate, mutation triage: the token estimator, the prompt cache, the per-message
tool-result budget, the engine's pre-model pipeline and run-scoped agent memory.

Each test pins a behaviour the earlier suites left implicit — a branch that only a precise
block shape reaches, an upper limit only the env override reads, a default that only a
foreign record exercises.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import app.query.query as engine
import app.utils.tool_result_storage as rs
from app.query.config import build_run_config
from app.query.query import drain, run_loop
from app.query.state import initial_loop_state
from app.query.transitions import Terminal, Terminated
from app.services.api.claude import get_prompt_caching_enabled, prepare_cached_request
from app.services.compact.auto_compact import AutoCompactOutcome
from app.services.compact.compact import (
    CompactionResult,
    create_compact_boundary_message,
)
from app.state.app_state_store import AppStateStore
from app.tool import ContentReplacementState, tool_context_for
from app.tools.agent_tool.agent_memory import AgentMemoryStore
from app.utils import tokens
from tests.test_loop_engine import FakeModel, make_fake_deps

CONFIG = build_run_config("budget-run", postgres_checkpointing=False)


def foreign(**attrs: Any) -> Any:
    """A record that is not a LangChain message: only the attributes it is given."""
    return SimpleNamespace(**attrs)


# ── utils/tokens.py ───────────────────────────────────────────────────────

def test_an_unknown_block_is_estimated_from_its_json():
    block = {"type": "custom", "payload": "p" * 40}
    assert tokens.estimate_block_tokens(block) == tokens.estimate_string_tokens(
        '{"type": "custom", "payload": "' + "p" * 40 + '"}')


def test_json_compact_keeps_unicode_and_stringifies_the_unserializable():
    assert tokens._json_compact({"k": "é"}) == '{"k":"é"}'
    assert tokens._json_compact({"s": {1}}) == '{"s":"{1}"}'


def test_rough_estimation_of_each_block_kind():
    rough = tokens._rough_for_block
    assert rough(123456789012) == 3  # str() of a non-dict, non-str block
    assert rough({"type": "text"}) == 0
    assert rough({"type": "tool_result", "content": "x" * 40}) == 10
    # name + compact JSON input: "Bash" + '{"c":"ééé"}' is 15 characters -> round(3.75) == 4
    assert rough({"type": "tool_use", "name": "Bash", "input": {"c": "ééé"}}) == 4
    # Neither name nor input: "" + "{}" -> round(0.5) == 1 (half rounds up)
    assert rough({"type": "tool_use"}) == 1
    assert rough({"type": "thinking"}) == 0
    assert rough({"type": "redacted_thinking", "data": "d" * 40}) == 10
    assert rough({"type": "redacted_thinking"}) == 0


def test_rough_estimation_sums_every_tool_call_and_tolerates_foreign_records():
    msg = foreign(type="ai", content="c" * 40, tool_calls=[
        {"name": "abcd", "args": {}},  # "abcd{}" -> round(1.5) == 2
        {"args": {"a": 1}},  # "" + '{"a":1}' -> round(1.75) == 2
    ])
    assert tokens.rough_token_count_estimation_for_message(msg) == 10 + 2 + 2
    assert tokens.rough_token_count_estimation_for_message(foreign(type="human")) == 0
    assert tokens.rough_token_count_estimation_for_message(foreign()) == 0


def test_a_message_flagged_only_by_api_error_is_synthetic():
    synthetic = AIMessage(content="x", id="r1", additional_kwargs={"api_error": "overloaded"},
                          usage_metadata={"input_tokens": 9, "output_tokens": 1, "total_tokens": 10})
    assert tokens._is_synthetic(synthetic) is True
    assert tokens.get_token_usage(synthetic) is None
    assert tokens._assistant_message_id(synthetic) is None
    assert tokens._assistant_message_id(HumanMessage(content="h", id="h1")) is None
    assert tokens._assistant_message_id(AIMessage(content="a", id="a1")) == "a1"


def test_records_without_the_usual_attributes_have_no_usage_or_id():
    assert tokens.get_token_usage(foreign()) is None
    assert tokens.get_token_usage(foreign(type="ai")) is None
    assert tokens._assistant_message_id(foreign()) is None
    assert tokens._assistant_message_id(foreign(type="ai", additional_kwargs={})) is None


def test_the_walk_back_reaches_a_split_record_at_index_zero():
    usage = {"input_tokens": 1000, "output_tokens": 0, "total_tokens": 1000}
    first = AIMessage(content="a", id="r", usage_metadata=usage)
    second = AIMessage(content="x" * 400, id="r", usage_metadata=usage)
    # The walk back lands on index 0, so the second record (100 tokens) is estimated too.
    assert tokens.token_count_with_estimation([first, second]) == 1000 + 100


@pytest.mark.parametrize("value", ["true", " TRUE ", "yes", "On"])
def test_env_truthy_accepts_upstreams_spellings(monkeypatch, value):
    monkeypatch.setenv("TDDAGENTS_DISABLE_1M_CONTEXT", value)
    assert tokens.has_1m_context("claude-sonnet-4-6[1m]") is False


def test_model_output_table_with_upper_limits():
    assert tokens.get_model_max_output_tokens("claude-opus-4-6") == (64_000, 128_000)
    assert tokens.get_model_max_output_tokens("claude-sonnet-4-6") == (32_000, 128_000)
    assert tokens.get_model_max_output_tokens("claude-opus-4-5") == (32_000, 64_000)
    assert tokens.get_model_max_output_tokens("claude-opus-4-1") == (32_000, 32_000)
    assert tokens.get_model_max_output_tokens("claude-opus-4") == (32_000, 32_000)
    assert tokens.get_model_max_output_tokens("claude-3-opus") == (4_096, 4_096)
    assert tokens.get_model_max_output_tokens("claude-3-sonnet") == (8_192, 8_192)
    assert tokens.get_model_max_output_tokens("claude-3-haiku") == (4_096, 4_096)
    assert tokens.get_model_max_output_tokens("claude-3-5-haiku") == (8_192, 8_192)
    assert tokens.get_model_max_output_tokens("claude-3-7-sonnet") == (32_000, 64_000)


def test_the_override_is_capped_at_exactly_the_upper_limit(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_MAX_OUTPUT_TOKENS", "10000000")
    expected = {
        "claude-opus-4-6": 128_000, "claude-sonnet-4-6": 128_000, "claude-opus-4-5": 64_000,
        "claude-opus-4-1": 32_000, "claude-3-sonnet": 8_192, "claude-3-haiku": 4_096,
        "claude-3-5-sonnet": 8_192, "claude-3-7-sonnet": 64_000,
    }
    for model, upper in expected.items():
        assert tokens.get_max_output_tokens_for_model(model) == upper, model


def test_an_override_of_one_is_honoured(monkeypatch):
    assert tokens.validate_bounded_int_env_var("1", 32_000, 64_000) == 1
    monkeypatch.delenv("TDDAGENTS_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.setenv("TDDAGENTS_AUTO_COMPACT_WINDOW", "1")
    assert tokens.get_effective_context_window_size("o4-mini") == 1 - 20_000


# ── services/api/claude.py ────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["yes", " ON "])
def test_prompt_caching_disable_accepts_yes_and_on(monkeypatch, value):
    monkeypatch.setenv("TDDAGENTS_DISABLE_PROMPT_CACHING", value)
    assert get_prompt_caching_enabled() is False


def test_no_system_text_keeps_the_messages_but_still_marks_the_tail(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_DISABLE_PROMPT_CACHING", raising=False)
    messages = [SystemMessage(content="sys"), HumanMessage(content="hi")]
    assert prepare_cached_request(messages, provider="anthropic", system_texts=[]) == (
        messages, {"cache_control": {"type": "ephemeral"}})


# ── utils/tool_result_storage.py ─────────────────────────────────────────────────

def test_sanitize_path_hashes_only_past_the_maximum():
    assert rs.sanitize_path("a" * 200) == "a" * 200
    assert rs.sanitize_path("a" * 201).startswith("a" * 200 + "-")


def test_safe_file_stem_replaces_and_strips_exactly():
    assert rs.safe_file_stem("a/b") == "a-b"
    assert rs.safe_file_stem("X.y") == "X.y"
    assert rs.safe_file_stem("..x") == "x"


def test_format_file_size_at_gigabytes_and_beyond():
    assert rs.format_file_size(1024 ** 3) == "1GB"
    assert rs.format_file_size(2 * 1024 ** 4) == "2048GB"


def test_a_large_result_preview_says_there_is_more(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    out = rs.maybe_persist_large_result("x" * 60_000, tool_name="Bash", tool_use_id="c1", run_id="r",
                                        working_directory=tmp_path / "ws")
    assert out.endswith("\n...\n" + rs.PERSISTED_OUTPUT_CLOSING_TAG)


def test_content_size_and_text_read_only_text_blocks():
    content = ["raw", {"type": "text", "text": "ab"}, {"type": "text"}, {"type": "other", "text": "zz"},
               {"text": "no-type"}, {"type": "text", "text": "cd"}]
    assert rs._content_size(content) == 4
    assert rs._content_text(content) == "abcd"


def test_candidate_groups_split_only_on_a_new_response():
    def tool(i: int) -> ToolMessage:
        return ToolMessage(content=f"r{i}", tool_call_id=f"c{i}")

    messages = [
        tool(0),
        SimpleNamespace(),  # a record with no type at all
        AIMessage(content="", id="a"), tool(1),
        AIMessage(content="", id="a"), tool(2),  # split from the same response: same group
        AIMessage(content="", id="b"), tool(3),
        SimpleNamespace(type="ai"), tool(4),  # no id attribute: always a new response
        AIMessage(content=""), tool(5),
        SimpleNamespace(type="tool", tool_call_id="c6"),  # no content: not a candidate
    ]
    groups = rs._collect_candidates_by_message(messages)
    assert [[c.tool_use_id for c in g] for g in groups] == [["c0"], ["c1", "c2"], ["c3"], ["c4"], ["c5"]]


def test_tool_name_map_reads_past_non_assistant_records():
    messages = [
        HumanMessage(content="h"),
        SimpleNamespace(),
        SimpleNamespace(type="ai"),
        AIMessage(content="", tool_calls=[{"name": "Grep", "args": {}, "id": "c1"}]),
    ]
    assert rs._tool_name_map(messages) == {"c1": "Grep"}


def test_replace_leaves_records_without_an_id_or_type_alone():
    no_id = SimpleNamespace(type="tool")
    no_type = SimpleNamespace()
    out = rs._replace_tool_result_contents([no_id, no_type], {"None": "r", "XXXX": "r"})
    assert out[0] is no_id and out[1] is no_type


def _turn(*results: tuple[str, str | None, int]) -> list[Any]:
    """One assistant response and its results: (tool_use_id, tool name or None, size)."""
    calls = [{"name": name, "args": {}, "id": cid} for cid, name, _ in results if name is not None]
    out: list[Any] = [AIMessage(content="", tool_calls=calls)]
    out += [ToolMessage(content="x" * size, tool_call_id=cid) for cid, _, size in results]
    return out


def _enforce(messages: list[Any], state: ContentReplacementState, tmp_path: Any, **kw: Any) -> Any:
    return rs.enforce_tool_result_budget(messages, state, run_id="r", working_directory=tmp_path / "ws", **kw)


def test_selection_stops_once_the_total_is_exactly_the_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    messages = _turn(("c1", "Bash", 100_000), ("c2", "Bash", 100_000), ("c3", "Bash", 100_000))
    _, newly = _enforce(messages, ContentReplacementState(), tmp_path)
    assert [r.tool_use_id for r in newly] == ["c1"]


def test_a_skipped_name_is_never_persisted_and_others_still_are(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    # c2 has no tool call in the history, so its name is "" — which this run skips.
    messages = [HumanMessage(content="task"), *_turn(("c1", "Grep", 250_000), ("c2", None, 250_000))]
    state = ContentReplacementState()
    out, newly = _enforce(messages, state, tmp_path, skip_tool_names=frozenset({""}))
    assert [r.tool_use_id for r in newly] == ["c1"]
    assert state.seen_ids == {"c1", "c2"}
    assert out[-1] is messages[-1]


def test_a_settled_group_does_not_stop_later_groups(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    messages = [*_turn(("c1", "Bash", 10)), *_turn(("c2", "Bash", 250_000))]
    state = ContentReplacementState(seen_ids={"c1"})
    _, newly = _enforce(messages, state, tmp_path)
    assert [r.tool_use_id for r in newly] == ["c2"]


def test_a_failed_write_does_not_stop_the_next_replacement(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    real = rs.persist_tool_result
    monkeypatch.setattr(rs, "persist_tool_result",
                        lambda text, cid, run_id, wd: None if cid == "c1" else real(text, cid, run_id, wd))
    messages = _turn(("c1", "Bash", 250_000), ("c2", "Bash", 250_000))
    _, newly = _enforce(messages, ContentReplacementState(), tmp_path)
    assert [r.tool_use_id for r in newly] == ["c2"]


def test_the_replacement_lands_in_the_runs_store_and_says_there_is_more(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    messages = _turn(("c1", "Bash", 250_000))
    out = rs.apply_tool_result_budget(messages, ContentReplacementState(), run_id="r",
                                      working_directory=tmp_path / "ws")
    path = rs.tool_results_dir("r", tmp_path / "ws") / "c1.txt"
    assert path.read_text() == "x" * 250_000
    assert f"Full output saved to: {path}\n" in out[-1].content
    assert out[-1].content.endswith("\n...\n" + rs.PERSISTED_OUTPUT_CLOSING_TAG)


def test_provisioning_from_history_restores_the_recorded_replacements():
    messages = _turn(("c1", "Bash", 10))
    record = rs.ToolResultReplacementRecord(tool_use_id="c1", replacement="prev")
    state = rs.provision_content_replacement_state(messages, [record], enabled=True)
    assert state is not None and state.replacements == {"c1": "prev"}


# ── query/query.py ───────────────────────────────────────────────────────────────

def _capture_budget(monkeypatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def fake(messages, state, **kw):
        seen.append({"messages": list(messages), "state": state, **kw})
        return list(messages)

    monkeypatch.setattr(engine, "apply_tool_result_budget", fake)
    return seen


def test_the_engine_budget_skips_tools_that_opted_out(monkeypatch, tmp_path):
    seen = _capture_budget(monkeypatch)
    crs = ContentReplacementState()
    tools = (
        SimpleNamespace(name="ReadFile", max_result_size_chars=math.inf),
        SimpleNamespace(max_result_size_chars=math.inf),  # unnamed
        SimpleNamespace(name="Bash"),  # no declared size: finite
        SimpleNamespace(name="Grep", max_result_size_chars=100),
    )
    context = SimpleNamespace(content_replacement_state=crs, workspace=SimpleNamespace(root=tmp_path), tools=tools)
    messages = (HumanMessage(content="m"),)
    assert engine._apply_tool_result_budget(messages, context, CONFIG) == list(messages)
    (call,) = seen
    assert call == {"messages": list(messages), "state": crs, "run_id": "budget-run",
                    "working_directory": str(tmp_path), "skip_tool_names": frozenset({"ReadFile", ""})}


def test_the_engine_budget_without_a_workspace_or_tools(monkeypatch):
    seen = _capture_budget(monkeypatch)
    crs = ContentReplacementState()
    engine._apply_tool_result_budget((), SimpleNamespace(content_replacement_state=crs), CONFIG)
    engine._apply_tool_result_budget((), SimpleNamespace(content_replacement_state=crs, workspace=object(),
                                                         tools=None), CONFIG)
    assert [(c["working_directory"], c["skip_tool_names"]) for c in seen] == [(".", frozenset())] * 2


def test_the_engine_budget_is_off_without_a_state(monkeypatch):
    seen = _capture_budget(monkeypatch)
    messages = (HumanMessage(content="m"),)
    assert engine._apply_tool_result_budget(messages, SimpleNamespace(), CONFIG) == list(messages)
    assert seen == []


def _run(state: Any, deps: Any) -> list[Any]:
    async def go() -> list[Any]:
        return [e async for e in run_loop(state, CONFIG, deps)]

    return asyncio.run(go())


def test_the_loop_budgets_with_its_own_context_and_config(monkeypatch):
    seen = _capture_budget(monkeypatch)
    crs = ContentReplacementState()
    context = dataclasses.replace(tool_context_for(AppStateStore()), content_replacement_state=crs)
    state = initial_loop_state((HumanMessage(content="task"),), context)
    _run(state, make_fake_deps(FakeModel([[AIMessage(content="done")]])))
    assert seen[0]["state"] is crs and seen[0]["run_id"] == "budget-run"


def test_microcompact_receives_the_runs_config():
    configs: list[Any] = []

    async def micro(messages, state, config):
        configs.append(config)
        return messages

    deps = dataclasses.replace(make_fake_deps(FakeModel([[AIMessage(content="done")]])), microcompact=micro)
    _run(initial_loop_state((HumanMessage(content="task"),), tool_context_for(AppStateStore())), deps)
    assert configs == [CONFIG]


def _compaction() -> CompactionResult:
    return CompactionResult(boundary_marker=create_compact_boundary_message("auto", 10),
                            summary_messages=[HumanMessage(content="summary")], attachments=[], hook_results=[])


def test_a_result_without_was_compacted_is_not_a_compaction():
    async def auto(messages, state, config):
        return AutoCompactOutcome(was_compacted=False, compaction_result=_compaction())

    task = HumanMessage(content="task")
    model = FakeModel([[AIMessage(content="done")]])
    deps = dataclasses.replace(make_fake_deps(model), autocompact=auto)
    _run(initial_loop_state((task,), tool_context_for(AppStateStore())), deps)
    assert list(model.seen[0].messages) == [task]


def test_the_blocking_precheck_is_skipped_right_after_a_compaction(monkeypatch):
    monkeypatch.setattr(engine, "token_count_with_estimation", lambda messages: 10_000_000)
    monkeypatch.setattr(engine, "is_auto_compact_enabled", lambda: False)

    async def auto(messages, state, config):
        return AutoCompactOutcome(was_compacted=True, compaction_result=_compaction(), consecutive_failures=0)

    model = FakeModel([[AIMessage(content="done")]])
    deps = dataclasses.replace(make_fake_deps(model), autocompact=auto)
    events = _run(initial_loop_state((HumanMessage(content="task"),), tool_context_for(AppStateStore())), deps)
    assert len(model.seen) == 1
    assert events[-1] != Terminated(reason=Terminal.BLOCKING_LIMIT)


def test_the_blocking_limit_message_is_an_invalid_request_api_error(monkeypatch):
    monkeypatch.setattr(engine, "token_count_with_estimation", lambda messages: 10_000_000)
    monkeypatch.setattr(engine, "is_auto_compact_enabled", lambda: False)
    events = _run(initial_loop_state((HumanMessage(content="task"),), tool_context_for(AppStateStore())),
                  make_fake_deps(FakeModel([])))
    assert events[-2].additional_kwargs == {"is_api_error_message": True, "api_error": "invalid_request"}


def test_stop_failure_on_prompt_too_long_reads_the_turns_history():
    from tests.hook_doubles import ScriptedDispatcher
    from tests.test_loop_hooks_call_sites import Model, ctx, deps

    d = ScriptedDispatcher()
    ptl = AIMessage(content="too long", additional_kwargs={"api_error": "prompt_too_long"})
    state = initial_loop_state((HumanMessage(content="t"),), ctx(d))
    asyncio.run(drain(run_loop(state, CONFIG, deps(Model([[ptl]])))))
    assert d.payload("StopFailure")["last_assistant_message"] == "too long"


# ── tools/agent_tool/agent_memory.py ────────────────────────────────────────────────────────

def test_session_memory_without_an_id_uses_the_default_session(tmp_path):
    store = AgentMemoryStore(base_dir=tmp_path, user_home=tmp_path / "home")
    assert store.get_memory_dir("tester", "session") == (
        tmp_path / ".tddagents" / "session-memory" / "default_session" / "tester")


@pytest.mark.parametrize("scope,description", [
    ("session", "session-scoped (preserved across queries within this active session)"),
    ("local", "local-scoped (persisted on this machine, untracked by VCS)"),
    ("project", "project-scoped (shared via version control)"),
    ("user", "user-scoped (universal across all projects on this machine)"),
])
def test_each_scope_is_described_in_the_prompt(tmp_path, scope, description):
    store = AgentMemoryStore(base_dir=tmp_path, user_home=tmp_path / "home")
    prompt = store.load_memory_prompt("tester", scope, session_id="s")
    assert f"You have access to a {description} memory store at: " in prompt


def test_existing_notes_follow_a_blank_line(tmp_path):
    store = AgentMemoryStore(base_dir=tmp_path, user_home=tmp_path / "home")
    mdir = store.ensure_memory_dir_exists("tester", "project")
    assert mdir is not None
    (mdir / "MEMORY.md").write_text("  remember this  \n")
    prompt = store.load_memory_prompt("tester", "project")
    assert prompt.endswith("decisions.\n\n## Existing Memory Notes:\nremember this")
