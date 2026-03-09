"""ADK model factory — LiteLlm for OpenAI/Anthropic/Zhipu."""

from __future__ import annotations

import os

from google.adk.models.lite_llm import LiteLlm

import config


def _zhipu_api_key() -> None:
    """Map ZHIPUAI_API_KEY to ZAI_API_KEY for LiteLLM compatibility."""
    if not os.environ.get("ZAI_API_KEY") and os.environ.get("ZHIPUAI_API_KEY"):
        os.environ.setdefault("ZAI_API_KEY", os.environ["ZHIPUAI_API_KEY"])


def create_adk_model(settings: config.Settings | None = None) -> LiteLlm:
    """Create LiteLlm model from config. Supports openai, anthropic, zhipu."""
    s = settings or config.settings
    provider = s.llm_provider.lower()
    model_id = s.model_id
    api_base = s.api_base_url

    _zhipu_api_key()

    if provider == "openai":
        model_str = f"openai/{model_id}"
    elif provider == "anthropic":
        model_str = f"anthropic/{model_id}"
    elif provider == "zhipu":
        model_str = f"zai/{model_id}"
    else:
        model_str = f"openai/{model_id}"

    kwargs: dict = {"model": model_str}
    if api_base:
        base = api_base.rstrip("/")
        kwargs["api_base"] = base

    return LiteLlm(**kwargs)
