"""The evaluation must actually recover the injected patterns.

This runs the real benchmark on a reduced warehouse. It is the test that would
fail if the agents stopped finding what the generator put there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentic_analytics.data.generator import GeneratorConfig, generate_warehouse
from agentic_analytics.evaluation.cases import CASES, EXPECTED_PATTERNS
from agentic_analytics.evaluation.harness import run_benchmark


@pytest.fixture(scope="module")
def benchmark_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("bench")
    generate_warehouse(out, GeneratorConfig(n_customers=15_000, n_products=400, seed=31))
    return out


@pytest.fixture(scope="module")
async def report(benchmark_warehouse: Path) -> dict[str, Any]:
    return await run_benchmark(benchmark_warehouse)


async def test_every_case_runs(report: dict[str, Any]) -> None:
    assert report["summary"]["cases"] == len(CASES)
    assert report["summary"]["task_completion"] == 1.0


async def test_the_artefact_states_what_it_measures(report: dict[str, Any]) -> None:
    """A benchmark that does not say what it measures invites misreading."""
    summary = report["summary"]
    assert summary["benchmark_kind"] == "deterministic end-to-end engine benchmark"
    assert summary["provider_mode"] == "fake"
    assert "planning quality" in summary["does_not_measure"]
    assert "excludes model inference latency" in summary["deterministic_engine_seconds_note"]


async def test_all_injected_patterns_are_recovered(report: dict[str, Any]) -> None:
    summary = report["summary"]
    assert summary["patterns_missed"] == [], summary["patterns_missed"]
    assert summary["patterns_found"] == len(EXPECTED_PATTERNS)


async def test_no_case_fails_its_checks(report: dict[str, Any]) -> None:
    failures = {case["case_id"]: case["failures"] for case in report["cases"] if case["failures"]}
    assert not failures, failures


async def test_candidate_and_published_rates_use_different_denominators(
    report: dict[str, Any],
) -> None:
    """The two support rates answer different questions.

    Candidate support is how many proposed findings survived verification,
    and is expected to be below 1.0 -- a run that never withholds anything is
    not verifying. Published support is how many findings that *reached the
    report* are supported, and must be exactly 1.0, because publishing an
    unsupported finding is the failure this system exists to prevent.
    """
    summary = report["summary"]
    candidates = summary["candidate_findings"]
    published = summary["published_findings"]
    withheld = summary["withheld_findings"]

    assert candidates == published + withheld
    assert summary["supported_candidate_findings"] == published
    assert summary["candidate_support_rate"] == pytest.approx(
        summary["supported_candidate_findings"] / candidates
    )
    assert summary["publication_gate_integrity"] == 1.0
    assert summary["unsupported_published_findings"] == 0
    assert summary["published_with_supported_verdict"] == published
    # The two rates must not be the same number by accident.
    assert summary["candidate_support_rate"] < summary["publication_gate_integrity"]


async def test_the_gate_metric_says_what_it_is(report: dict[str, Any]) -> None:
    """It is a consistency check on the publication gate, not an accuracy score.

    The old name, `published_support_rate`, read like the latter. The report
    now carries the caveat alongside the number so a reader of the JSON does
    not have to find it in the documentation.
    """
    summary = report["summary"]
    assert "published_support_rate" not in summary, "the misleading name is back"
    note = summary["publication_gate_integrity_note"]
    assert "not an independent" in note
    assert "semantic accuracy" in note


async def test_numeric_verification_names_its_denominator(report: dict[str, Any]) -> None:
    """Per published finding, not per numeric literal.

    `verify_numbers` checks every figure in a finding and returns one
    verdict, so the denominator was never a count of literals. The old name
    `numeric_assertions` implied it was.
    """
    summary = report["summary"]
    assert "numeric_assertions" not in summary
    assert "numeric_accuracy" not in summary
    assert summary["published_findings_numeric_checked"] == summary["published_findings"]
    assert (
        summary["published_findings_numeric_valid"] == summary["published_findings_numeric_checked"]
    )
    assert summary["published_finding_numeric_verification_rate"] == 1.0


async def test_all_sql_is_read_only(report: dict[str, Any]) -> None:
    assert report["summary"]["sql_validity"] == 1.0


async def test_all_tool_calls_succeed(report: dict[str, Any]) -> None:
    assert report["summary"]["tool_call_validity"] == 1.0


async def test_no_unsupported_finding_is_published(report: dict[str, Any]) -> None:
    assert report["summary"]["unsupported_published_findings"] == 0


async def test_provenance_is_complete(report: dict[str, Any]) -> None:
    assert report["summary"]["provenance_completeness"] == 1.0


async def test_every_chart_field_exists(report: dict[str, Any]) -> None:
    assert report["summary"]["chart_field_validity"] == 1.0


async def test_the_verifier_rejects_something(report: dict[str, Any]) -> None:
    """A suite where nothing is ever withheld is not testing the verifier."""
    assert report["summary"]["withheld_findings"] > 0


async def test_statistical_cases_run_a_real_test(report: dict[str, Any]) -> None:
    by_id = {c["case_id"]: c for c in report["cases"]}
    for case in CASES:
        if case.expect_statistical_test:
            assert by_id[case.case_id]["statistical_test_run"], case.case_id


async def test_the_engine_answers_without_model_written_sql(
    report: dict[str, Any],
) -> None:
    """The measure of whether this is an engine or a text-to-SQL wrapper.

    Not a target to hit -- whatever the implementation does is what gets
    reported -- but it is asserted to stay above zero so a regression toward
    generated SQL is visible rather than silent.
    """
    summary = report["summary"]
    assert summary["deterministic_tool_calls"] > 0
    assert summary["resolved_without_generated_sql"] is not None
    assert summary["resolved_without_generated_sql"] > 0.5, (
        "most analysis should resolve through governed tools, not generated SQL"
    )


async def test_driver_decomposition_is_actually_exercised(
    report: dict[str, Any],
) -> None:
    """The deterministic attribution is the answer to a 'why' question."""
    assert report["summary"]["decomposition_tool_calls"] > 0
    assert report["summary"]["statistical_tool_calls"] > 0


# ----------------------------------------------- expectation semantics
def test_q1_requires_both_metrics_and_both_directions() -> None:
    """The case documents "both directions"; the schema must enforce it.

    `expect_metrics` used to be satisfied by *any* listed metric, so a run
    that recovered the margin decline and missed the revenue rise scored the
    same as one that found both — while the note said otherwise.
    """
    from agentic_analytics.evaluation.cases import CASES

    q1 = next(c for c in CASES if c.case_id == "Q1")
    assert set(q1.expect_metrics_all) == {"gross_margin_pct", "revenue"}
    assert not q1.expect_metrics_any
    assert dict(q1.expect_metric_directions) == {
        "gross_margin_pct": "down",
        "revenue": "up",
    }


def test_only_genuinely_interchangeable_metrics_use_any() -> None:
    """`any` is for alternative spellings of one result, not separate facts."""
    from agentic_analytics.evaluation.cases import CASES

    for case in CASES:
        if case.expect_metrics_any:
            assert case.case_id == "Q5", (
                f"{case.case_id} uses expect_metrics_any; check the alternatives "
                "really are interchangeable ways to say the same thing"
            )


def test_a_direction_is_checked_against_the_metric_that_reports_it() -> None:
    """A "fell" somewhere else in the run must not satisfy it."""
    from agentic_analytics.agents.schemas import PublishedFinding
    from agentic_analytics.analytics.results import ResultSnapshot
    from agentic_analytics.evaluation.harness import _metric_directions_hold

    def finding(text: str, metric: str, result_id: str) -> PublishedFinding:
        return PublishedFinding(
            finding_id=f"fin_{metric}",
            text=text,
            kind="calculated_fact",
            task_id="t",
            result_ids=[result_id],
            evidence_cells=[],
            metric_ids=[metric],
            verification_status="supported",
            verifier_reason="ok",
            claimed_change={"type": "difference", "from": 10.0, "to": 5.0},
        )

    results = {"res_ts": ResultSnapshot(tool_name="analyze_timeseries")}
    published = [
        finding("gross_margin_pct fell from 10 to 5.", "gross_margin_pct", "res_ts"),
        finding("revenue fell from 10 to 5.", "revenue", "res_ts"),
    ]
    # Revenue is down here, so expecting it up must fail even though the
    # corpus is full of the word "fell".
    holds, failures = _metric_directions_hold(
        published,
        (("gross_margin_pct", "down"), ("revenue", "up")),
        results,
    )
    assert not holds
    assert any("revenue" in f for f in failures), failures


def test_a_segment_comparison_is_not_read_as_a_trend() -> None:
    """ "Apparel highest, Toys lowest" is not "revenue rose"."""
    from agentic_analytics.agents.schemas import PublishedFinding
    from agentic_analytics.analytics.results import ResultSnapshot
    from agentic_analytics.evaluation.harness import _signed_change

    comparison = PublishedFinding(
        finding_id="fin_seg",
        text="Across category, Apparel has the highest revenue and Toys the lowest.",
        kind="calculated_fact",
        task_id="t",
        result_ids=["res_seg"],
        evidence_cells=[],
        metric_ids=["revenue"],
        verification_status="supported",
        verifier_reason="ok",
        claimed_change={"type": "difference", "from": 825693.0, "to": 1522302.0},
    )
    results = {"res_seg": ResultSnapshot(tool_name="compare_segments")}
    assert _signed_change(comparison, results) is None


def test_q6_requires_the_causal_guard_specifically(report: dict[str, Any]) -> None:
    """Not "something was withheld" but "the causal rule withheld something".

    An unrelated numeric mismatch used to satisfy this case, which exists to
    prove causal over-claims are caught.
    """
    from agentic_analytics.evaluation.cases import CASES

    q6 = next(c for c in CASES if c.case_id == "Q6")
    assert q6.expect_causal_rejection is True

    case = next(c for c in report["cases"] if c["case_id"] == "Q6")
    assert case["causal_rejection_observed"] is True
    assert case["effect_sign_correct"] is True


def test_sql_validity_uses_the_real_guard() -> None:
    """Not a prefix check. The scorer and the runtime must agree."""
    from agentic_analytics.verification.sql import is_read_only

    ok, _ = is_read_only("SELECT category, SUM(net_revenue) FROM order_items GROUP BY 1")
    assert ok
    # Starts with SELECT and would pass a prefix check; the guard refuses it.
    refused, why = is_read_only("SELECT * FROM read_csv_auto('/etc/passwd')")
    assert not refused and why
    refused, why = is_read_only("SELECT 1; DROP TABLE orders")
    assert not refused and why
