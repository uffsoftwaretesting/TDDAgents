"""
Extended thinking: which models support it, the run's configuration, and the request parameter.

Ported from:
- `reference/claude-code/src/utils/thinking.ts` -> `ThinkingConfig`, `modelSupportsThinking`,
  `modelSupportsAdaptiveThinking`, `shouldEnableThinkingByDefault`,
- `reference/claude-code/src/utils/context.ts` -> `getMaxThinkingTokensForModel`,
- `reference/claude-code/src/main.tsx` -> the `--thinking` / `--max-thinking-tokens` /
  `MAX_THINKING_TOKENS` resolution,
- `reference/claude-code/src/services/api/claude.ts` -> the `thinking` request parameter (adaptive
  when the model supports it, else a budget below `max_tokens`) and its rule that temperature is
  sent only while thinking is off.

**TDDAgents** (user decisions 2026-11-01):
- **Thinking is off unless asked**: upstream enables it by default; here `None` (nothing asked) is
  off, and it is on only through `--thinking`, `--max-thinking-tokens`, `MAX_THINKING_TOKENS`, the
  setting `alwaysThinkingEnabled: true` or the SDK's `set_max_thinking_tokens`.
- **OpenAI runs map it to `reasoning_effort`** on reasoning models (`openai_reasoning_effort`):
  disabled is `minimal`, adaptive is `medium`, and a budget picks `low` / `medium` / `high` by the
  `OPENAI_REASONING_BUDGET_TIERS` bounds. Upstream has no OpenAI provider; the mapping is ours.
- The 3P (Bedrock/Vertex) and `ant` branches do not apply: TDDAgents is first party only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from app.utils.claudemd import is_env_truthy
from app.utils.model_cost import get_canonical_name
from app.utils.tokens import get_model_max_output_tokens

#: `ThinkingConfig`: `{"type": "adaptive"}`, `{"type": "disabled"}` or
#: `{"type": "enabled", "budgetTokens": n}`; `None` is "not asked" (off here).
ThinkingConfig = Mapping[str, Any]

DISABLED: dict[str, Any] = {"type": "disabled"}
ADAPTIVE: dict[str, Any] = {"type": "adaptive"}
#: `CLAUDE_CODE_DISABLE_THINKING` / `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING`.
DISABLE_THINKING_ENV = "TDDAGENTS_DISABLE_THINKING"
DISABLE_ADAPTIVE_THINKING_ENV = "TDDAGENTS_DISABLE_ADAPTIVE_THINKING"
#: Upstream reads `MAX_THINKING_TOKENS` under this very name.
MAX_THINKING_TOKENS_ENV = "MAX_THINKING_TOKENS"
#: The budgets (exclusive upper bounds) of `low` and `medium` reasoning effort; above is `high`.
OPENAI_REASONING_BUDGET_TIERS: tuple[tuple[int, str], ...] = ((4_096, "low"), (16_384, "medium"))
#: The OpenAI model families that take `reasoning_effort`.
OPENAI_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def model_supports_thinking(model: str) -> bool:
    """`modelSupportsThinking`, first party: every Claude model but the 3 family."""
    return "claude-3-" not in get_canonical_name(model)


def model_supports_adaptive_thinking(model: str) -> bool:
    """`modelSupportsAdaptiveThinking`, first party: the 4-6 models, never another known family."""
    canonical = get_canonical_name(model)
    if "opus-4-6" in canonical or "sonnet-4-6" in canonical:
        return True
    return not any(family in canonical for family in ("opus", "sonnet", "haiku"))


def get_max_thinking_tokens_for_model(model: str) -> int:
    """`getMaxThinkingTokensForModel`: one below the model's output upper limit."""
    return get_model_max_output_tokens(model)[1] - 1


def should_enable_thinking_by_default(settings: Mapping[str, Any]) -> bool:
    """
    `shouldEnableThinkingByDefault`, with TDDAgents' default off: `MAX_THINKING_TOKENS` > 0, or the
    setting `alwaysThinkingEnabled: true`.
    """
    raw = os.environ.get(MAX_THINKING_TOKENS_ENV)
    if raw:
        try:
            return int(raw) > 0
        except ValueError:
            return False
    return settings.get("alwaysThinkingEnabled") is True


def resolve_thinking_config(
    thinking: str | None, max_thinking_tokens: int | None, settings: Mapping[str, Any]
) -> ThinkingConfig | None:
    """
    `main.tsx`: `--thinking` first, then `MAX_THINKING_TOKENS` / `--max-thinking-tokens` (>0 a
    budget, 0 off), else the default (`should_enable_thinking_by_default`: adaptive, or `None`).
    """
    if thinking in ("adaptive", "enabled"):
        return dict(ADAPTIVE)
    if thinking == "disabled":
        return dict(DISABLED)
    raw = os.environ.get(MAX_THINKING_TOKENS_ENV)
    tokens = int(raw) if raw and raw.lstrip("-").isdigit() else max_thinking_tokens
    if tokens is not None:
        if tokens > 0:
            return {"type": "enabled", "budgetTokens": tokens}
        if tokens == 0:
            return dict(DISABLED)
    return dict(ADAPTIVE) if should_enable_thinking_by_default(settings) else None


def anthropic_thinking_param(
    model: str, thinking_config: ThinkingConfig | None, max_output_tokens: int
) -> dict[str, Any] | None:
    """`claude.ts`: the request's `thinking`, or `None` when thinking is off or unsupported."""
    if thinking_config is None or thinking_config.get("type") == "disabled":
        return None
    if is_env_truthy(os.environ.get(DISABLE_THINKING_ENV)) or not model_supports_thinking(model):
        return None
    if not is_env_truthy(os.environ.get(DISABLE_ADAPTIVE_THINKING_ENV)) and model_supports_adaptive_thinking(model):
        return {"type": "adaptive"}
    budget = get_max_thinking_tokens_for_model(model)
    if thinking_config.get("type") == "enabled" and thinking_config.get("budgetTokens") is not None:
        budget = int(thinking_config["budgetTokens"])
    return {"type": "enabled", "budget_tokens": min(max_output_tokens - 1, budget)}


def openai_reasoning_effort(model: str, thinking_config: ThinkingConfig | None) -> str | None:
    """TDDAgents' mapping onto OpenAI's `reasoning_effort` (reasoning models only); `None` sends nothing."""
    if thinking_config is None or not get_canonical_name(model).startswith(OPENAI_REASONING_MODEL_PREFIXES):
        return None
    kind = thinking_config.get("type")
    if kind == "disabled":
        return "minimal"
    budget = thinking_config.get("budgetTokens") if kind == "enabled" else None
    if budget is None:
        return "medium"
    for bound, effort in OPENAI_REASONING_BUDGET_TIERS:
        if int(budget) < bound:
            return effort
    return "high"


def thinking_request_kwargs(
    provider: str, model: str, thinking_config: ThinkingConfig | None, max_output_tokens: int
) -> tuple[dict[str, Any], bool]:
    """The chat model's thinking kwargs, and whether temperature must be left out (thinking on)."""
    if provider == "anthropic":
        thinking = anthropic_thinking_param(model, thinking_config, max_output_tokens)
        return ({"thinking": thinking}, True) if thinking is not None else ({}, False)
    if provider == "openai":
        effort = openai_reasoning_effort(model, thinking_config)
        return ({"reasoning_effort": effort}, False) if effort is not None else ({}, False)
    return {}, False


__all__ = [
    "ADAPTIVE",
    "DISABLED",
    "DISABLE_ADAPTIVE_THINKING_ENV",
    "DISABLE_THINKING_ENV",
    "MAX_THINKING_TOKENS_ENV",
    "OPENAI_REASONING_BUDGET_TIERS",
    "OPENAI_REASONING_MODEL_PREFIXES",
    "ThinkingConfig",
    "anthropic_thinking_param",
    "get_max_thinking_tokens_for_model",
    "model_supports_adaptive_thinking",
    "model_supports_thinking",
    "openai_reasoning_effort",
    "resolve_thinking_config",
    "should_enable_thinking_by_default",
    "thinking_request_kwargs",
]
