"""Extended thinking (`app/utils/thinking.py`): off unless asked, Anthropic's parameter, OpenAI's effort."""

from __future__ import annotations

from typing import Any

import pytest

from app.utils.thinking import (
    ADAPTIVE,
    DISABLED,
    anthropic_thinking_param,
    get_max_thinking_tokens_for_model,
    model_supports_adaptive_thinking,
    model_supports_thinking,
    openai_reasoning_effort,
    resolve_thinking_config,
    should_enable_thinking_by_default,
    thinking_request_kwargs,
)


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MAX_THINKING_TOKENS", "TDDAGENTS_DISABLE_THINKING", "TDDAGENTS_DISABLE_ADAPTIVE_THINKING"):
        monkeypatch.delenv(name, raising=False)


def test_which_models_think() -> None:
    assert model_supports_thinking("claude-sonnet-4-5") and model_supports_thinking("claude-haiku-5-5")
    assert not model_supports_thinking("claude-3-7-sonnet-20250219")
    assert model_supports_adaptive_thinking("claude-opus-4-6") and model_supports_adaptive_thinking("claude-sonnet-4-6")
    assert not model_supports_adaptive_thinking("claude-sonnet-4-5")
    assert not model_supports_adaptive_thinking("claude-haiku-5-5")
    assert model_supports_adaptive_thinking("some-new-model")
    assert get_max_thinking_tokens_for_model("claude-sonnet-4-5") == 63_999


def test_off_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not should_enable_thinking_by_default({})
    assert should_enable_thinking_by_default({"alwaysThinkingEnabled": True})
    assert resolve_thinking_config(None, None, {}) is None
    assert resolve_thinking_config(None, None, {"alwaysThinkingEnabled": True}) == ADAPTIVE
    monkeypatch.setenv("MAX_THINKING_TOKENS", "0")
    assert not should_enable_thinking_by_default({"alwaysThinkingEnabled": True})
    monkeypatch.setenv("MAX_THINKING_TOKENS", "bogus")
    assert not should_enable_thinking_by_default({})


def test_the_flags_resolve_as_main_tsx(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_thinking_config("enabled", 5, {}) == ADAPTIVE == resolve_thinking_config("adaptive", None, {})
    assert resolve_thinking_config("disabled", 5, {"alwaysThinkingEnabled": True}) == DISABLED
    assert resolve_thinking_config(None, 2048, {}) == {"type": "enabled", "budgetTokens": 2048}
    assert resolve_thinking_config(None, 0, {"alwaysThinkingEnabled": True}) == DISABLED
    monkeypatch.setenv("MAX_THINKING_TOKENS", "3000")
    assert resolve_thinking_config(None, 10, {}) == {"type": "enabled", "budgetTokens": 3000}


def test_the_anthropic_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    budget = {"type": "enabled", "budgetTokens": 2000}
    assert anthropic_thinking_param("claude-sonnet-4-5", None, 8000) is None
    assert anthropic_thinking_param("claude-sonnet-4-5", DISABLED, 8000) is None
    assert anthropic_thinking_param("claude-3-7-sonnet", budget, 8000) is None
    assert anthropic_thinking_param("claude-sonnet-4-5", budget, 8000) == {"type": "enabled", "budget_tokens": 2000}
    assert anthropic_thinking_param("claude-sonnet-4-5", budget, 1500) == {"type": "enabled", "budget_tokens": 1499}
    assert anthropic_thinking_param("claude-sonnet-4-5", ADAPTIVE, 8000) == {"type": "enabled", "budget_tokens": 7999}
    assert anthropic_thinking_param("claude-opus-4-6", budget, 8000) == {"type": "adaptive"}
    monkeypatch.setenv("TDDAGENTS_DISABLE_ADAPTIVE_THINKING", "1")
    assert anthropic_thinking_param("claude-opus-4-6", budget, 8000) == {"type": "enabled", "budget_tokens": 2000}
    monkeypatch.setenv("TDDAGENTS_DISABLE_THINKING", "1")
    assert anthropic_thinking_param("claude-sonnet-4-5", budget, 8000) is None


def test_the_openai_mapping() -> None:
    assert openai_reasoning_effort("gpt-4o", ADAPTIVE) is None
    assert openai_reasoning_effort("gpt-5", None) is None
    assert openai_reasoning_effort("gpt-5", DISABLED) == "minimal"
    assert openai_reasoning_effort("o3-mini", ADAPTIVE) == "medium"
    for budget, effort in ((1024, "low"), (4095, "low"), (4096, "medium"), (16_383, "medium"), (16_384, "high")):
        assert openai_reasoning_effort("gpt-5-mini", {"type": "enabled", "budgetTokens": budget}) == effort
    assert openai_reasoning_effort("gpt-5", {"type": "enabled"}) == "medium"


def test_request_kwargs_per_provider() -> None:
    budget = {"type": "enabled", "budgetTokens": 2000}
    assert thinking_request_kwargs("anthropic", "claude-sonnet-4-5", budget, 8000) == (
        {"thinking": {"type": "enabled", "budget_tokens": 2000}}, True)
    assert thinking_request_kwargs("anthropic", "claude-sonnet-4-5", None, 8000) == ({}, False)
    assert thinking_request_kwargs("openai", "gpt-5", budget, 8000) == ({"reasoning_effort": "low"}, False)
    assert thinking_request_kwargs("openai", "gpt-4o", budget, 8000) == ({}, False)
    assert thinking_request_kwargs("gemini", "g", budget, 8000) == ({}, False)


def test_the_request_sends_thinking_and_drops_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.query.config import build_run_config
    from app.query.state import initial_loop_state
    from app.services.api.claude import open_model_stream
    from app.services.api.with_retry import RetryContext
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for
    from langchain_core.messages import HumanMessage

    got: list[dict[str, Any]] = []

    class Model:
        def astream(self, messages: Any, config: Any = None, **kwargs: Any) -> Any:
            return iter(())

    def factory(provider: str, **kwargs: Any) -> Model:
        got.append(kwargs)
        return Model()

    monkeypatch.setattr("app.services.api.claude.get_chat_model", factory)
    monkeypatch.setattr("app.services.api.claude.request_system_texts", lambda s, c: ["system"])
    ctx = tool_context_for(AppStateStore())
    state = initial_loop_state((HumanMessage(content="task"),), ctx)
    config = build_run_config("r")
    open_model_stream(state, config, RetryContext(model="claude-sonnet-4-5"))
    ctx.thinking_config = {"type": "enabled", "budgetTokens": 2000}
    open_model_stream(state, config, RetryContext(model="claude-sonnet-4-5"))
    assert "temperature" in got[0] and "thinking" not in got[0]
    assert got[1]["thinking"] == {"type": "enabled", "budget_tokens": 2000} and "temperature" not in got[1]


def test_flags_options_and_workers() -> None:
    import app.main as entry
    from app.session.driver import development_run_options

    args = entry.parse_args(["-p", "x", "--thinking", "disabled", "--max-thinking-tokens", "500"])
    options = entry.session_options(args)
    assert (options.thinking, options.max_thinking_tokens) == ("disabled", 500)
    run = development_run_options(options)
    assert (run["thinking"], run["max_thinking_tokens"]) == ("disabled", 500)
