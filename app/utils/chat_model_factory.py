from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI

def get_chat_model(model_name: str, **kwargs):
    """
    Factory function to instantiate a chat model dynamically.

    Args:
        model_name (str): The name of the chat model to use. Options: "anthropic", "openai", "gemini".
        **kwargs: Additional parameters to pass to the model (e.g., temperature, model version).

    Returns:
        An instantiated chat model object.

    Raises:
        ValueError: If the model_name is not supported.
    """
    if model_name.lower() == "anthropic":
        return ChatAnthropic(**kwargs)
    elif model_name.lower() == "openai":
        return ChatOpenAI(**kwargs)
    elif model_name.lower() == "gemini":
        return ChatGoogleGenerativeAI(**kwargs)
    else:
        raise ValueError(f"Unsupported model: {model_name}")