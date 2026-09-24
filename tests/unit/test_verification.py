"""Deterministic verification is what stops a wrong number from being published."""

from __future__ import annotations

import pytest

from agentic_analytics.analytics.results import ResultSnapshot, StatisticalResult
from agentic_analytics.verification.charts import (
    ChartError,
    ChartRequest,
    build_chart,
    validate_chart,
)
from agentic_analytics.verification.claims import check_claim, is_causal
from agentic_analytics.verification.numeric import extract_numbers, verify_numbers


@pytest.fixture
def timeseries() -> ResultSnapshot:
    return ResultSnapshot(
        tool_name="analyze_timeseries",
        result_id="res_ts",
        columns=["period", "gross_margin_pct", "change_abs"],
        rows=[
            ["2025-04-01T00:00:00", 40.936876, None],
            ["2025-07-01T00:00:00", 33.270671, -7.666205],
        ],
        row_count=2,
    )


@pytest.fixture
def cells() -> list[tuple[float, str]]:
    return [(40.936876, "res_ts[0].gross_margin_pct"), (33.270671, "res_ts[1].gross_margin_pct")]


def test_correct_difference_is_accepted(
    timeseries: ResultSnapshot, cells: list[tuple[float, str]]
) -> None:
    verdict = verify_numbers(
        "gross_margin_pct fell from 40.94% to 33.27%, a change of 7.67 percentage points.",
        {"type": "difference", "from": 40.936876, "to": 33.270671, "stated": -7.666205},
        cells,
        [timeseries],
    )
    assert verdict.ok, verdict.reason
    assert all(c.matched for c in verdict.checks)


def test_wrong_difference_is_rejected(
    timeseries: ResultSnapshot, cells: list[tuple[float, str]]
) -> None:
    verdict = verify_numbers(
        "gross_margin_pct fell from 40.94% to 33.27%, a change of 12.50 percentage points.",
        {"type": "difference", "from": 40.936876, "to": 33.270671, "stated": -12.5},
        cells,
        [timeseries],
    )
    assert not verdict.ok
    assert "does not match" in verdict.reason


def test_inverted_direction_is_rejected(
    timeseries: ResultSnapshot, cells: list[tuple[float, str]]
) -> None:
    verdict = verify_numbers(
        "gross_margin_pct rose 7.67 percentage points.",
        {"type": "difference", "from": 40.936876, "to": 33.270671, "stated": 7.666205},
        cells,
        [timeseries],
    )
    assert not verdict.ok


def test_invented_number_is_rejected(
    timeseries: ResultSnapshot, cells: list[tuple[float, str]]
) -> None:
    verdict = verify_numbers("Revenue was 8,412,900 last quarter.", None, cells, [timeseries])
    assert not verdict.ok
    assert "do not appear" in verdict.reason


def test_percent_change_is_recomputed(timeseries: ResultSnapshot) -> None:
    cells = [(100.0, "res_ts[0].x"), (114.2, "res_ts[1].x")]
    snapshot = ResultSnapshot(
        tool_name="compute_metric",
        result_id="res_ts",
        columns=["x"],
        rows=[[100.0], [114.2]],
        row_count=2,
    )
    ok = verify_numbers(
        "x rose 14.2% from 100.00 to 114.20.",
        {"type": "percent_change", "from": 100.0, "to": 114.2, "stated": 14.2},
        cells,
        [snapshot],
    )
    assert ok.ok, ok.reason
    bad = verify_numbers(
        "x rose 41.2% from 100.00 to 114.20.",
        {"type": "percent_change", "from": 100.0, "to": 114.2, "stated": 41.2},
        cells,
        [snapshot],
    )
    assert not bad.ok


def test_dates_are_not_treated_as_measurements() -> None:
    assert extract_numbers("margin fell in 2025-07-01 and Q3 2025") == []


def test_significance_thresholds_are_not_measurements() -> None:
    """'significant at the 5% level' is a threshold, not a claim that x = 5."""
    assert extract_numbers("the difference is significant at the 5% level") == []
    assert extract_numbers("the 95% confidence interval is -0.21 to -0.18") == [-0.21, -0.18]


def test_statistical_values_are_citable() -> None:
    snapshot = ResultSnapshot(
        tool_name="statistical_test",
        result_id="res_st",
        columns=["group", "rate"],
        rows=[["late", 0.5046], ["on_time", 0.7042]],
        row_count=2,
        statistical_result=StatisticalResult(
            test_name="two-proportion z-test",
            statistic=-23.527,
            p_value=2.15e-122,
            effect_size=-0.412,
            effect_size_name="Cohen's h",
            sample_sizes={"late": 3401, "on_time": 26599},
        ),
    )
    verdict = verify_numbers(
        "A two-proportion z-test on late=3,401, on_time=26,599 returned p = 2.15e-122 "
        "with Cohen's h = -0.412.",
        None,
        [],
        [snapshot],
    )
    assert verdict.ok, verdict.reason


def test_numbers_with_no_cited_result_are_rejected() -> None:
    verdict = verify_numbers("Revenue rose 14%.", None, [], [])
    assert not verdict.ok


CAUSAL = [
    "Discounting caused the margin decline.",
    "Margin fell because of the product mix shift.",
    "The increase was due to the affiliate channel.",
    "Late delivery led to lower repeat purchasing.",
    "Higher discounts drove the margin down.",
    "The mix shift resulted in a lower margin.",
]
HEDGED = [
    "Margin decline is associated with higher discounting.",
    "The mix shift correlates with the margin decline.",
    "Higher discounting may explain part of the decline.",
    "Late delivery appears to accompany lower repeat purchasing.",
]


@pytest.mark.parametrize("text", CAUSAL)
def test_causal_language_is_detected(text: str) -> None:
    assert is_causal(text)


@pytest.mark.parametrize("text", HEDGED)
def test_hedged_language_is_allowed(text: str) -> None:
    assert not is_causal(text)


def test_causal_claim_on_observational_data_is_rejected(timeseries: ResultSnapshot) -> None:
    verdict = check_claim(
        "The discount increase caused the margin decline.",
        "interpretation",
        [timeseries],
        True,
    )
    assert not verdict.ok
    assert verdict.rule == "causal_from_observational"


def test_significance_without_a_test_is_rejected(timeseries: ResultSnapshot) -> None:
    verdict = check_claim(
        "The difference between quarters is statistically significant.",
        "calculated_fact",
        [timeseries],
        True,
    )
    assert not verdict.ok
    assert verdict.rule == "significance_without_test"


def test_significance_with_a_test_is_allowed() -> None:
    snapshot = ResultSnapshot(
        tool_name="statistical_test",
        result_id="r",
        columns=["g"],
        rows=[["a"]],
        row_count=1,
        statistical_result=StatisticalResult(test_name="t", statistic=1.0, p_value=0.001),
    )
    assert check_claim("The difference is significant.", "statistical_result", [snapshot], True).ok


def test_claim_with_no_evidence_is_rejected() -> None:
    verdict = check_claim("Revenue rose.", "calculated_fact", [], False)
    assert not verdict.ok
    assert verdict.rule == "no_evidence"


# ------------------------------------------------------------------- charts


@pytest.fixture
def segments() -> ResultSnapshot:
    return ResultSnapshot(
        tool_name="compare_segments",
        result_id="res_seg",
        columns=["region", "revenue"],
        rows=[["West", 100.0], ["East", 80.0], ["North", None]],
        row_count=3,
    )


def test_chart_is_built_with_inline_data(segments: ResultSnapshot) -> None:
    spec = build_chart(ChartRequest(mark="bar", x="region", y="revenue"), segments)
    assert spec["mark"]["type"] == "bar"
    assert "values" in spec["data"]
    # The null row is dropped; a gap reads as a real zero otherwise.
    assert len(spec["data"]["values"]) == 2
    assert "url" not in str(spec)


def test_scatter_maps_to_the_point_mark(segments: ResultSnapshot) -> None:
    spec = build_chart(ChartRequest(mark="scatter", x="region", y="revenue"), segments)
    assert spec["mark"]["type"] == "point"


BAD_CHARTS = [
    ("pie mark", ChartRequest(mark="pie", x="region", y="revenue")),
    ("unknown x", ChartRequest(mark="bar", x="nope", y="revenue")),
    ("unknown y", ChartRequest(mark="bar", x="region", y="nope")),
    ("bad type", ChartRequest(mark="bar", x="region", y="revenue", y_type="script")),
    ("unknown color", ChartRequest(mark="bar", x="region", y="revenue", color="nope")),
]


@pytest.mark.parametrize("name,request_", BAD_CHARTS, ids=[n for n, _ in BAD_CHARTS])
def test_bad_chart_requests_are_rejected(
    name: str, request_: ChartRequest, segments: ResultSnapshot
) -> None:
    with pytest.raises(ChartError):
        build_chart(request_, segments)


HOSTILE_SPECS = [
    (
        "remote data",
        {"data": {"url": "https://evil.example/x.json"}, "mark": "bar", "encoding": {}},
    ),
    (
        "transform",
        {
            "data": {"values": []},
            "mark": "bar",
            "encoding": {"x": {"field": "region", "type": "nominal"}},
            "transform": [{"calculate": "alert(1)", "as": "z"}],
        },
    ),
    (
        "signals",
        {"data": {"values": []}, "mark": "bar", "encoding": {}, "signals": [{"name": "x"}]},
    ),
    (
        "xss field name",
        {
            "data": {"values": []},
            "mark": "bar",
            "encoding": {"x": {"field": "<img src=x onerror=alert(1)>", "type": "nominal"}},
        },
    ),
    (
        "nested expr",
        {
            "data": {"values": []},
            "mark": "bar",
            "encoding": {"x": {"field": "region", "type": "nominal", "expr": "alert(1)"}},
        },
    ),
]


@pytest.mark.parametrize("name,spec", HOSTILE_SPECS, ids=[n for n, _ in HOSTILE_SPECS])
def test_hostile_specs_are_rejected(
    name: str, spec: dict[str, object], segments: ResultSnapshot
) -> None:
    with pytest.raises(ChartError):
        validate_chart(spec, segments)  # type: ignore[arg-type]


def test_chart_row_count_is_bounded() -> None:
    snapshot = ResultSnapshot(
        tool_name="compute_metric",
        result_id="r",
        columns=["x", "y"],
        rows=[[str(i), float(i)] for i in range(500)],
        row_count=500,
    )
    spec = build_chart(ChartRequest(mark="line", x="x", y="y"), snapshot, max_rows=50)
    assert len(spec["data"]["values"]) == 50


def test_result_with_no_plottable_rows_is_rejected() -> None:
    snapshot = ResultSnapshot(
        tool_name="compute_metric",
        result_id="r",
        columns=["x", "y"],
        rows=[["a", None]],
        row_count=1,
    )
    with pytest.raises(ChartError, match="no plottable rows"):
        build_chart(ChartRequest(mark="bar", x="x", y="y"), snapshot)
