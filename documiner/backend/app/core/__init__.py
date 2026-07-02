from langchain_openai import ChatOpenAI

from core.config import get_settings


def create_llm(settings=None, temperature=0.3, max_tokens=5000, model_override=None, enable_thinking=None, **kwargs):
    """LLM factory — creates a ChatOpenAI instance with the given parameters."""
    if settings is None:
        settings = get_settings()
    return ChatOpenAI(
        model=model_override or settings.agent_model,
        base_url=settings.agent_base_url,
        api_key=settings.agent_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )