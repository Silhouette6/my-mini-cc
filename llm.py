"""LLM factory — create the right ChatModel based on settings."""

from langchain_core.language_models import BaseChatModel

import config


def create_llm() -> BaseChatModel:
    """Return a BaseChatModel instance based on settings.llm_provider.

    API keys are read from standard env vars by each LangChain integration:
      - OPENAI_API_KEY for openai
      - ANTHROPIC_API_KEY for anthropic
      - ZHIPUAI_API_KEY for zhipu
    """
    settings = config.settings
    provider = settings.llm_provider.lower()

    timeout = settings.llm_request_timeout

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict = {"model": settings.model_id, "timeout": timeout}
        if settings.api_base_url:
            kwargs["base_url"] = settings.api_base_url
        return ChatAnthropic(**kwargs)

    if provider == "zhipu":
        from langchain_community.chat_models import ChatZhipuAI

        kwargs: dict = {"model": settings.model_id, "timeout": timeout}
        if settings.api_base_url:
            base = settings.api_base_url.rstrip("/")
            # ChatZhipuAI 需要完整 URL（含 /chat/completions）
            if not base.endswith("/chat/completions"):
                base = f"{base}/chat/completions"
            kwargs["zhipuai_api_base"] = base
        return ChatZhipuAI(**kwargs)

    # Default: openai-compatible (covers OpenAI, local APIs, proxies)
    from langchain_openai import ChatOpenAI

    kwargs: dict = {"model": settings.model_id, "request_timeout": timeout}
    if settings.api_base_url:
        kwargs["base_url"] = settings.api_base_url
    return ChatOpenAI(**kwargs)
