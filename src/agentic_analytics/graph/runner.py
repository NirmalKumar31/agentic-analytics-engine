"""Run orchestration: connect MCP, run the graph, assemble the result."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from agentic_analytics.agents.schemas import (
    AnalysisReport,
    ChartSpec,
    PublishedFinding,
    TaskOutcome,
    Verdict,
)
from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.config import Settings, get_settings
from agentic_analytics.events import EventBus, EventType
from agentic_analytics.graph.build import RunContext, build_graph
from agentic_analytics.graph.state import AnalysisState
from agentic_analytics.llm.base import LLMProvider
from agentic_analytics.llm.registry import build_provider
from agentic_analytics.logging import get_logger
from agentic_analytics.mcp_layer.client import AnalyticsToolset, ToolBudget
from agentic_analytics.warehouse.session import AnalysisSession

log = get_logger(__name__)


#: How a run ended, as a stable identifier.
#:
#: `completed` means the graph ran to the end. It does not mean anything was
#: published: a run that honestly found nothing publishable is complete and
#: says why in its limitations, which is a different outcome from a run that
#: broke -- and the two used to be reported identically, because a
#: `RunResult` object existed in both cases.
RunOutcome = Literal[
    "completed",
    "failed",
    "timeout",
    "budget_exhausted",
    "refused",
    "cancelled",
]


@dataclass
class RunResult:
    """Everything a run produced, ready to serialise."""

    run_id: str
    question: str
    session_id: str
    dataset: dict[str, Any]
    report: AnalysisReport | None
    published: list[PublishedFinding] = field(default_factory=list)
    rejected: list[Verdict] = field(default_factory=list)
    charts: list[ChartSpec] = field(default_factory=list)
    tasks: list[TaskOutcome] = field(default_factory=list)
    results: dict[str, ResultSnapshot] = field(default_factory=dict)
    mcp_trace: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    stopped_reason: str = ""
    #: Set from the stop reason, or explicitly on the failure path. A caller
    #: must not infer success from the existence of this object.
    outcome: RunOutcome = "completed"

    @property
    def failed(self) -> bool:
        """Whether the run broke rather than finished.

        A zero-finding run is not a failure. A run that raised is, and it
        used to be indistinguishable because both returned a `RunResult`.
        """
        return self.outcome != "completed"

    def to_public_dict(self) -> dict[str, Any]:
        """The shape the API and the recordings use."""
        return {
            "run_id": self.run_id,
            "question": self.question,
            "dataset": self.dataset,
            "report": self.report.model_dump() if self.report else None,
            "findings": [f.model_dump() for f in self.published],
            "rejected": [v.model_dump() for v in self.rejected],
            "charts": [c.model_dump() for c in self.charts],
            "tasks": [t.model_dump() for t in self.tasks],
            "results": {
                rid: snapshot.model_dump(mode="json") for rid, snapshot in self.results.items()
            },
            "mcp_trace": self.mcp_trace,
            "events": self.events,
            "metrics": self.metrics,
            "stopped_reason": self.stopped_reason,
            "outcome": self.outcome,
        }


def _outcome_for(exc: BaseException) -> RunOutcome:
    """Classify a run that raised, so the cause survives to the API."""
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, TimeoutError):
        return "timeout"
    name = type(exc).__name__
    if "Budget" in name:
        return "budget_exhausted"
    return "failed"


#: Stop reasons the graph sets for itself, mapped to an outcome. A reason
#: not listed here is a run that stopped without finishing, which is a
#: failure even though nothing raised.
_STOP_OUTCOMES: tuple[tuple[str, RunOutcome], ...] = (
    ("time budget", "timeout"),
    ("reached its time limit", "timeout"),
    ("token limit", "budget_exhausted"),
    ("budget", "budget_exhausted"),
    ("declined", "refused"),
    ("cancelled", "cancelled"),
    ("dataset was closed", "cancelled"),
)


def _outcome_from_reason(reason: str) -> RunOutcome:
    """The outcome a stop reason implies.

    An empty reason is an ordinary completion, including the case where
    nothing was published: the report says why, and calling that a failure
    would make "found nothing" indistinguishable from "broke".
    """
    if not reason:
        return "completed"
    lowered = reason.lower()
    for needle, outcome in _STOP_OUTCOMES:
        if needle in lowered:
            return outcome
    return "failed"


async def run_analysis(
    question: str,
    session: AnalysisSession,
    mcp_target: Any,
    *,
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
    events: EventBus | None = None,
    run_id: str | None = None,
    telemetry: dict[str, Any] | None = None,
) -> RunResult:
    """Execute one analysis end to end."""
    cfg = settings or get_settings()
    bus = events or EventBus()
    own_provider = provider is None
    llm = provider or build_provider(cfg)
    started = time.monotonic()
    rid = run_id or f"run_{int(time.time() * 1000):x}"

    bus.emit(
        EventType.RUN_STARTED,
        run_id=rid,
        question=question,
        provider=llm.name,
        session_id=session.session_id,
    )

    budget = ToolBudget(
        max_total=cfg.budgets.max_total_tool_calls,
        max_per_task=cfg.budgets.max_tool_calls_per_task,
    )
    state: AnalysisState = {}
    try:
        async with AnalyticsToolset(
            mcp_target,
            session_id=session.session_id,
            session_key=session.session_key,
            budget=budget,
            events=bus,
            # The provider this run holds, not the process default. A run
            # driven by a cloud model may not see an uploaded file's raw
            # cells even when the run beside it, driven locally, may.
            remote_inference=llm.remote_inference,
        ) as toolset:
            ctx = RunContext(session, toolset, llm, bus, cfg.budgets, telemetry)
            if telemetry is not None:
                # A live handle, not a copy. If the caller abandons this run
                # on a timeout, the trace is the only record of what the
                # tool loop actually did, and it is thrown away with the
                # coroutine otherwise -- a timed-out question then reports
                # zero tool calls, which is precisely the case where knowing
                # them matters most.
                telemetry["toolset"] = toolset
            graph = build_graph(ctx)
            state = await graph.ainvoke(
                {"question": question, "session_id": session.session_id},
                # One superstep per node plus the fan-out; the ceiling is a
                # backstop, since the graph already terminates by structure.
                {"recursion_limit": 40},
            )
            trace = toolset.public_trace()
            # Captured here, while the toolset is still open: the results
            # belonging to *this* run, not every result on a session that a
            # sibling comparison run is writing to at the same time.
            run_results = {
                rid: session.results.get(rid)
                for rid in toolset.result_ids
                if session.results.has(rid)
            }
    except Exception as exc:
        log.exception("run_failed", run_id=rid)
        bus.emit(EventType.RUN_FAILED, run_id=rid, reason=f"{type(exc).__name__}")
        bus.close()
        if own_provider:
            await llm.aclose()
        return RunResult(
            run_id=rid,
            question=question,
            session_id=session.session_id,
            dataset=session.catalog(),
            report=None,
            events=[e.model_dump() for e in bus.history],
            stopped_reason=f"the run failed ({type(exc).__name__})",
            outcome=_outcome_for(exc),
        )

    duration = time.monotonic() - started
    published = list(state.get("published", []))
    rejected = list(state.get("rejected", []))
    tasks = list(state.get("task_outcomes", []))
    charts = list(state.get("charts", []))

    result = RunResult(
        run_id=rid,
        question=question,
        session_id=session.session_id,
        dataset=session.catalog(),
        report=state.get("report"),
        published=published,
        rejected=rejected,
        charts=charts,
        tasks=tasks,
        results=run_results,
        mcp_trace=trace,
        metrics={
            "runtime_seconds": round(duration, 3),
            "provider": llm.name,
            "analysis_tasks": len(tasks),
            "tasks_succeeded": sum(1 for t in tasks if t.status == "succeeded"),
            "tasks_warned": sum(1 for t in tasks if t.status == "warning"),
            "tasks_failed": sum(1 for t in tasks if t.status == "failed"),
            "mcp_tool_calls": len(trace),
            "mcp_tool_failures": sum(1 for c in trace if not c["ok"]),
            "findings_proposed": len(published) + len(rejected),
            "findings_published": len(published),
            "findings_rejected": len(rejected),
            "charts": len(charts),
            **llm.usage.as_dict(),
        },
        stopped_reason=state.get("stopped_reason", ""),
        outcome=_outcome_from_reason(state.get("stopped_reason", "")),
    )

    bus.emit(
        EventType.RUN_COMPLETED,
        run_id=rid,
        **{k: v for k, v in result.metrics.items() if k != "by_role"},
    )
    bus.close()
    result.events = [e.model_dump() for e in bus.history]
    if own_provider:
        await llm.aclose()
    return result
