# import logging
# import json
# import urllib.request
# import urllib.error
# from typing import Union, List
# from langchain_openai import AzureChatOpenAI, ChatOpenAI

# from app.core.config import Settings
# from app.llms.model_config import ModelConfig

# def create_llm(
#     settings: Settings,
#     temperature: float = 0.7,
#     max_tokens: int | None = None,
#     model_override: str | None = None,
#     enable_thinking: bool | None = None,
# ) -> LangChainModel:
#     """Create a native LangChain model based on settings.
    
#     Returns the actual LangChain model instance (not a wrapper), making it fully
#     compatible with native LangChain features like create_agent, tool binding, etc.
    
#     Args:
#         settings: Application settings
#         temperature: Sampling temperature (0.0 to 1.0)
#         max_tokens: Maximum tokens to generate
#         model_override: Optional model/deployment name override
        
#     Returns:
#         Native LangChain model instance (AzureChatOpenAI or ChatOpenAI)
        
#     Raises:
#         ValueError: If provider is unknown
        
#     Examples:
#         >>> settings = Settings()
#         >>> model = create_llm(settings, temperature=0.3, max_tokens=4000)
#         >>> # Use directly with create_agent
#         >>> agent = create_agent(model=model, tools=[...], ...)
#         >>> # Or bind tools
#         >>> model_with_tools = model.bind_tools([...])
#     """
#     provider = settings.llm_provider.lower()
    
# def _create_vllm_model(
#     settings: Settings,
#     temperature: float,
#     max_tokens: int | None,
#     model_override: str | None,
#     enable_thinking: bool | None = None,
# ) -> ChatOpenAI:
#     """Create vLLM model instance using OpenAI-compatible API."""
#     model_name = model_override or settings.vllm_model

#     # Best-effort: if the configured model isn't served by the vLLM endpoint,
#     # try a sensible fallback to avoid hard failures.
#     available_models = _fetch_vllm_models(settings.vllm_base_url, settings.vllm_api_key)
#     if available_models and model_name not in available_models:
#         fallback = None

#         # Common pattern in this repo: "*-qwen" variants.
#         if model_name.endswith("-qwen"):
#             candidate = model_name[: -len("-qwen")]
#             if candidate in available_models:
#                 fallback = candidate

#         if fallback is None:
#             fallback = available_models[0]

#         logger.warning(
#             "Configured vLLM model '%s' not available; falling back to '%s'. Available: %s",
#             model_name,
#             fallback,
#             ", ".join(available_models[:10]) + ("..." if len(available_models) > 10 else ""),
#         )
#         model_name = fallback
    
#     # Check if this is a reasoning model and adjust max_tokens if needed
#     is_reasoning = ModelConfig.is_reasoning_model(model_name)
#     if is_reasoning and max_tokens:
#         adjusted_max_tokens = ModelConfig.get_adjusted_max_tokens(model_name, max_tokens)
#         if adjusted_max_tokens != max_tokens:
#             logger.info(
#                 f"Adjusted max_tokens for reasoning model {model_name}: "
#                 f"{max_tokens} -> {adjusted_max_tokens}"
#             )
#             max_tokens = adjusted_max_tokens
    
#     # Get model-specific kwargs
#     model_kwargs = ModelConfig.get_model_kwargs(model_name)
    
#     thinking_enabled = enable_thinking if enable_thinking is not None else False
#     return ChatOpenAI(
#         base_url=settings.vllm_base_url,
#         api_key=settings.vllm_api_key, # type: ignore
#         model=model_name,
#         temperature=temperature,
#         max_completion_tokens=max_tokens,
#         model_kwargs=model_kwargs,
#         extra_body={"chat_template_kwargs": {"enable_thinking": thinking_enabled}},
#     )