"""Statistical results must be computed, not asserted.

These tests check the arithmetic against independent references (SciPy called
directly, or a closed-form value) rather than against the implementation's own
output.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats as sps

from agentic_analytics.analytics.filters import Filter
from agentic_analytics.analytics.stats import (
    StatsError,
    correlation_matrix,
    statistical_test,
)
from agentic_analytics.warehouse.session import AnalysisSession


def test_two_proportion_z_matches_an_independent_calculation(
    session: AnalysisSession,
) -> None:
    snapshot = statistical_test(
        session,
        "two_proportion_z",
        {
            "model": "customer_lifecycle",
            "group_column": "first_delivery_status",
            "value_column": "is_repeat",
            "groups": ["late", "on_time"],
        },
    )
    result = snapshot.statistical_result
    assert result is not None

    (_, n_a, s_a, _), (_, n_b, s_b, _) = snapshot.rows
    p_a, p_b = s_a / n_a, s_b / n_b
    pooled = (s_a + s_b) / (n_a + n_b)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    expected_z = (p_a - p_b) / se
    assert result.statistic == pytest.approx(expected_z, rel=1e-9)
    assert result.p_value == pytest.approx(2 * sps.norm.sf(abs(expected_z)), rel=1e-9)
    assert result.sample_sizes == {"late": n_a, "on_time": n_b}
    assert result.effect_size_name == "Cohen's h"
    assert result.confidence_interval is not None
    low, high = result.confidence_interval
    assert low < (p_a - p_b) < high


def test_late_delivery_group_repeats_less(session: AnalysisSession) -> None:
    """The injected direction must be recovered by the test, not assumed."""
    snapshot = statistical_test(
        session,
        "two_proportion_z",
        {
            "model": "customer_lifecycle",
            "group_column": "first_delivery_status",
            "value_column": "is_repeat",
            "groups": ["late", "on_time"],
        },
    )
    rates = {row[0]: row[3] for row in snapshot.rows}
    assert rates["late"] < rates["on_time"]
    assert snapshot.statistical_result is not None
    assert snapshot.statistical_result.p_value < 0.01


def test_every_test_carries_a_correlation_is_not_causation_warning(
    session: AnalysisSession,
) -> None:
    snapshot = statistical_test(
        session,
        "two_proportion_z",
        {
            "model": "customer_lifecycle",
            "group_column": "first_delivery_status",
            "value_column": "is_repeat",
            "groups": ["late", "on_time"],
        },
    )
    assert snapshot.statistical_result is not None
    joined = " ".join(snapshot.statistical_result.warnings).lower()
    assert "causation" in joined


def test_welch_t_test_matches_scipy(session: AnalysisSession) -> None:
    snapshot = statistical_test(
        session,
        "welch_t_test",
        {
            "model": "fulfillment",
            "group_column": "carrier",
            "value_column": "delivery_days",
            "groups": ["RapidPost", "MetroShip"],
        },
    )
    result = snapshot.statistical_result
    assert result is not None
    (_, n_a, m_a, sd_a), (_, n_b, m_b, sd_b) = snapshot.rows
    reference = sps.ttest_ind_from_stats(m_a, sd_a, n_a, m_b, sd_b, n_b, equal_var=False)
    assert result.statistic == pytest.approx(float(reference.statistic), rel=1e-9)
    assert result.p_value == pytest.approx(float(reference.pvalue), rel=1e-9)


def test_chi_square_matches_scipy(session: AnalysisSession) -> None:
    snapshot = statistical_test(
        session,
        "chi_square",
        {"model": "sales", "row_column": "category", "column_column": "customer_segment"},
    )
    result = snapshot.statistical_result
    assert result is not None
    table = np.array([[float(v) for v in row[1:]] for row in snapshot.rows])
    chi2, p, _dof, _expected = sps.chi2_contingency(table)
    assert result.statistic == pytest.approx(float(chi2), rel=1e-9)
    assert result.p_value == pytest.approx(float(p), rel=1e-9)
    v = math.sqrt(chi2 / (table.sum() * (min(table.shape) - 1)))
    assert result.effect_size == pytest.approx(v, rel=1e-9)


def test_anova_matches_a_closed_form(session: AnalysisSession) -> None:
    snapshot = statistical_test(
        session,
        "one_way_anova",
        {"model": "sales", "group_column": "category", "value_column": "unit_price"},
    )
    result = snapshot.statistical_result
    assert result is not None
    ns = np.array([r[1] for r in snapshot.rows], dtype=float)
    means = np.array([r[2] for r in snapshot.rows], dtype=float)
    sds = np.array([r[3] for r in snapshot.rows], dtype=float)
    grand = (ns * means).sum() / ns.sum()
    ss_b = (ns * (means - grand) ** 2).sum()
    ss_w = ((ns - 1) * sds**2).sum()
    f = (ss_b / (len(ns) - 1)) / (ss_w / (ns.sum() - len(ns)))
    assert result.statistic == pytest.approx(f, rel=1e-8)
    assert result.effect_size == pytest.approx(ss_b / (ss_b + ss_w), rel=1e-8)


def test_pearson_correlation_matches_scipy(session: AnalysisSession) -> None:
    snapshot = statistical_test(
        session,
        "pearson_correlation",
        {"model": "fulfillment", "x_column": "delivery_days", "y_column": "is_late_int"},
    )
    result = snapshot.statistical_result
    assert result is not None
    assert -1.0 <= result.statistic <= 1.0
    assert result.confidence_interval is not None
    low, high = result.confidence_interval
    assert low <= result.statistic <= high


def test_spearman_is_a_different_test(session: AnalysisSession) -> None:
    kwargs = {"model": "fulfillment", "x_column": "delivery_days", "y_column": "is_late_int"}
    pearson = statistical_test(session, "pearson_correlation", dict(kwargs))
    spearman = statistical_test(session, "spearman_correlation", dict(kwargs))
    assert pearson.statistical_result is not None
    assert spearman.statistical_result is not None
    assert spearman.statistical_result.test_name == "Spearman rank correlation"
    assert pearson.statistical_result.test_name == "Pearson correlation"


def test_filters_apply_to_a_test(session: AnalysisSession) -> None:
    unfiltered = statistical_test(
        session,
        "welch_t_test",
        {
            "model": "fulfillment",
            "group_column": "carrier",
            "value_column": "delivery_days",
            "groups": ["RapidPost", "MetroShip"],
        },
    )
    filtered = statistical_test(
        session,
        "welch_t_test",
        {
            "model": "fulfillment",
            "group_column": "carrier",
            "value_column": "delivery_days",
            "groups": ["RapidPost", "MetroShip"],
        },
        filters=[Filter(column="region", op="=", value="Northeast")],
    )
    assert unfiltered.statistical_result is not None
    assert filtered.statistical_result is not None
    assert filtered.statistical_result.sample_sizes != unfiltered.statistical_result.sample_sizes


def test_unknown_test_type_is_rejected(session: AnalysisSession) -> None:
    with pytest.raises(StatsError, match="unknown test_type"):
        statistical_test(session, "t_test_but_vibes", {"model": "sales"})


def test_unknown_model_is_rejected(session: AnalysisSession) -> None:
    with pytest.raises(StatsError, match="unknown model or table"):
        statistical_test(
            session,
            "two_proportion_z",
            {"model": "nope", "group_column": "a", "value_column": "b"},
        )


def test_unknown_column_is_rejected(session: AnalysisSession) -> None:
    with pytest.raises(StatsError, match="not found"):
        statistical_test(
            session,
            "two_proportion_z",
            {"model": "customer_lifecycle", "group_column": "nope", "value_column": "is_repeat"},
        )


def test_wrong_group_count_is_rejected(session: AnalysisSession) -> None:
    with pytest.raises(StatsError, match="exactly two groups"):
        statistical_test(
            session,
            "two_proportion_z",
            {"model": "sales", "group_column": "category", "value_column": "is_returned"},
        )
    with pytest.raises(StatsError, match="at least three groups"):
        statistical_test(
            session,
            "one_way_anova",
            {
                "model": "customer_lifecycle",
                "group_column": "first_delivery_status",
                "value_column": "order_count",
            },
        )


def test_column_name_cannot_carry_sql(session: AnalysisSession) -> None:
    with pytest.raises(StatsError, match="not found"):
        statistical_test(
            session,
            "two_proportion_z",
            {
                "model": "customer_lifecycle",
                "group_column": 'is_repeat" FROM x; DROP TABLE orders --',
                "value_column": "is_repeat",
            },
        )


def test_correlation_matrix_is_symmetric_with_a_unit_diagonal(
    session: AnalysisSession,
) -> None:
    snapshot = correlation_matrix(
        session, "sales", ["quantity", "unit_price", "unit_cost", "list_price"]
    )
    assert snapshot.columns == [
        "column_name",
        "quantity",
        "unit_price",
        "unit_cost",
        "list_price",
    ]
    names = [r[0] for r in snapshot.rows]
    values = {r[0]: dict(zip(snapshot.columns[1:], r[1:], strict=True)) for r in snapshot.rows}
    for name in names:
        assert values[name][name] == pytest.approx(1.0, abs=1e-6)
    for a in names:
        for b in names:
            assert values[a][b] == pytest.approx(values[b][a], abs=1e-6)
    assert any("causation" in w.lower() for w in snapshot.warnings)


def test_correlation_matrix_column_count_is_bounded(session: AnalysisSession) -> None:
    with pytest.raises(StatsError, match="between 2 and"):
        correlation_matrix(session, "sales", ["quantity"])
