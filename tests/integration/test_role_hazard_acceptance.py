"""The production defect, reproduced and fixed, through the real path.

`What is total website_visits by age?` returned "The total age is 48,195
across 1,200 rows" -- an answer about ages, presented as an answer about
website visits. The chain was: inference called `website_visits` an
identifier because it is near-unique, called `age` a measure, the resolver
correctly read `age` as the requested grouping, then let `age` into the
measure pool as well, and the measure/dimension collision was "repaired"
by deleting the grouping.

Every assertion here runs the real upload, the real `infer_schema`, the
real resolver, real SQL and real execution. Expected values come from an
independent DuckDB query over the same file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import duckdb
import pytest
from tests.corpus.runner import RemoteFakeProvider
from tests.fixtures.saas_accounts import ROWS, write_csv

from agentic_analytics.config import Settings
from agentic_analytics.graph.runner import RunResult, run_analysis
from agentic_analytics.llm.fake import FakeProvider
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.warehouse.session import SessionManager, open_upload_session


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_csv(tmp_path_factory.mktemp("role-hazard"))


@pytest.fixture(scope="module")
def oracle(dataset: Path) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute(f"CREATE TABLE t AS SELECT * FROM read_csv_auto('{dataset}')")
    return connection


def _run(dataset: Path, question: str, *, remote: bool = False) -> RunResult:
    async def go() -> RunResult:
        manager = SessionManager()
        settings = Settings(
            upload_dir=dataset.parent / "uploads",
            live_analytics_enabled=True,
            uploads_enabled=True,
            provider_mode="fake",
            log_json=False,
        )
        provider = RemoteFakeProvider() if remote else FakeProvider()
        try:
            session = manager.add(open_upload_session(dataset, dataset.name, "csv"))
            return await run_analysis(
                question,
                session,
                build_server(manager, settings),
                settings=settings,
                provider=provider,
            )
        finally:
            await provider.aclose()
            manager.close_all()

    return asyncio.run(go())


def _aggregate(result: RunResult) -> Any:
    return next(s for s in result.results.values() if s.tool_name == "aggregate_for_question")


def _contract(result: RunResult) -> dict[str, Any]:
    return dict(result.query_contract or {})


# ───────────────────────────────── the inference that set the trap
def test_inference_still_reads_the_measure_as_an_identifier(dataset: Path) -> None:
    """The fix must not depend on inference being corrected.

    A near-unique numeric column is a poor *grouping*, and calling it an
    identifier is right. What was wrong was concluding it could not be
    summed. Pinned so the test below is exercising the explicit-role rule
    and not a reclassification.
    """
    from agentic_analytics.analytics.semantic import infer_schema

    manager = SessionManager()
    try:
        session = manager.add(open_upload_session(dataset, dataset.name, "csv"))
        schema = infer_schema(session, "uploaded_data")
    finally:
        manager.close_all()

    assert "website_visits" in schema.identifiers
    assert "website_visits" not in schema.measures
    assert "age" in schema.measures
    assert "team_size" in schema.dimensions


# ───────────────────────────────── the defect itself
def test_an_explicitly_named_near_unique_measure_is_summed(
    dataset: Path, oracle: duckdb.DuckDBPyConnection
) -> None:
    expected = {
        int(age): (int(total), int(rows))
        for age, total, rows in oracle.execute(
            "SELECT age, sum(website_visits), count(*) FROM t GROUP BY 1 ORDER BY 1"
        ).fetchall()
    }
    age_sum = int(oracle.execute("SELECT sum(age) FROM t").fetchone()[0])
    assert len(expected) == 41

    result = _run(dataset, "What is total website_visits by age?")
    contract = _contract(result)

    assert contract.get("operation") == "sum"
    assert contract.get("measure") == "website_visits"
    assert contract.get("dimensions") == ["age"]
    # The grouping was never dropped, and `age` was never the measure.
    assert contract.get("dimension") == "age"

    snapshot = _aggregate(result)
    got = {int(row[0]): (int(row[1]), int(row[2])) for row in snapshot.rows}
    assert got == expected
    assert len(got) == 41

    coverage = snapshot.group_coverage
    assert coverage is not None and coverage.complete
    assert coverage.rows_matching == ROWS

    published = " ".join(f.text for f in result.published)
    assert published, result.stopped_reason
    # The released build published the sum of ages. It must not appear.
    assert f"{age_sum:,}" not in published
    assert "age is" not in published.lower()


def test_question_coverage_reports_the_grouping_as_covered(dataset: Path) -> None:
    result = _run(dataset, "What is total website_visits by age?")
    coverage = result.question_coverage

    assert coverage is not None
    assert coverage.complete
    assert "dimensions" in coverage.required_components
    assert "measure" in coverage.required_components
    assert not coverage.missing_components
    assert not coverage.rejection_codes


# ───────────────────────────────── the other oracle questions
def test_revenue_by_business_type_returns_every_group(
    dataset: Path, oracle: duckdb.DuckDBPyConnection
) -> None:
    expected = {
        str(kind): round(float(total), 2)
        for kind, total in oracle.execute(
            "SELECT business_type, sum(annual_revenue) FROM t GROUP BY 1"
        ).fetchall()
    }
    assert len(expected) == 4

    snapshot = _aggregate(_run(dataset, "What is the total annual_revenue by business_type?"))
    got = {str(row[0]): round(float(row[1]), 2) for row in snapshot.rows}

    assert got == expected
    assert snapshot.group_coverage is not None
    assert snapshot.group_coverage.complete


def test_an_inclusive_age_filter_applies_both_bounds(
    dataset: Path, oracle: duckdb.DuckDBPyConnection
) -> None:
    expected = {
        str(region): round(float(avg), 2)
        for region, avg in oracle.execute(
            "SELECT region, avg(annual_revenue) FROM t WHERE age >= 30 AND age <= 40 GROUP BY 1"
        ).fetchall()
    }

    result = _run(dataset, "What is the average annual_revenue by region for age 30 to 40?")
    contract = _contract(result)

    # The filter must not become a second grouping, and the grouping must
    # not become a filter.
    assert contract.get("dimensions") == ["region"]
    operators = sorted(f["operator"] for f in contract.get("filters") or [])
    assert operators == ["<=", ">="]

    snapshot = _aggregate(result)
    assert {str(row[0]): round(float(row[1]), 2) for row in snapshot.rows} == expected


def test_two_explicit_groupings_are_both_preserved(
    dataset: Path, oracle: duckdb.DuckDBPyConnection
) -> None:
    """A two-cut question must not be reduced to one cut."""
    expected = {
        (str(region), str(kind)): int(total)
        for region, kind, total in oracle.execute(
            "SELECT region, business_type, sum(website_visits) FROM t GROUP BY 1, 2"
        ).fetchall()
    }

    result = _run(dataset, "What is the total website_visits by region and business_type?")
    contract = _contract(result)
    assert contract.get("dimensions") == ["region", "business_type"]
    # The singular projection must not pretend a two-cut request was one.
    assert contract.get("dimension") is None

    snapshot = _aggregate(result)
    got = {(str(row[0]), str(row[1])): int(row[2]) for row in snapshot.rows}
    assert got == expected
    # More groups than either cut alone would produce, which is what
    # distinguishes a preserved two-cut result from a collapsed one. The
    # fixture's two columns correlate, so not every combination occurs --
    # the count comes from the oracle rather than from arithmetic.
    regions = {region for region, _ in expected}
    kinds = {kind for _, kind in expected}
    assert len(got) > max(len(regions), len(kinds))


def test_a_role_collision_refuses_rather_than_deleting_a_component(dataset: Path) -> None:
    """Grouping a column by itself cannot be represented, so it refuses.

    It no longer reaches the collision branch at all: excluding resolved
    groupings from the measure pool means `age` is never a measure
    candidate here, so measure resolution runs out of options first and
    refuses. That is the better failure -- the collision cannot occur --
    and the branch remains as a backstop with its own unit test.
    """
    result = _run(dataset, "What is the total age by age?")

    assert result.outcome == "refused", result.outcome
    assert not result.published
    assert result.stopped_reason
    coverage = result.question_coverage
    assert coverage is not None and not coverage.complete


def test_both_provider_modes_reach_the_same_contract_and_values(dataset: Path) -> None:
    question = "What is total website_visits by age?"
    local = _run(dataset, question)
    remote = _run(dataset, question, remote=True)

    assert _contract(local).get("canonical_contract") == _contract(remote).get("canonical_contract")
    assert _contract(local).get("contract_hash") == _contract(remote).get("contract_hash")
    assert [f.text for f in local.published] == [f.text for f in remote.published]
