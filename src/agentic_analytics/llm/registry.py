"""Provider selection.

The default is the scripted provider. Reaching a paid endpoint requires both
``AAE_PROVIDER_MODE=cloud`` and a key in the environment, so no default path
spends money.
"""

from __future__ import annotations

from agentic_analytics.config import Settings, get_settings
from agentic_analytics.llm.base import LLMError, LLMProvider
from agentic_analytics.llm.fake import FakeProvider


def build_provider(settings: Settings | None = None) -> LLMProvider:
    """Construct the provider named by configuration."""
    cfg = settings or get_settings()
    max_calls = cfg.budgets.max_llm_calls

    if cfg.provider_mode == "fake":
        return FakeProvider(max_calls=max_calls)

    if cfg.provider_mode == "local":
        from agentic_analytics.llm.ollama import OllamaProvider

        return OllamaProvider(
            base_url=cfg.ollama_base_url,
            model=cfg.ollama_model,
            max_calls=max_calls,
            think=cfg.ollama_think,
            timeout_seconds=cfg.ollama_timeout_seconds,
        )

    if cfg.provider_mode == "cloud":
        if not cfg.cloud_api_key:
            raise LLMError("AAE_PROVIDER_MODE=cloud requires AAE_CLOUD_API_KEY to be set")
        from agentic_analytics.llm.cloud import CloudProvider

        return CloudProvider(
            api_key=cfg.cloud_api_key,
            model=cfg.cloud_model,
            base_url=cfg.cloud_base_url,
            max_calls=max_calls,
        )

    raise LLMError(f"unknown provider mode {cfg.provider_mode!r}")
