"""`countMessagesTokensWithAPI` (`app/services/token_estimation.py`) and the provider seam's count call."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.services.token_estimation import (
    TOOLS_ONLY_PLACEHOLDER,
    api_token_counter,
    count_messages_tokens_with_api,
)
from app.utils.analyze_context import rough_count_tokens

TOOL = {"name": "Bash", "description": "Run a command", "input_schema": {"type": "object", "properties": {}}}


class Counter:
    def __init__(self, answer: Any = 42) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str, list[Any], list[Any]]] = []

    async def __call__(self, provider: str, model: str, messages: Sequence[Any],
                       tools: Sequence[Mapping[str, Any]]) -> Any:
        self.calls.append((provider, model, list(messages), list(tools)))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_the_provider_counts_the_messages_and_tools() -> None:
    counter = Counter(42)
    messages = [HumanMessage(content="hi")]
    assert asyncio.run(count_messages_tokens_with_api(messages, [TOOL], model="m", provider="openai",
                                                      counter=counter)) == 42
    assert counter.calls == [("openai", "m", messages, [TOOL])]


def test_a_tools_only_count_sends_upstreams_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.config import Config

    monkeypatch.setattr(Config, "CHAT_MODEL", "anthropic")
    counter = Counter(7)
    assert asyncio.run(count_messages_tokens_with_api([], [TOOL], model="m", counter=counter)) == 7
    (provider, _, (placeholder,), _) = counter.calls[0]
    assert provider == "anthropic"
    assert isinstance(placeholder, HumanMessage) and placeholder.content == TOOLS_ONLY_PLACEHOLDER == "foo"


@pytest.mark.parametrize("answer", [RuntimeError("down"), None, "12"])
def test_a_failed_or_missing_count_is_none(answer: Any) -> None:
    assert asyncio.run(count_messages_tokens_with_api([HumanMessage(content="x")], [], model="m",
                                                      provider="anthropic", counter=Counter(answer))) is None


async def _count(counter: Any, messages: Sequence[Any], tools: Sequence[Any]) -> Any:
    return await counter(messages, tools)


def test_the_counter_falls_back_to_the_rough_estimate() -> None:
    messages = [HumanMessage(content="hello there, how are you")]
    exact = api_token_counter("m", provider="openai", counter=Counter(99))
    assert asyncio.run(_count(exact, messages, [TOOL])) == 99
    rough = api_token_counter("m", provider="gemini", counter=Counter(None))
    assert asyncio.run(_count(rough, messages, [TOOL])) == asyncio.run(rough_count_tokens(messages, [TOOL]))


def test_an_unknown_provider_has_no_count() -> None:
    from app.utils.chat_model_factory import count_input_tokens

    assert asyncio.run(count_input_tokens("gemini", "m", [HumanMessage(content="x")], [])) is None


# ── the seam's static surface (CLAUDE.md: an exempt seam needs one) ──────────

def test_the_sdk_surface_the_count_call_uses_exists() -> None:
    import anthropic
    import openai
    from langchain_anthropic.chat_models import _format_messages
    from langchain_openai.chat_models.base import _construct_responses_api_input

    count = inspect.signature(anthropic.AsyncAnthropic(api_key="x").messages.count_tokens).parameters
    assert {"model", "messages", "system", "tools"} <= set(count)
    assert "max_retries" in inspect.signature(anthropic.AsyncAnthropic).parameters
    tokens = inspect.signature(openai.AsyncOpenAI(api_key="x").responses.input_tokens.count).parameters
    assert {"model", "input", "tools"} <= set(tokens)
    assert "max_retries" in inspect.signature(openai.AsyncOpenAI).parameters
    system, formatted = _format_messages([SystemMessage(content="sys"), HumanMessage(content="hi"),
                                          AIMessage(content="yo")])
    assert system == "sys" and [m["role"] for m in formatted] == ["user", "assistant"]
    assert _construct_responses_api_input([HumanMessage(content="hi")])[0]["role"] == "user"
