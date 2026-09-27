"""The deadlock, reproduced through the stack that actually deadlocked.

The unit tests prove `held_connection` is bounded. They do not prove the
path that hung, which was longer than the helper:

    LangGraph worker -> MCP client -> anyio.to_thread -> analytics tool
        -> held_connection -> DuckDB session

The thread layer is what made the original failure unrecoverable. Tool calls
run in `anyio.to_thread` workers, and those are not cancellable by default:
a cancel scope waits for the thread to finish rather than interrupting it.
So a worker blocked on an unbounded lock could not be reached by the
per-call timeout, the per-question timeout, or SIGINT. Fifty-one minutes,
two seconds of CPU, six idle sockets.

This test holds the session's connection from outside and fires concurrent
real MCP tool calls at it. What it asserts is not "an error was raised" but
that the calls *came back at all*.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentic_analytics.mcp_layer.client import AnalyticsToolset, ToolBudget, ToolCallFailed
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.warehouse.session import SessionManager, open_demo_session

#: Every wait in this test is bounded, including the test itself. A
#: regression here is a hang, and a hanging test in CI is the same outage in
#: a different place.
OUTER_TIMEOUT = 120.0
HOLD_SECONDS = 6.0
LOCK_WAIT = 0.5


@pytest.fixture
def demo(warehouse_dir: Path) -> Iterator[tuple[SessionManager, str, str]]:
    manager = SessionManager()
    session = manager.add(open_demo_session(warehouse_dir))
    try:
        yield manager, session.session_id, session.session_key
    finally:
        manager.close_all()


@pytest.mark.timeout(int(OUTER_TIMEOUT))
async def test_concurrent_tool_calls_survive_a_held_connection(
    demo: tuple[SessionManager, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, session_id, session_key = demo
    session = manager.get(session_id, session_key)

    # Bound the wait far below the test's own ceiling, so a regression shows
    # up as a failure rather than as a slow pass.
    monkeypatch.setattr("agentic_analytics.analytics.execute.LOCK_WAIT_SECONDS", LOCK_WAIT)

    released = threading.Event()
    holding = threading.Event()

    def hold_the_connection() -> None:
        """A tool call that owns the connection, from a real other thread."""
        session.lock.acquire()
        holding.set()
        try:
            released.wait(timeout=HOLD_SECONDS * 4)
        finally:
            session.lock.release()

    holder = threading.Thread(target=hold_the_connection, daemon=True)
    holder.start()
    assert holding.wait(timeout=10), "the holder never took the connection"

    async with AnalyticsToolset(
        build_server(manager),
        session_id=session_id,
        session_key=session_key,
        budget=ToolBudget(max_total=64, max_per_task=64),
    ) as toolset:

        async def call(index: int) -> str:
            try:
                await toolset.call("compute_metric", {"metric": "revenue"}, task_id=f"t{index}")
                return "ok"
            except ToolCallFailed as exc:
                return f"failed: {exc}"

        started = time.monotonic()
        # D: every waiter must come back, so this gather is itself bounded.
        results = await asyncio.wait_for(
            asyncio.gather(*(call(i) for i in range(6))), timeout=OUTER_TIMEOUT / 2
        )
        elapsed = time.monotonic() - started

        # C: waiters return a bounded, readable, retryable error.
        assert all(r.startswith("failed:") for r in results), results
        assert all("busy" in r for r in results), results
        # F: and they came back quickly, rather than after the holder let go.
        assert elapsed < HOLD_SECONDS, (
            f"waiters took {elapsed:.1f}s, i.e. they waited for the holder instead of giving up"
        )

        # E: once the connection is free, normal work succeeds again.
        released.set()
        holder.join(timeout=10)
        assert not holder.is_alive()

        payload = await toolset.call("compute_metric", {"metric": "revenue"}, task_id="after")
        assert payload["row_count"] >= 1


@pytest.mark.timeout(int(OUTER_TIMEOUT))
async def test_the_event_loop_stays_responsive_while_the_connection_is_held(
    demo: tuple[SessionManager, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The symptom that made the original hang undiagnosable.

    Nothing ran: not the timers, not the signal handler. If the loop is
    alive while workers are blocked, every other bound in the system still
    works. If it is not, none of them do.
    """
    manager, session_id, session_key = demo
    session = manager.get(session_id, session_key)
    monkeypatch.setattr("agentic_analytics.analytics.execute.LOCK_WAIT_SECONDS", LOCK_WAIT)

    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    session.lock.acquire()
    beat = asyncio.create_task(heartbeat())
    try:
        async with AnalyticsToolset(
            build_server(manager),
            session_id=session_id,
            session_key=session_key,
            budget=ToolBudget(max_total=64, max_per_task=64),
        ) as toolset:
            with pytest.raises(ToolCallFailed):
                await asyncio.wait_for(
                    toolset.call("compute_metric", {"metric": "revenue"}, task_id="t"),
                    timeout=OUTER_TIMEOUT / 4,
                )
    finally:
        beat.cancel()
        session.lock.release()

    assert ticks > 0, "the event loop made no progress while a worker was blocked"
