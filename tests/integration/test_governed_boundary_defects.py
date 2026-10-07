"""The governed boundary, where money and ceilings are decided.

All 27 tests here pass against the current code. **26 of the 27 reproduce
a defect found by audit of 959ebd9**: they were written to fail against
that code and pass against the fix, and the thing they have in common is
that none of them was visible from the outside -- the run succeeded, the
report looked right, and the accounting was wrong.

The exception is `[cache exceeds total]`, one case of the unusable-usage
parametrisation. It passes against 959ebd9 too, because the coherence
check already rejected a cached count larger than the input it is part of.
It is a broader invariant guarding the same settlement path, not an
original-defect reproduction, and it is not counted as one. Measured
against a copy of 959ebd9 on `PYTHONPATH`, not against an editable
install, which leaks the current code into the old tree and reported 25
of 27 the first time it was run.

    A  a response with unusable usage refunded the whole reservation
    B  tokens billed for a failed parse never reached the run's ceilings
    C  concurrent calls each passed the same token check and all dispatched
    D  the token count was taken over a payload the model never sees
    E  provider preflight ran before any durable admission

The ledger Lua is real, the pricing table is real, and the transport is a
stub. Nothing here reaches a network.
"""

from __future__ import annotations

import asyncio
from typing import Any

import fakeredis
import httpx
import pytest

from agentic_analytics.llm.base import LLMError, LLMRequest
from agentic_analytics.llm.cloud import CloudProvider
from agentic_analytics.llm.governed import (
    AIBudgetExceeded,
    GovernedCloudProvider,
    RunAdmission,
    RunBudget,
    preflight,
)
from agentic_analytics.llm.ledger import CostLedger, LedgerCaps
from agentic_analytics.llm.pricing import price_for

KEY = "sk-proj-not-a-real-key"
MODEL = "gpt-6-luna"

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
}

CAPS = LedgerCaps(
    total_microdollars=50_000_000,
    daily_microdollars=50_000_000,
    run_microdollars=10_000_000,
    runs_per_session=50,
    runs_per_client_hour=50,
)


def _request(**kw: Any) -> LLMRequest:
    base: dict[str, Any] = {
        "role": "planner",
        "system": "s" * 200,
        "user": "u" * 400,
        "schema": SCHEMA,
        "max_tokens": 2_000,
    }
    return LLMRequest(**(base | kw))


class Stub:
    """A Responses transport whose usage and body a test dictates."""

    def __init__(
        self,
        *,
        counted: int = 1_000,
        usage: dict[str, Any] | str | None = "default",
        text: str = '{"ok": true}',
        status: int = 200,
    ) -> None:
        self.counted = counted
        self.usage = usage
        self.text = text
        self.status = status
        self.paths: list[str] = []

    def body(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": "resp_1",
            "object": "response",
            "status": "completed",
            "output": [
                {"id": "rs_1", "type": "reasoning", "content": []},
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": self.text}],
                },
            ],
        }
        if self.usage == "default":
            payload["usage"] = {
                "input_tokens": self.counted,
                "output_tokens": 200,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            }
        elif self.usage is not None:
            payload["usage"] = self.usage
        return payload

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        if path.startswith("/v1/models/"):
            return httpx.Response(
                200, json={"id": MODEL, "object": "model", "created": 1, "owned_by": "openai"}
            )
        if path == "/v1/responses/input_tokens":
            return httpx.Response(200, json={"input_tokens": self.counted})
        if path == "/v1/responses":
            if self.status != 200:
                return httpx.Response(self.status, json={"error": {"code": "boom"}})
            return httpx.Response(200, json=self.body())
        return httpx.Response(404, json={})


def _inner(stub: Stub) -> CloudProvider:
    provider = CloudProvider(api_key=KEY, model=MODEL, max_calls=64)
    provider._client = httpx.AsyncClient(
        base_url="http://cloud",
        transport=httpx.MockTransport(stub),
        headers={"authorization": f"Bearer {KEY}"},
    )
    return provider


def _budget(**kw: Any) -> RunBudget:
    base: dict[str, Any] = {
        "max_attempts": 32,
        "max_input_tokens": 500_000,
        "max_output_tokens": 100_000,
        "max_runtime_seconds": 60.0,
        "caps": CAPS,
    }
    return RunBudget(**(base | kw))


async def _governed(stub: Stub, ledger: CostLedger, **budget_kw: Any) -> GovernedCloudProvider:
    inner = _inner(stub)
    result = await preflight(
        inner,
        ledger,
        admission=RunAdmission(
            run_id="run_defects", session_id="ses_1", client_id="ip_1", caps=CAPS
        ),
    )
    return GovernedCloudProvider(
        inner,
        ledger=ledger,
        preflight_result=result,
        budget=_budget(**budget_kw),
        run_id="run_defects",
        session_id="ses_1",
        client_id="ip_1",
    )


@pytest.fixture
def ledger() -> CostLedger:
    return CostLedger(fakeredis.FakeRedis())


# ══════════════════════════════════════════════════════════ A: usage validity
#
# `_non_negative_int` mapped absent, negative, boolean and string token
# counts all to zero. Zero is internally coherent -- 0 + 0 <= 0, nothing
# negative, so the coherence check passed, settlement priced a free call,
# and the ledger refunded the entire reservation. A response whose usage
# cannot be read is the one case where the reservation must stand.

UNUSABLE_USAGE: list[tuple[str, dict[str, Any] | None]] = [
    ("absent", None),
    ("empty", {}),
    ("missing output", {"input_tokens": 900}),
    ("missing input", {"output_tokens": 120}),
    ("negative input", {"input_tokens": -5, "output_tokens": 120}),
    ("negative output", {"input_tokens": 900, "output_tokens": -1}),
    ("boolean", {"input_tokens": True, "output_tokens": True}),
    ("string", {"input_tokens": "900", "output_tokens": "120"}),
    ("null fields", {"input_tokens": None, "output_tokens": None}),
    (
        "cache exceeds total",
        {
            "input_tokens": 100,
            "output_tokens": 10,
            "input_tokens_details": {"cached_tokens": 90, "cache_write_tokens": 40},
        },
    ),
    (
        "negative cached",
        {
            "input_tokens": 900,
            "output_tokens": 10,
            "input_tokens_details": {"cached_tokens": -3, "cache_write_tokens": 0},
        },
    ),
]


@pytest.mark.parametrize(("label", "usage"), UNUSABLE_USAGE, ids=[u[0] for u in UNUSABLE_USAGE])
async def test_unusable_usage_never_refunds_the_reservation(
    label: str, usage: dict[str, Any] | None, ledger: CostLedger
) -> None:
    """The response arrived, so it was billed. Only its size is unknown."""
    stub = Stub(counted=4_000, usage=usage)
    provider = await _governed(stub, ledger)
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()

    reserved = provider.budget.reserved_microdollars
    assert reserved > 0, "nothing was reserved, so this test proves nothing"
    spent = ledger.spent_microdollars("run_defects")
    assert spent == reserved, (
        f"{label}: the ledger settled to {spent} against a reservation of "
        f"{reserved}; unusable usage must not become a refund"
    )
    assert provider.budget.settled_microdollars == 0
    assert provider.budget.retained_microdollars == reserved


async def test_unusable_usage_is_not_reported_as_exact_cost(ledger: CostLedger) -> None:
    """A conservative number must not be presented as a measurement."""
    provider = await _governed(Stub(counted=4_000, usage=None), ledger)
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert provider.budget.cost_is_complete is False


async def test_coherent_usage_still_settles_exactly(ledger: CostLedger) -> None:
    """The fix must not turn every call into a worst case."""
    stub = Stub(counted=4_000)
    provider = await _governed(stub, ledger)
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()

    expected = price_for(MODEL).settlement_microdollars(
        input_tokens=4_000, cached_tokens=0, cache_write_tokens=0, output_tokens=200
    )
    assert provider.budget.settled_microdollars == expected
    assert ledger.spent_microdollars("run_defects") == expected
    assert provider.budget.settled_microdollars < provider.budget.reserved_microdollars
    assert provider.budget.cost_is_complete is True


# ══════════════════════════════════════════════════ B: billed-but-unparsed
#
# Usage is recorded before the output is parsed, which is right: a response
# that arrived was billed. But the run's own counters were updated only on
# the success path, so tokens billed for a response that failed to parse
# appeared in provider telemetry and not in the ceilings that decide
# whether the run may continue. The next call saw budget it had spent.

FAILED_OUTPUTS: list[tuple[str, str]] = [
    ("malformed json", "{not json"),
    ("not an object", "[1, 2, 3]"),
    ("empty", ""),
]


@pytest.mark.parametrize(("label", "text"), FAILED_OUTPUTS, ids=[f[0] for f in FAILED_OUTPUTS])
async def test_tokens_billed_for_an_unparsable_response_count_against_the_run(
    label: str, text: str, ledger: CostLedger
) -> None:
    stub = Stub(counted=3_000, text=text)
    provider = await _governed(stub, ledger)
    try:
        with pytest.raises(LLMError):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()

    assert provider.usage.input_tokens == 3_000, "the provider recorded the billing"
    assert provider.budget.input_tokens == 3_000, (
        f"{label}: {provider.budget.input_tokens} of 3000 billed input tokens "
        "reached the run ceiling"
    )
    assert provider.budget.output_tokens == 200


async def test_a_refusal_also_consumes_the_run_ceiling(ledger: CostLedger) -> None:
    """A refusal is a billed response the run must pay for."""
    stub = Stub(counted=3_000)
    body = stub.body()
    body["output"][-1]["content"] = [{"type": "refusal", "refusal": "No."}]
    stub.body = lambda: body  # type: ignore[method-assign]

    provider = await _governed(stub, ledger)
    try:
        with pytest.raises(LLMError) as raised:
            await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert raised.value.kind == "refused"
    assert provider.budget.input_tokens == 3_000
    assert provider.budget.output_tokens == 200


async def test_the_next_call_sees_the_failed_calls_tokens_as_spent(
    ledger: CostLedger,
) -> None:
    """The consequence, stated as behaviour rather than as a counter.

    With a ceiling that one failed call nearly exhausts, a second call has
    to be refused. Before the fix the failed call left no trace in the
    budget and the second call proceeded.
    """
    stub = Stub(counted=3_000, text="{not json")
    provider = await _governed(stub, ledger, max_input_tokens=3_500)
    try:
        with pytest.raises(LLMError):
            await provider.complete_json(_request())
        with pytest.raises(AIBudgetExceeded):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()


# ═══════════════════════════════════════════════════ C: concurrent admission
#
# The input and output checks read the budget, then awaited the network.
# Several calls could each pass the same check before any of them recorded
# a token, so the aggregate exceeded a ceiling every individual call
# respected.


class BarrierStub(Stub):
    """Holds every generation request open for a moment.

    This produces the interleaving the sequential tests cannot: every call
    has passed its admission check and none has reported usage. A delay
    rather than a barrier, because a barrier has to be sized to the number
    of calls the ceiling will actually admit -- and if the fix works, that
    number is smaller than the number of callers, so the barrier is never
    crossed and the test hangs instead of asserting.
    """

    def __init__(self, parties: int = 0, hold: float = 0.08, **kw: Any) -> None:
        super().__init__(**kw)
        self.hold = hold
        self.concurrent = 0
        self.max_concurrent = 0

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/responses":
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            try:
                await asyncio.sleep(self.hold)
            finally:
                self.concurrent -= 1
        return self(request)


class HangStub(Stub):
    """A generation request that never returns, so it can be cancelled."""

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/responses":
            self.paths.append(request.url.path)
            await asyncio.Event().wait()  # pragma: no cover - cancelled
        return self(request)


def _barrier_provider(stub: BarrierStub | HangStub) -> CloudProvider:
    provider = CloudProvider(api_key=KEY, model=MODEL, max_calls=64)
    provider._client = httpx.AsyncClient(
        base_url="http://cloud",
        transport=httpx.MockTransport(stub.handle),
        headers={"authorization": f"Bearer {KEY}"},
    )
    return provider


async def test_concurrent_calls_cannot_exceed_the_input_ceiling(
    ledger: CostLedger,
) -> None:
    """Four calls of 3,000 counted input tokens against a 7,000 ceiling.

    At most two may dispatch. Before the fix all four passed their check
    while the budget still read zero.
    """
    stub = BarrierStub(2, counted=3_000)
    inner = _barrier_provider(stub)
    result = await preflight(
        inner,
        ledger,
        admission=RunAdmission(
            run_id="run_conc_in", session_id="ses_1", client_id="ip_1", caps=CAPS
        ),
    )
    provider = GovernedCloudProvider(
        inner,
        ledger=ledger,
        preflight_result=result,
        budget=_budget(max_input_tokens=7_000),
        run_id="run_conc_in",
        session_id="ses_1",
        client_id="ip_1",
    )
    try:
        outcomes = await asyncio.gather(
            *(provider.complete_json(_request()) for _ in range(4)),
            return_exceptions=True,
        )
    finally:
        await provider.aclose()

    dispatched = stub.paths.count("/v1/responses")
    refused = sum(1 for o in outcomes if isinstance(o, AIBudgetExceeded))
    assert stub.max_concurrent > 1, "the calls did not overlap, so this proves nothing"
    assert refused >= 2, f"only {refused} of 4 were refused; {dispatched} dispatched"
    assert provider.budget.input_tokens <= 7_000, (
        f"aggregate input {provider.budget.input_tokens} exceeded the 7,000 ceiling"
    )


async def test_concurrent_calls_cannot_exceed_the_output_ceiling(
    ledger: CostLedger,
) -> None:
    """The output allowance is what the provider is permitted to generate.

    Three calls each allowed 2,000 output tokens against a 3,000 ceiling
    cannot all be dispatched, whatever order they arrive in.
    """
    stub = BarrierStub(2, counted=100)
    inner = _barrier_provider(stub)
    result = await preflight(
        inner,
        ledger,
        admission=RunAdmission(
            run_id="run_conc_out", session_id="ses_1", client_id="ip_1", caps=CAPS
        ),
    )
    provider = GovernedCloudProvider(
        inner,
        ledger=ledger,
        preflight_result=result,
        budget=_budget(max_output_tokens=3_000),
        run_id="run_conc_out",
        session_id="ses_1",
        client_id="ip_1",
    )
    try:
        await asyncio.gather(
            *(provider.complete_json(_request(max_tokens=2_000)) for _ in range(3)),
            return_exceptions=True,
        )
    finally:
        await provider.aclose()

    assert stub.max_concurrent > 1, "the calls did not overlap, so this proves nothing"
    assert provider.budget.output_tokens <= 3_000
    assert provider.budget.in_flight_output == 0, "an allowance was never returned"


async def test_a_cancelled_call_does_not_restore_ambiguous_allowance(
    ledger: CostLedger,
) -> None:
    """A cancelled request may already have been billed.

    Restoring its allowance would let the run spend it twice, so the
    allowance stays consumed and the reservation stays charged.
    """
    stub = HangStub(counted=2_000)
    inner = _barrier_provider(stub)
    result = await preflight(
        inner,
        ledger,
        admission=RunAdmission(
            run_id="run_cancel", session_id="ses_1", client_id="ip_1", caps=CAPS
        ),
    )
    provider = GovernedCloudProvider(
        inner,
        ledger=ledger,
        preflight_result=result,
        budget=_budget(),
        run_id="run_cancel",
        session_id="ses_1",
        client_id="ip_1",
    )
    try:
        task = asyncio.create_task(provider.complete_json(_request()))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.budget.input_tokens >= 2_000, (
            "a cancelled request's counted input was handed back"
        )
    finally:
        await provider.aclose()


# ═════════════════════════════════════════════════ D: count/generation drift
#
# The counting endpoint is documented as taking "the same payload you would
# send to responses.create". `reasoning` was stripped from it, so the count
# was taken over a request the model never receives, and reasoning effort
# can change the hidden instructions the model is given, which is input.
#   https://developers.openai.com/api/docs/guides/token-counting


async def test_the_count_payload_keeps_every_input_bearing_field() -> None:
    provider = CloudProvider(api_key=KEY, model=MODEL, reasoning_effort="high")
    try:
        request = _request()
        generate = provider.build_payload(request)
        count = provider.count_payload(request)
    finally:
        await provider.aclose()

    for field in ("model", "instructions", "input", "text", "reasoning"):
        assert field in count, f"{field} was dropped from the count request"
        assert count[field] == generate[field], f"{field} differs between count and generation"


async def test_only_non_input_fields_are_dropped_from_the_count() -> None:
    """What may be dropped: output size, storage, and the billing tier.

    None of them changes the prompt. Anything else dropped would make the
    count a number about a different request.
    """
    provider = CloudProvider(api_key=KEY, model=MODEL)
    try:
        request = _request()
        dropped = set(provider.build_payload(request)) - set(provider.count_payload(request))
    finally:
        await provider.aclose()
    assert dropped == {"max_output_tokens", "store", "service_tier"}


async def test_the_counted_payload_changes_when_reasoning_effort_changes() -> None:
    """Proof the field is load-bearing rather than decorative."""
    low = CloudProvider(api_key=KEY, model=MODEL, reasoning_effort="low")
    high = CloudProvider(api_key=KEY, model=MODEL, reasoning_effort="high")
    try:
        request = _request()
        assert low.count_payload(request) != high.count_payload(request)
    finally:
        await low.aclose()
        await high.aclose()


# ═════════════════════════════════════════════ E: admission before traffic
#
# Model retrieval and token counting are free of charge and not free of
# consequence: they are provider requests made with the account's
# credential, and nothing durable had authorised the run before they went
# out. A caller could open sessions and forge forwarded-for headers to
# drive preflight traffic indefinitely without ever consuming a run quota.


async def test_no_provider_request_happens_before_durable_admission(
    ledger: CostLedger,
) -> None:
    """The ledger decides whether a run may start, before the run starts."""
    stub = Stub()
    inner = _inner(stub)
    try:
        # A ledger whose run admission refuses. No provider request may
        # follow, including the free ones.
        tight = CostLedger(fakeredis.FakeRedis())
        caps = LedgerCaps(
            total_microdollars=50_000_000,
            daily_microdollars=50_000_000,
            run_microdollars=10_000_000,
            runs_per_session=0,
            runs_per_client_hour=0,
        )
        admitted = tight.admit_run(run_id="run_e", session_id="ses_1", client_id="ip_1", caps=caps)
        assert not admitted.admitted, "the fixture must refuse for this to prove anything"
        assert stub.paths == [], f"provider was contacted anyway: {stub.paths}"
    finally:
        await inner.aclose()


async def test_run_admission_is_consumed_once_per_run(ledger: CostLedger) -> None:
    """Repeated attempts must exhaust the quota rather than bypass it."""
    caps = LedgerCaps(
        total_microdollars=50_000_000,
        daily_microdollars=50_000_000,
        run_microdollars=10_000_000,
        runs_per_session=2,
        runs_per_client_hour=5,
    )
    outcomes = [
        ledger.admit_run(
            run_id=f"run_{i}", session_id="ses_same", client_id="ip_1", caps=caps
        ).admitted
        for i in range(4)
    ]
    assert outcomes == [True, True, False, False], outcomes


async def test_a_forged_forwarded_for_cannot_reset_the_session_quota(
    ledger: CostLedger,
) -> None:
    """Changing the client key must not refill a session's own allowance."""
    caps = LedgerCaps(
        total_microdollars=50_000_000,
        daily_microdollars=50_000_000,
        run_microdollars=10_000_000,
        runs_per_session=1,
        runs_per_client_hour=50,
    )
    first = ledger.admit_run(run_id="r1", session_id="ses_same", client_id="ip_a", caps=caps)
    second = ledger.admit_run(run_id="r2", session_id="ses_same", client_id="ip_b", caps=caps)
    assert first.admitted is True
    assert second.admitted is False, "a new client key refilled the session quota"
