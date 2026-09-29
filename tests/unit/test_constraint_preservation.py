"""A result that answered a different question cannot publish.

The claim is true, its numbers trace to real cells, and a critic reading
only the sentence has no basis to object. What is wrong is upstream: the
query behind it did not apply a restriction the question stated. This is
the gate that catches that, and it is deterministic on purpose -- an
unfiltered result must be incapable of publishing for a filtered
question however confident a model is that the sentence is accurate.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.analytics.row_filters import RowFilter
from agentic_analytics.analytics.upload_plan import QuestionMapping
from agentic_analytics.verification.constraints import (
    MISSING_DIMENSION,
    MISSING_FILTER,
    WRONG_MEASURE,
    check_constraints,
)

RANGE = (
    RowFilter("headcount", ">=", Decimal("30"), "30 to 40"),
    RowFilter("headcount", "<=", Decimal("40"), "30 to 40"),
)

FILTERED = QuestionMapping(
    operation="average",
    table="uploaded_data",
    measure="spend",
    dimension="territory",
    confident=True,
    filters=RANGE,
    named_columns=["spend", "territory"],
)


def snapshot(**params: object) -> ResultSnapshot:
    """A result carrying the plan it was executed with."""
    parameters: dict[str, object] = {"operation": "average"}
    parameters.update(params)
    return ResultSnapshot(
        tool_name="aggregate_for_question",
        columns=["territory", "average_spend", "row_count"],
        rows=[["North", 100.0, 10]],
        row_count=1,
        parameters=parameters,
    )


def applied(*filters: RowFilter) -> list[dict[str, object]]:
    return [f.as_dict() for f in filters]


def test_a_result_that_applied_the_restriction_publishes() -> None:
    verdict = check_constraints(
        None,
        FILTERED,
        [snapshot(measure="spend", dimension="territory", filters=applied(*RANGE))],
    )
    assert verdict.applicable
    assert verdict.preserved


def test_an_unfiltered_result_cannot_publish_for_a_filtered_question() -> None:
    """The released defect, as a rule.

    Same measure, same grouping, same shape of answer -- computed over
    every row. It looked exactly like an answer.
    """
    verdict = check_constraints(
        None, FILTERED, [snapshot(measure="spend", dimension="territory", filters=[])]
    )
    assert verdict.applicable
    assert not verdict.preserved
    assert verdict.rule == MISSING_FILTER
    assert "restricts the rows" in verdict.reason


def test_a_partially_filtered_result_cannot_publish() -> None:
    """One half of a range is not the range. A result bounded below but
    not above covers a different population."""
    verdict = check_constraints(
        None,
        FILTERED,
        [snapshot(measure="spend", dimension="territory", filters=applied(RANGE[0]))],
    )
    assert not verdict.preserved
    assert verdict.rule == MISSING_FILTER


def test_a_filter_on_the_wrong_column_cannot_publish() -> None:
    wrong = RowFilter("spend", ">=", Decimal("30"), "30 to 40")
    verdict = check_constraints(
        None,
        FILTERED,
        [snapshot(measure="spend", dimension="territory", filters=applied(wrong, RANGE[1]))],
    )
    assert not verdict.preserved
    assert verdict.rule == MISSING_FILTER


def test_exclusive_bounds_do_not_satisfy_an_inclusive_request() -> None:
    """The boundary rows are the difference between two populations."""
    exclusive = (
        RowFilter("headcount", ">", Decimal("30"), "30 to 40"),
        RowFilter("headcount", "<", Decimal("40"), "30 to 40"),
    )
    verdict = check_constraints(
        None,
        FILTERED,
        [snapshot(measure="spend", dimension="territory", filters=applied(*exclusive))],
    )
    assert not verdict.preserved
    assert verdict.rule == MISSING_FILTER


def test_a_dropped_grouping_is_named_as_such() -> None:
    grouped = QuestionMapping(
        operation="average",
        table="uploaded_data",
        measure="spend",
        dimension="territory",
        confident=True,
        named_columns=["spend", "territory"],
    )
    verdict = check_constraints(None, grouped, [snapshot(measure="spend", dimension=None)])
    assert not verdict.preserved
    assert verdict.rule == MISSING_DIMENSION
    assert "breakdown by territory" in verdict.reason


def test_a_different_measure_is_named_as_such() -> None:
    verdict = check_constraints(
        None,
        FILTERED,
        [snapshot(measure="headcount", dimension="territory", filters=applied(*RANGE))],
    )
    assert not verdict.preserved
    assert verdict.rule == WRONG_MEASURE


def test_citing_nothing_that_records_a_plan_cannot_publish() -> None:
    """A profile result carries no plan, so it cannot evidence that the
    restriction was honoured. This is what stopped a column profile
    publishing as the answer to a filtered question."""
    verdict = check_constraints(None, FILTERED, [ResultSnapshot(tool_name="profile_table")])
    assert not verdict.preserved
    assert verdict.rule == MISSING_FILTER


def test_one_good_result_among_several_is_enough() -> None:
    """A claim resting partly on a profile and partly on the filtered
    aggregate is still grounded in the filtered aggregate."""
    verdict = check_constraints(
        None,
        FILTERED,
        [
            ResultSnapshot(tool_name="profile_table"),
            snapshot(measure="spend", dimension="territory", filters=applied(*RANGE)),
        ],
    )
    assert verdict.preserved


@pytest.mark.parametrize(
    "mapping",
    [
        None,
        QuestionMapping(operation="profile", table="uploaded_data", confident=False),
        # Nothing the question fixed, so nothing can have gone missing.
        QuestionMapping(operation="sum", table="uploaded_data", measure="spend", confident=True),
    ],
)
def test_the_gate_abstains_when_there_is_no_contract_to_check(mapping: object) -> None:
    """It must not fire on the governed warehouse or on an unrestricted
    question, or every one of them would refuse."""
    assert not check_constraints(None, mapping, [snapshot()]).applicable


# ───────────────────────────────── the gate as wired, not the module alone
class _PermissiveCritic:
    """A model that approves everything.

    The point of the gate is that it holds when the model is wrong, so
    the model here is as wrong as it can be: it calls the claim supported
    and says it answers the question.
    """

    remote_inference = True

    async def complete_json(self, request: object) -> dict[str, object]:
        return {
            "finding_id": "",
            "status": "supported",
            "reason": "the figures match the cited result",
            "answers_question": True,
            "relevance_reason": "reports the requested averages",
        }

    async def aclose(self) -> None:
        return None


async def _verdict_for(filters_applied: list[dict[str, object]]):
    from agentic_analytics.agents.critic import verify_finding
    from agentic_analytics.agents.schemas import CandidateFinding
    from agentic_analytics.analytics.results import EvidenceCell

    snap = ResultSnapshot(
        result_id="res_agg",
        tool_name="aggregate_for_question",
        columns=["territory", "average_spend", "row_count"],
        rows=[["North", 100.0, 10]],
        row_count=1,
        column_types={"territory": "VARCHAR", "average_spend": "DOUBLE", "row_count": "BIGINT"},
        parameters={
            "table": "uploaded_data",
            "operation": "average",
            "measure": "spend",
            "dimension": "territory",
            "filters": filters_applied,
        },
    )
    finding = CandidateFinding(
        text="North has an average spend of 100.0.",
        result_ids=[snap.result_id],
        evidence_cells=[
            EvidenceCell(result_id=snap.result_id, row=0, column="average_spend", value=100.0)
        ],
    )
    verdict, _ = await verify_finding(
        finding,
        {snap.result_id: snap},
        _PermissiveCritic(),  # type: ignore[arg-type]
        None,
        question="average spend by territory for headcount 30 to 40",
        mapping=FILTERED,
    )
    return verdict


async def test_the_critic_cannot_publish_an_unfiltered_result() -> None:
    """End to end through `verify_finding`, with the model saying yes.

    Testing `check_constraints` alone leaves the wiring unverified --
    disabling the gate in the critic breaks nothing that a module-level
    test can see.
    """
    verdict = await _verdict_for([])
    assert verdict.status == "unsupported"
    assert verdict.rule == MISSING_FILTER
    assert verdict.answers_question is False
    assert "restricts the rows" in verdict.reason


async def test_the_critic_publishes_when_the_restriction_was_applied() -> None:
    """The gate must not reject everything: the same claim, from a result
    that did honour the restriction, is publishable."""
    verdict = await _verdict_for([f.as_dict() for f in RANGE])
    assert verdict.status == "supported"
    assert verdict.rule != MISSING_FILTER
