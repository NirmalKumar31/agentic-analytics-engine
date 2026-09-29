"""Waiting for the session's connection must be bounded.

An evaluation run wedged for fifty-one minutes: six idle sockets, two
seconds of CPU consumed, a 600-second per-call ceiling and a 2400-second
per-question ceiling both silently defeated, and the process would not even
respond to SIGINT.

The cause is a combination that looks harmless in isolation. Tool calls run
in `anyio.to_thread` workers, which by default cannot be cancelled -- a
cancel scope waits for the thread to finish rather than interrupting it. And
`with session.lock` waits forever. So one worker holding the connection
makes every other worker unreachable: their timeouts fire and cannot take
effect, because the threads they want to cancel are blocked in C, not
running Python.

Bounding the wait turns that into an error an agent can read and retry.
"""

from __future__ import annotations

import threading
import time

import pytest

from agentic_analytics.analytics.execute import (
    LOCK_WAIT_SECONDS,
    QueryError,
    held_connection,
)


class _Session:
    """Only the attribute the helper touches."""

    def __init__(self) -> None:
        self.lock = threading.Lock()


def test_the_connection_is_released_afterwards() -> None:
    session = _Session()
    with held_connection(session):  # type: ignore[arg-type]
        assert session.lock.locked()
    assert not session.lock.locked()


def test_the_connection_is_released_even_when_the_query_raises() -> None:
    session = _Session()
    with pytest.raises(ValueError), held_connection(session):  # type: ignore[arg-type]
        raise ValueError("query blew up")
    assert not session.lock.locked(), "a failed query must not strand the connection"


def test_a_busy_connection_fails_instead_of_waiting_forever() -> None:
    """The whole point: an error, not an unbounded block."""
    session = _Session()
    session.lock.acquire()
    try:
        started = time.monotonic()
        with (
            pytest.raises(QueryError) as raised,
            held_connection(session, timeout_seconds=0.2),  # type: ignore[arg-type]
        ):
            pytest.fail("should not have acquired the lock")
        elapsed = time.monotonic() - started
    finally:
        session.lock.release()

    assert elapsed < 5, f"waited {elapsed:.1f}s despite a 0.2s bound"
    assert "busy" in str(raised.value)
    # The message tells an agent what to do differently.
    assert "narrower" in str(raised.value) or "fewer" in str(raised.value)


def test_a_failed_acquisition_does_not_release_someone_elses_lock() -> None:
    """Releasing a lock this thread never held would corrupt the next query."""
    session = _Session()
    session.lock.acquire()
    with (
        pytest.raises(QueryError),
        held_connection(session, timeout_seconds=0.05),  # type: ignore[arg-type]
    ):
        pass
    assert session.lock.locked(), "the holder's lock was released by the waiter"
    session.lock.release()


def test_contending_threads_all_terminate() -> None:
    """Six workers against one connection is the real shape of the hang."""
    session = _Session()
    errors: list[str] = []
    done = threading.Event()

    def worker() -> None:
        try:
            with held_connection(session, timeout_seconds=0.3):  # type: ignore[arg-type]
                time.sleep(0.05)
        except QueryError as exc:
            errors.append(str(exc))

    session.lock.acquire()
    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()

    def release_later() -> None:
        time.sleep(1.0)
        session.lock.release()
        done.set()

    threading.Thread(target=release_later).start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive(), "a worker never gave up on the connection"
    done.wait(timeout=10)

    assert errors, "every worker should have timed out while the lock was held"


def test_the_default_bound_is_finite_and_sane() -> None:
    assert 0 < LOCK_WAIT_SECONDS <= 120


def test_every_connection_use_goes_through_the_bounded_helper() -> None:
    """`with session.lock` is the shape that caused the hang.

    Adding one back anywhere reintroduces a wait that no timeout can reach,
    so this fails the moment it reappears.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "agentic_analytics"
    # The code form, with its colon -- prose about the pattern is fine.
    offenders = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "with session.lock:" in path.read_text()
    ]
    assert not offenders, f"unbounded session.lock use in: {offenders}"
