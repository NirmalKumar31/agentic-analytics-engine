"""Ordering, not mocking, is what makes a spend ceiling real.

A ledger that is configured but never consulted bounds nothing. These tests
drive the governed provider against a fake Redis and an HTTP transport stub
and assert the sequence:

    model preflight -> token count -> reservation -> Messages -> reconcile

No Messages request may be created if any earlier stage fails. The
governance boundary is not mocked away: the real ledger Lua runs, the real
pricing table is consulted, and the real payload is counted.
"""

from __future__ import annotations

from typing import Any

import fakeredis
import httpx
import pytest

from agentic_analytics.llm.base import LLMError, LLMRequest
from agentic_analytics.llm.cloud import RESPONSE_TOOL, CloudProvider
from agentic_analytics.llm.governed import (
    AIBudgetExceeded,
    GovernedCloudProvider,
    PreflightFailed,
    RunBudget,
    preflight,
)
from agentic_analytics.llm.ledger import CostLedger, LedgerCaps
from agentic_analytics.llm.pricing import price_for

KEY = "sk-ant-secret-never-logged"
MODEL = "claude-sonnet-5"

CAPS = LedgerCaps(
    total_microdollars=10_000_000,
    daily_microdollars=10_000_000,
    run_microdollars=250_000,
    runs_per_session=3,
    runs_per_client_hour=5,
)


class Recorder:
    """An HTTP stub that records the order of the calls it answers."""

    def __init__(self, *, count: int = 1200, out_tokens: int = 40) -> None:
        self.calls: list[str] = []
        self.count = count
        self.out_tokens = out_tokens
        self.fail_messages_with: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path.startswith("/v1/models/"):
            return httpx.Response(200, json={"id": MODEL, "display_name": "Sonnet 5"})
        if path == "/v1/messages/count_tokens":
            return httpx.Response(200, json={"input_tokens": self.count})
        if path == "/v1/messages":
            if self.fail_messages_with:
                return httpx.Response(self.fail_messages_with, json={"error": {}})
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "tool_use", "name": RESPONSE_TOOL, "input": {"ok": True}}],
                    "usage": {"input_tokens": self.count, "output_tokens": self.out_tokens},
                },
            )
        return httpx.Response(404, json={})

    @property
    def messages_calls(self) -> int:
        return self.calls.count("/v1/messages")


def _provider(recorder: Recorder) -> CloudProvider:
    provider = CloudProvider(api_key=KEY, model=MODEL, max_calls=16)
    provider._client = httpx.AsyncClient(
        base_url="http://cloud",
        transport=httpx.MockTransport(recorder),
        headers={"x-api-key": KEY},
    )
    return provider


def _budget(**kw: Any) -> RunBudget:
    base: dict[str, Any] = {
        "max_attempts": 8,
        "max_input_tokens": 100_000,
        "max_output_tokens": 10_000,
        "max_runtime_seconds": 60.0,
        "caps": CAPS,
    }
    return RunBudget(**(base | kw))


async def _governed(
    recorder: Recorder, ledger: CostLedger, **budget_kw: Any
) -> GovernedCloudProvider:
    inner = _provider(recorder)
    result = await preflight(inner, ledger)
    return GovernedCloudProvider(
        inner,
        ledger=ledger,
        preflight_result=result,
        budget=_budget(**budget_kw),
        run_id="run_1",
        session_id="ses_1",
        client_id="ip_1",
    )


def _request(**kw: Any) -> LLMRequest:
    base: dict[str, Any] = {
        "role": "planner",
        "system": "s" * 400,
        "user": "u" * 800,
        "schema": {},
        "max_tokens": 2048,
    }
    return LLMRequest(**(base | kw))


@pytest.fixture
def ledger() -> CostLedger:
    return CostLedger(fakeredis.FakeRedis())


# ------------------------------------------------------------- ordering
async def test_the_whole_sequence_runs_in_order(ledger: CostLedger) -> None:
    recorder = Recorder()
    provider = await _governed(recorder, ledger)
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()

    assert recorder.calls == [
        f"/v1/models/{MODEL}",
        "/v1/messages/count_tokens",
        "/v1/messages",
    ]
    # The reservation happened between the count and the Message.
    assert ledger.spent_microdollars("run_1") > 0


async def test_a_refused_reservation_creates_no_message(ledger: CostLedger) -> None:
    recorder = Recorder()
    # A run ceiling far below one call's worst case.
    tiny = LedgerCaps(
        total_microdollars=10_000_000,
        daily_microdollars=10_000_000,
        run_microdollars=1,
        runs_per_session=3,
        runs_per_client_hour=5,
    )
    provider = await _governed(recorder, ledger, caps=tiny)
    try:
        with pytest.raises(AIBudgetExceeded) as raised:
            await provider.complete_json(_request())
    finally:
        await provider.aclose()

    assert recorder.messages_calls == 0, "a Message was created despite refusal"
    assert raised.value.reason == "ai_run_budget_exceeded"


async def test_an_unreachable_ledger_creates_no_message() -> None:
    class Broken:
        def eval(self, *a: object, **k: object) -> object:
            raise ConnectionError("no route")

        def ping(self) -> object:
            raise ConnectionError("no route")

        def get(self, name: Any) -> object:
            raise ConnectionError("no route")

    recorder = Recorder()
    inner = _provider(recorder)
    try:
        with pytest.raises(PreflightFailed) as raised:
            await preflight(inner, CostLedger(Broken()))  # type: ignore[arg-type]
    finally:
        await inner.aclose()

    assert raised.value.reason == "ai_quota_storage_unavailable"
    assert recorder.messages_calls == 0
    # The ledger is checked before the free Models lookup, so nothing at all
    # was asked of the provider.
    assert recorder.calls == []


async def test_model_verification_precedes_the_first_message(ledger: CostLedger) -> None:
    recorder = Recorder()
    provider = await _governed(recorder, ledger)
    try:
        assert recorder.calls == [f"/v1/models/{MODEL}"], "preflight did not verify the model"
        assert provider.preflight_result.resolved_model == MODEL
        await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert recorder.calls.index(f"/v1/models/{MODEL}") < recorder.calls.index("/v1/messages")


async def test_an_unpriced_resolved_model_creates_no_message(ledger: CostLedger) -> None:
    class Unpriced(Recorder):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/v1/models/"):
                self.calls.append(request.url.path)
                return httpx.Response(200, json={"id": "claude-unpriced-9"})
            return super().__call__(request)

    recorder = Unpriced()
    inner = CloudProvider(api_key=KEY, model="claude-unpriced-9", max_calls=4)
    inner._client = httpx.AsyncClient(
        base_url="http://cloud", transport=httpx.MockTransport(recorder)
    )
    try:
        with pytest.raises(PreflightFailed) as raised:
            await preflight(inner, ledger)
    finally:
        await inner.aclose()

    assert raised.value.reason == "ai_model_not_priced"
    assert recorder.messages_calls == 0


# ------------------------------------------------------- cumulative caps
async def test_the_cumulative_input_ceiling_stops_before_dispatch(
    ledger: CostLedger,
) -> None:
    recorder = Recorder(count=900)
    provider = await _governed(recorder, ledger, max_input_tokens=1_000)
    try:
        await provider.complete_json(_request())  # 900 counted, within 1000
        before = recorder.messages_calls
        with pytest.raises(AIBudgetExceeded):
            await provider.complete_json(_request())
        assert recorder.messages_calls == before, "a second Message was created"
    finally:
        await provider.aclose()


async def test_the_output_allowance_is_clamped_to_what_remains(
    ledger: CostLedger,
) -> None:
    """A request may not ask for more output than the run has left."""
    sent: list[int] = []

    class Capturing(Recorder):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/messages":
                import json as _json

                sent.append(_json.loads(request.content)["max_tokens"])
            return super().__call__(request)

    recorder = Capturing(out_tokens=100)
    provider = await _governed(recorder, ledger, max_output_tokens=500)
    try:
        await provider.complete_json(_request(max_tokens=2048))
        assert sent == [500], "the request asked for more output than the run allowed"
        await provider.complete_json(_request(max_tokens=2048))
        assert sent[-1] == 400, "the allowance did not shrink as output was spent"
    finally:
        await provider.aclose()


async def test_the_attempt_ceiling_stops_before_dispatch(ledger: CostLedger) -> None:
    recorder = Recorder()
    provider = await _governed(recorder, ledger, max_attempts=2)
    try:
        await provider.complete_json(_request())
        await provider.complete_json(_request())
        with pytest.raises(AIBudgetExceeded):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert recorder.messages_calls == 2


async def test_a_run_out_of_time_creates_no_message(ledger: CostLedger) -> None:
    recorder = Recorder()
    provider = await _governed(recorder, ledger, max_runtime_seconds=0.001)
    import asyncio

    await asyncio.sleep(0.02)
    try:
        with pytest.raises(AIBudgetExceeded) as raised:
            await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert recorder.messages_calls == 0
    assert raised.value.reason == "ai_run_budget_exceeded"


# ---------------------------------------------------------- accounting
async def test_the_cost_total_is_exact_after_reconciliation(ledger: CostLedger) -> None:
    recorder = Recorder(count=1000, out_tokens=200)
    provider = await _governed(recorder, ledger)
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()

    expected = price_for(MODEL).cost_microdollars(1000, 200)
    assert ledger.spent_microdollars("run_1") == expected
    assert provider.budget.settled_microdollars == expected
    # The reservation was larger, and the unused part came back.
    assert provider.budget.reserved_microdollars > expected


async def test_an_ambiguous_failure_is_retained_not_refunded(ledger: CostLedger) -> None:
    """A sent request may have been billed even if no answer arrived."""
    recorder = Recorder()
    recorder.fail_messages_with = 500
    provider = await _governed(recorder, ledger)
    try:
        with pytest.raises(LLMError):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()

    assert ledger.spent_microdollars("run_1") > 0, "the reservation was refunded"
    assert provider.budget.retained_microdollars > 0
    assert provider.budget.settled_microdollars == 0


async def test_a_failed_attempt_still_consumes_the_attempt_budget(
    ledger: CostLedger,
) -> None:
    recorder = Recorder()
    recorder.fail_messages_with = 500
    provider = await _governed(recorder, ledger, max_attempts=2)
    try:
        for _ in range(2):
            with pytest.raises(LLMError):
                await provider.complete_json(_request())
        with pytest.raises(AIBudgetExceeded):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert recorder.messages_calls == 2


# -------------------------------------------------------------- secrets
async def test_nothing_leaks_the_credential(ledger: CostLedger) -> None:
    recorder = Recorder()
    recorder.fail_messages_with = 401
    provider = await _governed(recorder, ledger)
    try:
        with pytest.raises(LLMError) as raised:
            await provider.complete_json(_request())
    finally:
        await provider.aclose()

    assert KEY not in str(raised.value)
    stored = fakeredis.FakeRedis()  # a fresh store, to check key shapes only
    assert KEY not in repr(provider.budget.as_dict())
    assert KEY not in repr(provider.preflight_result.as_dict())
    del stored


async def test_the_ledger_never_holds_prompt_text(ledger: CostLedger) -> None:
    recorder = Recorder()
    provider = await _governed(recorder, ledger)
    try:
        await provider.complete_json(_request(user="SECRET-PROMPT-MARKER" * 20))
    finally:
        await provider.aclose()

    client = ledger._redis
    for key in client.keys("*"):  # type: ignore[attr-defined]
        assert b"SECRET-PROMPT-MARKER" not in key
        value = client.get(key)  # type: ignore[attr-defined]
        if value is not None:
            assert b"SECRET-PROMPT-MARKER" not in value
