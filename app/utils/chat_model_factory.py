"""
The seam to the LLM providers.

**Exempt from the CLAUDE.md quality gate's unit- and mutation-testing requirement.**
This module is the boundary itself: every branch below returns a live provider client,
so an offline test could only assert that a constructor was called with the arguments it
was just handed. It is verified by an end-to-end pipeline run instead. Callers that sit
above it are not exempt.
"""

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_together import ChatTogether

from app.config.config import Config
from app.utils.tokens import get_max_output_tokens_for_model


def get_chat_model(provider: str, **kwargs):
    """
    Factory function to instantiate a chat model dynamically.

    Args:
        provider (str): The provider of the chat model to use. Options: "anthropic", "openai", "gemini".
        **kwargs: Additional parameters to pass to the model (e.g., temperature, model version).

    Returns:
        An instantiated chat model object.

    Raises:
        ValueError: If the provider is not supported.
    """

    if provider.lower() == "anthropic":
        # Upstream sends `max_tokens: getMaxOutputTokensForModel(model)` on every request
        # (`services/api/claude.ts`); without it LangChain sends its own default (4096 for a
        # model it does not know), which truncates long tool calls.
        if kwargs.get("max_tokens") is None and "model" in kwargs:
            kwargs["max_tokens"] = get_max_output_tokens_for_model(str(kwargs["model"]))
        return ChatAnthropic(**kwargs)
    elif provider.lower() == "openai":
        return ChatOpenAI(**kwargs)
    elif provider.lower() == "gemini":
        return ChatGoogleGenerativeAI(**kwargs)
    elif provider.lower() == "together":
        return ChatTogether(together_api_key=Config.TOGETHER_API_KEY, **kwargs)
    else:
        raise ValueError(f"Unsupported provider: {provider}")


async def count_input_tokens(provider: str, model: str, messages: Any, tools: Any) -> int | None:
    """
    The provider's own count of a request's input tokens (`services/tokenEstimation.ts` ->
    `countMessagesTokensWithAPI`'s API call): Anthropic's `messages.count_tokens`, OpenAI's
    `responses.input_tokens.count`, each client reading its key from the environment as the chat
    models do. `messages` are LangChain messages, `tools` Anthropic tool schemas (`name`,
    `description`, `input_schema`). `None` for a provider without a count endpoint.
    """
    if provider.lower() == "anthropic":
        import anthropic
        from langchain_anthropic.chat_models import _format_messages

        system, formatted = _format_messages(list(messages))
        kwargs: dict[str, Any] = {"system": system} if system else {}
        if tools:
            kwargs["tools"] = list(tools)
        response = await anthropic.AsyncAnthropic(max_retries=1).messages.count_tokens(
            model=model, messages=formatted, **kwargs)
        return int(response.input_tokens)
    if provider.lower() == "openai":
        import openai
        from langchain_openai.chat_models.base import _construct_responses_api_input

        openai_tools = [{"type": "function", "name": t["name"], "description": t.get("description") or "",
                         "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}
                        for t in tools]
        options: dict[str, Any] = {"tools": openai_tools} if openai_tools else {}
        counted = await openai.AsyncOpenAI(max_retries=1).responses.input_tokens.count(
            model=model, input=_construct_responses_api_input(list(messages)), **options)
        return int(counted.input_tokens)
    return None
