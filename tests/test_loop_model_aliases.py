"""Phase 7: model aliases (`utils/model/model.ts` -> `parseUserSpecifiedModel`) and the agent
model resolution (`utils/model/agent.ts` -> `getAgentModel`), with an OpenAI alias table."""

from __future__ import annotations

import pytest

from app.utils.model.model import (
    DEFAULT_MODEL_ALIAS,
    MODEL_ALIASES,
    get_agent_model,
    get_default_haiku_model,
    parse_user_specified_model,
    provider_for_model,
)


def test_the_claude_aliases_resolve_to_upstreams_defaults(monkeypatch):
    for name in ("SONNET", "OPUS", "HAIKU"):
        monkeypatch.delenv(f"TDDAGENTS_DEFAULT_{name}_MODEL", raising=False)
    assert parse_user_specified_model("sonnet") == "claude-sonnet-4-6"
    assert parse_user_specified_model("opus") == "claude-opus-4-6"
    assert parse_user_specified_model("haiku") == "claude-haiku-4-5-20251001"
    assert parse_user_specified_model("  Sonnet ") == "claude-sonnet-4-6"


def test_an_env_override_wins_for_a_claude_alias(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_DEFAULT_OPUS_MODEL", "claude-opus-x")
    assert parse_user_specified_model("opus") == "claude-opus-x"


def test_the_openai_alias_is_the_default():
    assert DEFAULT_MODEL_ALIAS == "gpt-5-mini"
    assert parse_user_specified_model("gpt-5-mini") == "gpt-5-mini"
    assert set(MODEL_ALIASES) == {"sonnet", "opus", "haiku", "gpt-5-mini"}


def test_the_default_haiku_is_upstreams_haiku_45(monkeypatch):
    # `getDefaultHaikuModel`: `ANTHROPIC_DEFAULT_HAIKU_MODEL`, else `getModelStrings().haiku45`.
    monkeypatch.delenv("TDDAGENTS_DEFAULT_HAIKU_MODEL", raising=False)
    assert get_default_haiku_model() == "claude-haiku-4-5"
    monkeypatch.setenv("TDDAGENTS_DEFAULT_HAIKU_MODEL", "claude-haiku-x")
    assert get_default_haiku_model() == "claude-haiku-x"


def test_a_full_model_id_passes_through_trimmed():
    assert parse_user_specified_model(" gpt-4.1 ") == "gpt-4.1"


@pytest.mark.parametrize(
    ("model", "provider"),
    [("claude-sonnet-4-6", "anthropic"), ("gpt-5-mini", "openai"), ("o4-mini", "fallback"), ("gpt-4.1", "fallback")],
)
def test_the_provider_follows_the_model(model, provider):
    assert provider_for_model(model, default="fallback") == provider


class TestGetAgentModel:
    def test_inherit_and_unset_take_the_parents_model(self, monkeypatch):
        monkeypatch.delenv("TDDAGENTS_SUBAGENT_MODEL", raising=False)
        assert get_agent_model("inherit", "o4-mini") == "o4-mini"
        assert get_agent_model(None, "o4-mini") == "o4-mini"

    def test_the_tool_model_wins_over_the_definition(self, monkeypatch):
        monkeypatch.delenv("TDDAGENTS_SUBAGENT_MODEL", raising=False)
        assert get_agent_model("haiku", "o4-mini", "sonnet") == "claude-sonnet-4-6"
        assert get_agent_model("inherit", "o4-mini", "o4-mini") == "o4-mini"

    def test_a_family_alias_matching_the_parents_tier_keeps_the_parent(self, monkeypatch):
        """`aliasMatchesParentTier`: no surprising downgrade to the alias default."""
        monkeypatch.delenv("TDDAGENTS_SUBAGENT_MODEL", raising=False)
        assert get_agent_model("opus", "claude-opus-4-1-20250805") == "claude-opus-4-1-20250805"
        assert get_agent_model(None, "claude-sonnet-4-5", "sonnet") == "claude-sonnet-4-5"
        assert get_agent_model("haiku", "claude-sonnet-4-5") == "claude-haiku-4-5-20251001"

    def test_the_definition_model_is_resolved(self, monkeypatch):
        monkeypatch.delenv("TDDAGENTS_SUBAGENT_MODEL", raising=False)
        assert get_agent_model("gpt-4.1", "o4-mini") == "gpt-4.1"

    def test_the_subagent_env_overrides_everything(self, monkeypatch):
        monkeypatch.setenv("TDDAGENTS_SUBAGENT_MODEL", "haiku")
        assert get_agent_model("opus", "o4-mini", "sonnet") == "claude-haiku-4-5-20251001"
