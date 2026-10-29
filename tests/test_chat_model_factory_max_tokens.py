"""The Anthropic client carries upstream's `max_tokens` (`services/api/claude.ts` -> `maxOutputTokens`).

`app/utils/chat_model_factory.py` is a seam, but building a `ChatAnthropic` makes no request,
so the one decision it adds is checked offline.
"""

from __future__ import annotations

from app.utils.chat_model_factory import get_chat_model
from app.utils.tokens import get_max_output_tokens_for_model


def test_an_anthropic_client_gets_the_models_max_output_tokens(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_MAX_OUTPUT_TOKENS", raising=False)
    chat = get_chat_model("anthropic", model="claude-sonnet-5", api_key="test")
    assert chat.max_tokens == get_max_output_tokens_for_model("claude-sonnet-5") == 32_000


def test_a_callers_max_tokens_wins():
    chat = get_chat_model("anthropic", model="claude-haiku-4-5", api_key="test", max_tokens=256)
    assert chat.max_tokens == 256
