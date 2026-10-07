"""The token ceiling under concurrency, repeatedly.

The accounting is lock-free: admission is a run of statements with no
`await` in it, so a coroutine cannot be interleaved partway through. That
is a claim about the shape of the code, and the way to test a claim like
that is to run many interleavings and check the invariant at every point
an observer can see:

    committed + in-flight tokens never exceed the ceiling

Not "the total afterwards is right" -- that can hold while a ceiling was
briefly blown through. The stub samples the budget on every request, from
inside the window where allowances are outstanding.

Repeated rather than run once. A scheduling bug that appears in one
interleaving in twenty is still a bug, and a single pass is as likely to
miss it as to find it.

The requested shape was an `asyncio.Barrier`, and it cannot be used here:
a barrier for four callers never releases when the ceiling admits two, so
the test deadlocks instead of failing. The hold is timed instead --
every admitted call keeps its allowance outstanding while the others
arrive, which produces the same overlap the barrier was for, and lets
the refused callers be observed being refused rather than hanging.
"""

from __future__ import annotations

import asyncio
from typing import Any

import fakeredis
import httpx
import pytest

from agentic_analytics.llm.base import LLMRequest
from agentic_analytics.llm.cloud import CloudProvider
from agentic_analytics.llm.governed import (
    AIBudgetExceeded,
    GovernedCloudProvider,
    RunAdmission,
    RunBudget,
    preflight,
)
from agentic_analytics.llm.ledger import CostLedger, LedgerCaps

KEY = "sk-proj-not-real"
MODEL = "gpt-6-luna"
SCHEMA: dict[str, Any] = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

CAPS = LedgerCaps(
    total_microdollars=500_000_000,
    daily_microdollars=500_000_000,
    run_microdollars=100_000_000,
    runs_per_session=500,
    runs_per_client_hour=500,
)

#: How many times each interleaving test repeats. Enough that a one-in-ten
#: scheduling order is very unlikely to be missed, small enough to stay
#: inside a normal test run.
REPEATS = 25


def _request(**kw: Any) -> LLMRequest:
    base: dict[str, Any] = {
        "role": "planner",
        "system": "s" * 120,
        "user": "u" * 240,
        "schema": SCHEMA,
        "max_tokens": 1_000,
    }
    return LLMRequest(**(base | kw))


class SamplingStub:
    """Answers requests while watching the budget from inside the window.

    Every generation request records `committed + in-flight` at the moment
    it is in flight, which is exactly when an over-admission would be
    visible and exactly when the totals afterwards would hide it.
    """

    def __init__(self, counted: int, out_tokens: int, hold: float = 0.02) -> None:
        self.counted = counted
        self.out_tokens = out_tokens
        self.hold = hold
        self.budget: RunBudget | None = None
        self.peak_input = 0
        self.peak_output = 0
        self.max_concurrent = 0
        self._live = 0

    def _sample(self) -> None:
        budget = self.budget
        if budget is None:
            return
        self.peak_input = max(self.peak_input, budget.input_tokens + budget.in_flight_input)
        self.peak_output = max(self.peak_output, budget.output_tokens + budget.in_flight_output)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/v1/models/"):
            return httpx.Response(
                200,
                json={"id": MODEL, "object": "model", "created": 1, "owned_by": "openai"},
            )
        if path == "/v1/responses/input_tokens":
            return httpx.Response(200, json={"input_tokens": self.counted})

        self._live += 1
        self.max_concurrent = max(self.max_concurrent, self._live)
        self._sample()
        try:
            await asyncio.sleep(self.hold)
            self._sample()
        finally:
            self._live -= 1
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "object": "response",
                "status": "completed",
                "output": [
                    {"id": "rs_1", "type": "reasoning", "content": []},
                    {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": '{"ok": true}'}],
                    },
                ],
                "usage": {
                    "input_tokens": self.counted,
                    "output_tokens": self.out_tokens,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )


async def _governed(stub: SamplingStub, **budget_kw: Any) -> GovernedCloudProvider:
    inner = CloudProvider(api_key=KEY, model=MODEL, max_calls=500)
    inner._client = httpx.AsyncClient(
        base_url="http://cloud",
        transport=httpx.MockTransport(stub),
        headers={"authorization": f"Bearer {KEY}"},
    )
    ledger = CostLedger(fakeredis.FakeRedis())
    # Admit first, as production does: a reservation against a run with no
    # durable admission is refused, so a test that skipped admission was
    # exercising a path the application does not have.
    result = await preflight(
        inner,
        ledger,
        admission=RunAdmission(
            run_id="run_stress", session_id="ses_1", client_id="ip_1", caps=CAPS
        ),
    )
    base: dict[str, Any] = {
        "max_attempts": 500,
        "max_input_tokens": 100_000,
        "max_output_tokens": 100_000,
        "max_runtime_seconds": 120.0,
        "caps": CAPS,
    }
    budget = RunBudget(**(base | budget_kw))
    stub.budget = budget
    return GovernedCloudProvider(
        inner,
        ledger=ledger,
        preflight_result=result,
        budget=budget,
        run_id="run_stress",
        session_id="ses_1",
        client_id="ip_1",
    )


@pytest.mark.parametrize("attempt", range(REPEATS))
async def test_the_input_ceiling_holds_at_every_observable_point(attempt: int) -> None:
    """Twelve callers, a ceiling that admits four."""
    stub = SamplingStub(counted=2_500, out_tokens=10)
    provider = await _governed(stub, max_input_tokens=10_000)
    try:
        outcomes = await asyncio.gather(
            *(provider.complete_json(_request()) for _ in range(12)),
            return_exceptions=True,
        )
    finally:
        await provider.aclose()

    assert stub.max_concurrent > 1, "the calls did not overlap, so nothing was tested"
    assert stub.peak_input <= 10_000, (
        f"attempt {attempt}: committed+in-flight input reached {stub.peak_input}"
    )
    assert provider.budget.input_tokens <= 10_000
    assert provider.budget.in_flight_input == 0, "an allowance was never returned"
    assert sum(1 for o in outcomes if isinstance(o, AIBudgetExceeded)) >= 8


@pytest.mark.parametrize("attempt", range(REPEATS))
async def test_the_output_ceiling_holds_at_every_observable_point(attempt: int) -> None:
    stub = SamplingStub(counted=50, out_tokens=900)
    provider = await _governed(stub, max_output_tokens=4_000)
    try:
        await asyncio.gather(
            *(provider.complete_json(_request(max_tokens=1_000)) for _ in range(10)),
            return_exceptions=True,
        )
    finally:
        await provider.aclose()

    assert stub.max_concurrent > 1
    assert stub.peak_output <= 4_000, (
        f"attempt {attempt}: committed+in-flight output reached {stub.peak_output}"
    )
    assert provider.budget.in_flight_output == 0


@pytest.mark.parametrize("attempt", range(REPEATS))
async def test_cancellation_leaves_the_accounting_consistent(attempt: int) -> None:
    """Cancelling mid-flight must not corrupt the counters.

    It may, and does -- leave the allowance consumed and the money
    retained. That is deliberate: a cancelled request may already have been
    served and charged, so handing the allowance back would let the run
    spend it twice. What must not happen is an in-flight counter left
    dangling, which would shrink the run's budget permanently.
    """
    stub = SamplingStub(counted=1_000, out_tokens=100, hold=0.2)
    provider = await _governed(stub)
    try:
        tasks = [asyncio.create_task(provider.complete_json(_request())) for _ in range(6)]
        await asyncio.sleep(0.05)
        for task in tasks[:3]:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await provider.aclose()

    budget = provider.budget
    assert budget.in_flight_input == 0, f"attempt {attempt}: dangling in-flight input"
    assert budget.in_flight_output == 0, f"attempt {attempt}: dangling in-flight output"
    assert budget.input_tokens >= 0
    assert budget.output_tokens >= 0
    assert stub.peak_input <= budget.max_input_tokens
    # Conservative on purpose: a cancelled call keeps its allowance spent.
    assert budget.input_tokens >= 3_000, "cancelled calls handed their counted input back"


@pytest.mark.parametrize("attempt", range(REPEATS))
async def test_mixed_success_failure_and_cancellation(attempt: int) -> None:
    """All three interleavings at once, which is how a real run ends."""

    class Flaky(SamplingStub):
        def __init__(self) -> None:
            super().__init__(counted=800, out_tokens=80, hold=0.03)
            self.calls = 0

        async def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/responses":
                self.calls += 1
                if self.calls % 3 == 0:
                    # Billed, then unparsable. The tokens still count.
                    response = await super().__call__(request)
                    body = response.json()
                    body["output"][-1]["content"] = [{"type": "output_text", "text": "{not json"}]
                    return httpx.Response(200, json=body)
            return await super().__call__(request)

    stub = Flaky()
    provider = await _governed(stub, max_input_tokens=40_000)
    try:
        tasks = [asyncio.create_task(provider.complete_json(_request())) for _ in range(9)]
        await asyncio.sleep(0.02)
        tasks[-1].cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await provider.aclose()

    budget = provider.budget
    assert budget.in_flight_input == 0
    assert budget.in_flight_output == 0
    assert stub.peak_input <= 40_000
    # Every dispatched call paid, whatever became of its answer.
    assert budget.input_tokens >= stub.calls * 800
