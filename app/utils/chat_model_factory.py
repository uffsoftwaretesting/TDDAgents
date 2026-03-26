from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI

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
    else:
        raise ValueError(f"Unsupported provider: {provider}")