"""In-flight and completed analysis runs.

A run is started in the background and its events are streamed to the browser
while it executes. The registry is bounded and time-limited, because this is a
public demo with no authentication: nothing may accumulate without a ceiling.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agentic_analytics.events import EventBus
from agentic_analytics.graph.runner import RunResult
from agentic_analytics.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class CancellationResult:
    """What a cancellation actually achieved.

    `requested` is how many runs were asked to stop; `terminal` is how many
    genuinely finished within the grace period. When they differ, work is
    still live and the session it holds must not be closed.
    """

    requested: int
    terminal: int
    timed_out: int
    active_run_ids: list[str] = field(default_factory=list)

    @property
    def all_terminal(self) -> bool:
        """True only when nothing can still touch the session."""
        return self.timed_out == 0


@dataclass
class RunRecord:
    """One analysis run, live or finished."""

    run_id: str
    session_id: str
    question: str
    bus: EventBus
    created_at: float = field(default_factory=time.time)
    task: asyncio.Task[None] | None = None
    result: RunResult | None = None
    error: str | None = None

    #: Set when a run was stopped because its dataset went away -- deleted,
    #: replaced, or expired. Distinct from `failed`: nothing went wrong with
    #: the analysis, the thing it was analysing was withdrawn, and a reader
    #: should not be told the engine broke.
    cancelled: bool = False

    @property
    def status(self) -> str:
        if self.cancelled:
            return "cancelled"
        if self.error:
            return "failed"
        if self.result is not None:
            return "completed"
        return "running"

    @property
    def is_terminal(self) -> bool:
        """True once this run can no longer touch its session."""
        return self.status != "running"

    def public(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "question": self.question,
            "status": self.status,
            "created_at": self.created_at,
        }
        if self.result is not None:
            payload.update(self.result.to_public_dict())
        if self.error:
            payload["error"] = self.error
        return payload


class RunRegistry:
    """Bounded store of runs."""

    def __init__(self, max_runs: int = 24, ttl_seconds: float = 3600.0) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._max = max_runs
        self._ttl = ttl_seconds

    def create(self, session_id: str, question: str) -> RunRecord:
        record = RunRecord(
            run_id=f"run_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            question=question,
            bus=EventBus(),
        )
        self._runs[record.run_id] = record
        # After the insert, not before: evicting first leaves the registry
        # holding `max_runs + 1` the moment the new record lands, so the
        # ceiling was never quite what it claimed. The new record is both
        # the newest and still running, so it is never its own victim.
        self._evict()
        return record

    def get(self, run_id: str) -> RunRecord:
        self._evict()
        try:
            return self._runs[run_id]
        except KeyError:
            raise KeyError(f"unknown run {run_id!r}") from None

    def active_count(self) -> int:
        return sum(1 for r in self._runs.values() if r.status == "running")

    def session_run_count(self, session_id: str) -> int:
        """How many analyses this session has started, for its per-session cap."""
        return sum(1 for r in self._runs.values() if r.session_id == session_id)

    def _evict(self) -> None:
        """Drop expired runs, then trim to the ceiling. Never a running run.

        The ceiling used to be advisory. The loop picked the globally oldest
        record and stopped if it was still running -- so one long analysis at
        the front of the queue blocked eviction of every finished run behind
        it, and the registry grew past `max_runs` without bound while that
        analysis lived. A class that calls itself bounded should be.

        The fix is to choose the victim from removable records only. A
        running run is never evicted, because its task holds a session and
        dropping the record would lose the handle needed to cancel it.
        """
        cutoff = time.time() - self._ttl
        for run_id, record in list(self._runs.items()):
            if record.created_at < cutoff and record.is_terminal:
                self._runs.pop(run_id, None)

        while len(self._runs) > self._max:
            removable = [r for r in self._runs.values() if r.is_terminal]
            if not removable:
                # Every remaining run is still executing. Killing one to
                # satisfy a count would be worse than exceeding it, so the
                # ceiling yields and says so; `max_concurrent_analyses`
                # is what actually bounds this case.
                log.warning(
                    "run_registry_over_capacity",
                    runs=len(self._runs),
                    max_runs=self._max,
                    reason="every run is still active; none may be evicted",
                )
                break
            oldest = min(removable, key=lambda r: r.created_at)
            self._runs.pop(oldest.run_id, None)

    def active_for_session(self, session_id: str) -> list[RunRecord]:
        return [r for r in self._runs.values() if r.session_id == session_id and not r.is_terminal]

    async def cancel_session_detailed(
        self, session_id: str, *, reason: str = "the dataset was closed", grace_seconds: float = 5.0
    ) -> CancellationResult:
        """Stop every run using this session, and wait for it to unwind.

        This is the first half of the session teardown contract. A run holds
        the `AnalysisSession` object and therefore its DuckDB connection; if
        the session is closed underneath it, the next tool call reaches a
        closed connection and surfaces as an internal error to whoever is
        watching the stream. Guarding the connection with a lock does not
        help -- that serialises one query, and the analysis goes on to make
        another call afterwards.

        So a run is stopped before its session is, and stopped *properly*:
        cancelled, then awaited. Awaiting is the part that matters. A
        cancelled task has not finished unwinding, so its `finally` blocks --
        which close the provider and release the capacity slot -- have not
        run yet. Returning before they do is how a slot leaks.

        Returns what actually happened, not what was asked for. Requesting
        cancellation and achieving it are different events, and the previous
        version could not tell them apart: it suppressed the grace-period
        `TimeoutError` and returned a count, so a caller proceeded to close a
        session whose task was still running. The result below reports
        terminality, and the caller is expected to act on it.
        """
        records = self.active_for_session(session_id)
        if not records:
            return CancellationResult(requested=0, terminal=0, timed_out=0)

        for record in records:
            record.cancelled = True
            record.error = reason
            if record.task and not record.task.done():
                record.task.cancel()

        tasks = [r.task for r in records if r.task and not r.task.done()]
        if tasks:
            # `asyncio.wait`, not `gather` under a timeout. `gather` waits
            # for its children even after cancelling them, so a task that
            # swallows CancelledError defeats the timeout completely: the
            # wait returned only when the task chose to finish, and the
            # caller had no idea it had been held up. `wait` returns when
            # the deadline passes and reports what is still pending.
            await asyncio.wait(tasks, timeout=grace_seconds)

        still_active = [r.run_id for r in records if r.task is not None and not r.task.done()]
        for record in records:
            if record.run_id in still_active:
                # Leaving the bus open: the task may yet emit, and closing a
                # stream out from under a live run is the same class of
                # mistake as closing its connection.
                continue
            # The bus may already be closed by the run's own teardown;
            # closing twice is harmless and closing never is a hung stream.
            with contextlib.suppress(Exception):
                record.bus.close()

        result = CancellationResult(
            requested=len(records),
            terminal=len(records) - len(still_active),
            timed_out=len(still_active),
            active_run_ids=still_active,
        )
        log.info(
            "runs_cancelled_for_session",
            session_id=session_id,
            requested=result.requested,
            terminal=result.terminal,
            timed_out=result.timed_out,
            active_run_ids=still_active,
        )
        return result

    async def cancel_session(
        self,
        session_id: str,
        *,
        reason: str = "the dataset was closed",
        grace_seconds: float = 5.0,
    ) -> int:
        """How many runs were stopped. Prefer `cancel_session_detailed`.

        Kept because a count is all some callers need, but a count cannot
        distinguish "stopped" from "asked to stop", so anything deciding
        whether to close a session must use the detailed form.
        """
        result = await self.cancel_session_detailed(
            session_id, reason=reason, grace_seconds=grace_seconds
        )
        return result.requested

    async def shutdown(self, grace_seconds: float = 5.0) -> None:
        """Cancel in-flight runs and wait for them to unwind.

        Cancelling without awaiting leaves a task part-way through an async
        context manager -- an open MCP client, a DuckDB cursor -- and the
        exception surfaces later, attached to whatever happens to be running.
        A container receiving SIGTERM mid-analysis hits exactly this path.
        """
        tasks = [r.task for r in self._runs.values() if r.task and not r.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(grace_seconds):
                    await asyncio.gather(*tasks, return_exceptions=True)
        self._runs.clear()
