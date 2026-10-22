"""
Model prices (Phase 6).

Ported from `reference/claude-code/src/utils/modelCost.ts` -> `MODEL_COSTS`,
`tokensToUSDCost`, `getModelCosts`, `calculateUSDCost`, `formatModelPricing`, and
`utils/model/model.ts` -> `firstPartyNameToCanonical` / `getCanonicalName`.

Two deliberate divergences, both at the user's direction (2026-10-22):

* **An OpenAI table sits beside upstream's Claude table**, because this project's default
  model is OpenAI's. Its prices are maintained by hand (source and date on the table).
  OpenAI charges no surcharge for writing its automatic prompt cache, so the cache-write
  price is the input price; web-search requests are not priced (no tool here makes them).
* **An unknown model costs nothing and is flagged.** Upstream's `getModelCosts` falls back
  to the default model's prices and calls `setHasUnknownModelCost`; charging Claude prices
  for a model that is neither Claude nor in the OpenAI table would report a cost that is
  simply wrong. The flag is kept, so the total still says it may be inaccurate.

Not ported: Opus 4.6's fast-mode tier (`getOpus46CostTier`), which depends on first-party
fast mode this transport does not have; `resolveOverriddenModel` (Bedrock ARNs) — a provider
prefix such as `us.anthropic.` is matched through by `firstPartyNameToCanonical`'s
substring checks, exactly as upstream's are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelCosts:
    """`ModelCosts`: USD per million tokens, and per web-search request."""

    input_tokens: float
    output_tokens: float
    prompt_cache_write_tokens: float
    prompt_cache_read_tokens: float
    web_search_requests: float


@dataclass(frozen=True)
class Usage:
    """The fields of the API's `Usage` that cost reads, in Anthropic's shape (input excludes cache)."""

    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    web_search_requests: int = 0


#: Standard pricing tier for Sonnet models: $3 input / $15 output per Mtok.
COST_TIER_3_15 = ModelCosts(3, 15, 3.75, 0.3, 0.01)
#: Pricing tier for Opus 4/4.1: $15 input / $75 output per Mtok.
COST_TIER_15_75 = ModelCosts(15, 75, 18.75, 1.5, 0.01)
#: Pricing tier for Opus 4.5: $5 input / $25 output per Mtok.
COST_TIER_5_25 = ModelCosts(5, 25, 6.25, 0.5, 0.01)
#: Pricing for Haiku 3.5: $0.80 input / $4 output per Mtok.
COST_HAIKU_35 = ModelCosts(0.8, 4, 1, 0.08, 0.01)
#: Pricing for Haiku 4.5: $1 input / $5 output per Mtok.
COST_HAIKU_45 = ModelCosts(1, 5, 1.25, 0.1, 0.01)

#: `MODEL_COSTS`, keyed by canonical short name, exactly upstream's entries.
MODEL_COSTS: dict[str, ModelCosts] = {
    "claude-3-5-haiku": COST_HAIKU_35,
    "claude-haiku-4-5": COST_HAIKU_45,
    "claude-3-5-sonnet": COST_TIER_3_15,
    "claude-3-7-sonnet": COST_TIER_3_15,
    "claude-sonnet-4": COST_TIER_3_15,
    "claude-sonnet-4-5": COST_TIER_3_15,
    "claude-sonnet-4-6": COST_TIER_3_15,
    "claude-opus-4": COST_TIER_15_75,
    "claude-opus-4-1": COST_TIER_15_75,
    "claude-opus-4-5": COST_TIER_5_25,
    "claude-opus-4-6": COST_TIER_5_25,
}

#: OpenAI standard-tier prices (input / output / cache write = input / cached input), per Mtok.
#: Checked 2026-10-22, maintained by hand. Only o4-mini was confirmed on OpenAI's own model page
#: (https://platform.openai.com/docs/models/o4-mini); the other entries are launch prices
#: cross-checked against aggregator tables, kept at the user's direction — re-verify them
#: against https://platform.openai.com/docs/pricing before relying on a figure.
OPENAI_MODEL_COSTS: dict[str, ModelCosts] = {
    "o4-mini": ModelCosts(1.1, 4.4, 1.1, 0.275, 0),
    "o3": ModelCosts(2, 8, 2, 0.5, 0),
    "gpt-4.1": ModelCosts(2, 8, 2, 0.5, 0),
    "gpt-4.1-mini": ModelCosts(0.4, 1.6, 0.4, 0.1, 0),
    "gpt-4.1-nano": ModelCosts(0.1, 0.4, 0.1, 0.025, 0),
    "gpt-4o": ModelCosts(2.5, 10, 2.5, 1.25, 0),
    "gpt-4o-mini": ModelCosts(0.15, 0.6, 0.15, 0.075, 0),
    "gpt-5": ModelCosts(1.25, 10, 1.25, 0.125, 0),
    "gpt-5-mini": ModelCosts(0.25, 2, 0.25, 0.025, 0),
    "gpt-5-nano": ModelCosts(0.05, 0.4, 0.05, 0.005, 0),
}

#: `firstPartyNameToCanonical`'s checks, most specific first (order matters: 4-5 before 4).
_CLAUDE_CANONICAL = (
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-opus-4-1",
    "claude-opus-4",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-sonnet-4",
    "claude-haiku-4-5",
    "claude-3-7-sonnet",
    "claude-3-5-sonnet",
    "claude-3-5-haiku",
    "claude-3-opus",
)

#: An OpenAI id is a table key, optionally followed by its snapshot date (`o4-mini-2025-04-16`).
#: Exact, not a substring, so `o3-mini` is not priced as `o3`.
_OPENAI_SNAPSHOT = re.compile(r"^(?P<name>.+?)(?:-\d{4}-\d{2}-\d{2})?$")


def get_canonical_name(model: str) -> str:
    """`getCanonicalName`; an unrecognized name is returned lowercased, as upstream's fallthrough."""
    name = model.lower()
    for canonical in _CLAUDE_CANONICAL:
        if canonical in name:
            return canonical
    bare = name.rsplit("/", 1)[-1]
    match = _OPENAI_SNAPSHOT.match(bare)
    if match is not None and match.group("name") in OPENAI_MODEL_COSTS:
        return match.group("name")
    return name


def tokens_to_usd_cost(costs: ModelCosts, usage: Usage) -> float:
    """`tokensToUSDCost`."""
    return (
        usage.input_tokens / 1_000_000 * costs.input_tokens
        + usage.output_tokens / 1_000_000 * costs.output_tokens
        + usage.cache_read_input_tokens / 1_000_000 * costs.prompt_cache_read_tokens
        + usage.cache_creation_input_tokens / 1_000_000 * costs.prompt_cache_write_tokens
        + usage.web_search_requests * costs.web_search_requests
    )


def get_model_costs(model: str) -> tuple[ModelCosts | None, bool]:
    """`getModelCosts`: the prices and whether the model is known (no fallback; see module)."""
    short_name = get_canonical_name(model)
    costs = MODEL_COSTS.get(short_name) or OPENAI_MODEL_COSTS.get(short_name)
    return costs, costs is not None


def calculate_usd_cost(model: str, usage: Usage) -> tuple[float, bool]:
    """`calculateUSDCost`: the cost and whether it is known; an unknown model costs 0."""
    costs, known = get_model_costs(model)
    return (tokens_to_usd_cost(costs, usage) if costs is not None else 0.0), known


def _format_price(price: float) -> str:
    """`formatPrice`: integers without decimals, others with two."""
    return f"${int(price)}" if float(price).is_integer() else f"${price:.2f}"


def format_model_pricing(costs: ModelCosts) -> str:
    """`formatModelPricing`: e.g. `$3/$15 per Mtok`."""
    return f"{_format_price(costs.input_tokens)}/{_format_price(costs.output_tokens)} per Mtok"


__all__ = [
    "COST_HAIKU_35",
    "COST_HAIKU_45",
    "COST_TIER_3_15",
    "COST_TIER_5_25",
    "COST_TIER_15_75",
    "MODEL_COSTS",
    "OPENAI_MODEL_COSTS",
    "ModelCosts",
    "Usage",
    "calculate_usd_cost",
    "format_model_pricing",
    "get_canonical_name",
    "get_model_costs",
    "tokens_to_usd_cost",
]
