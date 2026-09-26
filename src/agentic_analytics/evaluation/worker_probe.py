"""A controlled probe of one agent role: `worker_findings`.

The first real-model sweep planned well and published nothing. Four of five
plans were directly executable, no schema validation failed, and yet no claim
ever reached the critic. Somewhere between "the tool returned a table" and "a
candidate finding exists" everything was being lost, and a 34-question sweep
costs an hour and still would not say *which* of three very different causes
was responsible:

1. the model cannot satisfy the evidence-cell contract,
2. the prompt or the schema obstructs a model that otherwise could, or
3. the tool results the worker was handed were useless, and the empty output
   was the correct response to them.

Those call for opposite fixes -- change the schema, change the prompt, or
change nothing about this layer at all -- so the probe removes the upstream
entirely. It hands the role *known-good* results, built here, with values
chosen so that every check has a right answer the engine can compute. What is
left is the model's own ability to read a table and cite it.

This is a diagnostic, not a benchmark. It has no pass mark, it never runs in
CI against a real model, and nothing it reports is a score. It answers one
question: given results that are definitely fine, does this role work?

Run it with::

    AAE_PROVIDER_MODE=local aae probe-worker-findings --out probe.json
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agentic_analytics.agents.base import StructuredCall, observe_structured_calls
from agentic_analytics.agents.critic import verify_finding
from agentic_analytics.agents.prompts import FINDINGS_FIELD_GUIDE, WORKER_FINDINGS
from agentic_analytics.agents.schemas import AnalysisTask, CandidateFinding
from agentic_analytics.agents.worker import FindingList, _findings_prompt
from agentic_analytics.analytics.results import (
    EvidenceCell,
    EvidenceKind,
    ResultSnapshot,
    StatisticalResult,
)
from agentic_analytics.llm.base import LLMError, LLMProvider
from agentic_analytics.logging import get_logger

log = get_logger(__name__)

#: Ceiling on one probe case. Generous: a cold 7B load can take minutes.
CASE_TIMEOUT_SECONDS = 300.0

#: The two axes the probe varies, so that "the model cannot do this" and
#: "we asked badly" can be told apart. `full` is the domain object the model
#: is asked for today, ids and all; `slim` is `ProposedFinding`, which asks
#: only for what the model must decide. `terse` is the original request;
#: `guided` adds the field guide.
SCHEMA_VARIANTS = ("full", "slim")
PROMPT_VARIANTS = ("terse", "guided")

TERSE_GUIDE = ""


class ProposedCell(BaseModel):
    """A cell reference, as the model gives it.

    The same three coordinates as `EvidenceCell` minus `label`, which the
    engine derives.
    """

    result_id: str = Field(
        description="The result_id shown above the table this cell is in, e.g. res_9f1c2a."
    )
    row: int = Field(description="Which row of that result, counting from 0 as printed.")
    column: str = Field(description="The column name, spelled exactly as printed.")
    value: Any = Field(
        default=None,
        description="The value in that cell, copied exactly. Leave out if unsure.",
    )


class ProposedFinding(BaseModel):
    """The probe's control arm: a finding schema with the ids taken out.

    This is **not** the production contract, and deliberately so. The
    hypothesis it was built to test was that asking a small model for
    `CandidateFinding` -- a domain object carrying its own id, its task
    linkage and its metric ids -- obstructed it, because an opaque string
    (`finding_id`) comes first in the generation order of every finding and
    is a strange thing to make a model produce before it has said anything.

    The probe answered no. At temperature 0 the two schemas produced byte
    identical output on all five cases; the request wording accounted for
    the entire difference. So the production schema was left alone and this
    stayed here, as the arm that makes that comparison repeatable if the
    model or the prompt changes.

    Everything verification needs is still asked for: the claim, the results
    it rests on, the exact cells, the kind of evidence, and any arithmetic
    asserted.
    """

    text: str = Field(
        description="One sentence stating what the results show. Copy numbers exactly."
    )
    kind: EvidenceKind = Field(
        default="calculated_fact",
        description=(
            "calculated_fact for a number read from the results, statistical_result "
            "for the output of a test that was run, interpretation for a reading "
            "that goes beyond what the numbers state."
        ),
    )
    result_ids: list[str] = Field(
        default_factory=list,
        description="Every result_id this claim depends on.",
    )
    evidence_cells: list[ProposedCell] = Field(
        default_factory=list,
        description="The specific cells the claim reads.",
    )
    claimed_change: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Only when the claim asserts arithmetic between cells, e.g. "
            '{"type": "difference", "from": 5.0, "to": 12.5, "stated": 7.5}. '
            "The engine recomputes it."
        ),
    )

    def to_candidate(
        self, task_id: str | None, metric_ids: list[str] | None = None
    ) -> CandidateFinding:
        """Build the domain object. The engine supplies what it owns."""
        return CandidateFinding(
            text=self.text,
            kind=self.kind,
            task_id=task_id,
            result_ids=list(self.result_ids),
            evidence_cells=[
                EvidenceCell(result_id=c.result_id, row=c.row, column=c.column, value=c.value)
                for c in self.evidence_cells
            ],
            metric_ids=list(metric_ids or []),
            claimed_change=self.claimed_change,
        )


class ProposedFindingList(BaseModel):
    findings: list[ProposedFinding] = Field(default_factory=list)


@dataclass
class ProbeCase:
    """One fixed situation handed to the worker-findings role."""

    case_id: str
    title: str
    objective: str
    results: list[ResultSnapshot]
    #: What a competent analyst would say here, in one line. Recorded in the
    #: artifact for the human reading it; nothing scores against it, because
    #: scoring free text against a sentence is the thing this project exists
    #: to avoid doing.
    expectation: str
    #: True when the honest response is to say nothing substantive. Used to
    #: interpret an empty result, not to grade one.
    substantive_conclusion_available: bool = True


@dataclass
class ClaimReport:
    """Every check the pipeline applies to one proposed claim."""

    finding_id: str
    text: str
    kind: str
    result_ids: list[str]
    metric_ids: list[str]
    claimed_change: dict[str, Any] | None
    evidence_cells: list[dict[str, Any]]

    # -- reference integrity, checked here against the known results
    result_ids_valid: bool = False
    evidence_cell_count: int = 0
    evidence_rows_valid: bool = False
    evidence_columns_valid: bool = False
    #: The model may echo a cell's value. When it does, is the echo right?
    evidence_values_copied: int = 0
    evidence_values_correct: int = 0
    #: Cells naming a field of `statistical_result` -- `p_value`,
    #: `effect_size` -- rather than a column of the row grid. Counted apart
    #: from an invalid column because it is not the same mistake: the value
    #: really is in the result, and `EvidenceCell` has no way to address it.
    statistical_field_references: int = 0

    # -- the real gates, run unmodified
    claim_shape_ok: bool = False
    claim_shape_rule: str = ""
    numeric_ok: bool = False
    numeric_rule: str = ""
    verdict_status: str = ""
    verdict_rule: str = ""
    verdict_reason: str = ""
    published: bool = False


@dataclass
class CaseReport:
    case_id: str
    title: str
    expectation: str
    substantive_conclusion_available: bool
    result_ids: list[str]

    schema_valid: bool = False
    stage: str = ""
    error: str = ""
    findings_emitted: int = 0
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    claims: list[ClaimReport] = field(default_factory=list)

    @property
    def published_count(self) -> int:
        return sum(1 for c in self.claims if c.published)


def probe_cases() -> list[ProbeCase]:
    """The five situations, in increasing order of what they ask for.

    Values are deliberately awkward -- 128450.75, not 128000 -- because a
    model that reproduces a round number may have guessed it, and a probe
    that cannot tell copying from guessing measures nothing.
    """
    simple = ResultSnapshot(
        result_id="res_simple",
        tool_name="aggregate_for_question",
        task_id="task_probe",
        sql="SELECT SUM(net_amount) AS total_revenue FROM orders",
        columns=["total_revenue"],
        rows=[[128450.75]],
        row_count=1,
    )
    grouped = ResultSnapshot(
        result_id="res_grouped",
        tool_name="aggregate_for_question",
        task_id="task_probe",
        sql="SELECT region, SUM(net_amount) AS revenue FROM orders GROUP BY region",
        columns=["region", "revenue"],
        rows=[
            ["North", 52100.0],
            ["South", 38200.5],
            ["East", 24900.25],
            ["West", 13249.5],
        ],
        row_count=4,
    )
    trend = ResultSnapshot(
        result_id="res_trend",
        tool_name="analyze_timeseries",
        task_id="task_probe",
        sql="SELECT month, COUNT(*) AS orders FROM orders GROUP BY month ORDER BY month",
        columns=["month", "orders"],
        rows=[
            ["2024-01", 812],
            ["2024-02", 874],
            ["2024-03", 901],
            ["2024-04", 968],
            ["2024-05", 1043],
            ["2024-06", 1117],
        ],
        row_count=6,
    )
    statistical = ResultSnapshot(
        result_id="res_stats",
        tool_name="compare_segments",
        task_id="task_probe",
        sql="SELECT cohort, AVG(basket_value) AS mean_basket FROM orders GROUP BY cohort",
        columns=["cohort", "mean_basket", "n"],
        rows=[
            ["returning", 84.32, 1420],
            ["new", 71.08, 1385],
        ],
        row_count=2,
        statistical_result=StatisticalResult(
            test_name="welch_t_test",
            statistic=6.41,
            p_value=0.00000019,
            sample_sizes={"returning": 1420, "new": 1385},
            effect_size=0.24,
            effect_size_name="cohens_d",
            confidence_interval=(9.19, 17.29),
            assumptions=["independent samples", "unequal variances permitted"],
        ),
    )
    # Case E. Nothing here supports a conclusion: two groups, twelve rows
    # between them, a 0.4% gap and no test. The honest outputs are a bare
    # descriptive fact or nothing at all. What must *not* appear is a claim
    # that one group is higher in any way that generalises, or any use of
    # the word "significant" -- the claim-shape gate blocks the latter, so
    # this case also checks that the gate is load-bearing against a real
    # model rather than only against the scripted one.
    inconclusive = ResultSnapshot(
        result_id="res_thin",
        tool_name="aggregate_for_question",
        task_id="task_probe",
        sql="SELECT plan, AVG(days_to_convert) AS mean_days FROM trials GROUP BY plan",
        columns=["plan", "mean_days", "n"],
        rows=[
            ["pro", 14.20, 7],
            ["basic", 14.26, 5],
        ],
        row_count=2,
        warnings=[
            "only 12 rows matched this filter",
            "no statistical test was run on this result",
        ],
    )

    return [
        ProbeCase(
            case_id="A",
            title="simple aggregate",
            objective="Report total revenue across all orders",
            results=[simple],
            expectation="Total revenue is 128450.75, cited from res_simple row 0.",
        ),
        ProbeCase(
            case_id="B",
            title="grouped comparison and ranking",
            objective="Compare revenue across regions and identify the largest",
            results=[grouped],
            expectation=("North is the largest region at 52100.00; West the smallest at 13249.50."),
        ),
        ProbeCase(
            case_id="C",
            title="time trend",
            objective="Describe how monthly order volume changed over the period",
            results=[trend],
            expectation="Orders rose every month, from 812 in January to 1117 in June.",
        ),
        ProbeCase(
            case_id="D",
            title="statistical result",
            objective="Report whether returning and new cohorts differ in basket value",
            results=[statistical],
            expectation=(
                "Returning customers' mean basket is 84.32 against 71.08 for new; "
                "Welch's t-test gives p < 0.001. A significance claim is permitted "
                "here because a test was actually run."
            ),
        ),
        ProbeCase(
            case_id="E",
            title="result supporting no substantive conclusion",
            objective="Determine whether trial plan affects time to convert",
            results=[inconclusive],
            expectation=(
                "Say nothing substantive: 12 rows, a 0.06-day gap and no test. "
                "A bare descriptive fact is acceptable; a difference claim, a "
                "significance claim or a causal claim is not."
            ),
            substantive_conclusion_available=False,
        ),
    ]


async def run_probe(
    provider: LLMProvider,
    cases: list[ProbeCase] | None = None,
    schema_variant: str = "full",
    prompt_variant: str = "guided",
) -> dict[str, Any]:
    """Run every case and return a reviewable report."""
    if schema_variant not in SCHEMA_VARIANTS:
        raise ValueError(f"unknown schema variant {schema_variant!r}")
    if prompt_variant not in PROMPT_VARIANTS:
        raise ValueError(f"unknown prompt variant {prompt_variant!r}")

    selected = cases if cases is not None else probe_cases()
    reports: list[CaseReport] = []
    started = time.time()

    for case in selected:
        reports.append(await _run_case(provider, case, schema_variant, prompt_variant))

    report = _summarise(provider, reports, time.time() - started)
    report["schema_variant"] = schema_variant
    report["prompt_variant"] = prompt_variant
    return report


async def _run_case(
    provider: LLMProvider,
    case: ProbeCase,
    schema_variant: str = "full",
    prompt_variant: str = "guided",
) -> CaseReport:
    by_id = {r.result_id: r for r in case.results}
    report = CaseReport(
        case_id=case.case_id,
        title=case.title,
        expectation=case.expectation,
        substantive_conclusion_available=case.substantive_conclusion_available,
        result_ids=list(by_id),
    )

    task = AnalysisTask(
        task_id="task_probe",
        objective=case.objective,
        preferred_tool="aggregate_for_question",
    )
    payloads = [r.compact() for r in case.results]

    calls: list[StructuredCall] = []
    before_in = provider.usage.input_tokens
    before_out = provider.usage.output_tokens
    started = time.time()
    findings: list[CandidateFinding] = []
    try:
        with observe_structured_calls(calls.append):
            findings = await _ask(provider, task, payloads, schema_variant, prompt_variant)
        report.schema_valid = True
    except LLMError as exc:
        report.error = str(exc)
        log.warning("probe_case_failed", case=case.case_id, error=str(exc))

    report.seconds = round(time.time() - started, 2)
    report.input_tokens = provider.usage.input_tokens - before_in
    report.output_tokens = provider.usage.output_tokens - before_out
    # The worker-findings call is the first; a later one is the critic's.
    worker_calls = [c for c in calls if c.role == "worker_findings"]
    report.stage = worker_calls[0].stage if worker_calls else "not_attempted"
    report.findings_emitted = len(findings)

    for finding in findings:
        finding.task_id = task.task_id
        report.claims.append(await _check_claim(provider, finding, by_id))
    return report


async def _ask(
    provider: LLMProvider,
    task: AnalysisTask,
    payloads: list[dict[str, Any]],
    schema_variant: str,
    prompt_variant: str,
) -> list[CandidateFinding]:
    """The call `worker.run_task` makes, with one axis varied at a time.

    The system prompt, the user-prompt builder, the role name and the schema
    are imported rather than copied, because a probe that drifts from the
    code path it is probing produces confident conclusions about something
    that is not running in production.
    """
    from agentic_analytics.agents.base import ask_into

    guide = FINDINGS_FIELD_GUIDE if prompt_variant == "guided" else TERSE_GUIDE
    user = _findings_prompt(task, payloads, field_guide=guide).strip()
    context = {"task": json.loads(task.model_dump_json()), "results": payloads}

    if schema_variant == "slim":
        slim = await ask_into(
            provider,
            ProposedFindingList,
            role="worker_findings",
            system=WORKER_FINDINGS,
            user=user,
            context=context,
        )
        # The engine builds the domain object, exactly as it would in a run.
        return [f.to_candidate(task.task_id, list(task.required_metrics)) for f in slim.findings]

    full = await ask_into(
        provider,
        FindingList,
        role="worker_findings",
        system=WORKER_FINDINGS,
        user=user,
        context=context,
    )
    return full.findings


async def _check_claim(
    provider: LLMProvider,
    finding: CandidateFinding,
    results: dict[str, ResultSnapshot],
) -> ClaimReport:
    """Reference integrity first, then the three real gates."""
    claim = ClaimReport(
        finding_id=finding.finding_id,
        text=finding.text,
        kind=finding.kind,
        result_ids=list(finding.result_ids),
        metric_ids=list(finding.metric_ids),
        claimed_change=finding.claimed_change,
        evidence_cells=[json.loads(c.model_dump_json()) for c in finding.evidence_cells],
        evidence_cell_count=len(finding.evidence_cells),
    )
    claim.result_ids_valid = bool(finding.result_ids) and all(
        r in results for r in finding.result_ids
    )

    rows_ok = bool(finding.evidence_cells)
    columns_ok = bool(finding.evidence_cells)
    for cell in finding.evidence_cells:
        snapshot = results.get(cell.result_id)
        if snapshot is None:
            rows_ok = columns_ok = False
            continue
        if cell.column not in snapshot.columns:
            if _is_statistical_field(snapshot, cell.column):
                claim.statistical_field_references += 1
            else:
                columns_ok = False
            continue
        if not 0 <= cell.row < len(snapshot.rows):
            rows_ok = False
            continue
        if cell.value is None:
            continue
        # The model echoed a value. Compare numerically where both sides are
        # numbers, so 52100 and 52100.0 are not recorded as a mismatch.
        claim.evidence_values_copied += 1
        actual = snapshot.cell(cell.row, cell.column)
        if _same_value(cell.value, actual):
            claim.evidence_values_correct += 1
    claim.evidence_rows_valid = rows_ok
    claim.evidence_columns_valid = columns_ok

    from agentic_analytics.verification.claims import check_claim
    from agentic_analytics.verification.numeric import verify_numbers

    cited = [results[r] for r in finding.result_ids if r in results]
    shape = check_claim(finding.text, finding.kind, cited, bool(finding.result_ids))
    claim.claim_shape_ok = shape.ok
    claim.claim_shape_rule = shape.rule

    if shape.ok:
        cells = [
            (float(v), f"{c.result_id}[{c.row}].{c.column}")
            for c in finding.evidence_cells
            if (v := _numeric_cell(c, results)) is not None
        ]
        numeric = verify_numbers(finding.text, finding.claimed_change, cells, cited)
        claim.numeric_ok = numeric.ok
        claim.numeric_rule = "passed" if numeric.ok else "numeric_mismatch"

    verdict, _ = await verify_finding(finding, results, provider)
    claim.verdict_status = verdict.status
    claim.verdict_rule = verdict.rule
    claim.verdict_reason = verdict.reason
    claim.published = verdict.status == "supported"
    return claim


def _is_statistical_field(snapshot: ResultSnapshot, column: str) -> bool:
    """True when `column` names a field of the result's statistical test."""
    if snapshot.statistical_result is None:
        return False
    return column in snapshot.statistical_result.model_dump()


def _numeric_cell(cell: Any, results: dict[str, ResultSnapshot]) -> float | None:
    snapshot = results.get(cell.result_id)
    if snapshot is None:
        return None
    try:
        value = snapshot.cell(cell.row, cell.column)
    except (KeyError, IndexError):
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _same_value(claimed: Any, actual: Any) -> bool:
    if isinstance(claimed, bool) or isinstance(actual, bool):
        return claimed is actual
    if isinstance(claimed, int | float) and isinstance(actual, int | float):
        return abs(float(claimed) - float(actual)) <= max(0.005, abs(float(actual)) * 1e-6)
    return str(claimed).strip() == str(actual).strip()


def _summarise(
    provider: LLMProvider, reports: list[CaseReport], wall_clock: float
) -> dict[str, Any]:
    claims = [c for r in reports for c in r.claims]
    answerable = [r for r in reports if r.substantive_conclusion_available]

    # The three diagnoses this probe exists to separate, stated as counts
    # rather than as a verdict, because which one is true is a judgement for
    # the person reading the artifact.
    diagnosis = {
        "cases_where_the_call_itself_failed": sum(1 for r in reports if not r.schema_valid),
        "cases_with_a_valid_response_and_no_finding": sum(
            1 for r in reports if r.schema_valid and not r.findings_emitted
        ),
        "answerable_cases_producing_a_published_finding": sum(
            1 for r in answerable if r.published_count
        ),
        "answerable_cases_producing_nothing": sum(1 for r in answerable if not r.findings_emitted),
        "claims_with_no_evidence_cell": sum(1 for c in claims if not c.evidence_cell_count),
        "claims_citing_an_unknown_result": sum(1 for c in claims if not c.result_ids_valid),
        "claims_with_an_invalid_row": sum(
            1 for c in claims if c.evidence_cell_count and not c.evidence_rows_valid
        ),
        "claims_with_an_invalid_column": sum(
            1 for c in claims if c.evidence_cell_count and not c.evidence_columns_valid
        ),
        "claims_that_miscopied_a_value": sum(
            1 for c in claims if c.evidence_values_copied > c.evidence_values_correct
        ),
        # A gap in the contract rather than a model error: a p-value lives in
        # `statistical_result`, not in a row, so a claim about one cannot
        # produce a valid cell reference however careful the model is.
        "claims_citing_a_statistical_field_as_a_column": sum(
            1 for c in claims if c.statistical_field_references
        ),
    }

    return {
        "probe": "worker_findings",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": provider.name,
        "model": getattr(provider, "model", provider.name),
        "wall_clock_seconds": round(wall_clock, 2),
        "cases": len(reports),
        "schema_valid_cases": sum(1 for r in reports if r.schema_valid),
        "total_findings_emitted": sum(r.findings_emitted for r in reports),
        "total_published": sum(r.published_count for r in reports),
        "claim_shape_failures": sum(1 for c in claims if not c.claim_shape_ok),
        "numeric_failures": sum(1 for c in claims if c.claim_shape_ok and not c.numeric_ok),
        "verdicts": _counts(c.verdict_status for c in claims),
        "withheld_by_rule": _counts(c.verdict_rule for c in claims if not c.published),
        "diagnosis": diagnosis,
        "usage": provider.usage.as_dict(),
        "case_reports": [asdict(r) for r in reports],
    }


def _counts(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))


def write_probe_report(report: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    return path
