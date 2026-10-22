"""Phase 6: model prices (`utils/modelCost.ts`) — Claude's table ported, OpenAI's added."""

from __future__ import annotations

import pytest

from app.loop.cost.model_cost import (
    COST_HAIKU_35,
    COST_HAIKU_45,
    COST_TIER_3_15,
    COST_TIER_5_25,
    COST_TIER_15_75,
    MODEL_COSTS,
    OPENAI_MODEL_COSTS,
    ModelCosts,
    Usage,
    calculate_usd_cost,
    format_model_pricing,
    get_canonical_name,
    get_model_costs,
    tokens_to_usd_cost,
)


@pytest.mark.parametrize(
    ("model", "canonical"),
    [
        ("claude-opus-4-6", "claude-opus-4-6"),
        ("claude-opus-4-5-20251101", "claude-opus-4-5"),
        ("claude-opus-4-1-20250805", "claude-opus-4-1"),
        ("claude-opus-4-20250514", "claude-opus-4"),
        ("claude-sonnet-4-6", "claude-sonnet-4-6"),
        ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5"),
        ("claude-sonnet-4-20250514", "claude-sonnet-4"),
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
        ("claude-3-7-sonnet-20250219", "claude-3-7-sonnet"),
        ("claude-3-5-sonnet-20241022", "claude-3-5-sonnet"),
        ("claude-3-5-haiku-20241022", "claude-3-5-haiku"),
        ("claude-3-opus-20240229", "claude-3-opus"),
        ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", "claude-sonnet-4-5"),
        ("CLAUDE-SONNET-4-5", "claude-sonnet-4-5"),
        ("o4-mini-2025-04-16", "o4-mini"),
        ("o4-mini", "o4-mini"),
        ("o3-2025-04-16", "o3"),
        ("gpt-4.1-mini-2025-04-14", "gpt-4.1-mini"),
        ("gpt-4.1-nano", "gpt-4.1-nano"),
        ("gpt-4.1-2025-04-14", "gpt-4.1"),
        ("gpt-4o-mini-2024-07-18", "gpt-4o-mini"),
        ("gpt-4o-2024-08-06", "gpt-4o"),
        ("gpt-5-mini-2025-08-07", "gpt-5-mini"),
        ("gpt-5-nano", "gpt-5-nano"),
        ("gpt-5-2025-08-07", "gpt-5"),
        ("openai/o4-mini", "o4-mini"),
        ("mystery-model-9", "mystery-model-9"),
    ],
)
def test_canonical_names(model, canonical):
    assert get_canonical_name(model) == canonical


def test_the_claude_table_is_upstreams():
    assert MODEL_COSTS["claude-3-5-haiku"] is COST_HAIKU_35
    assert MODEL_COSTS["claude-haiku-4-5"] is COST_HAIKU_45
    for name in ("claude-3-5-sonnet", "claude-3-7-sonnet", "claude-sonnet-4", "claude-sonnet-4-5",
                 "claude-sonnet-4-6"):
        assert MODEL_COSTS[name] is COST_TIER_3_15
    for name in ("claude-opus-4", "claude-opus-4-1"):
        assert MODEL_COSTS[name] is COST_TIER_15_75
    for name in ("claude-opus-4-5", "claude-opus-4-6"):
        assert MODEL_COSTS[name] is COST_TIER_5_25
    assert len(MODEL_COSTS) == 11
    assert COST_TIER_3_15 == ModelCosts(3, 15, 3.75, 0.3, 0.01)
    assert COST_TIER_15_75 == ModelCosts(15, 75, 18.75, 1.5, 0.01)
    assert COST_TIER_5_25 == ModelCosts(5, 25, 6.25, 0.5, 0.01)
    assert COST_HAIKU_35 == ModelCosts(0.8, 4, 1, 0.08, 0.01)
    assert COST_HAIKU_45 == ModelCosts(1, 5, 1.25, 0.1, 0.01)


def test_the_openai_table():
    assert OPENAI_MODEL_COSTS["o4-mini"] == ModelCosts(1.1, 4.4, 1.1, 0.275, 0)
    assert OPENAI_MODEL_COSTS["o3"] == ModelCosts(2, 8, 2, 0.5, 0)
    assert OPENAI_MODEL_COSTS["gpt-4.1"] == ModelCosts(2, 8, 2, 0.5, 0)
    assert OPENAI_MODEL_COSTS["gpt-4.1-mini"] == ModelCosts(0.4, 1.6, 0.4, 0.1, 0)
    assert OPENAI_MODEL_COSTS["gpt-4.1-nano"] == ModelCosts(0.1, 0.4, 0.1, 0.025, 0)
    assert OPENAI_MODEL_COSTS["gpt-4o"] == ModelCosts(2.5, 10, 2.5, 1.25, 0)
    assert OPENAI_MODEL_COSTS["gpt-4o-mini"] == ModelCosts(0.15, 0.6, 0.15, 0.075, 0)
    assert OPENAI_MODEL_COSTS["gpt-5"] == ModelCosts(1.25, 10, 1.25, 0.125, 0)
    assert OPENAI_MODEL_COSTS["gpt-5-mini"] == ModelCosts(0.25, 2, 0.25, 0.025, 0)
    assert OPENAI_MODEL_COSTS["gpt-5-nano"] == ModelCosts(0.05, 0.4, 0.05, 0.005, 0)
    assert len(OPENAI_MODEL_COSTS) == 10


def test_tokens_to_usd_cost_sums_every_component():
    costs = ModelCosts(1, 2, 3, 4, 5)
    usage = Usage(input_tokens=1_000_000, output_tokens=2_000_000, cache_read_input_tokens=3_000_000,
                  cache_creation_input_tokens=4_000_000, web_search_requests=6)
    assert tokens_to_usd_cost(costs, usage) == pytest.approx(1 + 4 + 12 + 12 + 30)


def test_known_models_are_priced_and_unknown_ones_are_free_and_flagged():
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert get_model_costs("claude-sonnet-4-5") == (COST_TIER_3_15, True)
    assert get_model_costs("o4-mini-2025-04-16") == (OPENAI_MODEL_COSTS["o4-mini"], True)
    assert get_model_costs("mystery") == (None, False)
    cost, known = calculate_usd_cost("claude-sonnet-4-5", usage)
    assert cost == pytest.approx(18) and known is True
    cost, known = calculate_usd_cost("o4-mini", usage)
    assert cost == pytest.approx(5.5) and known is True
    assert calculate_usd_cost("mystery", usage) == (0.0, False)


def test_format_model_pricing():
    assert format_model_pricing(COST_TIER_3_15) == "$3/$15 per Mtok"
    assert format_model_pricing(COST_HAIKU_35) == "$0.80/$4 per Mtok"
    assert format_model_pricing(ModelCosts(22.5, 1.1, 0, 0, 0)) == "$22.50/$1.10 per Mtok"
