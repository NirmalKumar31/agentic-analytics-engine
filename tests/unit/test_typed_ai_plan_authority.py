"""A typed cloud plan may interpret language. It may not exceed the question.

Every case here was executed before the guards existed. The plans are not
malformed. They validate against the response schema, name real columns
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
    question = "What was the average annual revenue in 2024 using signup_date?"
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
    question = "Which region had the highest annual revenue in 2024 using signup_date?"
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
        "What was the total annual revenue in 2024 using signup_date?",
        "Which region had the highest annual revenue in 2024 using signup_date?",
        "What was the average annual revenue by region in 2024 using signup_date?",
        "Show the monthly trend of annual revenue in 2024 using signup_date",
        "What is the average annual revenue by region?",
    ],
)
def test_an_accepted_contract_survives_its_own_revalidation(question: str) -> None:
    """The engine has to accept the contract it just issued.

    `time_field` is the axis a trend is grouped along; `period_field` is the
    column a period filters. The revalidation folded the two into one, which
    put a trend axis on every non-trend mapping and changed its canonical
    hash without changing its SQL, so the MCP boundary reported "the
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
    trend = resolve_question(
        "Show the monthly trend of annual revenue in 2024 using signup_date", SCHEMA
    )
    assert trend.time_field == "signup_date"
    assert trend.period_field == "signup_date"

    rank = resolve_question(
        "Which region had the highest annual revenue in 2024 using signup_date?", SCHEMA
    )
    assert rank.time_field is None
    assert rank.period_field == "signup_date"


def test_a_lifecycle_date_is_not_silently_treated_as_the_measure_period() -> None:
    mapping = resolve_question("What was the total annual revenue in 2024?", SCHEMA)

    assert not mapping.confident
    assert mapping.issues == ("ambiguous_period_semantics",)
    assert "name the date column explicitly" in mapping.explanation


def test_a_lifecycle_date_is_not_silently_treated_as_a_trend_axis() -> None:
    mapping = resolve_question("Show the monthly trend of annual revenue", SCHEMA)

    assert not mapping.confident
    assert mapping.issues == ("ambiguous_period_semantics",)
    assert "name the date column explicitly" in mapping.explanation

    cloud = mapping_from_plan(
        "Show the monthly trend of annual revenue",
        SCHEMA,
        _plan(
            operation="trend",
            operation_source="monthly trend",
            time_field="signup_date",
            time_grain="month",
        ),
    )
    assert not cloud.confident
    assert "name the date column explicitly" in cloud.explanation


def test_a_generic_event_date_can_define_a_period_without_extra_wording() -> None:
    schema = {
        **SCHEMA,
        "fields": [
            field if field["name"] != "signup_date" else {**field, "name": "order_date"}
            for field in SCHEMA["fields"]
        ],
        "time_fields": ["order_date"],
    }

    mapping = resolve_question("What was the total annual revenue in 2024?", schema)

    assert mapping.confident, mapping.explanation
    assert mapping.period_field == "order_date"


def test_an_inert_sort_flag_does_not_split_two_identical_interpretations() -> None:
    """Found by a paid Compare Both run.

    Asked "the average annual revenue by region", the cloud plan returned
    `ascending: true` and the rules `false`. Direction only reaches the SQL
    for a ranking, so both executed identically -- same values, same
    coverage, and the page still reported "different governed
    interpretations" over a field that changes nothing.
    """
    question = "What is the average annual revenue by region?"
    rules = resolve_question(question, SCHEMA)
    assert rules.confident and rules.operation == "average"

    flipped = mapping_from_plan(
        question,
        SCHEMA,
        _plan(dimension="region", dimension_source="region", ascending=True),
    )

    assert flipped.confident, flipped.explanation
    assert flipped.contract_hash == rules.contract_hash
    assert flipped.canonical_dict() == rules.canonical_dict()


def test_a_ranking_still_distinguishes_its_direction() -> None:
    """Normalising must not make top and bottom the same contract."""
    highest = resolve_question("Which region has the highest annual revenue?", SCHEMA)
    lowest = resolve_question("Which region has the lowest annual revenue?", SCHEMA)

    assert highest.confident and lowest.confident
    assert highest.operation == lowest.operation == "rank"
    assert highest.ascending is False and lowest.ascending is True
    assert highest.contract_hash != lowest.contract_hash
