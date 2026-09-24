"""The metric layer must produce correct SQL and reject invalid requests."""

from __future__ import annotations

import itertools

import pytest

from agentic_analytics.analytics.compute import (
    MetricError,
    analyze_timeseries,
    compare_segments,
    compute_metric,
)
from agentic_analytics.analytics.filters import Filter, FilterError, coerce_filters
from agentic_analytics.warehouse.metrics import grain_expression, load_registry
from agentic_analytics.warehouse.session import AnalysisSession


def test_registry_loads_and_every_metric_has_a_model() -> None:
    reg = load_registry()
    assert reg.metrics
    for name, metric in reg.metrics.items():
        assert metric.model in reg.models, name
        assert metric.description.strip(), name
        assert metric.sql.strip(), name


def test_every_metric_executes(session: AnalysisSession) -> None:
    """A definition that does not run is a definition that lies."""
    reg = load_registry()
    for name in reg.metrics:
        snapshot = compute_metric(session, name)
        assert snapshot.row_count == 1, name
        assert snapshot.columns == [name], name


def test_every_declared_dimension_is_usable(session: AnalysisSession) -> None:
    reg = load_registry()
    for name in reg.metrics:
        for dim in reg.dimensions_for(name):
            snapshot = compute_metric(session, name, dimensions=[dim])
            assert snapshot.columns == [dim, name], (name, dim)
            assert snapshot.row_count >= 1, (name, dim)


def test_gross_margin_is_a_ratio_of_sums_not_an_average(session: AnalysisSession) -> None:
    """Averaging per-row margins gives a different, wrong answer."""
    gm = compute_metric(session, "gross_margin_pct").rows[0][0]
    revenue = compute_metric(session, "revenue").rows[0][0]
    profit = compute_metric(session, "gross_profit").rows[0][0]
    assert isinstance(gm, float) and isinstance(revenue, float) and isinstance(profit, float)
    assert gm == pytest.approx(100.0 * profit / revenue, rel=1e-9)


def test_timeseries_change_columns_are_computed(session: AnalysisSession) -> None:
    snapshot = analyze_timeseries(session, "revenue", grain="quarter")
    assert snapshot.columns == ["period", "revenue", "prev_period", "change_abs", "change_pct"]
    rows = snapshot.rows
    assert rows[0][2] is None, "first period has no predecessor"
    for prev, current in itertools.pairwise(rows):
        assert current[2] == pytest.approx(prev[1])
        assert current[3] == pytest.approx(current[1] - prev[1], rel=1e-6)


def test_compare_segments_ranks_and_shares(session: AnalysisSession) -> None:
    snapshot = compare_segments(session, "revenue", "region")
    assert snapshot.columns == [
        "region",
        "revenue",
        "share_of_total_pct",
        "diff_vs_best",
        "rank_desc",
    ]
    ranks = [r[4] for r in snapshot.rows]
    assert ranks == sorted(ranks)
    shares = [r[2] for r in snapshot.rows]
    assert sum(s for s in shares if s is not None) == pytest.approx(100.0, abs=0.01)
    assert snapshot.rows[0][3] == pytest.approx(0.0)


def test_share_of_total_is_null_for_a_mixed_sign_metric(session: AnalysisSession) -> None:
    """A share of a total that straddles zero is not a meaningful number."""
    snapshot = compare_segments(session, "contribution_margin", "acquisition_channel")
    values = [r[1] for r in snapshot.rows]
    if min(values) < 0 < max(values):
        assert all(r[2] is None for r in snapshot.rows)


def test_segment_filter_restricts_results(session: AnalysisSession) -> None:
    snapshot = compare_segments(session, "revenue", "region", segments=["West", "Midwest"])
    assert {r[0] for r in snapshot.rows} == {"West", "Midwest"}


def test_unknown_metric_is_rejected(session: AnalysisSession) -> None:
    with pytest.raises(MetricError, match="unknown metric"):
        compute_metric(session, "profit_margin_maybe")


def test_unknown_dimension_is_rejected(session: AnalysisSession) -> None:
    with pytest.raises(MetricError, match="does not support dimension"):
        compute_metric(session, "revenue", dimensions=["carrier"])


def test_invalid_grain_is_rejected(session: AnalysisSession) -> None:
    with pytest.raises(ValueError, match="invalid time_grain"):
        grain_expression("order_date", "fortnight")


def test_grain_cannot_carry_sql(session: AnalysisSession) -> None:
    with pytest.raises(ValueError):
        compute_metric(session, "revenue", time_grain="month'); DROP TABLE orders --")


def test_too_many_dimensions_is_rejected(session: AnalysisSession) -> None:
    with pytest.raises(MetricError, match="at most"):
        compute_metric(
            session,
            "revenue",
            dimensions=["region", "category", "brand", "customer_segment"],
        )


def test_timeseries_rejects_a_foreign_time_column(session: AnalysisSession) -> None:
    with pytest.raises(MetricError, match="is measured over"):
        analyze_timeseries(session, "revenue", grain="month", time_column="signup_date")


def test_filters_are_parameterised_not_interpolated(session: AnalysisSession) -> None:
    """A filter value containing SQL is matched as a literal string."""
    snapshot = compute_metric(
        session,
        "revenue",
        filters=[Filter(column="region", op="=", value="West'; DROP TABLE orders --")],
    )
    assert snapshot.rows[0][0] in (None, 0, 0.0)
    still_there = compute_metric(session, "orders").rows[0][0]
    assert isinstance(still_there, int | float) and still_there > 0


def test_filter_on_unlisted_column_is_rejected(session: AnalysisSession) -> None:
    with pytest.raises(FilterError, match="cannot filter on"):
        compute_metric(session, "revenue", filters=[Filter(column="unit_cost", op=">", value=0)])


def test_coerce_filters_accepts_both_shapes() -> None:
    assert coerce_filters({"region": "West"})[0].column == "region"
    assert coerce_filters([{"column": "region", "op": "in", "value": ["West"]}])[0].op == "in"
    assert coerce_filters(None) == []
    with pytest.raises(FilterError):
        coerce_filters("region = West")
    with pytest.raises(FilterError):
        coerce_filters([{"col": "region"}])


def test_in_filter_value_limit() -> None:
    from agentic_analytics.analytics.filters import build_where

    with pytest.raises(FilterError, match="the limit is"):
        build_where(
            [Filter(column="region", op="in", value=list(range(200)))],
            allowed_columns={"region"},
        )


def test_between_filter_needs_two_values() -> None:
    from agentic_analytics.analytics.filters import build_where

    with pytest.raises(FilterError, match="exactly two values"):
        build_where(
            [Filter(column="order_date", op="between", value=["2025-01-01"])],
            allowed_columns={"order_date"},
        )


def test_upload_style_session_has_no_metric_layer(session: AnalysisSession) -> None:
    session.registry = None
    with pytest.raises(MetricError, match="no metric layer"):
        compute_metric(session, "revenue")
