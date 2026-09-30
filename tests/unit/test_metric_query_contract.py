"""Regression coverage for the hosted grouped-total failure.

The live Compare Both run answered ``total revenue by region`` with a trend
in one pane and one arbitrary region in the other.  A cited partial result is
still the wrong answer; this test holds the registry contract and its complete
engine-written answer together.
"""

from __future__ import annotations

from agentic_analytics.analytics.metric_plan import MetricQuestionMapping, resolve_question
from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.verification.canonical import canonical_answer
from agentic_analytics.verification.coverage import check_answer_coverage
from agentic_analytics.warehouse.metrics import load_registry


def _snapshot(**changes: object) -> ResultSnapshot:
    params: dict[str, object] = {
        "metric": "revenue",
        "dimensions": ["region"],
        "time_grain": None,
        "filters": [],
    }
    params.update(changes)
    return ResultSnapshot(
        tool_name="compute_metric",
        columns=["region", "revenue"],
        rows=[["Midwest", 1918803.0499999924], ["West", 2389883.0]],
        parameters=params,
        column_types={"region": "VARCHAR", "revenue": "DOUBLE"},
    )


def test_grouped_total_resolves_to_one_registry_contract() -> None:
    mapping = resolve_question("What is the total revenue by region?", load_registry())
    assert mapping is not None and mapping.confident
    assert mapping.metric == "revenue"
    assert mapping.dimensions == ("region",)
    assert mapping.time_grain is None


def test_grouped_contract_rejects_a_trend_or_single_segment_result() -> None:
    mapping = MetricQuestionMapping("revenue", ("region",), metric_format="currency")
    assert (
        check_answer_coverage(mapping, [_snapshot(dimensions=[])]).rule
        == "missing_required_dimension"
    )
    assert not check_answer_coverage(mapping, [_snapshot(time_grain="month")]).complete


def test_engine_writes_complete_grouped_answer_and_formats_currency() -> None:
    mapping = MetricQuestionMapping("revenue", ("region",), metric_format="currency")
    finding = canonical_answer(mapping, _snapshot())
    assert finding is not None
    assert finding.text == "Revenue by region: Midwest: $1,918,803.05; West: $2,389,883.00."
    assert len(finding.evidence_cells) == 2
    assert {cell.row for cell in finding.evidence_cells} == {0, 1}


def test_unknown_named_grouping_refuses_instead_of_dropping_it() -> None:
    mapping = resolve_question("total revenue by imaginary territory", load_registry())
    assert mapping is not None
    assert not mapping.confident
    assert "grouping" in mapping.explanation
