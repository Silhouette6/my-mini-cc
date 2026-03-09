"""LLM factory — create the right ChatModel based on settings."""

from langchain_core.language_models import BaseChatModel

import config


def create_llm() -> BaseChatModel:
    """Return a BaseChatModel instance based on settings.llm_provider.

    API keys are read from standard env vars by each LangChain integration:
      - OPENAI_API_KEY for openai
      - ANTHROPIC_API_KEY for anthropic
      - ZHIPUAI_API_KEY for zhipu

    注意：不在工厂层添加 .with_retry()，因为那会返回 RunnableRetry
    对象，而 create_agent() 内部需要调用 llm.bind_tools()，该方法只
    存在于 BaseChatModel，不存在于 RunnableRetry。重试逻辑在
    core.py（agent 调用）和 memory/summary.py（LLM 直接调用）两处
    分别通过 tenacity 实现。
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
