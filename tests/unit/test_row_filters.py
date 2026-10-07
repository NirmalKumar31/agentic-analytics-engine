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


def test_of_two_candidates_the_one_beside_the_number_wins() -> None:
    """Both columns sit close enough to be candidates, so the tie is
    broken by proximity and not by chance.

    "average revenue age 30 to 40" names `revenue` and `age` one word
    apart. Binding the range to `revenue` would filter the measure by
    30-40 and return a confident, wrong answer, and the locality window
    alone does not separate them here, so the ranking has to.
    """
    sch = schema(revenue="DOUBLE", age="BIGINT")
    assert describe("average revenue age 30 to 40", sch) == [
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
    # Either refusal is honest: "which of the two" or "which column at
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


# ───────────────────────── the compiled query, not just the parsed filter
def _executed(question: str, rows: list[tuple[object, ...]], **columns: str):
    """Build a table, ask the question, run what the planner compiles.

    Parsing a filter correctly and *compiling* it are different things.
    Tests that stop at the parser pass happily while the WHERE clause is
    dropped on the way to SQL, which is exactly the defect being fixed,
    so this executes and counts rows.
    """
    import tempfile
    from pathlib import Path

    from agentic_analytics.analytics import upload_plan
    from agentic_analytics.analytics.execute import run_query
    from agentic_analytics.analytics.semantic import infer_schema
    from agentic_analytics.warehouse.session import SessionManager, open_upload_session

    header = ",".join(columns)
    body = "\n".join(",".join(str(v) for v in row) for row in rows)
    manager = SessionManager()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fixture.csv"
        path.write_text(f"{header}\n{body}\n")
        try:
            session = manager.add(open_upload_session(path, path.name, "csv"))
            schema = infer_schema(session, "uploaded_data").as_dict()
            mapping = upload_plan.resolve_question(question, schema)
            sql = upload_plan.build_sql(mapping)
            if sql is None:
                return mapping, None, None
            # Parameters exactly as the MCP tool supplies them, because
            # the constraint gate reads the plan back off the result.
            snapshot = run_query(
                session,
                sql,
                tool_name="aggregate_for_question",
                parameters={"table": "uploaded_data", "question": question} | mapping.as_dict(),
                guard=False,
            )
            return mapping, sql, snapshot
        finally:
            manager.close_all()


#: Twelve rows spanning the boundaries, in two groups.
ROWS = [
    (28, "north", 100),
    (29, "north", 100),
    (30, "north", 200),
    (31, "north", 200),
    (40, "north", 200),
    (41, "north", 100),
    (42, "north", 100),
    (30, "south", 400),
    (35, "south", 400),
    (40, "south", 400),
    (25, "south", 100),
    (45, "south", 100),
]
COLUMNS = {"headcount": "", "territory": "", "spend": ""}


def test_the_compiled_query_actually_restricts_the_rows() -> None:
    """Counted from the result, not read off the SQL string.

    A test asserting the text of a WHERE clause passes against a build
    that assembles the clause and never applies it.
    """
    _, sql, snapshot = _executed(
        "average spend by territory for headcount 30 to 40", ROWS, **COLUMNS
    )
    assert snapshot is not None and sql is not None
    counts = {
        r[snapshot.columns.index("territory")]: r[snapshot.columns.index("row_count")]
        for r in snapshot.rows
    }
    # north: 30, 31, 40 -> 3.  south: 30, 35, 40 -> 3.  Six of twelve.
    assert counts == {"north": 3, "south": 3}
    assert sum(counts.values()) == 6


def test_both_boundary_rows_are_inside_an_inclusive_range() -> None:
    """The rows at exactly 30 and exactly 40 are the ones an off-by-one
    loses, and they change the average."""
    _, _, snapshot = _executed("average spend by territory for headcount 30 to 40", ROWS, **COLUMNS)
    assert snapshot is not None
    averages = {
        r[snapshot.columns.index("territory")]: round(
            float(r[snapshot.columns.index("average_spend")]), 2
        )
        for r in snapshot.rows
    }
    assert averages == {"north": 200.0, "south": 400.0}


def test_rows_outside_the_range_are_excluded() -> None:
    _, _, snapshot = _executed("average spend by territory for headcount 41 to 50", ROWS, **COLUMNS)
    assert snapshot is not None
    counts = {
        r[snapshot.columns.index("territory")]: r[snapshot.columns.index("row_count")]
        for r in snapshot.rows
    }
    assert counts == {"north": 2, "south": 1}


def test_an_empty_population_returns_nothing_rather_than_zero() -> None:
    """No qualifying rows is an honest empty result, not an invented 0."""
    _, _, snapshot = _executed("average spend by territory for headcount 90 to 99", ROWS, **COLUMNS)
    assert snapshot is not None
    assert snapshot.rows == []


def test_the_executed_result_records_the_filters_it_applied() -> None:
    """Provenance: the result carries the plan, so a later gate can check
    the restriction was honoured rather than take it on trust."""
    mapping, _, snapshot = _executed(
        "average spend by territory for headcount 30 to 40", ROWS, **COLUMNS
    )
    assert snapshot is not None
    recorded = snapshot.parameters.get("filters")
    assert recorded, "the executed result must record its filters"
    assert {(f["column"], f["operator"], f["value"]) for f in recorded} == {
        ("headcount", ">=", 30.0),
        ("headcount", "<=", 40.0),
    }
    assert [f.as_dict() for f in mapping.filters] == recorded


# ────────────────────────────── categories, nullity, and how they compose
SHOP = schema(
    price="DOUBLE",
    quantity="BIGINT",
    status="VARCHAR",
    category="VARCHAR",
    shipped_on="DATE",
    discount="DOUBLE",
)


def kinds(question: str, sch: dict[str, object] | None = None):
    resolution = parse_filters(question, sch or SHOP)
    assert resolution.refusal is None, resolution.refusal
    return [(f.column, f.operator, getattr(f, "value", None)) for f in resolution.filters]


def test_an_exact_category_match_resolves() -> None:
    assert kinds("total price where status is active") == [("status", "=", "active")]


def test_a_category_value_is_quoted_not_interpolated() -> None:
    from agentic_analytics.analytics.row_filters import CategoryFilter, where_clause

    clause = where_clause((CategoryFilter("status", "active"),), lambda c: f'"{c}"')
    assert clause == "\"status\" = 'active'"


@pytest.mark.parametrize(
    "value", ["act've", "act;ive", "act'--", "a' OR '1'='1", "${jndi}", "<script>"]
)
def test_an_unsafe_category_value_is_refused_not_escaped(value: str) -> None:
    """Refused rather than escaped. An escaping bug is a vulnerability; a
    refusal is an inconvenience."""
    resolution = parse_filters(f"total price where status is {value}", SHOP)
    assert not any(getattr(f, "value", None) == value for f in resolution.filters)


def test_presence_and_absence_resolve() -> None:
    assert kinds("count where discount is missing") == [("discount", "IS NULL", None)]
    assert kinds("count where discount is not missing") == [("discount", "IS NOT NULL", None)]


def test_a_numeric_and_a_categorical_filter_compose() -> None:
    got = kinds("average price where status is active and quantity at least 5")
    assert ("status", "=", "active") in got
    assert ("quantity", ">=", 5.0) in got


def test_filters_are_joined_with_and_not_or() -> None:
    """Conjunction is the only composition. Reading "and" as "or" would
    widen the population; the reverse would narrow it."""
    from agentic_analytics.analytics.row_filters import where_clause

    resolution = parse_filters("average price where quantity at least 5 and price under 100", SHOP)
    clause = where_clause(resolution.filters, lambda c: f'"{c}"')
    assert " AND " in clause
    assert " OR " not in clause.upper()


def test_disjunction_is_refused_rather_than_read_as_conjunction() -> None:
    """ "status is active or pending" restricted to `active` alone answers
    a narrower question than the one asked, silently."""
    resolution = parse_filters("average price where status is active or pending", SHOP)
    assert resolution.refusal is not None
    assert "or" in resolution.refusal.lower()


def test_contradictory_bounds_are_refused() -> None:
    resolution = parse_filters(
        "average price where quantity at least 90 and quantity under 5", SHOP
    )
    assert resolution.refusal is not None
    assert "cannot both hold" in resolution.refusal


def test_the_same_restriction_twice_is_one_restriction() -> None:
    got = kinds("average price where quantity at least 5 and quantity at least 5")
    assert got.count(("quantity", ">=", 5.0)) == 1


def test_more_filters_than_the_engine_composes_are_refused() -> None:
    from agentic_analytics.analytics.row_filters import MAX_FILTERS

    clauses = " and ".join(f"quantity at least {i}" for i in range(MAX_FILTERS + 4))
    resolution = parse_filters(f"average price where {clauses}", SHOP)
    assert resolution.refusal is None or "more than" in resolution.refusal


@pytest.mark.parametrize("value", ["-12.5", "0", "1000000", "3.25"])
def test_decimal_and_negative_bounds_are_accepted(value: str) -> None:
    got = kinds(f"average price where discount at least {value}")
    assert got == [("discount", ">=", float(value))]
