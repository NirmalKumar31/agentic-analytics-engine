"""A query that matched nothing is not a query that failed.

`_matched_no_rows` decides whether the engine ran the accepted contract and
the data held nothing matching it. Getting it wrong in either direction is
worse than the defect it fixes:

  - Too eager, and a real answer is reported as "no rows", which silently
    withholds a number the reader asked for.
  - Too cautious, and an empty result keeps being reported as an execution
    failure, which tells the reader the system broke when it worked.

So the cases here are mostly about the boundary: a legitimate aggregate
whose value happens to be zero, and a snapshot shaped in a way the helper
was not expecting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentic_analytics.graph.build import (
    _aggregate_has_no_values,
    _matched_no_rows,
    _no_rows_limitation,
    _no_values_limitation,
)


@dataclass
class _Snapshot:
    """Only the two attributes the helper reads."""

    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)


def test_a_grouped_query_with_no_matching_rows_returns_nothing() -> None:
    # GROUP BY can only emit groups that exist, so nothing matching means no
    # output rows at all.
    assert _matched_no_rows(_Snapshot(columns=["region", "total", "row_count"], rows=[]))


def test_an_ungrouped_query_with_no_matching_rows_returns_one_empty_row() -> None:
    # `SELECT sum(x) ... WHERE false` still returns a row: a NULL measure and
    # a row count of zero. Testing `rows` alone missed this, and the ungrouped
    # case kept being reported as a failure after the grouped one was fixed.
    assert _matched_no_rows(_Snapshot(columns=["total_revenue", "row_count"], rows=[[None, 0]]))


def test_an_aggregate_that_legitimately_sums_to_zero_is_an_answer() -> None:
    # The boundary that matters. Rows existed and their measure summed to
    # zero, which is a number the reader asked for.
    assert not _matched_no_rows(_Snapshot(columns=["total_revenue", "row_count"], rows=[[0, 17]]))


def test_a_group_with_rows_is_an_answer_even_beside_an_empty_one() -> None:
    # Defensive: if any group carries rows, the query matched something.
    assert not _matched_no_rows(
        _Snapshot(
            columns=["region", "total", "row_count"],
            rows=[["North", 10, 3], ["South", None, 0]],
        )
    )


def test_a_snapshot_without_a_row_count_column_is_left_alone() -> None:
    # Conservative on anything unexpected: reporting a real answer as "no
    # rows" is a worse error than the one this fixes.
    assert not _matched_no_rows(_Snapshot(columns=["total_revenue"], rows=[[None]]))


def test_matching_rows_with_only_null_measure_values_are_not_called_no_rows() -> None:
    snapshot = _Snapshot(
        columns=["total_revenue", "row_count", "value_count"], rows=[[None, 17, 0]]
    )

    assert not _matched_no_rows(snapshot)
    assert _aggregate_has_no_values(snapshot)


def test_a_zero_aggregate_with_values_is_not_empty() -> None:
    snapshot = _Snapshot(columns=["total_revenue", "row_count", "value_count"], rows=[[0, 17, 17]])

    assert not _aggregate_has_no_values(snapshot)


def test_a_malformed_row_is_left_alone() -> None:
    assert not _matched_no_rows(_Snapshot(columns=["total", "row_count"], rows=[["x"]]))
    assert not _matched_no_rows(
        _Snapshot(columns=["total", "row_count"], rows=[[1, "not a number"]])
    )


@dataclass
class _Filter:
    column: str
    operator: str
    value: Any


@dataclass
class _Mapping:
    filters: tuple[Any, ...] = ()
    period: tuple[str, str] | None = None
    period_field: str | None = None
    measure: str | None = "revenue"


def test_the_explanation_names_the_filters_that_produced_it() -> None:
    # "No rows matched" alone sends a reader looking for a fault in their
    # data. Naming the predicate tells them where to look.
    text = _no_rows_limitation(_Mapping(filters=(_Filter("region", "=", "Atlantis"),)))
    assert "region = Atlantis" in text
    assert "The analysis ran" in text


def test_the_explanation_names_a_period_that_matched_nothing() -> None:
    text = _no_rows_limitation(
        _Mapping(period=("2019-01-01", "2019-12-31"), period_field="order_date")
    )
    assert "order_date between 2019-01-01 and 2019-12-31" in text


def test_the_explanation_covers_both_a_filter_and_a_period() -> None:
    text = _no_rows_limitation(
        _Mapping(
            filters=(_Filter("units", ">", 99),),
            period=("2019-01-01", "2019-12-31"),
            period_field="order_date",
        )
    )
    assert "units > 99" in text
    assert "order_date between" in text


def test_an_empty_table_is_explained_without_blaming_a_filter() -> None:
    # Reachable: a query over a table that holds nothing to aggregate has no
    # restriction to name, and inventing one would be a lie.
    text = _no_rows_limitation(_Mapping())
    assert "No rows were available" in text
    assert "filters" not in text


def test_all_null_values_name_the_measure_and_matching_population() -> None:
    text = _no_values_limitation(
        _Mapping(measure="revenue"),
        _Snapshot(columns=["total_revenue", "row_count", "value_count"], rows=[[None, 17, 0]]),
    )

    assert "revenue had no non-null values" in text
    assert "17 matching rows" in text
    assert "No aggregate was published" in text
