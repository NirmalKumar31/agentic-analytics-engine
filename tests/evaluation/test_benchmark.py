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


async def test_all_injected_patterns_are_recovered(report: dict[str, Any]) -> None:
    summary = report["summary"]
    assert summary["patterns_missed"] == [], summary["patterns_missed"]
    assert summary["patterns_found"] == len(EXPECTED_PATTERNS)


async def test_no_case_fails_its_checks(report: dict[str, Any]) -> None:
    failures = {case["case_id"]: case["failures"] for case in report["cases"] if case["failures"]}
    assert not failures, failures


async def test_numeric_accuracy_is_perfect(report: dict[str, Any]) -> None:
    """Every published number was recomputed from the cells it cites."""
    assert report["summary"]["numeric_accuracy"] == 1.0


async def test_all_sql_is_read_only(report: dict[str, Any]) -> None:
    assert report["summary"]["sql_validity"] == 1.0


async def test_all_tool_calls_succeed(report: dict[str, Any]) -> None:
    assert report["summary"]["tool_call_validity"] == 1.0


async def test_no_unsupported_finding_is_published(report: dict[str, Any]) -> None:
    assert report["summary"]["unsupported_findings_published"] == 0


async def test_provenance_is_complete(report: dict[str, Any]) -> None:
    assert report["summary"]["provenance_completeness"] == 1.0


async def test_every_chart_field_exists(report: dict[str, Any]) -> None:
    assert report["summary"]["chart_field_validity"] == 1.0


async def test_the_verifier_rejects_something(report: dict[str, Any]) -> None:
    """A suite where nothing is ever rejected is not testing the verifier."""
    assert report["summary"]["total_findings_rejected"] > 0


async def test_statistical_cases_run_a_real_test(report: dict[str, Any]) -> None:
    by_id = {c["case_id"]: c for c in report["cases"]}
    for case in CASES:
        if case.expect_statistical_test:
            assert by_id[case.case_id]["statistical_test_run"], case.case_id
