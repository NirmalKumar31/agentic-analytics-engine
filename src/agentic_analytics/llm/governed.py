"""The only path a production cloud call takes.

A ledger that is configured but never consulted bounds nothing, and a
ceiling enforced in one entry point is not a ceiling: the web application
and the evaluation CLI both reach the same provider, so the governance has
to live with the provider rather than beside one caller.

Every billable completion goes through the same sequence, and a failure at
any stage means no Message is created:

    model preflight -> token count -> reservation -> Messages -> reconcile

Reserving before dispatch is the point. Counting afterwards discovers the
ceiling only once it has been passed, and a provider that is slow or failing
is exactly when a run makes the most calls.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from agentic_analytics.llm.base import (
    BudgetError,
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMUsage,
)
from agentic_analytics.llm.cloud import CloudProvider
from agentic_analytics.llm.ledger import (
    Admission,
    CostLedger,
    LedgerCaps,
    LedgerUnavailable,
)
from agentic_analytics.llm.pricing import (
    ModelPrice,
    UnknownModelPrice,
    price_for,
    usage_is_coherent,
)
from agentic_analytics.llm.strict_schema import to_strict
from agentic_analytics.logging import get_logger

log = get_logger(__name__)


class AIBudgetExceeded(LLMError):
    """A ceiling stopped this run. Carries a stable reason for the UI."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message, kind="budget_exhausted")
        self.reason = reason


class PreflightFailed(LLMError):
    """The run was refused before any billable request."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message, kind="transport_error")
        self.reason = reason


@dataclass
class RunBudget:
    """Cumulative ceilings for one AI run."""

    max_attempts: int
    max_input_tokens: int
    max_output_tokens: int
    max_runtime_seconds: float
    caps: LedgerCaps

    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reserved_microdollars: int = 0
    settled_microdollars: int = 0
    retained_microdollars: int = 0
    deadline: float = field(default=0.0)

    def start(self) -> None:
        self.deadline = time.monotonic() + self.max_runtime_seconds

    def out_of_time(self) -> bool:
        return self.deadline > 0 and time.monotonic() > self.deadline

    def remaining_output(self) -> int:
        return max(0, self.max_output_tokens - self.output_tokens)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_attempts": self.attempts,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reserved_microdollars": self.reserved_microdollars,
            "settled_microdollars": self.settled_microdollars,
            "conservatively_retained_microdollars": self.retained_microdollars,
        }


@dataclass(frozen=True)
class PreflightResult:
    """What preflight established, for the artifact."""

    requested_model: str
    resolved_model: str
    price: ModelPrice
    ledger_healthy: bool
    #: Response schemas rewritten into the strict dialect and accepted. The
    #: count is local; `schemas_checked_remotely` is how many were put to
    #: the provider's own parser through the token-count endpoint, which
    #: validates a schema without generating anything.
    strict_schemas_ok: int = 0
    schemas_checked_remotely: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_model": self.requested_model,
            "resolved_model": self.resolved_model,
            "pricing_source": self.price.source,
            "pricing_reviewed": self.price.reviewed,
            # The short-context tier, which is the one every run of this
            # size is priced at. The long-context rates exist in the table
            # and are reported by the CLI rather than here, where four more
            # numbers would obscure the two that matter.
            "input_per_mtok_microdollars": self.price.short.input_per_mtok,
            "cached_input_per_mtok_microdollars": self.price.short.cached_input_per_mtok,
            "cache_write_per_mtok_microdollars": self.price.short.cache_write_per_mtok,
            "output_per_mtok_microdollars": self.price.short.output_per_mtok,
        }


async def preflight(provider: CloudProvider, ledger: CostLedger) -> PreflightResult:
    """Establish that a paid run may start, spending no completion.

    Order matters. The ledger is checked first because an unreachable one
    means the run cannot be bounded however good the model is, and the
    Models lookup is free while a Message is not.
    """
    if not ledger.healthy():
        raise PreflightFailed(
            "AI Analytics is unavailable because its usage accounting is not reachable.",
            "ai_quota_storage_unavailable",
        )

    resolved = await provider.verify_model()
    resolved_id = str(resolved.get("id") or provider.model)

    try:
        price = price_for(resolved_id)
    except UnknownModelPrice:
        # The identity that answered is not the one we can price. Refusing
        # is the only safe outcome: an unpriced model cannot be reserved
        # for, so it cannot be bounded.
        raise PreflightFailed(
            "AI Analytics is unavailable because the configured model has no "
            "reviewed pricing entry.",
            "ai_model_not_priced",
        ) from None

    local_ok, probe = _strict_schema_check()
    # One real round trip, with the most demanding schema. Converting
    # locally proves the rewrite is self-consistent; only the provider can
    # say whether it accepts the result, and the token-count endpoint
    # answers that without creating a response.
    remote_ok = 0
    if probe is not None:
        await provider.count_input_tokens(probe)
        remote_ok = 1

    log.info("ai_preflight_ok", requested=provider.model, resolved=resolved_id)
    return PreflightResult(
        requested_model=provider.model,
        resolved_model=resolved_id,
        price=price,
        ledger_healthy=True,
        strict_schemas_ok=local_ok,
        schemas_checked_remotely=remote_ok,
    )


def _strict_schema_check() -> tuple[int, LLMRequest | None]:
    """Convert every active response schema, and pick one to send.

    A schema that cannot be expressed strictly would otherwise surface as a
    400 on the first paid call of whichever agent owns it -- possibly not
    the first agent to run. Converting all of them here turns that into a
    refusal before anything is spent.
    """
    from agentic_analytics.agents.base import active_response_schemas

    schemas = active_response_schemas()
    for schema in schemas.values():
        # Raises StrictSchemaError, which `open_governed_cloud_provider`
        # surfaces as a preflight failure.
        to_strict(schema)
    if not schemas:
        return 0, None
    # The largest converted schema: the one most likely to meet a limit.
    hardest = max(schemas.items(), key=lambda kv: len(json.dumps(kv[1])))
    probe = LLMRequest(
        role="preflight",
        system="Preflight schema validation.",
        user="Preflight schema validation.",
        schema=hardest[1],
        max_tokens=16,
    )
    return len(schemas), probe


class GovernedCloudProvider(LLMProvider):
    """A cloud provider that cannot spend outside its budget.

    Wraps rather than subclasses, so the transport stays one class and the
    governance stays one class, and a test can prove the ordering between
    them.
    """

    name = "cloud"
    requires_credentials = True
    remote_inference = True

    def __init__(
        self,
        inner: CloudProvider,
        *,
        ledger: CostLedger,
        preflight_result: PreflightResult,
        budget: RunBudget,
        run_id: str,
        session_id: str,
        client_id: str,
    ) -> None:
        # Before `super().__init__`, which assigns a usage counter that the
        # property below redirects onto the wrapped provider.
        self._inner = inner
        super().__init__(max_calls=budget.max_attempts)
        self._ledger = ledger
        self._preflight = preflight_result
        self._price = preflight_result.price
        self.budget = budget
        self._run_id = run_id
        self._session_id = session_id
        self._client_id = client_id
        self._dispatched: set[str] = set()
        budget.start()

    # Token accounting lives on the wrapped provider, which records what
    # the API reported rather than what we guessed.
    @property
    def usage(self) -> LLMUsage:
        return self._inner.usage

    @usage.setter
    def usage(self, value: LLMUsage) -> None:
        # `LLMProvider.__init__` assigns a fresh counter. Token accounting
        # belongs to the wrapped provider, which records what the API
        # reported rather than what this layer estimated.
        self._inner.usage = value

    @property
    def resolved_model(self) -> dict[str, Any] | None:
        return self._inner.resolved_model

    @property
    def preflight_result(self) -> PreflightResult:
        return self._preflight

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        self._guard_run_limits()

        # 1. The exact payload, counted by the provider.
        counted_input = await self._inner.count_input_tokens(request)

        # 2. Cumulative input ceiling, before anything billable.
        if self.budget.input_tokens + counted_input > self.budget.max_input_tokens:
            raise AIBudgetExceeded(
                "This AI run reached its input token limit.", "ai_run_budget_exceeded"
            )

        # 3. Clamp the output allowance to what the run has left, and refuse
        #    rather than send a request whose allowance is zero.
        allowance = min(request.max_tokens, self.budget.remaining_output())
        if allowance <= 0:
            raise AIBudgetExceeded(
                "This AI run reached its output token limit.", "ai_run_budget_exceeded"
            )
        bounded = request.model_copy(update={"max_tokens": allowance})

        # 4. Reserve counted input plus the maximum permitted output, both
        #    priced pessimistically. Nothing here can know whether an input
        #    token will be served from cache, written to it, or neither, and
        #    the three rates differ by more than tenfold -- so the dearest
        #    one is assumed and the difference is released at settlement.
        worst_case = self._price.reservation_microdollars(counted_input, allowance)
        self.budget.attempts += 1
        call_id = f"c{self.budget.attempts}"
        admission = self._reserve(call_id, worst_case)

        # A repeated reservation id must not authorise a second free
        # dispatch. Idempotency protects the ledger, not the provider.
        if admission.reservation_id in self._dispatched:
            raise AIBudgetExceeded(
                "This AI run repeated a request that was already dispatched.",
                "ai_run_budget_exceeded",
            )
        self._dispatched.add(admission.reservation_id)
        self.budget.reserved_microdollars += admission.reserved_microdollars

        before_in = self.usage.input_tokens
        before_out = self.usage.output_tokens
        try:
            payload = await self._inner.complete_json(bounded)
        except (BudgetError, LLMError):
            # The request left this process. It may have been billed even
            # if no answer came back, so the reservation stands: a spend
            # ceiling should assume a sent request was charged.
            self._ledger.abandon(reservation_id=admission.reservation_id)
            self.budget.retained_microdollars += admission.reserved_microdollars
            raise

        actual_in = self.usage.input_tokens - before_in
        actual_out = self.usage.output_tokens - before_out
        self.budget.input_tokens += actual_in
        self.budget.output_tokens += actual_out

        # Settle from the provider's own categories. `input_tokens` already
        # includes the cached and cache-written parts, so the ordinary part
        # is what remains once they are removed.
        cached = int(getattr(self._inner, "last_cached_tokens", 0))
        cache_written = int(getattr(self._inner, "last_cache_write_tokens", 0))
        if usage_is_coherent(
            input_tokens=actual_in,
            cached_tokens=cached,
            cache_write_tokens=cache_written,
            output_tokens=actual_out,
        ):
            actual = self._price.settlement_microdollars(
                input_tokens=actual_in,
                cached_tokens=cached,
                cache_write_tokens=cache_written,
                output_tokens=actual_out,
            )
        else:
            # The response arrived and was billed, but its usage does not
            # add up, so there is no honest number to settle to. The
            # conservative reservation is kept rather than replaced by a
            # figure derived from counts that contradict each other.
            log.warning(
                "ai_usage_incoherent",
                run_id=self._run_id,
                call=call_id,
            )
            self._ledger.abandon(reservation_id=admission.reservation_id)
            self.budget.retained_microdollars += admission.reserved_microdollars
            return payload
        outcome = self._ledger.settle(
            run_id=self._run_id,
            reservation_id=admission.reservation_id,
            actual_microdollars=actual,
        )
        self.budget.settled_microdollars += actual
        log.info(
            "ai_call_settled",
            run_id=self._run_id,
            call=call_id,
            reserved=admission.reserved_microdollars,
            actual=actual,
            outcome=outcome,
        )
        return payload

    def _guard_run_limits(self) -> None:
        if self.budget.out_of_time():
            raise AIBudgetExceeded("This AI run reached its time limit.", "ai_run_budget_exceeded")
        if self.budget.attempts >= self.budget.max_attempts:
            raise AIBudgetExceeded(
                "This AI run reached its model call limit.", "ai_run_budget_exceeded"
            )

    def _reserve(self, call_id: str, amount: int) -> Admission:
        try:
            admission = self._ledger.reserve(
                run_id=self._run_id,
                call_id=call_id,
                session_id=self._session_id,
                client_id=self._client_id,
                amount_microdollars=amount,
                caps=self.budget.caps,
                first_call_of_run=self.budget.attempts == 1,
            )
        except LedgerUnavailable:
            raise AIBudgetExceeded(
                "AI Analytics is unavailable because its usage accounting is not reachable.",
                "ai_quota_storage_unavailable",
            ) from None
        if not admission.admitted:
            raise AIBudgetExceeded(message_for(admission.reason), admission.reason)
        return admission

    async def aclose(self) -> None:
        await self._inner.aclose()


#: Stable reason to readable sentence. A visitor never sees a key, and none
#: of these names a provider, a credential or a remaining balance.
_MESSAGES = {
    "ai_global_limit_reached": (
        "AI mode has reached its public demo usage limit. "
        "Deterministic Analytics is still available."
    ),
    "ai_daily_limit_reached": (
        "AI mode has reached today's public demo usage limit. "
        "Deterministic Analytics is still available."
    ),
    "ai_session_limit_reached": (
        "This dataset session has used its AI runs. Deterministic Analytics is still available."
    ),
    "ai_client_limit_reached": (
        "Too many AI runs from this address in the last hour. "
        "Deterministic Analytics is still available."
    ),
    "ai_run_budget_exceeded": "This AI run reached its safety budget.",
    "ai_quota_storage_unavailable": (
        "AI Analytics is unavailable because its usage accounting is not reachable."
    ),
    "ai_model_not_priced": (
        "AI Analytics is unavailable because the configured model has no reviewed pricing entry."
    ),
}


def message_for(reason: str) -> str:
    return _MESSAGES.get(
        reason,
        "AI mode has reached its public demo usage limit. "
        "Deterministic Analytics is still available.",
    )


async def open_governed_cloud_provider(
    cfg: Any,
    *,
    run_id: str,
    session_id: str,
    client_id: str,
    ledger: CostLedger,
) -> GovernedCloudProvider:
    """Build the only cloud provider a production path may use.

    One factory for the web application and the evaluation CLI. A second
    construction site is a second set of ceilings to forget, which is how a
    metered endpoint acquires an unmetered shortcut.

    Preflight runs here, so a run that cannot be priced or bounded is
    refused before its first billable request rather than during it.
    """
    inner = CloudProvider(
        api_key=cfg.cloud_api_key or "",
        model=cfg.cloud_model,
        base_url=cfg.cloud_base_url,
        max_calls=cfg.ai_max_llm_calls,
        timeout_seconds=cfg.cloud_timeout_seconds,
        reasoning_effort=cfg.cloud_reasoning_effort,
    )
    try:
        result = await preflight(inner, ledger)
    except BaseException:
        # Preflight owns the provider it was handed; a failure must not
        # leak the HTTP client.
        await inner.aclose()
        raise

    budget = RunBudget(
        max_attempts=cfg.ai_max_llm_calls,
        max_input_tokens=cfg.ai_max_input_tokens,
        max_output_tokens=cfg.ai_max_output_tokens,
        max_runtime_seconds=cfg.ai_max_runtime_seconds,
        caps=LedgerCaps.from_settings(cfg),
    )
    return GovernedCloudProvider(
        inner,
        ledger=ledger,
        preflight_result=result,
        budget=budget,
        run_id=run_id,
        session_id=session_id,
        client_id=client_id,
    )
