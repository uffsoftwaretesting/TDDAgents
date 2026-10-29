"""
Model aliases and the agent model resolution (Phase 7).

Ported from `reference/claude-code/src/utils/model/model.ts` -> `parseUserSpecifiedModel`,
`getDefaultSonnetModel`/`getDefaultOpusModel`/`getDefaultHaikuModel`, and
`utils/model/agent.ts` -> `getAgentModel`, `aliasMatchesParentTier`.

The Claude aliases resolve to upstream's first-party defaults (`configs.ts`), each
overridable by `TDDAGENTS_DEFAULT_<FAMILY>_MODEL` (upstream's `ANTHROPIC_DEFAULT_*_MODEL`).
At the user's direction an OpenAI alias table sits beside them, and the default alias is
OpenAI's — the run's own default model, so `inherit` and the default agree. Because a run
may now name a Claude model while its configured provider is OpenAI (or the reverse),
`provider_for_model` picks the provider from the model id where it is known.
"""

from __future__ import annotations

import os

from app.utils.model_cost import get_canonical_name

#: Upstream's first-party defaults for the family aliases.
CLAUDE_ALIASES: dict[str, str] = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}

#: TDDAgents' OpenAI aliases (user decision, 2026-10-22; gpt-5-mini replaced o4-mini on 2026-10-27,
#: when OpenAI deprecated o4-mini).
OPENAI_ALIASES: dict[str, str] = {"gpt-5-mini": "gpt-5-mini"}

#: Every alias the Agent tool's `model` field accepts.
MODEL_ALIASES: dict[str, str] = {**CLAUDE_ALIASES, **OPENAI_ALIASES}

#: The default alias: the run's default model (`Config.MODEL`).
DEFAULT_MODEL_ALIAS = "gpt-5-mini"

SUBAGENT_MODEL_ENV = "TDDAGENTS_SUBAGENT_MODEL"

#: `getModelStrings().haiku45`, without the date suffix (the API's alias for the latest snapshot).
HAIKU_45 = "claude-haiku-4-5"


def get_default_haiku_model() -> str:
    """`getDefaultHaikuModel`: `TDDAGENTS_DEFAULT_HAIKU_MODEL`, else Haiku 4.5."""
    return os.environ.get("TDDAGENTS_DEFAULT_HAIKU_MODEL") or HAIKU_45


def parse_user_specified_model(model_input: str) -> str:
    """`parseUserSpecifiedModel`: an alias resolves to its model, anything else passes through."""
    trimmed = model_input.strip()
    alias = trimmed.lower()
    if alias in CLAUDE_ALIASES:
        override = os.environ.get(f"TDDAGENTS_DEFAULT_{alias.upper()}_MODEL")
        return override or CLAUDE_ALIASES[alias]
    if alias in OPENAI_ALIASES:
        return OPENAI_ALIASES[alias]
    return trimmed


def provider_for_model(model: str, *, default: str) -> str:
    """The provider a model id belongs to, or `default` when the id is not one we know."""
    if model.lower().startswith("claude-"):
        return "anthropic"
    if model in OPENAI_ALIASES.values():
        return "openai"
    return default


def _alias_matches_parent_tier(alias: str, parent_model: str) -> bool:
    """`aliasMatchesParentTier`: only the bare Claude family aliases match."""
    family = alias.lower()
    return family in CLAUDE_ALIASES and family in get_canonical_name(parent_model)


def get_agent_model(agent_model: str | None, parent_model: str, tool_specified_model: str | None = None) -> str:
    """`getAgentModel` (no Bedrock region prefixes: this build has no Bedrock provider)."""
    env_model = os.environ.get(SUBAGENT_MODEL_ENV)
    if env_model:
        return parse_user_specified_model(env_model)
    if tool_specified_model:
        if _alias_matches_parent_tier(tool_specified_model, parent_model):
            return parent_model
        return parse_user_specified_model(tool_specified_model)
    # `getDefaultSubagentModel()` is 'inherit'.
    model = agent_model or "inherit"
    if model == "inherit":
        return parent_model
    if _alias_matches_parent_tier(model, parent_model):
        return parent_model
    return parse_user_specified_model(model)


__all__ = [
    "CLAUDE_ALIASES",
    "DEFAULT_MODEL_ALIAS",
    "HAIKU_45",
    "MODEL_ALIASES",
    "OPENAI_ALIASES",
    "SUBAGENT_MODEL_ENV",
    "get_agent_model",
    "get_default_haiku_model",
    "parse_user_specified_model",
    "provider_for_model",
]
