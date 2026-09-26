"""A session may not close while a run can still use it.

An analysis holds an `AnalysisSession`, and therefore the DuckDB connection
behind it. Four separate paths close a session -- an explicit DELETE, opening
another dataset in the same browser, TTL expiry, and capacity eviction -- and
each one could previously do it while a run was mid-flight. The run's next
tool call then reaches a closed connection and surfaces to the visitor as an
internal error.

Locking the connection does not fix that: it serialises one query while the
analysis goes on to make another call afterwards. The contract has to be
about ownership, not about individual statements:

    teardown request -> cancel the runs -> wait for them to unwind -> close

"Wait" is the load-bearing word. A cancelled task has not finished; its
`finally` block -- which closes the provider and returns the capacity slot --
has not run yet. Returning before it does is how a slot leaks.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.api.runs import RunRecord, RunRegistry
from agentic_analytics.config import Settings
from agentic_analytics.events import EventBus
from agentic_analytics.warehouse.session import SessionManager, open_demo_session

REPO = Path(__file__).resolve().parents[2]


# ------------------------------------------------- RunRegistry bound
def _record(registry: RunRegistry, session_id: str, age: float) -> RunRecord:
    record = registry.create(session_id, "q")
    record.created_at = time.time() - age
    return record


def _finish(record: RunRecord) -> RunRecord:
    record.error = "done"
    return record


def test_a_running_oldest_record_no_longer_blocks_eviction() -> None:
    """The bug: the ceiling was advisory whenever the oldest run was live.

    `_evict` picked the globally oldest record and stopped if it was still
    running, so one long analysis at the front of the queue kept every
    finished run behind it and the registry grew past `max_runs` without
    bound.
    """
    registry = RunRegistry(max_runs=3, ttl_seconds=3600)
    oldest_running = _record(registry, "ses_a", age=500)  # still running

    for index in range(8):
        _finish(_record(registry, f"ses_{index}", age=400 - index))

    assert len(registry._runs) <= 3, "the registry exceeded its own ceiling"
    assert registry.active_count() == 1
    assert oldest_running.run_id in registry._runs, "a running run was evicted"


def test_the_oldest_removable_run_goes_first() -> None:
    registry = RunRegistry(max_runs=2, ttl_seconds=3600)
    old = _finish(_record(registry, "s", age=300))
    middle = _finish(_record(registry, "s", age=200))
    newest = _finish(_record(registry, "s", age=100))
    registry.create("s", "trigger")

    assert old.run_id not in registry._runs
    assert middle.run_id not in registry._runs or newest.run_id in registry._runs


def test_a_registry_full_of_running_runs_keeps_them_all() -> None:
    """Killing a live analysis to satisfy a count would be worse than the count.

    `max_concurrent_analyses` is what actually bounds this case; the registry
    logs and yields rather than cancelling work nobody asked it to cancel.
    """
    registry = RunRegistry(max_runs=2, ttl_seconds=3600)
    records = [_record(registry, f"s{i}", age=100 - i) for i in range(5)]
    registry.create("s", "one more")
    for record in records:
        assert record.run_id in registry._runs


def test_ttl_eviction_still_spares_running_runs() -> None:
    registry = RunRegistry(max_runs=50, ttl_seconds=1.0)
    stale_done = _finish(_record(registry, "s", age=10))
    stale_running = _record(registry, "s", age=10)
    registry.create("s", "trigger")

    assert stale_done.run_id not in registry._runs
    assert stale_running.run_id in registry._runs


def test_counts_stay_correct_after_eviction() -> None:
    registry = RunRegistry(max_runs=3, ttl_seconds=3600)
    live = _record(registry, "ses_live", age=900)
    for index in range(6):
        _finish(_record(registry, "ses_live", age=100 - index))

    assert registry.active_count() == 1
    assert registry.session_run_count("ses_live") == len(registry._runs)
    assert registry.active_for_session("ses_live") == [live]
    assert registry.active_for_session("ses_other") == []


# ----------------------------------------------- cancel_session contract
async def test_cancel_session_awaits_the_task_before_returning() -> None:
    """The guarantee the whole contract rests on."""
    registry = RunRegistry()
    record = registry.create("ses_1", "q")
    unwound = asyncio.Event()

    async def slow() -> None:
        try:
            await asyncio.sleep(30)
        finally:
            await asyncio.sleep(0)  # a real teardown yields at least once
            unwound.set()

    record.task = asyncio.create_task(slow())
    await asyncio.sleep(0)

    cancelled = await registry.cancel_session("ses_1")

    assert cancelled == 1
    assert unwound.is_set(), "cancel_session returned before the task unwound"
    assert record.status == "cancelled"
    assert record.task.done()


async def test_cancel_session_ignores_other_sessions_and_finished_runs() -> None:
    registry = RunRegistry()
    other = registry.create("ses_other", "q")
    other.task = asyncio.create_task(asyncio.sleep(30))
    done = _finish(registry.create("ses_1", "q"))

    assert await registry.cancel_session("ses_1") == 0
    assert done.status == "failed"
    assert not other.task.done()

    other.task.cancel()
    await asyncio.gather(other.task, return_exceptions=True)


async def test_cancel_session_closes_the_event_stream() -> None:
    """A stream nobody closes leaves a browser waiting forever."""
    registry = RunRegistry()
    record = registry.create("ses_1", "q")
    record.bus = EventBus()
    record.task = asyncio.create_task(asyncio.sleep(30))
    await asyncio.sleep(0)

    await registry.cancel_session("ses_1")

    drained = [event async for event in record.bus.subscribe()]
    assert drained == [] or drained is not None  # subscribe terminates


async def test_cancel_session_handles_several_runs_for_one_session() -> None:
    registry = RunRegistry()
    records = [registry.create("ses_1", f"q{i}") for i in range(3)]
    for record in records:
        record.task = asyncio.create_task(asyncio.sleep(30))
    await asyncio.sleep(0)

    assert await registry.cancel_session("ses_1") == 3
    assert all(r.status == "cancelled" for r in records)
    assert all(r.task is not None and r.task.done() for r in records)


# ------------------------------------------- end to end through the API
class _SlowProvider:
    """Stands in for a provider so a run is long enough to interrupt."""


def _settings(warehouse_dir: Path, tmp_path: Path, **overrides: Any) -> Settings:
    class _Pointed(Settings):
        @property
        def demo_warehouse_dir(self) -> Path:
            return warehouse_dir

    defaults: dict[str, Any] = {
        "upload_dir": tmp_path / "uploads",
        "recordings_dir": REPO / "examples" / "recordings",
        "live_analytics_enabled": True,
        "uploads_enabled": True,
        "log_json": False,
    }
    return _Pointed(**(defaults | overrides))


def _slow_run(monkeypatch: pytest.MonkeyPatch, started: threading.Event) -> None:
    """Make every analysis hang until cancelled, deterministically.

    A `threading.Event`, not an asyncio one: `TestClient` drives the app on
    its own event loop, so a flag set there is not observable by awaiting in
    the test's loop.
    """

    async def hang(*args: Any, **kwargs: Any) -> Any:
        started.set()
        await asyncio.sleep(60)
        raise AssertionError("the run was never cancelled")

    monkeypatch.setattr("agentic_analytics.api.app.run_analysis", hang)


async def _wait(event: threading.Event, wait_seconds: float = 5.0) -> None:
    # Polled rather than `asyncio.timeout`, because the flag is set on the
    # TestClient's portal thread and there is nothing in this loop to await.
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if event.is_set():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the analysis never started")


def test_deleting_a_dataset_cancels_its_running_analysis(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    _slow_run(monkeypatch, started)
    cfg = _settings(warehouse_dir, tmp_path)
    app = create_app(cfg)

    with TestClient(app) as client:
        session_id = client.post("/api/datasets/demo").json()["session_id"]
        run_id = client.post(
            "/api/analyses", json={"session_id": session_id, "question": "why?"}
        ).json()["run_id"]

        # The DELETE must not hang, and must not leave the run running.
        assert client.delete(f"/api/datasets/{session_id}").status_code == 200

        # The dataset is gone, and so is the run's access to it.
        assert client.get(f"/api/datasets/{session_id}").status_code == 404
        assert client.get(f"/api/analyses/{run_id}").status_code == 404

    assert started.is_set(), "the analysis never began, so nothing was cancelled"


def test_capacity_is_released_when_a_run_is_cancelled(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deleted dataset must not cost the demo a concurrency slot.

    With one slot configured, a second analysis can only start if the first
    one's slot came back -- which only happens if the cancelled task ran its
    `finally`.
    """
    started = threading.Event()
    _slow_run(monkeypatch, started)
    cfg = _settings(warehouse_dir, tmp_path, max_concurrent_analyses=1)

    with TestClient(create_app(cfg)) as client:
        first = client.post("/api/datasets/demo").json()["session_id"]
        assert (
            client.post("/api/analyses", json={"session_id": first, "question": "one"}).status_code
            == 202
        )
        client.delete(f"/api/datasets/{first}")

        second = client.post("/api/datasets/demo").json()["session_id"]
        response = client.post("/api/analyses", json={"session_id": second, "question": "two"})

    assert response.status_code == 202, (
        f"the capacity slot leaked: {response.status_code} {response.text[:160]}"
    )


def test_opening_a_second_dataset_cancels_the_first_run(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacement is a teardown too, and takes the same path."""
    started = threading.Event()
    _slow_run(monkeypatch, started)
    cfg = _settings(warehouse_dir, tmp_path, max_concurrent_analyses=1)

    with TestClient(create_app(cfg)) as client:
        first = client.post("/api/datasets/demo").json()["session_id"]
        client.post("/api/analyses", json={"session_id": first, "question": "one"})

        # Same browser, same cookie: this replaces the session.
        second = client.post("/api/datasets/demo").json()["session_id"]
        assert second != first
        assert client.get(f"/api/datasets/{first}").status_code == 404

        # And the slot came back, so the new session can be analysed.
        assert (
            client.post("/api/analyses", json={"session_id": second, "question": "two"}).status_code
            == 202
        )


async def test_ttl_expiry_cancels_before_closing(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The janitor must take the same path as an explicit delete."""
    started = threading.Event()
    _slow_run(monkeypatch, started)
    # Long enough that the session survives being opened and analysed, short
    # enough that the sweep catches it during the test. A 50ms TTL expired
    # the session before the analysis could be posted, which tested nothing.
    cfg = _settings(
        warehouse_dir,
        tmp_path,
        session_ttl_seconds=1.5,
        session_sweep_seconds=0.2,
        max_concurrent_analyses=1,
    )

    with TestClient(create_app(cfg)) as client:
        session_id = client.post("/api/datasets/demo").json()["session_id"]
        started_run = client.post(
            "/api/analyses", json={"session_id": session_id, "question": "one"}
        )
        assert started_run.status_code == 202, started_run.text[:200]
        await _wait(started)

        await asyncio.sleep(2.5)  # past the TTL, and through at least one sweep

        client.cookies.clear()
        assert client.get(f"/api/datasets/{session_id}").status_code == 404

        # The slot returned, so the sweep cancelled rather than orphaned.
        fresh = client.post("/api/datasets/demo").json()["session_id"]
        assert (
            client.post("/api/analyses", json={"session_id": fresh, "question": "two"}).status_code
            == 202
        )


def test_session_eviction_cancels_the_evicted_sessions_run(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capacity eviction is the fourth teardown path and behaves the same."""
    started = threading.Event()
    _slow_run(monkeypatch, started)
    cfg = _settings(warehouse_dir, tmp_path, max_concurrent_sessions=1, max_concurrent_analyses=2)

    with TestClient(create_app(cfg)) as first, TestClient(create_app(cfg)) as _unused:
        session_id = first.post("/api/datasets/demo").json()["session_id"]
        assert (
            first.post(
                "/api/analyses", json={"session_id": session_id, "question": "one"}
            ).status_code
            == 202
        )
        # A second session forces the first out of a one-session manager.
        first.cookies.clear()
        replacement = first.post("/api/datasets/demo").json()["session_id"]
        assert replacement != session_id
        assert first.get(f"/api/datasets/{session_id}").status_code == 404


def test_a_normal_run_is_unaffected_by_any_of_this(warehouse_dir: Path, tmp_path: Path) -> None:
    """The regression guard: cancellation plumbing must not break the happy path."""
    cfg = _settings(warehouse_dir, tmp_path)
    with TestClient(create_app(cfg)) as client:
        session_id = client.post("/api/datasets/demo").json()["session_id"]
        started = client.post(
            "/api/analyses",
            json={"session_id": session_id, "question": "Describe the dataset"},
        )
        assert started.status_code == 202
        run_id = started.json()["run_id"]

        deadline = time.time() + 60
        status = "running"
        while time.time() < deadline:
            payload = client.get(f"/api/analyses/{run_id}").json()
            status = payload["status"]
            if status != "running":
                break
            time.sleep(0.2)

    assert status == "completed", status


# ------------------------------- a task that will not stop when asked
async def test_cancellation_reports_work_that_did_not_finish() -> None:
    """Requesting cancellation is not achieving it.

    The previous version suppressed the grace-period `TimeoutError` and
    returned a count, so a caller proceeded to close a session whose task
    was still running. The result now says so.
    """
    registry = RunRegistry()
    record = registry.create("ses_stubborn", "q")
    release = asyncio.Event()

    async def ignores_cancellation() -> None:
        # Genuinely uncooperative: it swallows every cancellation until it
        # is ready. A task mid-`finally`, or blocked in an uninterruptible
        # call, behaves the same way from the outside.
        deadline = time.time() + 15
        while not release.is_set() and time.time() < deadline:
            try:
                await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                continue

    record.task = asyncio.create_task(ignores_cancellation())
    await asyncio.sleep(0)

    result = await registry.cancel_session_detailed("ses_stubborn", grace_seconds=0.2)

    assert result.requested == 1
    assert result.terminal == 0
    assert result.timed_out == 1
    assert result.all_terminal is False
    assert result.active_run_ids == [record.run_id]

    # And once it does let go, a retry finds everything terminal.
    release.set()
    async with asyncio.timeout(5):
        await asyncio.gather(record.task, return_exceptions=True)
    again = await registry.cancel_session_detailed("ses_stubborn", grace_seconds=0.5)
    assert again.requested == 0, "the run is still reported as active"


def test_a_session_is_not_closed_while_a_run_still_holds_it(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole invariant, end to end through the API.

    A task that refuses to finish within the grace period must leave its
    session open, closing, and refusing new work -- and the DELETE must not
    claim the data is gone.
    """
    started = threading.Event()
    release = threading.Event()

    async def stubborn(*args: Any, **kwargs: Any) -> Any:
        started.set()
        # Swallows cancellation until released, so the grace period really
        # does expire with work still live.
        # Swallows cancellation until released or the deadline passes, so
        # the grace period really expires with work still live -- but it
        # always exits, or it would hang the test client's shutdown.
        deadline = time.time() + 15
        while not release.is_set() and time.time() < deadline:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue
        raise asyncio.CancelledError

    monkeypatch.setattr("agentic_analytics.api.app.run_analysis", stubborn)
    cfg = _settings(
        warehouse_dir, tmp_path, max_concurrent_analyses=2, session_cancel_grace_seconds=0.3
    )

    with TestClient(create_app(cfg)) as client:
        session_id = client.post("/api/datasets/demo").json()["session_id"]
        client.post("/api/analyses", json={"session_id": session_id, "question": "one"})
        assert started.wait(10), "the analysis never started"

        deleted = client.delete(f"/api/datasets/{session_id}")

        # Honest: cleanup is pending, so it does not say "deleted".
        assert deleted.status_code == 202, deleted.text[:200]
        assert deleted.json()["status"] == "closing"

        # It refuses new analyses while closing.
        refused = client.post("/api/analyses", json={"session_id": session_id, "question": "two"})
        assert refused.status_code in (409, 404), refused.status_code

        # Letting the task finish allows the deferred close to complete.
        release.set()
        deadline = time.time() + 20
        while time.time() < deadline:
            if client.get(f"/api/datasets/{session_id}").status_code == 404:
                break
            time.sleep(0.2)

        assert client.get(f"/api/datasets/{session_id}").status_code == 404


def test_a_deferred_close_does_not_affect_another_session(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One stuck teardown must not take the service with it."""
    started = threading.Event()
    release = threading.Event()

    async def stubborn(*args: Any, **kwargs: Any) -> Any:
        started.set()
        # Swallows cancellation until released or the deadline passes, so
        # the grace period really expires with work still live -- but it
        # always exits, or it would hang the test client's shutdown.
        deadline = time.time() + 15
        while not release.is_set() and time.time() < deadline:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue
        raise asyncio.CancelledError

    monkeypatch.setattr("agentic_analytics.api.app.run_analysis", stubborn)
    cfg = _settings(
        warehouse_dir, tmp_path, max_concurrent_analyses=3, session_cancel_grace_seconds=0.3
    )

    with TestClient(create_app(cfg)) as client:
        stuck = client.post("/api/datasets/demo").json()["session_id"]
        client.post("/api/analyses", json={"session_id": stuck, "question": "one"})
        assert started.wait(10)
        assert client.delete(f"/api/datasets/{stuck}").status_code == 202

        # A different browser opens its own dataset and is unaffected.
        client.cookies.clear()
        other = client.post("/api/datasets/demo")
        assert other.status_code == 200
        other_id = other.json()["session_id"]
        assert client.get(f"/api/datasets/{other_id}").status_code == 200

        release.set()


def test_a_closing_session_is_not_evicted_by_ttl(warehouse_dir: Path) -> None:
    """The janitor owns expiry, but not sessions the app is tearing down."""
    manager = SessionManager(ttl_seconds=0.01)
    session = manager.add(open_demo_session(warehouse_dir))
    manager.begin_closing(session.session_id)
    time.sleep(0.05)

    assert manager.expire_stale() == 0, "a closing session was expired underneath a run"
    assert manager.closing_session_ids() == [session.session_id]
    assert session.state == "closing"
    assert session.accepts_new_work is False
    manager.close_all()


def test_a_closing_session_is_not_evicted_for_capacity(warehouse_dir: Path) -> None:
    manager = SessionManager(max_sessions=1, ttl_seconds=600)
    first = manager.add(open_demo_session(warehouse_dir))
    manager.begin_closing(first.session_id)

    second = manager.add(open_demo_session(warehouse_dir))
    # The closing session survives: something is still using it.
    assert first.state == "closing"
    assert first.session_id in manager.closing_session_ids()
    assert manager.get(second.session_id, second.session_key) is second
    manager.close_all()


def test_a_closing_session_is_not_retired_by_a_replacement(warehouse_dir: Path) -> None:
    """Opening another dataset must not force-close one mid-teardown."""
    manager = SessionManager()
    session = manager.add(open_demo_session(warehouse_dir))
    manager.begin_closing(session.session_id)

    assert manager.drop_by_key(session.session_key) == 0
    assert session.state == "closing"
    manager.close_all()
