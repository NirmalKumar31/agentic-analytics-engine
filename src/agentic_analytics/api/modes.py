"""Which decision-maker drives a run, chosen per run rather than per process.

The server used to select one provider at startup from `AAE_PROVIDER_MODE`,
so a deployment could offer deterministic analytics or AI analytics but not
both, and "Compare Both" could not exist at all. The mode now arrives with
the analysis request.

What the browser may send is a closed set of two public names. It may not
name a provider class, a model, an endpoint or any provider configuration:
those are server-side, and a request that could choose them would be a
request that could point the server at an arbitrary host with the server's
own credential.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentic_analytics.config import Settings
from agentic_analytics.llm.base import LLMProvider


class RunMode(StrEnum):
    """The public choice of decision-maker for one run.

    Named `RunMode` because `ExecutionMode` already means something else in
    this API: the badge state a recorded run carries.

    Two values, and no third for `local`.

    Local models stay available to the CLI and the evaluation harness, where
    the operator controls the machine. Exposing one as a browser mode would
    put a public endpoint in front of an unmetered local process.
    """

    DETERMINISTIC = "deterministic"
    AI = "ai"


#: Why AI is not offered. Stable identifiers: the UI maps them to copy, and
#: a visitor never sees an exception string or a configuration value.
AI_DISABLED = "ai_disabled"
AI_NOT_CONFIGURED = "ai_not_configured"
AI_QUOTA_UNAVAILABLE = "ai_quota_storage_unavailable"

_REASON_TEXT = {
    AI_DISABLED: "AI Analytics is turned off on this deployment.",
    AI_NOT_CONFIGURED: "AI Analytics is not configured on this deployment.",
    AI_QUOTA_UNAVAILABLE: (
        "AI Analytics is unavailable because its usage accounting is not reachable."
    ),
}


class ModeUnavailable(RuntimeError):
    """A run was requested in a mode this deployment cannot serve."""

    def __init__(self, reason: str) -> None:
        super().__init__(_REASON_TEXT.get(reason, "This mode is unavailable."))
        self.reason = reason


@dataclass(frozen=True)
class AIAvailability:
    """Whether AI runs can be admitted, and why not when they cannot."""

    available: bool
    reason: str = ""

    @property
    def message(self) -> str:
        return _REASON_TEXT.get(self.reason, "") if self.reason else ""


def ai_availability(cfg: Settings, *, ledger_ready: bool = True) -> AIAvailability:
    """Decide whether AI is offered, without touching the provider.

    Deliberately cheap and side-effect free: it runs on every `/api/config`
    request. Whether the *model* resolves is a separate, cached preflight;
    this answers whether the deployment is configured to try at all.
    """
    if not cfg.ai_analytics_enabled:
        return AIAvailability(False, AI_DISABLED)
    if not cfg.cloud_api_key or not cfg.cloud_model:
        return AIAvailability(False, AI_NOT_CONFIGURED)
    if not ledger_ready:
        return AIAvailability(False, AI_QUOTA_UNAVAILABLE)
    return AIAvailability(True)


def build_provider_for_mode(
    cfg: Settings, mode: RunMode, *, ledger_ready: bool = True
) -> LLMProvider:
    """Construct the provider this run will use, and only that one.

    A deterministic run must not build a cloud provider, read the cloud
    credential or validate it, so that a deployment with no key -- or a
    broken one -- still serves deterministic analytics normally.
    """
    if mode is RunMode.DETERMINISTIC:
        from agentic_analytics.llm.fake import FakeProvider

        return FakeProvider(max_calls=cfg.budgets.max_llm_calls)

    availability = ai_availability(cfg, ledger_ready=ledger_ready)
    if not availability.available:
        raise ModeUnavailable(availability.reason)

    from agentic_analytics.llm.cloud import CloudProvider

    return CloudProvider(
        api_key=cfg.cloud_api_key or "",
        model=cfg.cloud_model,
        base_url=cfg.cloud_base_url,
        max_calls=cfg.ai_max_llm_calls,
        timeout_seconds=cfg.cloud_timeout_seconds,
        send_temperature=cfg.cloud_send_temperature,
    )


def provider_kind(mode: RunMode) -> str:
    """The implementation behind a public mode, for the run record."""
    return "scripted" if mode is RunMode.DETERMINISTIC else "cloud"
