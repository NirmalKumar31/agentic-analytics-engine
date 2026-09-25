"""Structured contracts between agents.

Every field here is consumed by the engine. There is no free-text "strategy"
or "reasoning" field: asking a model for prose that nothing reads costs tokens,
invites the model to argue with itself, and produces text that later leaks into
a report as if it were evidence.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from agentic_analytics.analytics.results import EvidenceCell, EvidenceKind

AnalysisType = Literal[
    "timeseries",
    "segmentation",
    "composition",
    "statistical_test",
    "correlation",
    "profiling",
]

PREFERRED_TOOLS = (
    "compute_metric",
    "compare_segments",
    "compare_periods",
    "analyze_timeseries",
    "decompose_change",
    "rank_contributors",
    "correlation_matrix",
    "statistical_test",
    "profile_dataset",
    "profile_table",
    "aggregate_for_question",
    "run_readonly_sql",
)

VerificationStatus = Literal["supported", "partially_supported", "unsupported"]

TaskStatus = Literal["succeeded", "warning", "failed"]


class QuestionAnalysis(BaseModel):
    """What the question is asking, in terms the planner can act on."""

    intent: str
    analysis_type: AnalysisType = "segmentation"
    target_metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    time_scope: str | None = None
    comparison_groups: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)

    @field_validator("intent")
    @classmethod
    def _intent_is_short(cls, v: str) -> str:
        # One sentence. The intent labels the run; it is not a place to think.
        return v.strip()[:240]


class AnalysisTask(BaseModel):
    """One unit of analysis, dispatched to a worker."""

    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    objective: str
    analysis_type: AnalysisType = "segmentation"
    required_metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    preferred_tool: str = "compute_metric"
    priority: int = 3
    depends_on: list[str] = Field(default_factory=list)

    # Tool-specific parameters. Present only for the tools that need them, so
    # a task is executable without the worker having to guess.
    time_grain: Literal["day", "week", "month", "quarter", "year"] | None = None
    # The period the question named, when it named one. A worker reports the
    # change *into* this period rather than the largest change anywhere.
    focus_period: str | None = None
    test_type: str | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    table: str | None = None
    columns: list[str] = Field(default_factory=list)
    # Two explicit windows for a driver decomposition.
    baseline_start: str | None = None
    baseline_end: str | None = None
    current_start: str | None = None
    current_end: str | None = None

    @field_validator("objective")
    @classmethod
    def _objective_is_short(cls, v: str) -> str:
        return v.strip()[:200]

    @field_validator("preferred_tool")
    @classmethod
    def _tool_is_known(cls, v: str) -> str:
        return v if v in PREFERRED_TOOLS else "compute_metric"


class AnalysisPlan(BaseModel):
    tasks: list[AnalysisTask] = Field(default_factory=list)


class CandidateFinding(BaseModel):
    """A claim a worker proposes, before verification."""

    finding_id: str = Field(default_factory=lambda: f"fin_{uuid.uuid4().hex[:8]}")
    text: str
    kind: EvidenceKind = "calculated_fact"
    task_id: str | None = None
    result_ids: list[str] = Field(default_factory=list)
    evidence_cells: list[EvidenceCell] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    # A worker may state the arithmetic it believes the cells imply. The
    # engine recomputes it; the claim is never taken on trust.
    claimed_change: dict[str, Any] | None = None

    @field_validator("text")
    @classmethod
    def _text_is_a_claim_not_an_essay(cls, v: str) -> str:
        return v.strip()[:400]


class Verdict(BaseModel):
    """The critic's ruling on one finding."""

    finding_id: str
    status: VerificationStatus
    reason: str
    #: Which gate decided this, as a stable identifier rather than prose.
    #: `causal_from_observational`, `significance_without_test`,
    #: `no_evidence`, `missing_result`, `numeric_mismatch`, `critic`, or
    #: `critic_unavailable`. Anything reading a verdict programmatically --
    #: the benchmark, the UI -- keys off this rather than matching on the
    #: wording, which is written for a person and may change.
    rule: str = ""
    # Set when deterministic arithmetic, not the critic, settled the matter.
    numeric_check: dict[str, Any] | None = None

    @field_validator("reason")
    @classmethod
    def _reason_is_short(cls, v: str) -> str:
        return v.strip()[:400]


class PublishedFinding(BaseModel):
    """A finding that passed verification and may appear in the report."""

    finding_id: str
    text: str
    kind: EvidenceKind
    task_id: str | None
    result_ids: list[str]
    evidence_cells: list[EvidenceCell]
    metric_ids: list[str]
    verification_status: VerificationStatus
    verifier_reason: str
    numeric_check: dict[str, Any] | None = None
    # The arithmetic the worker stated, kept so the provenance drawer can
    # show the calculation next to the cells it was computed from.
    claimed_change: dict[str, Any] | None = None


class TaskOutcome(BaseModel):
    """What one worker produced."""

    task_id: str
    status: TaskStatus = "succeeded"
    findings: list[CandidateFinding] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    tool_calls: int = 0
    error: str | None = None
    notes: list[str] = Field(default_factory=list)


class ChartSpec(BaseModel):
    """A validated Vega-Lite specification bound to one result."""

    chart_id: str = Field(default_factory=lambda: f"cht_{uuid.uuid4().hex[:8]}")
    title: str
    result_id: str
    finding_ids: list[str] = Field(default_factory=list)
    spec: dict[str, Any] = Field(default_factory=dict)


class ReportSection(BaseModel):
    heading: str
    body: str
    finding_ids: list[str] = Field(default_factory=list)


class ReportPlanSection(BaseModel):
    """One group of findings the reporter wants kept together.

    There is deliberately no heading field. A heading is short enough to look
    like a label and long enough to be a claim -- "Electronics underperformed"
    is five words and an unverified assertion -- and distinguishing the two
    needs exactly the semantic judgement the verification pipeline exists to
    avoid trusting. So the engine derives the heading from what is actually
    in the group, and the model keeps the part that is genuinely editorial:
    which findings belong together, and in what order.
    """

    finding_ids: list[str] = Field(default_factory=list)


class ReportPlan(BaseModel):
    """Everything the reporter model is allowed to decide.

    Deliberately contains no prose that asserts anything. The model chooses
    *which* verified findings appear, how they are grouped and in what order;
    the engine writes the report from the findings' own exact text. A model
    that is never asked for a factual sentence cannot introduce one, which is
    a stronger guarantee than checking afterwards for the ones we thought to
    look for.
    """

    executive_finding_ids: list[str] = Field(default_factory=list)
    sections: list[ReportPlanSection] = Field(default_factory=list)
    next_questions: list[str] = Field(default_factory=list)


class AnalysisReport(BaseModel):
    """The written deliverable. Numbers come from findings, never from prose."""

    question: str
    executive_summary: str
    key_findings: list[str] = Field(default_factory=list)
    sections: list[ReportSection] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    next_questions: list[str] = Field(default_factory=list)
