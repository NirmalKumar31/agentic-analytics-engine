"""The acceptance matrix, through the real path, against a real oracle.

Every question here runs the actual upload, schema inference, planning, SQL
compilation, execution, verification and report construction. No test
supplies a hand-written schema: doing that is what hid the defects this
module exists for -- the dictionary I wrote by hand classified the grouping
key correctly, and `infer_schema` did not.

Expected values come from an independent DuckDB query over the same file,
computed inside each test. Restating them as constants would only pin what
the engine already does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest
from tests.corpus.runner import RemoteFakeProvider
from tests.fixtures.retail_weekly import BRANCHES, ROWS, write_csv

from agentic_analytics.config import Settings
from agentic_analytics.graph.runner import RunResult, run_analysis
from agentic_analytics.llm.fake import FakeProvider
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.warehouse.session import SessionManager, open_upload_session


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_csv(tmp_path_factory.mktemp("real-shape"))


@pytest.fixture(scope="module")
def oracle(dataset: Path) -> duckdb.DuckDBPyConnection:
    """DuckDB reading the same file, with no engine code involved."""
    connection = duckdb.connect()
    connection.execute(
        f"CREATE TABLE t AS SELECT * FROM read_csv_auto('{dataset}', dateformat='%d-%m-%Y')"
    )
    return connection


def _run(dataset: Path, question: str, *, remote: bool = False) -> RunResult:
    import asyncio

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
            server = build_server(manager, settings)
            return await run_analysis(
                question, session, server, settings=settings, provider=provider
            )
        finally:
            await provider.aclose()
            manager.close_all()

    return asyncio.run(go())


def _answer(result: RunResult) -> str:
    assert result.published, f"nothing published: {result.stopped_reason}"
    return result.published[0].text


def _aggregate(result: RunResult) -> Any:
    return next(
        snapshot
        for snapshot in result.results.values()
        if snapshot.tool_name == "aggregate_for_question"
    )


def test_the_grouping_key_is_not_classified_as_a_measure(dataset: Path) -> None:
    """The production defect, at the point it entered the system."""
    from agentic_analytics.analytics.semantic import infer_schema

    manager = SessionManager()
    try:
        session = manager.add(open_upload_session(dataset, dataset.name, "csv"))
        schema = infer_schema(session, "uploaded_data")
    finally:
        manager.close_all()

    assert "Branch_No" in schema.dimensions
    assert "Branch_No" not in schema.measures
    assert "Weekly_Revenue" in schema.measures
    assert "Promo_Flag" in schema.dimensions
    assert "Trading_Date" in schema.time_fields


def test_a_breakdown_returns_every_group_and_says_so(
    dataset: Path, oracle: duckdb.DuckDBPyConnection
) -> None:
    """45 groups, past the old 25-group limit, reported as complete."""
    expected = {
        int(branch): round(float(total), 2)
        for branch, total in oracle.execute(
            "SELECT Branch_No, sum(Weekly_Revenue) FROM t GROUP BY 1"
        ).fetchall()
    }
    assert len(expected) == BRANCHES

    result = _run(dataset, "What is the total Weekly_Revenue by Branch_No?")
    snapshot = _aggregate(result)

    assert len(snapshot.rows) == BRANCHES
    coverage = snapshot.group_coverage
    assert coverage is not None
    assert coverage.complete
    assert coverage.groups_returned == BRANCHES
    assert coverage.groups_total == BRANCHES
    assert coverage.rows_matching == ROWS
    assert coverage.rows_represented == ROWS
    assert coverage.ordering == "dimension"

    returned = {int(row[0]): round(float(row[1]), 2) for row in snapshot.rows}
    assert returned == expected


def test_the_answer_never_truncates_a_value(dataset: Path) -> None:
    """`301,397,792.46` must never become `301,397`.

    A hard slice at 400 characters cut the engine's own complete answer
    mid-number, numeric verification rejected it for stating a value not in
    its results, and a two-group summary was published instead.
    """
    import re

    result = _run(dataset, "What is the total Weekly_Revenue by Branch_No?")
    text = _answer(result)

    # Every number in the answer resolves against the cited result.
    snapshot = _aggregate(result)
    cells = {round(float(row[1]), 2) for row in snapshot.rows}
    labels = {float(row[0]) for row in snapshot.rows}
    counts = {float(row[2]) for row in snapshot.rows}
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        value = float(token.replace(",", ""))
        assert value in cells or value in labels or value in counts, (
            f"{token} in the answer is not a value the cited result holds"
        )
    assert not text.rstrip().endswith(",")


def test_a_ranking_claims_only_the_end_it_was_asked_for(
    dataset: Path, oracle: duckdb.DuckDBPyConnection
) -> None:
    top = oracle.execute(
        "SELECT Branch_No, round(sum(Weekly_Revenue), 2) FROM t GROUP BY 1 ORDER BY 2 DESC LIMIT 1"
    ).fetchone()
    bottom = oracle.execute(
        "SELECT Branch_No, round(sum(Weekly_Revenue), 2) FROM t GROUP BY 1 ORDER BY 2 ASC LIMIT 1"
    ).fetchone()
    assert top and bottom and top[0] != bottom[0]

    text = _answer(_run(dataset, "Which Branch_No had the highest total Weekly_Revenue?"))

    assert "highest" in text.lower()
    assert str(top[0]) in text
    # The ten-row ranking cannot support a claim about the other end, and
    # said "39 the lowest" when 39 was the tenth highest.
    assert "lowest" not in text.lower()
    assert f"{bottom[1]:,.2f}" not in text


def test_a_trend_publishes_a_chronological_series(
    dataset: Path, oracle: duckdb.DuckDBPyConnection
) -> None:
    """A correct 33-point series used to publish an empty report."""
    expected = oracle.execute(
        "SELECT strftime(date_trunc('month', Trading_Date), '%Y-%m'), "
        "round(sum(Weekly_Revenue), 2) FROM t GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert len(expected) > 3

    result = _run(dataset, "Show the monthly trend of Weekly_Revenue")
    snapshot = _aggregate(result)
    periods = [str(row[0]) for row in snapshot.rows]

    assert periods == sorted(periods), "a trend must be chronological"
    assert periods == [str(period) for period, _ in expected]
    assert result.published, "a computed trend must publish its own answer"
    assert expected[0][0] in _answer(result)


def test_a_filtered_total_matches_the_oracle(
    dataset: Path, oracle: duckdb.DuckDBPyConnection
) -> None:
    expected = oracle.execute(
        "SELECT round(sum(Weekly_Revenue), 2) FROM t WHERE Promo_Flag = 1"
    ).fetchone()
    assert expected

    text = _answer(_run(dataset, "What is the total Weekly_Revenue where Promo_Flag is 1?"))
    assert f"{expected[0]:,.2f}" in text or f"{expected[0]:,.0f}" in text


def test_a_breakdown_by_the_binary_flag_covers_both_values(
    dataset: Path, oracle: duckdb.DuckDBPyConnection
) -> None:
    expected = {
        int(flag): round(float(total), 2)
        for flag, total in oracle.execute(
            "SELECT Promo_Flag, sum(Weekly_Revenue) FROM t GROUP BY 1"
        ).fetchall()
    }
    assert set(expected) == {0, 1}

    snapshot = _aggregate(_run(dataset, "What is the total Weekly_Revenue by Promo_Flag?"))
    assert {int(row[0]): round(float(row[1]), 2) for row in snapshot.rows} == expected
    assert snapshot.group_coverage is not None
    assert snapshot.group_coverage.complete


def test_a_missing_measure_is_refused_not_substituted(dataset: Path) -> None:
    """The grouping key is numeric, so averaging it would "work"."""
    result = _run(dataset, "What is the average gross_margin by Branch_No?")

    assert result.outcome == "refused"
    assert not result.published
    assert "gross_margin" in (result.stopped_reason or "")


def test_the_two_provider_modes_agree_on_this_dataset(dataset: Path) -> None:
    question = "What is the total Weekly_Revenue by Branch_No?"
    local = _run(dataset, question)
    remote = _run(dataset, question, remote=True)

    assert local.outcome == remote.outcome
    assert len(local.published) == len(remote.published)
    assert _answer(local) == _answer(remote)


def test_filter_words_do_not_decide_the_analysis(dataset: Path) -> None:
    """A clause a filter owns must not also choose the operation.

    "with Avg_Temp_C at least 50" contains "least", the ranking pattern
    matched it, the run then needed a grouping it was never given, and a
    plain filtered total was refused.
    """
    result = _run(dataset, "What is the total Weekly_Revenue with Avg_Temp_C at least 50?")

    assert result.outcome == "completed", result.stopped_reason
    contract = result.query_contract or {}
    assert contract.get("operation") == "sum"
    assert contract.get("dimension") is None
    assert [f["column"] for f in contract.get("filters") or []] == ["Avg_Temp_C"]


def test_a_filtered_total_is_not_grouped_by_the_filtered_column(dataset: Path) -> None:
    """Restricting to one value of a column is not a breakdown of it."""
    result = _run(dataset, "What is the total Weekly_Revenue where Promo_Flag is 1?")

    contract = result.query_contract or {}
    assert contract.get("dimension") is None
    assert [f["column"] for f in contract.get("filters") or []] == ["Promo_Flag"]
