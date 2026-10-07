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
    #: Rules first, a planner only when the rules cannot settle it.
    #:
    #: Not a third kind of decision-maker: it is a policy over the other
    #: two, which is why it does not change what may decide arithmetic.
    #: `DETERMINISTIC` and `AI` remain selectable so a run can be audited
    #: against one planner or compared across both.
    AUTO = "auto"


#: Why AI is not offered. Stable identifiers: the UI maps them to copy, and
#: a visitor never sees an exception string or a configuration value.
AI_DISABLED = "ai_disabled"
AI_NOT_CONFIGURED = "ai_not_configured"
AI_QUOTA_UNAVAILABLE = "ai_quota_storage_unavailable"
#: Not a deployment problem: a caller asked this function to build a paid
#: provider, which only `governed.open_governed_cloud_provider` may do.
AI_REQUIRES_GOVERNED_BUILD = "ai_requires_governed_build"

_REASON_TEXT = {
    AI_DISABLED: "AI Analytics is turned off on this deployment.",
    AI_NOT_CONFIGURED: "AI Analytics is not configured on this deployment.",
    AI_QUOTA_UNAVAILABLE: (
        "AI Analytics is unavailable because its usage accounting is not reachable."
    ),
    AI_REQUIRES_GOVERNED_BUILD: (
        "AI Analytics is unavailable because this run was not attached to its usage accounting."
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

    Only the deterministic provider is returned. Asking for the AI one
    raises even when the deployment is perfectly configured: a paid
    provider is constructed by
    :func:`agentic_analytics.llm.governed.open_governed_cloud_provider`
    and nowhere else, so that it cannot exist without the ledger that
    bounds its spending.
    """
    if mode in (RunMode.DETERMINISTIC, RunMode.AUTO):
        # An automatic run gets the scripted provider for everything the
        # graph does outside planning. Its cloud planner, if it needs one,
        # is built lazily and separately. See `RunContext.open_planner`,
        # so a question the rules answer never touches the credential.
        from agentic_analytics.llm.fake import FakeProvider

        return FakeProvider(max_calls=cfg.budgets.max_llm_calls)

    availability = ai_availability(cfg, ledger_ready=ledger_ready)
    if not availability.available:
        raise ModeUnavailable(availability.reason)

    # Availability is the cheap half of the answer and is all this function
    # can give. Building the provider is the expensive half, because a paid
    # provider may only be built attached to the ledger that bounds it --
    # an unmetered one here would be a run with no ceiling, which is the
    # defect this whole path exists to prevent. The caller is `execute()`,
    # which has the ledger and the run id.
    raise ModeUnavailable(AI_REQUIRES_GOVERNED_BUILD)


def provider_kind(mode: RunMode) -> str:
    """The implementation behind a public mode, for the run record.

    An automatic run does not know its own answer yet: whether a cloud
    planner is used depends on whether the rules settle the question, and
    that is decided inside the run. Recording "cloud" up front would
    claim a billable path for a run that may well make no request, so the
    record says `governed` and the route event says what actually
    happened.
    """
    if mode is RunMode.DETERMINISTIC:
        return "scripted"
    if mode is RunMode.AUTO:
        return "governed"
    return "cloud"
