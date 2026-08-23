"""
The seam to the LLM providers.

**Exempt from the CLAUDE.md quality gate's unit- and mutation-testing requirement.**
This module is the boundary itself: every branch below returns a live provider client,
so an offline test could only assert that a constructor was called with the arguments it
was just handed. It is verified by an end-to-end pipeline run instead. Callers that sit
above it are not exempt.
"""

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_together import ChatTogether

from app.config.config import Config


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
        return ChatAnthropic(**kwargs)
    elif provider.lower() == "openai":
        return ChatOpenAI(**kwargs)
    elif provider.lower() == "gemini":
        return ChatGoogleGenerativeAI(**kwargs)
    elif provider.lower() == "together":
        return ChatTogether(together_api_key=Config.TOGETHER_API_KEY, **kwargs)
    else:
        raise ValueError(f"Unsupported provider: {provider}")
