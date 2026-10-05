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
from agentic_analytics.analytics.results import QuestionCoverage, ResultSnapshot
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
    #: The accepted meaning of an uploaded-data question.  It contains
    #: schema identifiers and user-stated restrictions, never raw cells.
    query_contract: dict[str, Any] | None = None
    #: Question-to-contract completeness. This is not contract equality and
    #: not claim support; it answers whether the executed shape preserved
    #: every component the question fixed.
    question_coverage: QuestionCoverage | None = None
    #: What the planner understood the question to be asking.
    #:
    #: A contract is only accepted for uploaded data, so a run over the
    #: governed warehouse has none -- and everything that reads intent from
    #: `query_contract` was blind on exactly the path the public deployment
    #: uses. Compare then had nothing to compare and reported two absent
    #: contracts as identical, and the report had no way to tell whether a
    #: published finding answered the question or merely came first.
    #:
    #: This is that intent, recorded as the planner stated it: the analysis
    #: type, the metrics and groupings the question named, the period it
    #: fixed, and what it had to leave ambiguous.
    planner_interpretation: dict[str, Any] | None = None
    #: Stage durations in milliseconds. Exposed so the UI can say where the
    #: time went instead of implying the model computed the answer.
    timings: dict[str, float] = field(default_factory=dict)
    #: Why the chart is what it is, including the reason when there is none.
    chart_decision: dict[str, Any] = field(default_factory=dict)
    #: Which generation of the session's role confirmations this run used.
    #: Recorded so a completed run stays readable after the session's schema
    #: moves on: its evidence describes the schema it actually executed
    #: against, and is never relabelled by a later confirmation.
    schema_revision: int = 0
    #: The columns whose role mattered to the accepted contract, and where
    #: that role came from. Only columns the contract used -- a reader does
    #: not need the provenance of a column nothing touched.
    role_evidence: list[dict[str, Any]] = field(default_factory=list)
    #: How this answer should be presented, derived from the contract, the
    #: verified result and the coverage records. Optional and additive: a
    #: consumer that predates it still reads `report`, `findings` and
    #: `results` exactly as before.
    presentation: Any | None = None
    #: True when the cloud planner returned nothing usable and the engine's
    #: own contract executed instead. Compare Both must not present that as
    #: the model independently agreeing.
    planner_fallback: bool = False
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
            "query_contract": self.query_contract,
            "question_coverage": (
                self.question_coverage.model_dump() if self.question_coverage else None
            ),
            "planner_interpretation": self.planner_interpretation,
            "timings": dict(self.timings),
            "chart_decision": dict(self.chart_decision),
            "presentation": (
                self.presentation.model_dump(mode="json") if self.presentation else None
            ),
            "planner_fallback": self.planner_fallback,
            "stopped_reason": self.stopped_reason,
            "outcome": self.outcome,
            "schema_revision": self.schema_revision,
            "role_evidence": list(self.role_evidence),
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
    ("result_shape_too_large", "refused"),
    ("declined", "refused"),
    ("could not be grounded", "refused"),
    ("could not be mapped safely", "refused"),
    ("no executable task for this dataset", "refused"),
    ("cancelled", "cancelled"),
    ("dataset was closed", "cancelled"),
)


def _role_evidence(schema: dict[str, Any] | None, mapping: Any) -> list[dict[str, Any]]:
    """Where the roles the contract relied on came from.

    Only the columns the accepted contract actually used. A reader asking
    "why is this grouped that way" is asking about the grouping, not about
    the provenance of eleven columns nothing touched.

    A column whose role was confirmed is the interesting case, but a used
    column that inference settled is reported too: the audit should be able
    to say "this grouping is the engine's reading" as well as "this one is
    yours".
    """
    if not schema or mapping is None:
        return []

    used: dict[str, list[str]] = {}

    def note(column: str | None, how: str) -> None:
        if not column:
            return
        used.setdefault(str(column), [])
        if how not in used[str(column)]:
            used[str(column)].append(how)

    note(getattr(mapping, "measure", None), "measure")
    for dimension in getattr(mapping, "dimensions", ()) or ():
        note(dimension, "grouping")
    note(getattr(mapping, "time_field", None), "time")
    note(getattr(mapping, "period_field", None), "period")
    for restriction in getattr(mapping, "filters", ()) or ():
        note(getattr(restriction, "column", None), "filter")

    evidence: list[dict[str, Any]] = []
    for field_payload in schema.get("fields", []) or []:
        name = str(field_payload.get("name", ""))
        if name not in used:
            continue
        evidence.append(
            {
                "column": name,
                "effective_role": field_payload.get("role"),
                "inferred_role": field_payload.get("inferred_role", field_payload.get("role")),
                "role_source": field_payload.get("role_source", "inferred"),
                "ambiguous": bool(field_payload.get("ambiguous", False)),
                "used_as": used[name],
            }
        )
    return evidence


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


def _planner_interpretation(analysis: Any | None) -> dict[str, Any] | None:
    """What the planner read the question as, as a first-class record.

    Everything downstream that wanted to know what was asked read it from
    `query_contract`, and a contract is only accepted for uploaded data --
    so on the governed warehouse, which is what the public deployment
    serves, there was nothing to read. Compare reported two absent
    contracts as identical, and the report could not tell a finding that
    answered the question from one that merely came first.

    Only the fields the planner actually stated. `ambiguous` is recorded as
    a flag as well as its reasons, so a reader of the payload does not have
    to infer intent from the length of a list.
    """
    if analysis is None:
        return None
    ambiguities = [str(note) for note in getattr(analysis, "ambiguities", []) or []]
    return {
        "analysis_type": getattr(analysis, "analysis_type", None),
        "metrics": [str(m) for m in getattr(analysis, "target_metrics", []) or []],
        "dimensions": [str(d) for d in getattr(analysis, "dimensions", []) or []],
        "period": getattr(analysis, "time_scope", None),
        "ambiguous": bool(ambiguities),
        "ambiguities": ambiguities,
    }


def _question_coverage(
    question: str,
    schema: dict[str, Any] | None,
    mapping: Any,
    stopped_reason: str,
) -> QuestionCoverage | None:
    """Whether the executed contract covers what the question fixed.

    Independent of three things it is easy to confuse it with: planner
    agreement, result-group coverage, and claim support. Two planners can
    agree on a contract that answers a different question; a claim can be
    perfectly supported by a result that was never asked for.

    The first version of this derived its requirements from the accepted
    mapping, so a grouping the planner had dropped was never "required",
    nothing was ever missing, and `complete` was a rename of `confident`.
    Requirements now come from the question text.
    """
    from agentic_analytics.analytics.upload_plan import question_requirements

    if mapping is None or schema is None:
        return None

    reason = stopped_reason or ""
    codes: list[Any] = []
    details: list[str] = []
    if "result_shape_too_large" in reason:
        codes.append("result_shape_too_large")
        details.append(reason[:200])

    try:
        wanted = question_requirements(question, schema)
    except Exception:  # pragma: no cover - coverage must not break a run
        return None

    confident = bool(getattr(mapping, "confident", False))
    applied_dimensions = tuple(getattr(mapping, "dimensions", ()) or ())
    applied_filters = {
        (
            str(item.get("column", "")),
            str(item.get("operator", "")),
            "" if item.get("value") is None else str(item.get("value")),
        )
        for item in (f.as_dict() for f in getattr(mapping, "filters", ()) or ())
    }

    required: list[Any] = []
    missing: list[Any] = []

    def check(component: Any, is_required: bool, satisfied: bool, detail: str, code: Any) -> None:
        if not is_required:
            return
        required.append(component)
        if satisfied:
            return
        missing.append(component)
        if code not in codes:
            codes.append(code)
        details.append(detail)

    check(
        "operation",
        wanted.operation is not None,
        confident and getattr(mapping, "operation", None) == wanted.operation,
        f"the question asks for {wanted.operation}, and the executed contract used "
        f"{getattr(mapping, 'operation', None)}",
        "changed_requested_operation",
    )
    check(
        "measure",
        wanted.measure is not None,
        confident and getattr(mapping, "measure", None) == wanted.measure,
        f"the question names {wanted.measure!r} as the measure, and the executed "
        f"contract measured {getattr(mapping, 'measure', None)!r}",
        "missing_requested_measure",
    )
    check(
        "dimensions",
        bool(wanted.dimensions),
        confident and applied_dimensions == wanted.dimensions,
        f"the question asks for a breakdown by {', '.join(wanted.dimensions)}, and the "
        f"executed contract grouped by {', '.join(applied_dimensions) or 'nothing'}",
        "missing_requested_grouping",
    )
    check(
        "time_grain",
        wanted.time_grain is not None,
        confident and getattr(mapping, "time_grain", None) == wanted.time_grain,
        f"the question asks for a {wanted.time_grain} grain, and the executed contract "
        f"used {getattr(mapping, 'time_grain', None)}",
        "missing_requested_time_grain",
    )
    check(
        "period",
        wanted.period is not None,
        confident and tuple(getattr(mapping, "period", None) or ()) == wanted.period,
        "the question states a time period that the executed contract did not apply",
        "missing_requested_filter",
    )
    check(
        "filters",
        bool(wanted.filters),
        confident and set(wanted.filters) <= applied_filters,
        "the question states a row restriction that the executed contract did not apply",
        "missing_requested_filter",
    )
    check(
        "ranking_direction",
        wanted.ascending is not None,
        confident and bool(getattr(mapping, "ascending", False)) == wanted.ascending,
        "the question asks for the other end of the ranking",
        "changed_ranking_direction",
    )

    if not confident and "result_shape_too_large" not in codes:
        codes.append("unresolved_question")
        details.append(str(getattr(mapping, "explanation", "the question was not resolved")))

    complete = confident and not missing and not codes
    return QuestionCoverage(
        complete=complete,
        required_components=required,
        applied_components=[c for c in required if c not in missing] if confident else [],
        missing_components=missing,
        rejection_codes=codes,
        details=details[:6],
    )


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
    open_planner: Any | None = None,
    role_confirmations: Any | None = None,
) -> RunResult:
    """Execute one analysis end to end.

    `open_planner` turns on automatic routing: the rules resolve the
    question first and this factory is called only if they could not. It
    is a factory because building the governed cloud provider takes the
    ledger slot, and a question the rules already answered must not spend
    quota on work that never happens. Omitted, the run behaves exactly as
    before -- one provider, chosen by the caller.

    `role_confirmations` pins the generation of the session's role
    confirmations this run executes against. The caller captures it once so
    that a comparison's two children are guaranteed the same one: capturing
    per child would let a confirmation land between them and produce two
    runs of the same question under different schemas, reported side by
    side as though they were comparable.
    """
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
            ctx = RunContext(
                session,
                toolset,
                llm,
                bus,
                cfg.budgets,
                telemetry,
                open_planner=open_planner,
                role_confirmations=role_confirmations,
            )
            if telemetry is not None:
                # A live handle, not a copy. If the caller abandons this run
                # on a timeout, the trace is the only record of what the
                # tool loop actually did, and it is thrown away with the
                # coroutine otherwise -- a timed-out question then reports
                # zero tool calls, which is precisely the case where knowing
                # them matters most.
                telemetry["toolset"] = toolset
            # The generation this run executes against, taken from the
            # context so every later reference agrees with what the nodes
            # actually interpreted.
            schema_revision = ctx.schema_revision
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
        query_contract=(
            state["query_mapping"].as_dict() if state.get("query_mapping") is not None else None
        ),
        question_coverage=_question_coverage(
            question,
            state.get("upload_schema") or None,
            state.get("query_mapping"),
            state.get("stopped_reason", ""),
        ),
        planner_interpretation=_planner_interpretation(state.get("analysis")),
        timings=dict(state.get("timings") or {}),
        chart_decision=dict(state.get("chart_decision") or {}),
        schema_revision=schema_revision,
        role_evidence=_role_evidence(
            state.get("upload_schema") or None, state.get("query_mapping")
        ),
        planner_fallback=bool(getattr(state.get("query_mapping"), "planner_note", "")),
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
    result.presentation = _presentation_for(result, state)
    if own_provider:
        await llm.aclose()
    return result


def _presentation_for(result: RunResult, state: Any) -> Any | None:
    """How this run's answer should be presented.

    Built here rather than in the graph because it needs the finished run:
    the verified findings, the coverage records and the chart decision all
    exist only once the run has stopped.

    A failure to build one must never fail the run. The presentation is an
    additive description of a result that is already computed and already
    verified; losing it degrades the report to the older rendering path,
    which is a worse report rather than a wrong one.
    """
    from agentic_analytics.presentation import build_presentation

    snapshot = next(
        (s for s in result.results.values() if s.tool_name == "aggregate_for_question"),
        None,
    )
    mapping = state.get("query_mapping") if hasattr(state, "get") else None
    if mapping is None and snapshot is None:
        return None

    if snapshot is None and result.outcome == "completed":
        # A completed run this contract cannot describe.
        #
        # The presentation is built around an `aggregate_for_question`
        # snapshot: it reads the result's columns, cells and row counts to
        # say what the answer is. The demo warehouse does not produce one --
        # it resolves through the metric registry and executes different
        # tools -- while still setting `query_mapping`. So `mapping` was not
        # None, this guard let it through, and the builder's own "no
        # snapshot" branch returned a FAILURE presentation.
        #
        # Every demo run that published a verified finding was therefore
        # rendered as "The analysis could not be completed.", with the
        # finding it had just verified replaced by that sentence. The
        # workflow index still showed Verify complete beside it. That is the
        # demo path, which is the first thing a visitor sees.
        #
        # There is nothing to describe and nothing wrong: returning None
        # degrades to the findings-based report, which is exactly what the
        # docstring above says losing a presentation should do.
        #
        # Deliberately scoped to `completed`. A refusal or a failure has no
        # snapshot either, and its presentation is the useful one -- it
        # carries the reason. Those must keep being built.
        return None
    try:
        return build_presentation(
            mapping=mapping,
            snapshot=snapshot,
            findings=list(result.published),
            question_coverage=result.question_coverage,
            chart_decision=result.chart_decision,
            schema=state.get("upload_schema") if hasattr(state, "get") else None,
            planner_fallback=result.planner_fallback,
            outcome=result.outcome,
            stopped_reason=result.stopped_reason,
        )
    except Exception:  # pragma: no cover - defensive, see the docstring
        log.warning("presentation_build_failed", run_id=result.run_id)
        return None
