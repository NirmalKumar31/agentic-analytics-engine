"""In-flight and completed analysis runs.

A run is started in the background and its events are streamed to the browser
while it executes. The registry is bounded and time-limited, because this is a
public demo with no authentication: nothing may accumulate without a ceiling.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agentic_analytics.events import EventBus
from agentic_analytics.graph.runner import RunResult
from agentic_analytics.logging import get_logger

log = get_logger(__name__)


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

    @property
    def status(self) -> str:
        if self.error:
            return "failed"
        if self.result is not None:
            return "completed"
        return "running"

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
        self._evict()
        record = RunRecord(
            run_id=f"run_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            question=question,
            bus=EventBus(),
        )
        self._runs[record.run_id] = record
        return record

    def get(self, run_id: str) -> RunRecord:
        self._evict()
        try:
            return self._runs[run_id]
        except KeyError:
            raise KeyError(f"unknown run {run_id!r}") from None

    def active_count(self) -> int:
        return sum(1 for r in self._runs.values() if r.status == "running")

    def _evict(self) -> None:
        cutoff = time.time() - self._ttl
        for run_id, record in list(self._runs.items()):
            if record.created_at < cutoff and record.status != "running":
                self._runs.pop(run_id, None)
        while len(self._runs) > self._max:
            oldest = min(self._runs.values(), key=lambda r: r.created_at)
            if oldest.status == "running":
                break
            self._runs.pop(oldest.run_id, None)

    async def shutdown(self) -> None:
        for record in self._runs.values():
            if record.task and not record.task.done():
                record.task.cancel()
        self._runs.clear()
