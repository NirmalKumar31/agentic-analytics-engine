"""A typed cloud plan may interpret language. It may not exceed the question.

Every case here was executed before the guards existed. The plans are not
malformed -- they validate against the response schema, name real columns
and ground every excerpt in the question. They are the plans a capable
model plausibly returns, and each one answers a different question from
the one that was asked while looking entirely well-formed downstream.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentic_analytics.analytics.upload_plan import (
    mapping_from_contract,
    mapping_from_plan,
    resolve_question,
)

SCHEMA: dict[str, Any] = {
    "table": "uploaded_data",
    "fields": [
        {"name": "customer_name", "data_type": "VARCHAR"},
        {"name": "region", "data_type": "VARCHAR"},
        {"name": "annual_revenue", "data_type": "DOUBLE"},
        {"name": "signup_date", "data_type": "DATE"},
        {"name": "notes", "data_type": "VARCHAR"},
    ],
    "measures": ["annual_revenue"],
    "dimensions": ["region"],
    "time_fields": ["signup_date"],
    "aggregatable_if_named": [],
    "identifiers": ["customer_name"],
}


def _plan(**changes: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "table": "uploaded_data",
        "operation": "average",
        "operation_source": "average",
        "measure": "annual_revenue",
        "measure_source": "annual revenue",
        "dimension": None,
        "dimension_source": "",
        "filters": [],
        "time_field": None,
        "period_start": None,
        "period_end": None,
        "ascending": False,
        "confident": True,
        "ambiguity": "",
    }
    plan.update(changes)
    return plan


@pytest.mark.parametrize(
    ("column", "excerpt"),
    [("customer_name", "customer name"), ("notes", "notes")],
)
def test_ai_plan_cannot_group_by_a_column_the_engine_withholds(column: str, excerpt: str) -> None:
    """The privacy boundary has to hold in both modes.

    An identifier is kept out of `dimensions` because grouping by it puts
    its raw values into the result as group labels, and from there into a
    remote prompt. The validator checked only that the column existed, so
    the boundary held in deterministic mode alone.
    """
    question = f"What is the average annual revenue by {excerpt}?"
    mapping = mapping_from_plan(question, SCHEMA, _plan(dimension=column, dimension_source=excerpt))

    assert not mapping.confident
    assert "does not offer as a grouping" in mapping.explanation
    assert mapping.dimension is None


def test_ai_plan_cannot_add_a_time_period_the_question_did_not_state() -> None:
    """A narrowed population is a different question, silently answered."""
    mapping = mapping_from_plan(
        "What is the average annual revenue?",
        SCHEMA,
        _plan(time_field="signup_date", period_start="2024-01-01", period_end="2024-12-31"),
    )

    assert not mapping.confident
    assert "did not state" in mapping.explanation
    assert mapping.period is None


def test_ai_plan_cannot_shift_the_period_the_question_did_state() -> None:
    question = "What was the average annual revenue in 2024?"
    assert resolve_question(question, SCHEMA).period == ("2024-01-01", "2024-12-31")

    mapping = mapping_from_plan(
        question,
        SCHEMA,
        _plan(time_field="signup_date", period_start="2023-01-01", period_end="2023-12-31"),
    )

    assert not mapping.confident
    assert "changed the time period" in mapping.explanation


def test_ai_plan_cannot_reverse_a_ranking() -> None:
    """Every other field agrees, so nothing downstream can notice."""
    question = "Which region has the highest annual revenue?"
    rules = resolve_question(question, SCHEMA)
    assert rules.confident and rules.operation == "rank" and not rules.ascending

    mapping = mapping_from_plan(
        question,
        SCHEMA,
        _plan(
            operation="rank",
            operation_source="highest",
            dimension="region",
            dimension_source="region",
            ascending=True,
        ),
    )

    assert not mapping.confident
    assert "reversed the ranking direction" in mapping.explanation


def test_the_guards_do_not_refuse_a_plan_that_matches_the_question() -> None:
    """The point is to bound the cloud plan, not to reject every one.

    Without this, all four guards above could be satisfied by a validator
    that refuses unconditionally.
    """
    question = "Which region had the highest annual revenue in 2024?"
    rules = resolve_question(question, SCHEMA)
    assert rules.confident

    mapping = mapping_from_plan(
        question,
        SCHEMA,
        _plan(
            operation="rank",
            operation_source="highest",
            dimension="region",
            dimension_source="region",
            time_field="signup_date",
            period_start="2024-01-01",
            period_end="2024-12-31",
            ascending=False,
        ),
    )

    assert mapping.confident, mapping.explanation
    assert mapping.contract_hash == rules.contract_hash


def test_a_numeric_grouping_is_still_allowed_when_the_question_names_it() -> None:
    """The widening that the identifier rule must not undo."""
    schema = dict(SCHEMA, fields=[*SCHEMA["fields"], {"name": "store_id", "data_type": "BIGINT"}])
    mapping = mapping_from_plan(
        "What is the average annual revenue by store_id?",
        schema,
        _plan(dimension="store_id", dimension_source="store_id"),
    )

    assert mapping.confident, mapping.explanation
    assert mapping.dimension == "store_id"


@pytest.mark.parametrize(
    "question",
    [
        "What was the total annual revenue in 2024?",
        "Which region had the highest annual revenue in 2024?",
        "What was the average annual revenue by region in 2024?",
        "Show the monthly trend of annual revenue in 2024",
        "What is the average annual revenue by region?",
    ],
)
def test_an_accepted_contract_survives_its_own_revalidation(question: str) -> None:
    """The engine has to accept the contract it just issued.

    `time_field` is the axis a trend is grouped along; `period_field` is the
    column a period filters. The revalidation folded the two into one, which
    put a trend axis on every non-trend mapping and changed its canonical
    hash without changing its SQL -- so the MCP boundary reported "the
    accepted query contract changed before execution" and every ordinary
    question naming a period published nothing at all.
    """
    rules = resolve_question(question, SCHEMA)
    assert rules.confident, rules.explanation

    revalidated = mapping_from_contract(question, SCHEMA, rules.as_dict())

    assert revalidated.confident, revalidated.explanation
    assert revalidated.contract_hash == rules.contract_hash
    assert revalidated.canonical_dict() == rules.canonical_dict()


def test_a_trend_keeps_its_axis_and_a_rank_does_not_acquire_one() -> None:
    trend = resolve_question("Show the monthly trend of annual revenue in 2024", SCHEMA)
    assert trend.time_field == "signup_date"
    assert trend.period_field == "signup_date"

    rank = resolve_question("Which region had the highest annual revenue in 2024?", SCHEMA)
    assert rank.time_field is None
    assert rank.period_field == "signup_date"
