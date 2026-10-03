"""Completeness at and beyond the group ceiling.

The production defect lived exactly here: a breakdown of 45 groups was cut
to 25 by the SQL's own LIMIT, `snapshot.truncated` stayed false because the
transport limit was never reached, and the report called the result the
complete breakdown of every row.

So the boundary is tested on both sides of the old cap and on both sides of
the new one, and the claim the report is entitled to make is derived from
counted values at each point.
"""

from __future__ import annotations

import asyncio
import csv
import io
from pathlib import Path
from typing import Any

import pytest

from agentic_analytics.analytics.upload_plan import GROUP_RESULT_MAX
from agentic_analytics.config import Settings
from agentic_analytics.graph.runner import RunResult, run_analysis
from agentic_analytics.llm.fake import FakeProvider
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.warehouse.session import SessionManager, open_upload_session

ROWS_PER_GROUP = 12


def _dataset(directory: Path, groups: int) -> Path:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Branch_No", "Net_Value"])
    for group in range(1, groups + 1):
        for row in range(ROWS_PER_GROUP):
            writer.writerow([group, round(100 + group * 1.5 + row, 2)])
    path = directory / f"groups_{groups}.csv"
    path.write_text(buffer.getvalue())
    return path


def _run(path: Path, question: str) -> RunResult:
    async def go() -> RunResult:
        manager = SessionManager()
        settings = Settings(
            upload_dir=path.parent / "uploads",
            live_analytics_enabled=True,
            uploads_enabled=True,
            provider_mode="fake",
            log_json=False,
        )
        provider = FakeProvider()
        try:
            session = manager.add(open_upload_session(path, path.name, "csv"))
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


def _coverage(result: RunResult) -> Any:
    snapshot = next(s for s in result.results.values() if s.tool_name == "aggregate_for_question")
    return snapshot, snapshot.group_coverage


@pytest.mark.parametrize("groups", [24, 25, 26, 60])
def test_a_breakdown_below_the_ceiling_is_complete(tmp_path: Path, groups: int) -> None:
    """24 and 25 passed before the fix; 26 and 60 did not."""
    path = _dataset(tmp_path, groups)
    snapshot, coverage = _coverage(_run(path, "What is the total Net_Value by Branch_No?"))

    assert len(snapshot.rows) == groups
    assert coverage is not None
    assert coverage.complete
    assert coverage.groups_returned == groups
    assert coverage.groups_total == groups
    assert coverage.rows_matching == groups * ROWS_PER_GROUP
    assert coverage.rows_represented == groups * ROWS_PER_GROUP
    # The limit that used to cut this silently is not in play at all.
    assert coverage.query_limit is None


def test_exactly_the_ceiling_is_still_complete(tmp_path: Path) -> None:
    path = _dataset(tmp_path, GROUP_RESULT_MAX)
    snapshot, coverage = _coverage(_run(path, "What is the total Net_Value by Branch_No?"))

    assert coverage is not None
    assert coverage.complete
    assert coverage.groups_returned == GROUP_RESULT_MAX
    assert len(snapshot.rows) == GROUP_RESULT_MAX


def test_one_group_past_the_ceiling_is_refused_with_an_actionable_reason(
    tmp_path: Path,
) -> None:
    """Past the ceiling the answer is a refusal, not a partial breakdown.

    This test previously asserted a partial result carrying its coverage
    counters, which was the honest version of the wrong behaviour: the
    missing groups may hold most of the population, so the first N groups
    are not an approximation of a breakdown but a different answer. The
    engine now counts the requested shape before executing it and declines.
    """
    groups = GROUP_RESULT_MAX + 1
    path = _dataset(tmp_path, groups)
    result = _run(path, "What is the total Net_Value by Branch_No?")

    assert result.outcome == "refused", result.outcome
    assert not result.published
    reason = result.stopped_reason or ""
    assert "result_shape_too_large" in reason, reason
    # The exact numbers, so a visitor can judge how much to narrow by.
    assert str(groups) in reason, reason
    assert str(GROUP_RESULT_MAX) in reason, reason
    # And at least one concrete way forward.
    assert any(
        hint in reason
        for hint in ("narrow the period", "add a row filter", "remove one grouping", "ranking")
    ), reason
    # No aggregate result was produced, so nothing can be cited as an answer.
    assert not [s for s in result.results.values() if s.tool_name == "aggregate_for_question"]


def test_the_refusal_is_reported_as_coverage_not_as_a_crash(tmp_path: Path) -> None:
    """An oversized request is a bounded engine declining, not a failure."""
    path = _dataset(tmp_path, GROUP_RESULT_MAX + 1)
    result = _run(path, "What is the total Net_Value by Branch_No?")

    coverage = result.question_coverage
    assert coverage is not None
    assert not coverage.complete
    assert "result_shape_too_large" in coverage.rejection_codes
    assert result.outcome == "refused"


def test_an_explicit_ranking_is_short_by_request_not_by_shortfall(tmp_path: Path) -> None:
    """A top-N list is the answer, so it is not a partial breakdown."""
    path = _dataset(tmp_path, 60)
    snapshot, coverage = _coverage(_run(path, "Which Branch_No had the highest total Net_Value?"))

    assert len(snapshot.rows) <= 10
    # Not a breakdown, so no coverage block claims completeness either way.
    assert coverage is None
    assert "ORDER BY 2 DESC" in (snapshot.sql or "")


def test_a_breakdown_is_not_ordered_by_its_measure(tmp_path: Path) -> None:
    """Ordering by the aggregate is what turned a breakdown into a top-list."""
    path = _dataset(tmp_path, 30)
    snapshot, _ = _coverage(_run(path, "What is the total Net_Value by Branch_No?"))

    labels = [int(row[0]) for row in snapshot.rows]
    assert labels == sorted(labels), "a breakdown is ordered by its dimension"


def test_a_null_grouping_value_is_a_group_and_is_counted(tmp_path: Path) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Region_Code", "Net_Value"])
    for index in range(60):
        label = "" if index % 10 == 0 else f"r{index % 5}"
        writer.writerow([label, round(10 + index, 2)])
    path = tmp_path / "nulls.csv"
    path.write_text(buffer.getvalue())

    _, coverage = _coverage(_run(path, "What is the total Net_Value by Region_Code?"))

    assert coverage is not None
    assert coverage.complete
    # Every source row is behind some group, including the empty label.
    assert coverage.rows_represented == coverage.rows_matching == 60
    assert coverage.groups_returned == coverage.groups_total


def test_null_measure_values_are_not_reported_as_observations(tmp_path: Path) -> None:
    """Population and effective aggregate sample size are different facts."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Region", "Net_Value"])
    writer.writerows(
        [
            ["north", 10],
            ["north", ""],
            ["north", 30],
            ["south", ""],
            ["south", 50],
        ]
    )
    path = tmp_path / "null_measures.csv"
    path.write_text(buffer.getvalue())

    result = _run(path, "What is the average Net_Value by Region?")
    snapshot, coverage = _coverage(result)

    assert snapshot.columns == [
        "region",
        "average_net_value",
        "row_count",
        "value_count",
    ]
    assert {row[0]: tuple(row[1:]) for row in snapshot.rows} == {
        "north": (20.0, 3, 2),
        "south": (50.0, 2, 1),
    }
    assert coverage is not None
    assert coverage.rows_matching == coverage.rows_represented == 5
    assert coverage.observations_matching == coverage.observations_represented == 3
    assert result.presentation is not None
    assert result.presentation.scope.rows_matching == 5
    assert result.presentation.scope.observations_matching == 3
    caveat = next(
        item for item in result.presentation.caveats if item.code == "missing_measure_values"
    )
    assert "2 matching rows" in caveat.message
    assert "excluded from the aggregate" in caveat.message


def test_an_all_null_filtered_measure_is_no_findings_not_a_failure(tmp_path: Path) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Region", "Net_Value"])
    writer.writerows(
        [
            ["north", ""],
            ["north", ""],
            ["south", 40],
            ["south", 60],
        ]
    )
    path = tmp_path / "all_null_subset.csv"
    path.write_text(buffer.getvalue())

    result = _run(path, "What is the average Net_Value where Region is north?")

    assert result.outcome == "completed"
    assert result.stopped_reason == ""
    assert not result.published
    assert result.report is not None
    assert result.report.limitations == [
        "The analysis ran, but Net_Value had no non-null values across 2 matching rows. "
        "No aggregate was published."
    ]


def test_trend_coverage_excludes_rows_without_a_time_axis(tmp_path: Path) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Date", "Net_Value"])
    writer.writerows(
        [
            ["2024-01-05", 10],
            ["2024-01-20", 20],
            ["", 999],
            ["2024-02-10", ""],
        ]
    )
    path = tmp_path / "null_trend_axis.csv"
    path.write_text(buffer.getvalue())

    result = _run(path, "Show the monthly trend of Net_Value")
    snapshot, coverage = _coverage(result)

    assert [row[:2] for row in snapshot.rows] == [
        ["2024-01", 30.0],
        ["2024-02", None],
    ]
    assert coverage is not None
    assert coverage.rows_matching == coverage.rows_represented == 3
    assert coverage.observations_matching == coverage.observations_represented == 2
