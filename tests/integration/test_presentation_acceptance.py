"""The presentation contract through the real path, against real oracles.

Every case uploads a committed fixture, lets `infer_schema` profile it, runs
the real planner, SQL compiler, DuckDB execution and verification, and only
then builds the presentation. Nothing here hands the engine a hand-written
schema: the boolean and ordered-numeric rules depend on profiling evidence,
so testing them against a dictionary I wrote myself would prove nothing
about what ships.

Expected values are computed inside each test by an independent DuckDB
query over the same file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import duckdb
import pytest
from tests.fixtures.retail_monthly import write_csv as write_retail
from tests.fixtures.sleep_study import write_csv as write_sleep

from agentic_analytics.analytics.semantic import infer_schema
from agentic_analytics.config import Settings
from agentic_analytics.graph.runner import RunResult, run_analysis
from agentic_analytics.llm.fake import FakeProvider
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.presentation import (
    PresentationShape,
    SemanticKind,
    numbers_resolve,
)
from agentic_analytics.warehouse.session import SessionManager, open_upload_session


@pytest.fixture(scope="module")
def sleep(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_sleep(tmp_path_factory.mktemp("sleep"))


@pytest.fixture(scope="module")
def retail(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_retail(tmp_path_factory.mktemp("retail"))


def _oracle(dataset: Path, *, dates: bool = False) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    options = ", dateformat='%d-%m-%Y'" if dates else ""
    connection.execute(f"CREATE TABLE t AS SELECT * FROM read_csv_auto('{dataset}'{options})")
    return connection


@pytest.fixture(scope="module")
def sleep_oracle(sleep: Path) -> duckdb.DuckDBPyConnection:
    return _oracle(sleep)


@pytest.fixture(scope="module")
def retail_oracle(retail: Path) -> duckdb.DuckDBPyConnection:
    return _oracle(retail, dates=True)


def _present(dataset: Path, question: str) -> tuple[Any, RunResult]:
    """Run the real pipeline, then build the presentation from its output."""

    async def go() -> tuple[Any, RunResult]:
        manager = SessionManager()
        settings = Settings(
            upload_dir=dataset.parent / "uploads",
            live_analytics_enabled=True,
            uploads_enabled=True,
            provider_mode="fake",
            log_json=False,
        )
        provider = FakeProvider()
        try:
            session = manager.add(open_upload_session(dataset, dataset.name, "csv"))
            server = build_server(manager, settings)
            result = await run_analysis(
                question, session, server, settings=settings, provider=provider
            )
            schema = infer_schema(session, "uploaded_data")
        finally:
            await provider.aclose()
            manager.close_all()

        # The run's own presentation, not one rebuilt here. Rebuilding it
        # with a schema this test fetched itself hid a real defect: the
        # graph carries the profile as a dict, the builder read it with
        # `getattr`, found nothing, and a flag came back out as a bare 0
        # and 1 -- while this test, handing over the object, passed.
        assert result.presentation is not None, result.stopped_reason
        del schema
        return result.presentation, result

    return asyncio.run(go())


def _check_prose(presentation: Any, snapshot: Any) -> None:
    """Every figure in the published prose is one the engine recorded."""
    derived = [
        h.delta.raw_value
        for h in presentation.highlights
        if h.delta is not None and h.delta.raw_value is not None
    ]
    from decimal import Decimal

    for text in (presentation.headline, presentation.secondary_summary or ""):
        unresolved = numbers_resolve(
            text,
            snapshot,
            presentation.scope,
            derived=[Decimal(str(d)) for d in derived],
        )
        assert unresolved == [], f"{unresolved} in {text!r} is not a recorded figure"


# ───────────────────────────────── boolean two-group comparison


def test_a_flag_breakdown_names_its_states_and_not_zero_and_one(
    sleep: Path, sleep_oracle: duckdb.DuckDBPyConnection
) -> None:
    expected = {
        int(flag): (round(float(mean), 2), int(n))
        for flag, mean, n in sleep_oracle.execute(
            "SELECT blue_light_filter_active, avg(total_sleep_hours), count(*) "
            "FROM t GROUP BY 1 ORDER BY 1"
        ).fetchall()
    }
    assert expected == {0: (6.21, 4524), 1: (6.33, 3976)}

    presentation, result = _present(
        sleep, "What is the average total_sleep_hours by blue_light_filter_active?"
    )
    assert presentation.shape is PresentationShape.BOOLEAN_COMPARISON

    snapshot = next(s for s in result.results.values() if s.tool_name == "aggregate_for_question")
    returned = {int(row[0]): round(float(row[1]), 2) for row in snapshot.rows}
    assert returned == {0: 6.21, 1: 6.33}

    # The states are named. A bare 0 or 1 must not stand in for them.
    assert "On" in presentation.headline
    assert "Off" in presentation.headline
    flag = next(
        f for f in presentation.display_fields if f.source_name == "blue_light_filter_active"
    )
    assert flag.semantic_kind is SemanticKind.BOOLEAN
    assert flag.boolean_labels is not None

    # Both measured values, and a descriptive difference only.
    assert "6.33" in presentation.headline and "6.21" in presentation.headline
    highlight = presentation.highlights[0]
    assert highlight.delta is not None and highlight.delta.formatted_value == "0.12"
    combined = f"{presentation.headline} {presentation.secondary_summary}".lower()
    assert "significant" not in combined
    _check_prose(presentation, snapshot)


# ───────────────────────────────── three nominal categories


def test_three_categories_name_the_ends_without_enumerating_them(
    sleep: Path, sleep_oracle: duckdb.DuckDBPyConnection
) -> None:
    expected = {
        str(name): round(float(mean), 2)
        for name, mean in sleep_oracle.execute(
            "SELECT chronotype, avg(total_sleep_hours) FROM t GROUP BY 1"
        ).fetchall()
    }
    assert expected == {"Intermediate": 6.44, "Morning Lark": 7.38, "Night Owl": 5.01}

    presentation, result = _present(sleep, "What is the average total_sleep_hours by chronotype?")
    assert presentation.shape is PresentationShape.CATEGORICAL_BREAKDOWN
    assert "Morning Lark" in presentation.headline
    assert "7.38" in presentation.headline
    assert "5.01" in presentation.headline
    # The failure this replaces: a semicolon run repeating the table.
    assert presentation.headline.count(";") <= 1
    assert "further group" not in presentation.headline.lower()

    snapshot = next(s for s in result.results.values() if s.tool_name == "aggregate_for_question")
    _check_prose(presentation, snapshot)


# ───────────────────────────────── 48-value ordered numeric dimension


def test_an_age_breakdown_is_ordered_numeric_and_states_a_range(
    sleep: Path, sleep_oracle: duckdb.DuckDBPyConnection
) -> None:
    ages = sleep_oracle.execute(
        "SELECT age, round(avg(deep_sleep_pct), 2) m FROM t GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert len(ages) == 48
    highest = max(ages, key=lambda row: row[1])
    lowest = min(ages, key=lambda row: row[1])
    assert (int(highest[0]), float(highest[1])) == (54, 22.45)
    assert (int(lowest[0]), float(lowest[1])) == (64, 21.18)

    presentation, result = _present(sleep, "What is the average deep_sleep_pct by age?")
    assert presentation.shape is PresentationShape.ORDERED_NUMERIC_SERIES

    age = next(f for f in presentation.display_fields if f.source_name == "age")
    assert age.ordered, "an age axis is a sequence, not a set of labels"
    assert age.semantic_kind is SemanticKind.ORDERED_NUMERIC

    assert "highest" in presentation.headline.lower()
    assert "22.45" in presentation.headline and "21.18" in presentation.headline
    assert "descriptive" in (presentation.secondary_summary or "").lower()
    assert "no trend" in (presentation.secondary_summary or "").lower()

    snapshot = next(s for s in result.results.values() if s.tool_name == "aggregate_for_question")
    _check_prose(presentation, snapshot)


# ───────────────────────────────── 45-category complete breakdown


def test_a_45_store_breakdown_is_complete_and_claims_both_ends(
    retail: Path, retail_oracle: duckdb.DuckDBPyConnection
) -> None:
    totals = retail_oracle.execute(
        "SELECT Store, round(sum(Weekly_Revenue), 2) s FROM t GROUP BY 1 ORDER BY s DESC"
    ).fetchall()
    assert len(totals) == 45
    assert (int(totals[0][0]), float(totals[0][1])) == (20, 301397792.46)
    assert (int(totals[-1][0]), float(totals[-1][1])) == (33, 37160221.96)

    presentation, result = _present(retail, "What is the total Weekly_Revenue by Store?")
    snapshot = next(s for s in result.results.values() if s.tool_name == "aggregate_for_question")
    assert len(snapshot.rows) == 45
    coverage = snapshot.group_coverage
    assert coverage is not None and coverage.complete

    # Complete coverage, so both ends may be claimed.
    assert "highest" in presentation.headline.lower()
    assert "301,397,792.46" in presentation.headline
    assert "37,160,221.96" in presentation.headline
    # And never as an enumeration.
    assert presentation.headline.count(";") <= 1
    assert "further group" not in presentation.headline.lower()
    assert presentation.scope.groups_returned == 45
    _check_prose(presentation, snapshot)


# ───────────────────────────────── 33-period monthly series


def test_a_monthly_series_covers_every_period_without_listing_them(
    retail: Path, retail_oracle: duckdb.DuckDBPyConnection
) -> None:
    periods = retail_oracle.execute(
        "SELECT strftime(Trading_Date, '%Y-%m') p, round(sum(Weekly_Revenue), 2) s "
        "FROM t GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert len(periods) == 33

    presentation, result = _present(retail, "Show the monthly trend of Weekly_Revenue")
    assert presentation.shape is PresentationShape.TIME_SERIES
    snapshot = next(s for s in result.results.values() if s.tool_name == "aggregate_for_question")
    assert len(snapshot.rows) == 33

    # The periods belong in the chart and the table, never in the sentence.
    assert presentation.headline.count(";") <= 1
    assert "further period" not in presentation.headline.lower()
    _check_prose(presentation, snapshot)


# ───────────────────────────────── refusal


def test_a_missing_column_presents_a_refusal_not_an_empty_report(
    retail: Path,
) -> None:
    presentation, _ = _present(retail, "What is the average gross_margin by Store?")
    assert presentation.shape is PresentationShape.REFUSAL
    assert presentation.headline
    assert presentation.highlights == []
    assert presentation.table is None
    assert any(c.severity.value == "blocking" for c in presentation.caveats)


# ───────────────────────────────── cross-cutting invariants


@pytest.mark.parametrize(
    "question",
    [
        "What is the average total_sleep_hours by blue_light_filter_active?",
        "What is the average total_sleep_hours by chronotype?",
        "What is the average deep_sleep_pct by age?",
    ],
)
def test_no_presentation_exposes_an_engine_alias_as_the_subject(sleep: Path, question: str) -> None:
    """The reader asked about their own columns, not the engine's output."""
    presentation, _ = _present(sleep, question)
    for alias_marker in ("avg_", "total_", "_sum", "row_count"):
        assert alias_marker not in presentation.headline, (
            f"{alias_marker!r} is an engine alias and should not appear in prose"
        )


@pytest.mark.parametrize(
    "question",
    [
        "What is the average total_sleep_hours by blue_light_filter_active?",
        "What is the average deep_sleep_pct by age?",
    ],
)
def test_every_highlight_points_at_a_cell_that_holds_its_value(sleep: Path, question: str) -> None:
    presentation, result = _present(sleep, question)
    snapshot = next(s for s in result.results.values() if s.tool_name == "aggregate_for_question")
    assert presentation.highlights
    for highlight in presentation.highlights:
        assert highlight.evidence_cells
        for cell in highlight.evidence_cells:
            assert cell.result_id == snapshot.result_id
            assert snapshot.cell(cell.row, cell.column) == cell.value


def test_the_published_chart_specification_still_carries_no_rows(
    retail: Path,
) -> None:
    """The browser resolves `result_id`; the rows stay in the result once."""
    presentation, _ = _present(retail, "What is the total Weekly_Revenue by Store?")
    chart = presentation.chart
    assert chart is not None
    if chart.spec is not None:
        assert "data" not in chart.spec


# ───────────────────────── a completed run this contract cannot describe
#
# The presentation is built around an `aggregate_for_question` snapshot. The
# demo warehouse resolves through the metric registry and never produces one,
# while still setting `query_mapping` -- so the guard in `_presentation_for`
# let it through and the builder's own "no snapshot" branch returned a
# FAILURE presentation.
#
# Every demo run that published a verified finding was rendered as "The
# analysis could not be completed.", with the finding it had just verified
# replaced by that sentence. That is the first path a visitor takes.


def test_a_completed_run_without_a_snapshot_has_no_presentation() -> None:
    """Falling back to the findings report is right; claiming failure is not."""
    from agentic_analytics.graph.runner import RunResult, _presentation_for

    result = RunResult(
        run_id="run_1",
        question="What is total revenue by region?",
        session_id="ses_1",
        dataset={},
        report=None,
        outcome="completed",
    )
    # A mapping, no snapshot: the registry path.
    assert _presentation_for(result, {"query_mapping": object()}) is None


def test_a_refusal_without_a_snapshot_still_gets_its_presentation() -> None:
    """The scoping that matters. A refusal has no snapshot either, and its
    presentation is the useful one: it carries the reason."""
    from agentic_analytics.graph.runner import RunResult, _presentation_for

    result = RunResult(
        run_id="run_2",
        question="What is the total gross margin by region?",
        session_id="ses_1",
        dataset={},
        report=None,
        outcome="refused",
        stopped_reason="the question could not be mapped safely",
    )

    # A refusal carries a mapping: the resolver produced one and marked it
    # unconfident. With neither a mapping nor a snapshot the older guard
    # already returns None, which is a different path.
    class _Unconfident:
        confident = False
        explanation = "the question could not be mapped safely"

    presentation = _presentation_for(result, {"query_mapping": _Unconfident()})
    assert presentation is not None
    assert presentation.shape.value == "refusal"
    assert "could not be mapped safely" in presentation.headline


def test_a_failure_without_a_snapshot_still_gets_its_presentation() -> None:
    from agentic_analytics.graph.runner import RunResult, _presentation_for

    result = RunResult(
        run_id="run_3",
        question="q",
        session_id="ses_1",
        dataset={},
        report=None,
        outcome="failed",
        stopped_reason="the accepted contract could not be executed",
    )

    class _Mapping:
        confident = True

    presentation = _presentation_for(result, {"query_mapping": _Mapping()})
    assert presentation is not None
    assert presentation.shape.value == "failure"
    assert "could not be executed" in presentation.headline
