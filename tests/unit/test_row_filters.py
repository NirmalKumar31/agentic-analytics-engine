"""Row restrictions a question states, and what happens when one cannot
be resolved.

A released build answered "the average annual revenue by region for people
aged 30 to 40" over all 1,200 rows. It did not fail: it resolved the
measure, resolved the grouping, silently dropped the age range, and
returned four regional averages that looked exactly like an answer. The
numbers were right for a question nobody asked.

The rule these tests hold to is that a restriction is either applied or
refused, never dropped. Everything here is synthetic -- no fixture derives
from anyone's upload.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from agentic_analytics.analytics.row_filters import (
    RowFilter,
    parse_filters,
    where_clause,
)


def schema(**columns: str) -> dict[str, object]:
    """A schema in the shape the planner receives."""
    return {
        "table": "uploaded_data",
        "fields": [{"name": n, "data_type": t} for n, t in columns.items()],
    }


PEOPLE = schema(
    age="BIGINT",
    annual_revenue="DOUBLE",
    region="VARCHAR",
    signed_on="DATE",
)


def describe(question: str, sch: dict[str, object] | None = None) -> list[tuple[str, str, float]]:
    resolution = parse_filters(question, sch or PEOPLE)
    assert resolution.refusal is None, resolution.refusal
    return [(f.column, f.operator, float(f.value)) for f in resolution.filters]


# ───────────────────────────────────────────── the shapes people write in
@pytest.mark.parametrize(
    "question",
    [
        "What is the average annual revenue by region for people aged 30 to 40?",
        "average annual_revenue by region for ages 30 to 40",
        "average annual revenue by region where age is between 30 and 40",
        "average annual revenue by region for age between 30 and 40",
        "average annual revenue by region, age from 30 to 40",
    ],
)
def test_a_range_resolves_to_its_column_inclusively(question: str) -> None:
    """Every ordinary phrasing of the same restriction.

    Inclusive by default, because "30 to 40" includes both in every
    ordinary reading and a reader who meant otherwise says so.
    """
    assert describe(question) == [("age", ">=", 30.0), ("age", "<=", 40.0)]


def test_written_comparisons_resolve() -> None:
    assert describe("average annual revenue by region for age >= 30 and age <= 40") == [
        ("age", ">=", 30.0),
        ("age", "<=", 40.0),
    ]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("average annual revenue where age is at least 30", [("age", ">=", 30.0)]),
        ("average annual revenue where age is at most 40", [("age", "<=", 40.0)]),
        ("average annual revenue for age over 30", [("age", ">", 30.0)]),
        ("average annual revenue for age under 40", [("age", "<", 40.0)]),
        ("average annual revenue where age greater than 30", [("age", ">", 30.0)]),
        ("average annual revenue where age less than 40", [("age", "<", 40.0)]),
    ],
)
def test_boundary_words_carry_their_strictness(
    question: str, expected: list[tuple[str, str, float]]
) -> None:
    """ "at least" is inclusive and "over" is not. Getting this wrong
    silently changes the population by the rows sitting on the boundary."""
    assert describe(question) == expected


def test_an_exclusive_range_is_honoured_when_asked_for() -> None:
    got = describe("average annual revenue for age between 30 and 40 exclusive")
    assert got == [("age", ">", 30.0), ("age", "<", 40.0)]


def test_spoken_and_underscored_column_names_both_resolve() -> None:
    """`monthly ad spend` and `monthly_ad_spend` are the same request."""
    sch = schema(monthly_ad_spend="DOUBLE", region="VARCHAR")
    assert describe("total monthly ad spend over 500", sch) == [("monthly_ad_spend", ">", 500.0)]
    assert describe("total monthly_ad_spend over 500", sch) == [("monthly_ad_spend", ">", 500.0)]


def test_the_column_nearest_the_number_wins() -> None:
    """The measure is mentioned before the filter and is not the filter.

    "average annual revenue by region for people aged 30 to 40" names two
    numeric columns. Taking any match refused the question as ambiguous;
    taking the first would have filtered on revenue. The one spoken next
    to the number is the one being restricted.
    """
    assert describe("average annual revenue by region for people aged 30 to 40") == [
        ("age", ">=", 30.0),
        ("age", "<=", 40.0),
    ]


def test_two_filters_in_one_question_both_apply() -> None:
    sch = schema(age="BIGINT", team_size="BIGINT", annual_revenue="DOUBLE")
    got = describe("average annual revenue where age is at least 30 and team size under 10", sch)
    assert ("age", ">=", 30.0) in got
    assert ("team_size", "<", 10.0) in got


# ──────────────────────────────────────────────── what must be refused
def test_a_restriction_on_no_known_column_is_refused_not_dropped() -> None:
    """The defect, stated as a rule: an unresolvable restriction refuses.

    Falling through would answer a different question and present it as
    this one's answer.
    """
    resolution = parse_filters("average annual revenue for tenure 30 to 40", PEOPLE)
    assert resolution.refusal is not None
    assert resolution.constraint_detected
    assert not resolution.filters


def test_a_reversed_range_is_refused_with_an_honest_reason() -> None:
    resolution = parse_filters("average annual revenue for age 40 to 30", PEOPLE)
    assert resolution.refusal is not None
    assert "selects nothing" in resolution.refusal


def test_an_ambiguous_range_is_refused_rather_than_guessed() -> None:
    """Two equally close candidates is not a coin toss."""
    sch = schema(score_a="BIGINT", score_b="BIGINT", total="DOUBLE")
    resolution = parse_filters("total where score 10 to 20", sch)
    # Either refusal is honest -- "which of the two" or "which column at
    # all". What must not happen is a filter on one of them.
    assert resolution.refusal is not None
    assert not resolution.filters


@pytest.mark.parametrize(
    "bound",
    ["1e400", "NaN", "Infinity", "0x20", "30; DROP TABLE uploaded_data", "'; --"],
)
def test_malformed_and_injection_like_bounds_never_become_filters(bound: str) -> None:
    """Nothing from the question reaches SQL as text.

    A value becomes a predicate only after parsing as a finite decimal, so
    a bound that is not a number cannot be one.
    """
    resolution = parse_filters(f"average annual revenue where age is at least {bound}", PEOPLE)
    for applied in resolution.filters:
        assert applied.value.is_finite()
        assert str(applied.value).replace("-", "").replace(".", "").isdigit()


def test_a_filter_value_is_rendered_from_a_parsed_number() -> None:
    clause = where_clause(
        (RowFilter("age", ">=", Decimal("30")), RowFilter("age", "<=", Decimal("40.5"))),
        lambda c: f'"{c}"',
    )
    assert clause == '"age" >= 30 AND "age" <= 40.5'


# ─────────────────────────────────────────── periods stay the date layer's
@pytest.mark.parametrize(
    "question",
    [
        "total annual revenue in 1998",
        "total annual revenue in Q2 2025",
        "annual revenue by region in 2024",
    ],
)
def test_years_and_quarters_are_not_read_as_numeric_filters(question: str) -> None:
    """A period is not a row filter.

    Reading "in 1998" as a numeric bound would refuse questions the engine
    already answers, so year-like and quarter-like numbers are blanked
    before the scan.
    """
    resolution = parse_filters(question, PEOPLE)
    assert resolution.refusal is None
    assert not resolution.filters


def test_a_period_and_a_numeric_filter_compose() -> None:
    """Both restrictions survive; neither displaces the other."""
    resolution = parse_filters("average annual revenue in 2024 for age 30 to 40", PEOPLE)
    assert resolution.refusal is None
    assert [(f.column, f.operator, float(f.value)) for f in resolution.filters] == [
        ("age", ">=", 30.0),
        ("age", "<=", 40.0),
    ]


def test_an_unrestricted_question_detects_no_constraint() -> None:
    """The detector must not fire on ordinary questions, or every one of
    them would refuse."""
    for question in (
        "average annual revenue by region",
        "total annual revenue",
        "count of rows by region",
    ):
        resolution = parse_filters(question, PEOPLE)
        assert not resolution.constraint_detected, question
        assert resolution.refusal is None
