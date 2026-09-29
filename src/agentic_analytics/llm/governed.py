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
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMUsage,
)
from agentic_analytics.llm.cloud import CloudProvider, UsageRecord
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


#: Roles that verify rather than propose. They draw on the reserved share
#: of the output budget, because a claim nobody could check is withheld and
#: a run whose verifier was starved therefore publishes nothing.
VERIFICATION_ROLES = frozenset({"critic"})


@dataclass
class RunBudget:
    """Cumulative ceilings for one AI run."""

    max_attempts: int
    max_input_tokens: int
    max_output_tokens: int
    max_runtime_seconds: float
    caps: LedgerCaps
    #: Output tokens only the verification stage may spend. Zero disables
    #: the reservation, which is what the evaluation harness wants when it
    #: is measuring a model rather than serving a visitor.
    verification_reserve: int = 0

    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: The input total split by how it was billed. They sum to
    #: `input_tokens`, and the split is what makes a cost reproducible
    #: from the record: the three categories are priced differently and a
    #: total alone cannot be checked against a provider's invoice. The
    #: first paid run recorded only the total, so its cache behaviour had
    #: to be inferred from the settled figure rather than read.
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    #: Already inside `output_tokens`. Recorded for observability and
    #: never added to it -- a reasoning model that billed its thinking
    #: twice would be over-charged by the engine, not by the provider.
    reasoning_tokens: int = 0
    settlements: int = 0
    reservations: int = 0
    #: Allowance handed to calls that have been admitted and have not yet
    #: reported. Without it, several calls each read the same committed
    #: totals, each passed the same check, and together exceeded a ceiling
    #: that every one of them individually respected.
    in_flight_input: int = 0
    in_flight_output: int = 0
    reserved_microdollars: int = 0
    settled_microdollars: int = 0
    retained_microdollars: int = 0
    #: Calls whose reported usage could not be read, so whose cost is the
    #: reservation rather than a measurement.
    unusable_usage_calls: int = 0
    #: Provider HTTP requests, including the free ones. Distinct from
    #: `attempts`, which counts completions.
    provider_requests: int = 0
    deadline: float = field(default=0.0)

    def start(self) -> None:
        self.deadline = time.monotonic() + self.max_runtime_seconds

    def out_of_time(self) -> bool:
        return self.deadline > 0 and time.monotonic() > self.deadline

    @property
    def ordinary_input_tokens(self) -> int:
        """Input billed at the full rate: the total less what was served
        from cache and less what was written to it."""
        return self.input_tokens - self.cached_input_tokens - self.cache_write_input_tokens

    @property
    def usage_categories_are_coherent(self) -> bool:
        """Whether the categories still fit inside the total they split.

        Asserted rather than assumed: a negative ordinary count means the
        engine has mis-attributed tokens and any cost derived from the
        split is wrong.
        """
        return (
            self.cached_input_tokens >= 0
            and self.cache_write_input_tokens >= 0
            and self.reasoning_tokens >= 0
            and self.reasoning_tokens <= self.output_tokens
            and self.ordinary_input_tokens >= 0
        )

    def usage_report(self) -> dict[str, int | bool]:
        """Everything needed to reproduce this run's cost.

        Written as one structure so an artifact cannot record half of it.
        Nothing here derives from a prompt or a response body: these are
        counts and money, and they are what a reader needs to check the
        engine's arithmetic against a provider's invoice.
        """
        return {
            "input_tokens": self.input_tokens,
            "ordinary_input_tokens": self.ordinary_input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "provider_requests": self.provider_requests,
            "completion_attempts": self.attempts,
            "reservations": self.reservations,
            "settlements": self.settlements,
            "unusable_usage_calls": self.unusable_usage_calls,
            "settled_microdollars": self.settled_microdollars,
            "retained_microdollars": self.retained_microdollars,
            "cost_is_complete": self.cost_is_complete,
            "usage_categories_are_coherent": self.usage_categories_are_coherent,
        }

    @property
    def cost_is_complete(self) -> bool:
        """Whether every call in this run reported usable usage.

        False means the total is a conservative floor built from
        reservations, not a measurement, and must not be presented as one.
        """
        return self.unusable_usage_calls == 0

    def remaining_output(self) -> int:
        return max(0, self.max_output_tokens - self.output_tokens - self.in_flight_output)

    def remaining_output_for(self, role: str) -> int:
        """What one role may still spend on output.

        Verification is the last stage and the only one whose absence is
        silently destructive: a finding that cannot be checked is withheld,
        never waved through, so a run that spends its whole output budget
        proposing findings publishes nothing at all. That is exactly what
        happened the first time this ran against a reasoning model, whose
        hidden reasoning tokens count as output.

        So the proposing stages see a smaller budget than the verifying one.
        The reserve is not extra spending -- the ceiling is unchanged -- it
        is a claim on part of it that the earlier stages cannot take.
        """
        if role in VERIFICATION_ROLES:
            return self.remaining_output()
        return max(
            0,
            self.max_output_tokens
            - self.output_tokens
            - self.in_flight_output
            - self.verification_reserve,
        )

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
class RunAdmission:
    """Who a durable run slot is taken against.

    Grouped rather than passed as four arguments so that `preflight` cannot
    be called with an identity that is half-supplied.
    """

    run_id: str
    session_id: str
    client_id: str
    caps: LedgerCaps


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


async def preflight(
    provider: CloudProvider,
    ledger: CostLedger,
    *,
    admission: RunAdmission | None = None,
) -> PreflightResult:
    """Establish that a paid run may start, spending no completion.

    Order matters, and the order changed. The ledger is checked first
    because an unreachable one means the run cannot be bounded however good
    the model is. Then a durable run slot is taken -- before the first
    provider request, not before the first billable one.

    The Models lookup and the token count cost nothing, and "free" is not
    "unaccounted": they are requests made with the account's credential,
    and until a run was admitted a caller could drive them indefinitely by
    opening sessions and varying a forwarded-for header. `admission`
    carries the identity that slot is taken against; without it this
    performs the health check only, which is what a test harness wants.
    """
    if not ledger.healthy():
        raise PreflightFailed(
            "AI Analytics is unavailable because its usage accounting is not reachable.",
            "ai_quota_storage_unavailable",
        )

    if admission is not None:
        try:
            admitted = ledger.admit_run(
                run_id=admission.run_id,
                session_id=admission.session_id,
                client_id=admission.client_id,
                caps=admission.caps,
            )
        except LedgerUnavailable:
            raise PreflightFailed(
                "AI Analytics is unavailable because its usage accounting is not reachable.",
                "ai_quota_storage_unavailable",
            ) from None
        if not admitted.admitted:
            # Refused before a single provider request. This is the point
            # of doing it here.
            raise PreflightFailed(message_for(admitted.reason), admitted.reason)

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
        #
        # A request made with the account's credential, so it is counted
        # even though it is free. `provider_requests` was declared and
        # reported and never incremented, so the second paid smoke's
        # artifact recorded zero provider requests against fifteen
        # completions -- a number in a cost record that was simply not
        # true.
        self.budget.provider_requests += 1
        counted_input = await self._inner.count_input_tokens(request)

        # 2 and 3. Admission. Both ceilings are checked and the allowance
        #    is taken in one atomic step, because the previous shape read
        #    the committed totals, awaited the network, and only then
        #    recorded anything -- so several concurrent calls each passed
        #    the same check and together exceeded a ceiling every one of
        #    them individually respected.
        #
        #    The lock covers the bookkeeping and nothing else. Holding it
        #    across the request would serialise the run for no benefit: the
        #    allowance is already claimed by the time it is released.
        allowance, call_id = self._claim_tokens(counted_input, request)

        bounded = request.model_copy(update={"max_tokens": allowance})

        # 4. Reserve counted input plus the maximum permitted output, both
        #    priced pessimistically, immediately before dispatch. Nothing
        #    here can know whether an input token will be served from cache,
        #    written to it, or neither, and the three rates differ by more
        #    than tenfold -- so the dearest one is assumed and the
        #    difference is released at settlement.
        worst_case = self._price.reservation_microdollars(counted_input, allowance)
        try:
            admission = self._reserve(call_id, worst_case)
        except BaseException:
            # Refused or unreachable before anything was sent, so the
            # allowance was never at risk and goes back.
            self._release_in_flight(counted_input, allowance)
            raise

        # The billable request.
        self.budget.provider_requests += 1

        # A repeated reservation id must not authorise a second free
        # dispatch. Idempotency protects the ledger, not the provider.
        if admission.reservation_id in self._dispatched:
            self._release_in_flight(counted_input, allowance)
            raise AIBudgetExceeded(
                "This AI run repeated a request that was already dispatched.",
                "ai_run_budget_exceeded",
            )
        self._dispatched.add(admission.reservation_id)
        self.budget.reserved_microdollars += admission.reserved_microdollars

        try:
            payload, usage = await self._inner.complete_with_usage(bounded)
        except BaseException as exc:
            # Everything from here is billed-or-maybe-billed. The request
            # left this process, so neither the money nor the allowance
            # comes back: a cancellation or a transport error says nothing
            # about whether the provider served and charged for the call.
            attached = getattr(exc, "usage", None)
            self._commit(
                counted_input,
                allowance,
                attached if isinstance(attached, UsageRecord) else None,
                admission,
                call_id,
                settle=False,
            )
            raise

        self._commit(counted_input, allowance, usage, admission, call_id, settle=True)
        return payload

    def _claim_tokens(self, counted_input: int, request: LLMRequest) -> tuple[int, str]:
        """Check both token ceilings and take the allowance, atomically.

        Synchronous on purpose, and that is the fix. The previous shape
        read the committed totals, awaited the token count, and recorded
        nothing until the response came back -- so several concurrent calls
        each passed the same check and together exceeded a ceiling every
        one of them individually respected.

        No lock is needed once the window contains no `await`: a coroutine
        cannot be interleaved between statements that do not yield. A lock
        would also have to be re-acquired during cleanup, and a cancelled
        task cannot reliably await anything.
        """
        committed_in = self.budget.input_tokens + self.budget.in_flight_input
        if committed_in + counted_input > self.budget.max_input_tokens:
            raise AIBudgetExceeded(
                "This AI run reached its input token limit.", "ai_run_budget_exceeded"
            )
        allowance = min(request.max_tokens, self.budget.remaining_output_for(request.role))
        if allowance <= 0:
            raise AIBudgetExceeded(
                "This AI run reached its output token limit.", "ai_run_budget_exceeded"
            )
        self.budget.in_flight_input += counted_input
        self.budget.in_flight_output += allowance
        self.budget.attempts += 1
        return allowance, f"c{self.budget.attempts}"

    def _release_in_flight(self, counted_input: int, allowance: int) -> None:
        """Hand back an allowance claimed for a call that never left."""
        self.budget.in_flight_input -= counted_input
        self.budget.in_flight_output -= allowance

    def _commit(
        self,
        counted_input: int,
        allowance: int,
        usage: UsageRecord | None,
        admission: Admission,
        call_id: str,
        *,
        settle: bool,
    ) -> None:
        """Turn an in-flight allowance into consumed tokens and a cost.

        Two cases, and the difference between them is the whole point.

        Reported usage that reads cleanly is charged exactly, and the
        unused part of the reservation is released. The run's counters move
        by what the provider says it served -- including when the response
        then failed to parse, because a billed response is billed whether
        or not it could be understood.

        Usage that is absent or does not add up is not a measurement, so
        nothing is settled. The reservation stands in full, the run is
        charged the counted input and the entire output allowance it was
        permitted to use, and the run's cost is marked incomplete. Settling
        such a call to zero -- which is what reading an absent count as
        zero amounted to -- refunded real spending.
        """
        trustworthy = usage is not None and usage.valid
        if trustworthy:
            assert usage is not None
            actual_in, actual_out = usage.input_tokens, usage.output_tokens
        else:
            actual_in, actual_out = counted_input, allowance

        self.budget.in_flight_input -= counted_input
        self.budget.in_flight_output -= allowance
        self.budget.input_tokens += actual_in
        self.budget.output_tokens += actual_out
        if trustworthy:
            assert usage is not None
            # The split, so the cost can be recomputed from the record
            # rather than inferred from the total.
            self.budget.cached_input_tokens += usage.cached_tokens
            self.budget.cache_write_input_tokens += usage.cache_write_tokens
            self.budget.reasoning_tokens += usage.reasoning_tokens
        else:
            self.budget.unusable_usage_calls += 1

        if not trustworthy or not settle:
            log.warning(
                "ai_cost_retained",
                run_id=self._run_id,
                call=call_id,
                reason="usage unusable" if not trustworthy else "call did not complete",
            )
            self._ledger.abandon(reservation_id=admission.reservation_id)
            self.budget.retained_microdollars += admission.reserved_microdollars
            return

        assert usage is not None
        actual = self._price.settlement_microdollars(
            input_tokens=usage.input_tokens,
            cached_tokens=usage.cached_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            output_tokens=usage.output_tokens,
        )
        outcome = self._ledger.settle(
            run_id=self._run_id,
            reservation_id=admission.reservation_id,
            actual_microdollars=actual,
        )
        self.budget.settled_microdollars += actual
        self.budget.settlements += 1
        log.info(
            "ai_call_settled",
            run_id=self._run_id,
            call=call_id,
            reserved=admission.reserved_microdollars,
            actual=actual,
            outcome=outcome,
        )

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
        self.budget.reservations += 1
        return admission

    async def aclose(self) -> None:
        await self._inner.aclose()


#: Stable reason to readable sentence. A visitor never sees a key, and none
#: of these names a provider, a credential or a remaining balance.
_MESSAGES = {
    # Internal rather than a quota: a run reached a billable call without a
    # durable admission. The visitor sees the generic ceiling message; the
    # reason string is what the logs and tests key off.
    "ai_run_not_admitted": (
        "AI mode could not start this run. Deterministic Analytics is still available."
    ),
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
    caps = LedgerCaps.from_settings(cfg)
    try:
        result = await preflight(
            inner,
            ledger,
            # The durable slot is taken here, so no provider request --
            # not even a free one -- happens for a run the ledger has not
            # authorised.
            admission=RunAdmission(
                run_id=run_id,
                session_id=session_id,
                client_id=client_id,
                caps=caps,
            ),
        )
    except BaseException:
        # Preflight owns the provider it was handed; a failure must not
        # leak the HTTP client.
        await inner.aclose()
        raise

    budget = RunBudget(
        max_attempts=cfg.ai_max_llm_calls,
        max_input_tokens=cfg.ai_max_input_tokens,
        max_output_tokens=cfg.ai_max_output_tokens,
        verification_reserve=cfg.ai_verification_output_reserve,
        max_runtime_seconds=cfg.ai_max_runtime_seconds,
        caps=caps,
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
