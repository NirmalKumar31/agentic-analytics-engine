"""Provider selection for the unpaid providers.

The default is the scripted provider, and ``local`` reaches Ollama. Neither
costs anything, so this is the path an operator can run freely.

``AAE_PROVIDER_MODE=cloud`` no longer builds anything here; it raises. A
paid provider is constructed only by
:func:`agentic_analytics.llm.governed.open_governed_cloud_provider`, so
that one cannot exist without the ledger that bounds its spending. An
environment variable is not a spend control.
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
        # No unmetered shortcut. A bare `CloudProvider` spends without
        # reserving, so the only way to a paid endpoint is
        # `llm.governed.open_governed_cloud_provider`, which preflights the
        # model and charges each call against the durable ledger. The web
        # application and the evaluation CLI both go through it.
        raise LLMError(
            "a cloud provider cannot be built here: paid runs go through "
            "agentic_analytics.llm.governed.open_governed_cloud_provider, "
            "which requires AAE_AI_ANALYTICS_ENABLED, AAE_AI_QUOTA_REDIS_URL, "
            "a verified model and a reviewed pricing entry"
        )

    raise LLMError(f"unknown provider mode {cfg.provider_mode!r}")
