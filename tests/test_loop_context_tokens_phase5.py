"""Phase 5: token counting with the last API response, and the context window (offline)."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.utils.tokens import (
    COMPACT_MAX_OUTPUT_TOKENS,
    MODEL_CONTEXT_WINDOW_DEFAULT,
    get_context_window_for_model,
    get_effective_context_window_size,
    get_max_output_tokens_for_model,
    rough_token_count_estimation,
    rough_token_count_estimation_for_messages,
    token_count_with_estimation,
)


def usage(input_tokens: int, output_tokens: int) -> dict[str, int]:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}


# ── roughTokenCountEstimation ────────────────────────────────────────────────

def test_rough_estimation_rounds_half_up_like_math_round():
    assert rough_token_count_estimation("") == 0
    assert rough_token_count_estimation("a") == 0  # 0.25 -> 0
    assert rough_token_count_estimation("ab") == 1  # 0.5 -> 1 (Math.round), not banker's 0
    assert rough_token_count_estimation("abc") == 1  # 0.75 -> 1
    assert rough_token_count_estimation("abcdef") == 2  # 1.5 -> 2
    assert rough_token_count_estimation("abcdefghij") == 3  # 2.5 -> 3
    assert rough_token_count_estimation("ab", bytes_per_token=2) == 1


def test_rough_estimation_per_message_kind():
    text = "x" * 40  # 10 tokens
    assert rough_token_count_estimation_for_messages([HumanMessage(content=text)]) == 10
    assert rough_token_count_estimation_for_messages([AIMessage(content=text)]) == 10
    assert rough_token_count_estimation_for_messages([ToolMessage(content=text, tool_call_id="t")]) == 10
    # A system message is not counted (upstream counts only user, assistant, attachment).
    assert rough_token_count_estimation_for_messages([SystemMessage(content=text)]) == 0


def test_rough_estimation_of_blocks_and_tool_calls():
    blocks = AIMessage(content=[
        {"type": "text", "text": "x" * 8},
        {"type": "thinking", "thinking": "y" * 12},
        {"type": "image", "source": {}},
        {"type": "image_url", "image_url": {"url": "data:"}},
        {"type": "document", "source": {}},
        "zzzz",
    ])
    assert rough_token_count_estimation_for_messages([blocks]) == 2 + 3 + 2000 + 2000 + 2000 + 1
    call = AIMessage(content="", tool_calls=[{"name": "Bash", "args": {"command": "ls"}, "id": "c1"}])
    # name + compact JSON input: "Bash" + '{"command":"ls"}' = 4 + 16 = 20 chars -> 5 tokens
    assert rough_token_count_estimation_for_messages([call]) == 5
    other = HumanMessage(content=[{"type": "custom", "value": "abcd"}])
    assert rough_token_count_estimation_for_messages([other]) == round(len('{"type":"custom","value":"abcd"}') / 4)


def test_rough_estimation_of_tool_message_block_content():
    tool = ToolMessage(content=[{"type": "text", "text": "x" * 20}], tool_call_id="t")
    assert rough_token_count_estimation_for_messages([tool]) == 5
    assert rough_token_count_estimation_for_messages([HumanMessage(content="")]) == 0


# ── tokenCountWithEstimation ─────────────────────────────────────────────────

def test_without_usage_everything_is_estimated():
    msgs = [HumanMessage(content="x" * 40), AIMessage(content="y" * 40)]
    assert token_count_with_estimation(msgs) == 20


def test_the_last_usage_plus_an_estimate_of_what_follows():
    msgs = [
        HumanMessage(content="x" * 4000),
        AIMessage(content="old", usage_metadata=usage(50, 5)),
        HumanMessage(content="x" * 400),
        AIMessage(content="new", id="r2", usage_metadata=usage(1000, 200)),
        ToolMessage(content="z" * 40, tool_call_id="t"),
        HumanMessage(content="w" * 40),
    ]
    assert token_count_with_estimation(msgs) == 1200 + 10 + 10


def test_cache_tokens_are_part_of_langchains_input_count():
    message = AIMessage(content="a", usage_metadata={
        "input_tokens": 300, "output_tokens": 10, "total_tokens": 310,
        "input_token_details": {"cache_read": 200, "cache_creation": 50},
    })
    assert token_count_with_estimation([message]) == 310


def test_split_records_of_one_response_are_walked_back():
    """Interleaved results between records of one API response are estimated, not lost."""
    msgs = [
        HumanMessage(content="q"),
        AIMessage(content="", id="resp", tool_calls=[{"name": "A", "args": {}, "id": "a"}]),
        ToolMessage(content="r" * 40, tool_call_id="a"),
        AIMessage(content="", id="resp", usage_metadata=usage(100, 10),
                  tool_calls=[{"name": "B", "args": {}, "id": "b"}]),
    ]
    # Walks back to the first record of "resp", so the 10-token tool result and the
    # second record (B{} = 3 chars -> 1 token) are estimated on top of the usage.
    assert token_count_with_estimation(msgs) == 110 + 10 + 1


def test_the_walk_back_stops_at_a_different_response():
    msgs = [
        AIMessage(content="", id="first", usage_metadata=usage(10, 1)),
        ToolMessage(content="r" * 40, tool_call_id="a"),
        AIMessage(content="", id="second", usage_metadata=usage(100, 10)),
        HumanMessage(content="x" * 8),
    ]
    assert token_count_with_estimation(msgs) == 110 + 2


def test_a_synthetic_api_error_message_has_no_usage():
    error = AIMessage(content="API Error", usage_metadata=usage(999, 999),
                      additional_kwargs={"is_api_error_message": True})
    msgs = [AIMessage(content="", usage_metadata=usage(100, 10)), error]
    assert token_count_with_estimation(msgs) == 110 + 2


def test_an_assistant_message_with_zero_usage_still_counts():
    msgs = [AIMessage(content="abcd", usage_metadata=usage(0, 0)), HumanMessage(content="x" * 4)]
    assert token_count_with_estimation(msgs) == 0 + 1


# ── context window and output tokens ─────────────────────────────────────────

def test_upstream_constants():
    assert MODEL_CONTEXT_WINDOW_DEFAULT == 200_000
    assert COMPACT_MAX_OUTPUT_TOKENS == 20_000


def test_an_unknown_model_gets_the_default_window(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_DISABLE_1M_CONTEXT", raising=False)
    assert get_context_window_for_model("o4-mini") == 200_000
    assert get_context_window_for_model("claude-sonnet-4-6[1m]") == 1_000_000
    assert get_context_window_for_model("claude-sonnet-4-6[1M]") == 1_000_000
    monkeypatch.setenv("TDDAGENTS_DISABLE_1M_CONTEXT", "1")
    assert get_context_window_for_model("claude-sonnet-4-6[1m]") == 200_000


def test_max_output_tokens_table(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_MAX_OUTPUT_TOKENS", raising=False)
    cases = {
        "o4-mini": 32_000,
        "claude-opus-4-6": 64_000,
        "claude-sonnet-4-6": 32_000,
        "claude-opus-4-5": 32_000,
        "claude-sonnet-4": 32_000,
        "claude-haiku-4-5": 32_000,
        "claude-opus-4-1": 32_000,
        "claude-opus-4": 32_000,
        "claude-3-opus": 4_096,
        "claude-3-sonnet": 8_192,
        "claude-3-haiku": 4_096,
        "claude-3-5-sonnet": 8_192,
        "claude-3-5-haiku": 8_192,
        "claude-3-7-sonnet": 32_000,
        "CLAUDE-OPUS-4-6": 64_000,
    }
    for model, expected in cases.items():
        assert get_max_output_tokens_for_model(model) == expected, model


def test_max_output_tokens_env_override_is_bounded(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_MAX_OUTPUT_TOKENS", "1000")
    assert get_max_output_tokens_for_model("o4-mini") == 1000
    monkeypatch.setenv("TDDAGENTS_MAX_OUTPUT_TOKENS", "999999")
    assert get_max_output_tokens_for_model("o4-mini") == 64_000  # capped at the upper limit
    assert get_max_output_tokens_for_model("claude-3-opus") == 4_096
    for bad in ("abc", "0", "-5", ""):
        monkeypatch.setenv("TDDAGENTS_MAX_OUTPUT_TOKENS", bad)
        assert get_max_output_tokens_for_model("o4-mini") == 32_000


def test_effective_window_reserves_the_summary_output(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_AUTO_COMPACT_WINDOW", raising=False)
    monkeypatch.delenv("TDDAGENTS_MAX_OUTPUT_TOKENS", raising=False)
    assert get_effective_context_window_size("o4-mini") == 200_000 - 20_000
    # A model whose output is below the summary reservation reserves only that.
    assert get_effective_context_window_size("claude-3-opus") == 200_000 - 4_096
    monkeypatch.setenv("TDDAGENTS_AUTO_COMPACT_WINDOW", "100000")
    assert get_effective_context_window_size("o4-mini") == 100_000 - 20_000
    monkeypatch.setenv("TDDAGENTS_AUTO_COMPACT_WINDOW", "999999999")
    assert get_effective_context_window_size("o4-mini") == 180_000  # min with the real window
    for bad in ("x", "0", "-1"):
        monkeypatch.setenv("TDDAGENTS_AUTO_COMPACT_WINDOW", bad)
        assert get_effective_context_window_size("o4-mini") == 180_000
    monkeypatch.setenv("TDDAGENTS_AUTO_COMPACT_WINDOW", "150000abc")
    assert get_effective_context_window_size("o4-mini") == 130_000  # parseInt reads the leading digits
