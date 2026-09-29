"""A supported sentence is publishable only from the requested computation."""

from __future__ import annotations

from decimal import Decimal

from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.analytics.row_filters import RowFilter
from agentic_analytics.analytics.upload_plan import QuestionMapping
from agentic_analytics.verification.coverage import (
    WRONG_OPERATION,
    WRONG_PERIOD,
    check_answer_coverage,
)

MAPPING = QuestionMapping(
    operation="average",
    table="uploaded_data",
    measure="revenue",
    dimension="region",
    period=("2026-01-01", "2026-12-31"),
    period_field="sold_on",
    filters=(RowFilter("age", ">=", Decimal("30"), "age at least 30"),),
)


def result(**changes: object) -> ResultSnapshot:
    parameters: dict[str, object] = {
        "operation": "average",
        "measure": "revenue",
        "dimension": "region",
        "period": ["2026-01-01", "2026-12-31"],
        "filters": [MAPPING.filters[0].as_dict()],
    }
    parameters.update(changes)
    return ResultSnapshot(
        tool_name="aggregate_for_question",
        columns=["region", "average_revenue", "row_count"],
        rows=[["North", 12.5, 2]],
        parameters=parameters,
    )


def test_one_result_must_preserve_the_complete_contract() -> None:
    coverage = check_answer_coverage(MAPPING, [ResultSnapshot(tool_name="profile_table"), result()])
    assert coverage.applicable
    assert coverage.complete
    assert coverage.as_dict()["missing"] == []


def test_wrong_operation_is_a_distinct_failure() -> None:
    coverage = check_answer_coverage(MAPPING, [result(operation="sum")])
    assert not coverage.complete
    assert coverage.rule == WRONG_OPERATION
    assert coverage.missing == ("operation",)


def test_wrong_period_is_a_distinct_failure() -> None:
    coverage = check_answer_coverage(
        MAPPING,
        [result(period=["2025-01-01", "2025-12-31"])],
    )
    assert not coverage.complete
    assert coverage.rule == WRONG_PERIOD


def test_empty_population_is_still_a_valid_answer_shape() -> None:
    empty = result()
    empty.rows = []
    empty.row_count = 0
    assert check_answer_coverage(MAPPING, [empty]).complete


def test_profile_claims_are_left_to_support_and_relevance_gates() -> None:
    coverage = check_answer_coverage(MAPPING, [ResultSnapshot(tool_name="profile_table")])
    assert not coverage.applicable


def test_unrestricted_questions_still_check_operation_and_measure() -> None:
    mapping = QuestionMapping(
        operation="sum",
        table="uploaded_data",
        measure="revenue",
    )
    wrong = ResultSnapshot(
        tool_name="aggregate_for_question",
        columns=["average_revenue"],
        rows=[[10.0]],
        parameters={"operation": "average", "measure": "revenue"},
    )
    assert check_answer_coverage(mapping, [wrong]).rule == WRONG_OPERATION
