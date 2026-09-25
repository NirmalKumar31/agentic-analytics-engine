"""A test that cannot be computed must be refused, not approximated.

Two specific ways a statistic can be meaningless while looking fine:

* a two-proportion z-test run on a column that is numeric but not an
  indicator, where `SUM(value)` is read as a count of successes and quietly
  is not one;
* a Welch t-test whose standard error is zero, where the statistic is
  infinity or NaN rather than "very significant".

Both produce a number, and a number that reaches a finding reaches a report.
"""

from __future__ import annotations

import math
from pathlib import Path

import duckdb
import pytest

from agentic_analytics.analytics.stats import StatsError, statistical_test
from agentic_analytics.warehouse.session import AnalysisSession, TableInfo


def _session(rows: list[tuple[str, float]], column: str = "flag") -> AnalysisSession:
    """A tiny in-memory session holding one two-column table."""
    con = duckdb.connect(":memory:")
    con.execute(f'CREATE TABLE t (grp VARCHAR, "{column}" DOUBLE)')
    con.executemany("INSERT INTO t VALUES (?, ?)", rows)
    info = TableInfo(
        name="t",
        row_count=len(rows),
        columns=[{"name": "grp", "type": "VARCHAR"}, {"name": column, "type": "DOUBLE"}],
    )
    return AnalysisSession(
        session_id="ses_test",
        kind="upload",
        con=con,
        tables={"t": info},
        fingerprint="sha256:test",
        registry=None,
        source_label="test",
    )


def _binary_rows(n: int = 60) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    for index in range(n):
        rows.append(("a", 1.0 if index % 2 == 0 else 0.0))
        rows.append(("b", 1.0 if index % 5 == 0 else 0.0))
    return rows


def _run(session: AnalysisSession, test: str, **variables: object) -> object:
    return statistical_test(
        session,
        test_type=test,  # type: ignore[arg-type]
        variables={"table": "t", "group_column": "grp", **variables},
    )


# ------------------------------------------------- two-proportion z-test
def test_a_zero_one_column_is_accepted() -> None:
    session = _session(_binary_rows())
    try:
        snapshot = _run(session, "two_proportion_z", value_column="flag")
    finally:
        session.close()
    assert snapshot.statistical_result is not None  # type: ignore[attr-defined]
    assert snapshot.statistical_result.test_name == "two-proportion z-test"  # type: ignore[attr-defined]


def test_a_boolean_column_is_accepted() -> None:
    """Booleans cast to 0.0/1.0, which is the same indicator."""
    rows = [(g, float(v)) for g, v in [("a", True), ("b", False)] * 40]
    session = _session(rows)
    try:
        snapshot = _run(session, "two_proportion_z", value_column="flag")
    finally:
        session.close()
    assert snapshot.statistical_result is not None  # type: ignore[attr-defined]


@pytest.mark.parametrize("stray", [2.0, 5.0, -1.0, 0.5, 100.0])
def test_a_non_binary_value_is_refused(stray: float) -> None:
    """The failure this closes: SUM of a non-indicator read as successes.

    One stray value is enough. With 2s in the column the "rate" can exceed
    1, and every figure downstream -- the z statistic, the p-value, the
    confidence interval -- is computed from a quantity that is not a
    proportion.
    """
    rows = _binary_rows()
    rows[0] = ("a", stray)
    session = _session(rows)
    try:
        with pytest.raises(StatsError, match="0/1 or boolean"):
            _run(session, "two_proportion_z", value_column="flag")
    finally:
        session.close()


def test_the_refusal_names_the_column_and_shows_a_value() -> None:
    """A refusal a user cannot act on is only half useful."""
    rows = _binary_rows()
    rows[0] = ("a", 7.0)
    session = _session(rows)
    try:
        with pytest.raises(StatsError) as excinfo:
            _run(session, "two_proportion_z", value_column="flag")
    finally:
        session.close()
    message = str(excinfo.value)
    assert "flag" in message
    assert "7" in message


def test_nulls_do_not_make_a_binary_column_look_non_binary() -> None:
    session = _session([*_binary_rows(), ("a", None), ("b", None)])  # type: ignore[list-item]
    try:
        snapshot = _run(session, "two_proportion_z", value_column="flag")
    finally:
        session.close()
    assert snapshot.statistical_result is not None  # type: ignore[attr-defined]


# --------------------------------------------------------- Welch's t-test
def test_a_normal_welch_comparison_works() -> None:
    rows = [("a", float(i % 7)) for i in range(60)] + [("b", float(i % 11) + 2) for i in range(60)]
    session = _session(rows, column="amount")
    try:
        snapshot = _run(session, "welch_t_test", value_column="amount")
    finally:
        session.close()
    result = snapshot.statistical_result  # type: ignore[attr-defined]
    assert result is not None
    assert math.isfinite(result.statistic)
    assert math.isfinite(result.p_value)
    assert result.confidence_interval is not None
    assert all(math.isfinite(b) for b in result.confidence_interval)


def test_one_constant_group_is_still_a_defined_test() -> None:
    """Zero variance in one group does not make Welch's t undefined.

    The old docstring implied such a group was rejected. It is not, and it
    should not be: the statistic, the p-value and the interval are all
    finite, so the result is real and is returned.
    """
    rows = [("a", 5.0) for _ in range(40)] + [("b", float(i % 9)) for i in range(40)]
    session = _session(rows, column="amount")
    try:
        snapshot = _run(session, "welch_t_test", value_column="amount")
    finally:
        session.close()
    result = snapshot.statistical_result  # type: ignore[attr-defined]
    assert result is not None
    assert math.isfinite(result.statistic)
    assert math.isfinite(result.p_value)
    assert result.confidence_interval is not None
    assert all(math.isfinite(b) for b in result.confidence_interval)


def test_two_constant_groups_are_refused() -> None:
    """No sampling variability at all: the statistic is undefined.

    Returning infinity here would read as overwhelming significance.
    """
    rows = [("a", 5.0) for _ in range(40)] + [("b", 9.0) for _ in range(40)]
    session = _session(rows, column="amount")
    try:
        with pytest.raises(StatsError, match="undefined"):
            _run(session, "welch_t_test", value_column="amount")
    finally:
        session.close()


def test_two_identical_constant_groups_are_refused() -> None:
    rows = [("a", 5.0) for _ in range(40)] + [("b", 5.0) for _ in range(40)]
    session = _session(rows, column="amount")
    try:
        with pytest.raises(StatsError, match="undefined"):
            _run(session, "welch_t_test", value_column="amount")
    finally:
        session.close()


def test_no_statistical_result_carries_a_non_finite_number(warehouse_dir: Path) -> None:
    """Whatever the test, nothing non-finite may reach a snapshot."""
    from agentic_analytics.warehouse.session import open_demo_session

    session = open_demo_session(warehouse_dir)
    try:
        snapshot = statistical_test(
            session,
            test_type="two_proportion_z",
            variables={
                "model": "customer_lifecycle",
                "group_column": "first_delivery_status",
                "value_column": "is_repeat",
            },
        )
    finally:
        session.close()
    result = snapshot.statistical_result
    assert result is not None
    assert math.isfinite(result.statistic)
    assert math.isfinite(result.p_value)
    assert result.effect_size is not None and math.isfinite(result.effect_size)
    assert result.confidence_interval is not None
    assert all(math.isfinite(b) for b in result.confidence_interval)
