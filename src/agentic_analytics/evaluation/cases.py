"""Benchmark questions and what each one should recover.

Expectations come from :mod:`agentic_analytics.data.ground_truth`, which the
agents cannot see. A case passes when the run's published findings contain the
entity and direction the generator actually injected -- not when a model says
it found something.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentic_analytics.data.ground_truth import PATTERNS_BY_ID


@dataclass(frozen=True)
class BenchmarkCase:
    """One evaluated question."""

    case_id: str
    question: str
    pattern_id: str | None
    #: Metric names that must appear among the published findings.
    expect_metrics: tuple[str, ...] = ()
    #: Lowercased substrings, any one of which satisfies the entity check.
    expect_entity_any: tuple[str, ...] = ()
    expect_direction: str | None = None
    #: True when the question should provoke a real statistical test.
    expect_statistical_test: bool = False
    #: True when a causal over-claim should be caught and withheld.
    expect_rejection: bool = False
    notes: str = ""

    @property
    def pattern_description(self) -> str:
        if self.pattern_id is None:
            return ""
        return PATTERNS_BY_ID[self.pattern_id].description


CASES: list[BenchmarkCase] = [
    BenchmarkCase(
        case_id="Q1",
        question="Did gross margin decline in Q3 2025, and did revenue rise?",
        pattern_id="q3_margin_compression",
        expect_metrics=("gross_margin_pct", "revenue"),
        expect_direction="down",
        notes="Both directions must be recovered, not just the one the question leads with.",
    ),
    BenchmarkCase(
        case_id="Q2",
        question="Revenue increased in Q3 2025, but gross margin fell. What caused it?",
        pattern_id="q3_margin_compression",
        expect_metrics=("gross_margin_pct",),
        expect_entity_any=("discount", "electronics"),
        expect_direction="down",
        notes="The injected causes are discounting and a mix shift into Electronics.",
    ),
    BenchmarkCase(
        case_id="Q3",
        question="Which product category has the highest return rate?",
        pattern_id="home_kitchen_returns",
        expect_metrics=("return_rate",),
        expect_entity_any=("home & kitchen", "home and kitchen"),
        expect_direction="up",
    ),
    BenchmarkCase(
        case_id="Q4",
        question="Which customer segments are driving the increase in return rate?",
        pattern_id="home_kitchen_returns",
        expect_metrics=("return_rate",),
        expect_entity_any=("new",),
        expect_statistical_test=True,
    ),
    BenchmarkCase(
        case_id="Q5",
        question="Which acquisition channel has the weakest contribution margin?",
        pattern_id="affiliate_weak_contribution",
        expect_metrics=("contribution_margin_pct", "contribution_margin", "roas"),
        expect_entity_any=("affiliate",),
        expect_direction="down",
    ),
    BenchmarkCase(
        case_id="Q6",
        question="Do shipping delays appear to affect repeat purchasing?",
        pattern_id="delay_suppresses_repeat",
        expect_metrics=("repeat_purchase_rate",),
        expect_direction="down",
        expect_statistical_test=True,
        expect_rejection=True,
        notes="A causal reading of this association must be withheld.",
    ),
    BenchmarkCase(
        case_id="Q7",
        question="Which shipping carrier has the highest late delivery rate?",
        pattern_id="northeast_carrier_delay",
        expect_metrics=("late_delivery_rate",),
        expect_entity_any=("rapidpost",),
        expect_direction="up",
    ),
    BenchmarkCase(
        case_id="Q8",
        question="How did monthly revenue change over 2025?",
        pattern_id="q4_seasonality",
        expect_metrics=("revenue",),
        expect_direction="up",
        notes="A plain trend question; checks the engine handles the simple case too.",
    ),
]

EXPECTED_PATTERNS: set[str] = {c.pattern_id for c in CASES if c.pattern_id}


@dataclass
class CaseResult:
    """Scoring for a single case."""

    case_id: str
    question: str
    pattern_id: str | None
    pattern_found: bool = False
    metric_found: bool = False
    entity_found: bool = False
    direction_found: bool = False
    statistical_test_run: bool = False
    rejection_observed: bool = False
    numeric_accuracy: float = 0.0
    sql_validity: float = 0.0
    tool_call_validity: float = 0.0
    support_rate: float = 0.0
    provenance_completeness: float = 0.0
    chart_field_validity: float = 0.0
    findings_published: int = 0
    findings_rejected: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    runtime_seconds: float = 0.0
    failures: list[str] = field(default_factory=list)
