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
    assert summary["published_support_rate"] == 1.0
    assert summary["unsupported_published_findings"] == 0
    # The two rates must not be the same number by accident.
    assert summary["candidate_support_rate"] < summary["published_support_rate"]


async def test_numeric_accuracy_reports_its_denominator(report: dict[str, Any]) -> None:
    summary = report["summary"]
    assert summary["numeric_assertions"] == summary["published_findings"]
    assert summary["numeric_assertions_correct"] == summary["numeric_assertions"]
    assert summary["numeric_accuracy"] == 1.0


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
