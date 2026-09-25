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
    #: Metrics that must **all** appear. Use this when the question asks
    #: about several things and recovering one of them is not the answer.
    expect_metrics_all: tuple[str, ...] = ()
    #: Metrics of which **any one** satisfies the check. Use this only when
    #: the alternatives are genuinely interchangeable ways to express the
    #: same result, not when they are separate facts.
    expect_metrics_any: tuple[str, ...] = ()
    #: Direction per metric, checked against the findings that actually
    #: report that metric. A run where revenue rose and margin fell must not
    #: pass because the word "fell" appears somewhere in the corpus.
    expect_metric_directions: tuple[tuple[str, str], ...] = ()
    #: Lowercased substrings, any one of which satisfies the entity check.
    expect_entity_any: tuple[str, ...] = ()
    #: Direction anywhere in the published corpus. Only meaningful when the
    #: case reports a single metric; prefer `expect_metric_directions`.
    expect_direction: str | None = None
    #: True when the question should provoke a real statistical test.
    expect_statistical_test: bool = False
    #: Sign the test's effect size must carry, for a pattern whose signature
    #: is a difference *between groups* rather than a movement over time.
    #: Read from the SciPy-computed `effect_size`, not from wording.
    expect_effect_sign: str | None = None
    #: A causal over-claim must be caught. Checked against the rejection's
    #: own reason, not against "something was withheld".
    expect_causal_rejection: bool = False
    notes: str = ""

    @property
    def all_expected_metrics(self) -> tuple[str, ...]:
        """Every metric this case mentions, for reporting."""
        return tuple(dict.fromkeys([*self.expect_metrics_all, *self.expect_metrics_any]))

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
        # Two facts, not two ways of saying one. Recovering the margin drop
        # while missing the revenue rise is half an answer to a question that
        # asked for both.
        expect_metrics_all=("gross_margin_pct", "revenue"),
        expect_metric_directions=(
            ("gross_margin_pct", "down"),
            ("revenue", "up"),
        ),
        notes="Both directions must be recovered, not just the one the question leads with.",
    ),
    BenchmarkCase(
        case_id="Q2",
        question="Revenue increased in Q3 2025, but gross margin fell. What caused it?",
        pattern_id="q3_margin_compression",
        expect_metrics_all=("gross_margin_pct",),
        expect_metric_directions=(("gross_margin_pct", "down"),),
        expect_entity_any=("discount", "electronics"),
        notes="The injected causes are discounting and a mix shift into Electronics.",
    ),
    BenchmarkCase(
        case_id="Q3",
        question="Which product category has the highest return rate?",
        pattern_id="home_kitchen_returns",
        expect_metrics_all=("return_rate",),
        # No direction expectation: the question asks which category is
        # highest, and the answer to that is the entity, not a movement.
        # Asserting "up" here would only be checking that the word appears.
        expect_entity_any=("home & kitchen", "home and kitchen"),
    ),
    BenchmarkCase(
        case_id="Q4",
        question="Which customer segments are driving the increase in return rate?",
        pattern_id="home_kitchen_returns",
        expect_metrics_all=("return_rate",),
        expect_entity_any=("new",),
        expect_statistical_test=True,
    ),
    BenchmarkCase(
        case_id="Q5",
        question="Which acquisition channel has the weakest contribution margin?",
        pattern_id="affiliate_weak_contribution",
        # Genuinely interchangeable: any of these expresses "this channel is
        # the weakest", so recovering one of them answers the question.
        expect_metrics_any=("contribution_margin_pct", "contribution_margin", "roas"),
        expect_entity_any=("affiliate",),
        # Corpus-level and therefore weak; kept as a smoke check only,
        # because "weakest" here is cross-sectional rather than a movement.
        expect_direction="down",
    ),
    BenchmarkCase(
        case_id="Q6",
        question="Do shipping delays appear to affect repeat purchasing?",
        pattern_id="late_delivery_repeat_association",
        expect_metrics_all=("repeat_purchase_rate",),
        # No temporal direction. The injected pattern is a difference
        # *between groups* -- customers whose first delivery was late repeat
        # less -- not a movement over time, and the repeat rate does in fact
        # rise across this window. The old expectation said "down" and passed
        # only because some finding somewhere contained a down word; the
        # signed effect below is the thing the pattern actually asserts.
        expect_statistical_test=True,
        expect_effect_sign="negative",
        expect_causal_rejection=True,
        notes="A causal reading of this association must be withheld, as causal.",
    ),
    BenchmarkCase(
        case_id="Q7",
        question="Which shipping carrier has the highest late delivery rate?",
        pattern_id="northeast_carrier_delay",
        expect_metrics_all=("late_delivery_rate",),
        # As Q3: the entity is the answer to "which is highest".
        expect_entity_any=("rapidpost",),
    ),
    BenchmarkCase(
        case_id="Q8",
        question="How did monthly revenue change over 2025?",
        pattern_id="q4_seasonality",
        expect_metrics_all=("revenue",),
        expect_metric_directions=(("revenue", "up"),),
        notes="A plain trend question; checks the engine handles the simple case too.",
    ),
]

EXPECTED_PATTERNS: set[str] = {c.pattern_id for c in CASES if c.pattern_id}


@dataclass
class CaseResult:
    """Scoring for a single case.

    Finding counts are split by denominator, because one number cannot mean
    both things. A *candidate* is any finding a worker proposed; the
    candidate support rate is how many survived verification. A *published*
    finding is one that reached the report; the published support rate is how
    many of those carry a `supported` verdict, and it must be 1.0 by
    construction -- if it is not, the publication gate leaked.
    """

    case_id: str
    question: str
    pattern_id: str | None
    pattern_found: bool = False
    metric_found: bool = False
    entity_found: bool = False
    direction_found: bool = False
    statistical_test_run: bool = False
    effect_sign_correct: bool = False
    #: A rejection whose recorded reason is the causal guard, not merely
    #: that some finding somewhere was withheld.
    causal_rejection_observed: bool = False
    rejection_observed: bool = False

    # Findings, by denominator.
    candidate_findings: int = 0
    supported_candidate_findings: int = 0
    withheld_findings: int = 0
    published_findings: int = 0
    unsupported_published_findings: int = 0

    # Numeric verification. The denominator is *findings*, not numeric
    # literals: one finding can state several numbers and `verify_numbers`
    # checks all of them together, returning one verdict. Calling the
    # denominator "assertions" implied a literal-level count it never was.
    published_findings_numeric_checked: int = 0
    published_findings_numeric_valid: int = 0

    sql_statements: int = 0
    sql_statements_valid: int = 0
    tool_calls: int = 0
    tool_calls_ok: int = 0

    provenance_complete: int = 0
    chart_fields: int = 0
    chart_fields_valid: int = 0

    # Tool dependence: how much of the answer came from governed analytical
    # tools rather than from model-written SQL.
    deterministic_tool_calls: int = 0
    generated_sql_calls: int = 0
    statistical_tool_calls: int = 0
    decomposition_tool_calls: int = 0

    llm_calls: int = 0
    runtime_seconds: float = 0.0
    failures: list[str] = field(default_factory=list)

    @property
    def candidate_support_rate(self) -> float | None:
        """Share of proposed findings that survived verification."""
        if not self.candidate_findings:
            return None
        return self.supported_candidate_findings / self.candidate_findings

    @property
    def publication_gate_integrity(self) -> float | None:
        """Share of published findings carrying a supported verdict.

        What this measures, precisely: that the publication gate emitted
        nothing its own verification pipeline rejected. It is a consistency
        check on the gate, and it is 1.0 by construction unless the gate
        leaks -- which is worth watching, and is *not* an independent
        estimate of whether the findings are semantically right. The
        injected-pattern checks are what provide independent evidence.
        """
        if not self.published_findings:
            return None
        return (
            self.published_findings - self.unsupported_published_findings
        ) / self.published_findings

    @property
    def published_finding_numeric_verification_rate(self) -> float | None:
        """Share of published findings whose numbers all re-verified.

        Per finding, not per numeric literal. A finding stating three
        figures counts once and passes only if all three check out.
        """
        if not self.published_findings_numeric_checked:
            return None
        return self.published_findings_numeric_valid / self.published_findings_numeric_checked

    @property
    def sql_validity(self) -> float | None:
        if not self.sql_statements:
            return None
        return self.sql_statements_valid / self.sql_statements

    @property
    def tool_call_validity(self) -> float | None:
        if not self.tool_calls:
            return None
        return self.tool_calls_ok / self.tool_calls

    @property
    def provenance_completeness(self) -> float | None:
        if not self.published_findings:
            return None
        return self.provenance_complete / self.published_findings

    @property
    def chart_field_validity(self) -> float | None:
        if not self.chart_fields:
            return None
        return self.chart_fields_valid / self.chart_fields

    @property
    def resolved_without_generated_sql(self) -> float | None:
        total = self.deterministic_tool_calls + self.generated_sql_calls
        if not total:
            return None
        return self.deterministic_tool_calls / total

    def as_dict(self) -> dict[str, object]:
        payload = dict(self.__dict__)
        payload.update(
            {
                "candidate_support_rate": self.candidate_support_rate,
                "publication_gate_integrity": self.publication_gate_integrity,
                "published_finding_numeric_verification_rate": (
                    self.published_finding_numeric_verification_rate
                ),
                "sql_validity": self.sql_validity,
                "tool_call_validity": self.tool_call_validity,
                "provenance_completeness": self.provenance_completeness,
                "chart_field_validity": self.chart_field_validity,
                "resolved_without_generated_sql": self.resolved_without_generated_sql,
            }
        )
        return payload
