"""Durable admission control for paid runs.

Process-local counters cannot bound an anonymous paid endpoint: a cold start
resets them and a second instance never sees the first one's total, so the
ceiling a deployment believes it has is the ceiling times the number of
processes running.

These tests exercise the real Lua against a Redis command set, because the
atomicity is the control. Checking a limit in Python and writing afterwards
would let two callers both read "under the limit" and both proceed.
"""

from __future__ import annotations

import fakeredis
import pytest

from agentic_analytics.llm.ledger import (
    CLIENT_LIMIT,
    DAILY_LIMIT,
    GLOBAL_LIMIT,
    RUN_LIMIT,
    SESSION_LIMIT,
    CostLedger,
    LedgerCaps,
    LedgerUnavailable,
    open_ledger,
)
from agentic_analytics.llm.pricing import UnknownModelPrice, price_for

CAPS = LedgerCaps(
    total_microdollars=1_000_000,
    daily_microdollars=500_000,
    run_microdollars=100_000,
    runs_per_session=3,
    runs_per_client_hour=5,
)


@pytest.fixture
def ledger() -> CostLedger:
    return CostLedger(fakeredis.FakeRedis())


def _reserve(
    ledger: CostLedger,
    amount: int,
    *,
    run: str = "run_1",
    call: str = "c1",
    session: str = "ses_1",
    client: str = "ip_1",
    first: bool = False,
    caps: LedgerCaps = CAPS,
):
    return ledger.reserve(
        run_id=run,
        call_id=call,
        session_id=session,
        client_id=client,
        amount_microdollars=amount,
        caps=caps,
        first_call_of_run=first,
    )


# --------------------------------------------------------------- admission
def test_a_reservation_is_taken_before_the_call(ledger: CostLedger) -> None:
    admission = _reserve(ledger, 10_000, first=True)
    assert admission.admitted
    assert admission.reserved_microdollars == 10_000
    assert ledger.spent_microdollars("run_1") == 10_000


def test_the_run_ceiling_refuses_before_dispatch(ledger: CostLedger) -> None:
    assert _reserve(ledger, 90_000, call="c1", first=True).admitted
    refused = _reserve(ledger, 20_000, call="c2")
    assert not refused.admitted
    assert refused.reason == RUN_LIMIT


def test_the_daily_ceiling_refuses(ledger: CostLedger) -> None:
    for i in range(5):
        assert _reserve(
            ledger, 100_000, run=f"r{i}", call="c", session=f"s{i}", client=f"ip{i}", first=True
        ).admitted
    refused = _reserve(ledger, 100_000, run="r9", call="c", session="s9", client="ip9", first=True)
    assert not refused.admitted
    assert refused.reason == DAILY_LIMIT


def test_the_global_ceiling_refuses(ledger: CostLedger) -> None:
    caps = LedgerCaps(
        total_microdollars=150_000,
        daily_microdollars=10_000_000,
        run_microdollars=100_000,
        runs_per_session=99,
        runs_per_client_hour=99,
    )
    assert _reserve(ledger, 100_000, run="r1", call="c", caps=caps, first=True).admitted
    refused = _reserve(ledger, 100_000, run="r2", call="c", caps=caps, first=True)
    assert not refused.admitted
    assert refused.reason == GLOBAL_LIMIT


def test_the_session_run_ceiling_counts_runs_not_calls(ledger: CostLedger) -> None:
    for i in range(3):
        assert _reserve(ledger, 1_000, run=f"r{i}", call="c", first=True).admitted
    refused = _reserve(ledger, 1_000, run="r9", call="c", first=True)
    assert not refused.admitted
    assert refused.reason == SESSION_LIMIT
    # Further calls within an admitted run are not new runs.
    assert _reserve(ledger, 1_000, run="r0", call="c2", first=False).admitted


def test_the_client_hour_ceiling_refuses(ledger: CostLedger) -> None:
    caps = LedgerCaps(
        total_microdollars=10_000_000,
        daily_microdollars=10_000_000,
        run_microdollars=100_000,
        runs_per_session=99,
        runs_per_client_hour=2,
    )
    for i in range(2):
        assert _reserve(
            ledger, 1_000, run=f"r{i}", call="c", session=f"s{i}", caps=caps, first=True
        ).admitted
    refused = _reserve(ledger, 1_000, run="r9", call="c", session="s9", caps=caps, first=True)
    assert not refused.admitted
    assert refused.reason == CLIENT_LIMIT


# ------------------------------------------------------------- concurrency
def test_two_instances_cannot_overspend_the_shared_limit() -> None:
    """The point of the durable ledger, stated as a test.

    Two `CostLedger` objects over one store stand in for two Render
    instances. Both ask for budget that only one of them can have.
    """
    shared = fakeredis.FakeRedis()
    first, second = CostLedger(shared), CostLedger(shared)
    caps = LedgerCaps(
        total_microdollars=100_000,
        daily_microdollars=100_000,
        run_microdollars=100_000,
        runs_per_session=99,
        runs_per_client_hour=99,
    )
    a = first.reserve(
        run_id="r1",
        call_id="c",
        session_id="s1",
        client_id="ip",
        amount_microdollars=80_000,
        caps=caps,
        first_call_of_run=True,
    )
    b = second.reserve(
        run_id="r2",
        call_id="c",
        session_id="s2",
        client_id="ip",
        amount_microdollars=80_000,
        caps=caps,
        first_call_of_run=True,
    )
    assert a.admitted
    assert not b.admitted, "the second instance overspent the shared ceiling"


def test_many_concurrent_reservations_never_exceed_the_ceiling() -> None:
    import threading

    shared = fakeredis.FakeRedis()
    caps = LedgerCaps(
        total_microdollars=50_000,
        daily_microdollars=10_000_000,
        run_microdollars=10_000_000,
        runs_per_session=999,
        runs_per_client_hour=999,
    )
    admitted: list[bool] = []
    lock = threading.Lock()

    def attempt(i: int) -> None:
        ledger = CostLedger(shared)
        got = ledger.reserve(
            run_id=f"r{i}",
            call_id="c",
            session_id=f"s{i}",
            client_id="ip",
            amount_microdollars=10_000,
            caps=caps,
            first_call_of_run=True,
        )
        with lock:
            admitted.append(got.admitted)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert sum(admitted) == 5, f"{sum(admitted)} admitted against a 5-reservation ceiling"


def test_a_restart_does_not_reset_the_ledger() -> None:
    """A new process is a new object over the same store, not a clean slate."""
    shared = fakeredis.FakeRedis()
    caps = LedgerCaps(
        total_microdollars=100_000,
        daily_microdollars=100_000,
        run_microdollars=100_000,
        runs_per_session=99,
        runs_per_client_hour=99,
    )
    CostLedger(shared).reserve(
        run_id="r1",
        call_id="c",
        session_id="s",
        client_id="ip",
        amount_microdollars=90_000,
        caps=caps,
        first_call_of_run=True,
    )
    after_restart = CostLedger(shared)
    refused = after_restart.reserve(
        run_id="r2",
        call_id="c",
        session_id="s2",
        client_id="ip2",
        amount_microdollars=90_000,
        caps=caps,
        first_call_of_run=True,
    )
    assert not refused.admitted


# ------------------------------------------------------------ idempotency
def test_the_same_call_reserves_once(ledger: CostLedger) -> None:
    """A retry of one logical call must not be charged twice."""
    first = _reserve(ledger, 10_000, call="c1", first=True)
    again = _reserve(ledger, 10_000, call="c1")
    assert first.admitted and again.admitted
    assert ledger.spent_microdollars("run_1") == 10_000


# ---------------------------------------------------------- reconciliation
def test_settling_releases_only_the_unused_reservation(ledger: CostLedger) -> None:
    admission = _reserve(ledger, 50_000, first=True)
    assert (
        ledger.settle(
            run_id="run_1", reservation_id=admission.reservation_id, actual_microdollars=12_000
        )
        == "released"
    )
    assert ledger.spent_microdollars("run_1") == 12_000


def test_spending_more_than_reserved_charges_the_difference(ledger: CostLedger) -> None:
    admission = _reserve(ledger, 10_000, first=True)
    assert (
        ledger.settle(
            run_id="run_1", reservation_id=admission.reservation_id, actual_microdollars=25_000
        )
        == "charged"
    )
    assert ledger.spent_microdollars("run_1") == 25_000


def test_an_ambiguous_failure_is_not_refunded(ledger: CostLedger) -> None:
    """A request that timed out in transit may still have been billed."""
    admission = _reserve(ledger, 40_000, first=True)
    before = ledger.spent_microdollars("run_1")
    ledger.abandon(reservation_id=admission.reservation_id)
    assert ledger.spent_microdollars("run_1") == before == 40_000


def test_settling_an_unknown_reservation_is_reported(ledger: CostLedger) -> None:
    assert ledger.settle(run_id="run_1", reservation_id="nope", actual_microdollars=1) == (
        "unknown_reservation"
    )


def test_a_retry_consumes_the_same_run_budget(ledger: CostLedger) -> None:
    assert _reserve(ledger, 60_000, call="c1", first=True).admitted
    assert not _reserve(ledger, 60_000, call="c2").admitted, "a retry reset the run budget"


# -------------------------------------------------------------- failure
def test_an_unreachable_store_refuses_rather_than_guessing() -> None:
    class Broken:
        def eval(self, *a: object, **k: object) -> object:
            raise ConnectionError("no route to host")

        def ping(self) -> object:
            raise ConnectionError("no route to host")

        def get(self, name: str) -> object:
            raise ConnectionError("no route to host")

        def set(self, *a: object, **k: object) -> object: ...
        def delete(self, *a: object) -> object: ...

    ledger = CostLedger(Broken())  # type: ignore[arg-type]
    assert ledger.healthy() is False
    with pytest.raises(LedgerUnavailable):
        _reserve(ledger, 1_000, first=True)


def test_no_ledger_without_a_url() -> None:
    assert open_ledger(None) is None
    assert open_ledger("") is None


# ----------------------------------------------------------------- pricing
def test_money_is_integer_microdollars(ledger: CostLedger) -> None:
    admission = _reserve(ledger, 12_345, first=True)
    assert isinstance(admission.reserved_microdollars, int)
    assert isinstance(ledger.spent_microdollars("run_1"), int)


def test_an_unpriced_model_fails_closed() -> None:
    with pytest.raises(UnknownModelPrice):
        price_for("some-model-nobody-priced")


def test_pricing_rounds_up_so_a_ceiling_is_never_undershot() -> None:
    price = price_for("claude-sonnet-5")
    assert price.cost_microdollars(1, 0) == 2
    assert isinstance(price.cost_microdollars(4000, 2048), int)


def test_the_ledger_stores_no_content(ledger: CostLedger) -> None:
    """Counters and reservations only: no prompt, row, result or secret."""
    _reserve(ledger, 1_000, first=True)
    client = ledger._redis
    keys = [k.decode() for k in client.keys("*")]  # type: ignore[attr-defined]
    assert keys
    for key in keys:
        assert "question" not in key and "prompt" not in key
        value = client.get(key)  # type: ignore[attr-defined]
        if value is not None:
            assert value.decode().lstrip("-").isdigit(), f"{key} holds non-numeric data"
