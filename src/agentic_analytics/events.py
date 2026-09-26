"""The run event stream.

Every stage of a run emits a typed event. The same objects are written into
recordings, streamed to the browser over SSE, and asserted against in tests,
so the UI cannot show a stage that did not actually happen.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    """Closed set of run events.

    Closed rather than free-form because the frontend switches on these and a
    recording is validated against them.
    """

    RUN_STARTED = "run_started"
    DATASET_LOADED = "dataset_loaded"
    QUESTION_ANALYZED = "question_analyzed"
    PLAN_GENERATED = "plan_generated"
    ANALYSIS_TASK_STARTED = "analysis_task_started"
    MCP_TOOL_CALLED = "mcp_tool_called"
    MCP_TOOL_COMPLETED = "mcp_tool_completed"
    MCP_TOOL_FAILED = "mcp_tool_failed"
    ANALYSIS_TASK_COMPLETED = "analysis_task_completed"
    ANALYSIS_TASK_FAILED = "analysis_task_failed"
    FINDING_PROPOSED = "finding_proposed"
    FINDING_VERIFIED = "finding_verified"
    FINDING_REJECTED = "finding_rejected"
    FOLLOWUP_ROUND_STARTED = "followup_round_started"
    CHART_CREATED = "chart_created"
    CHART_REJECTED = "chart_rejected"
    REPORT_STARTED = "report_started"
    REPORT_COMPLETED = "report_completed"
    BUDGET_EXCEEDED = "budget_exceeded"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    #: The dataset went away while the analysis was running -- deleted,
    #: replaced or expired. Separate from `run_failed` because nothing went
    #: wrong with the analysis, and a browser watching the stream should not
    #: be told the engine broke.
    RUN_CANCELLED = "run_cancelled"


class RunEvent(BaseModel):
    """A single point on the run timeline."""

    event_id: str = Field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:12]}")
    seq: int = 0
    type: EventType
    at: float = Field(default_factory=time.time)
    # Free-form per-event payload. Everything placed here is already
    # redaction-safe: tool arguments are the validated arguments, never raw
    # provider errors or credentials.
    data: dict[str, Any] = Field(default_factory=dict)


class EventBus:
    """Fan-out of run events to the recorder and to any live SSE subscribers.

    Publishing never blocks on a slow subscriber: each subscriber owns a
    bounded queue and a subscriber that falls too far behind loses events
    rather than stalling the run.
    """

    def __init__(self, max_queue: int = 1024) -> None:
        self._subscribers: list[asyncio.Queue[RunEvent | None]] = []
        self._history: list[RunEvent] = []
        self._seq = 0
        self._max_queue = max_queue
        self._closed = False

    @property
    def history(self) -> list[RunEvent]:
        """Every event published so far, in order."""
        return list(self._history)

    def emit(self, type_: EventType, **data: Any) -> RunEvent:
        """Publish an event and return the stored copy."""
        self._seq += 1
        event = RunEvent(seq=self._seq, type=type_, data=data)
        self._history.append(event)
        for queue in self._subscribers:
            # A subscriber that cannot keep up is dropped from this event,
            # not from the stream.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        return event

    def close(self) -> None:
        """Signal end-of-stream to every subscriber."""
        if self._closed:
            return
        self._closed = True
        for queue in self._subscribers:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)

    async def subscribe(self) -> AsyncIterator[RunEvent]:
        """Yield events from now until the bus closes.

        Replays history first so a client that connects mid-run still sees a
        complete timeline.
        """
        queue: asyncio.Queue[RunEvent | None] = asyncio.Queue(maxsize=self._max_queue)
        backlog = list(self._history)
        self._subscribers.append(queue)
        try:
            for event in backlog:
                yield event
            last_seq = backlog[-1].seq if backlog else 0
            if self._closed:
                return
            while True:
                item = await queue.get()
                if item is None:
                    return
                if item.seq > last_seq:
                    last_seq = item.seq
                    yield item
        finally:
            if queue in self._subscribers:
                self._subscribers.remove(queue)
