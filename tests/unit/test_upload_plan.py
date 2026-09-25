"""The deterministic question-to-schema resolver.

Two properties are worth more than any individual mapping: a question that
names its columns is answered, and a question that does not is *refused*
rather than answered with a plausible-looking number.
"""

from __future__ import annotations

import pytest

from agentic_analytics.analytics.upload_plan import (
    GROUP_LIMIT,
    RANK_LIMIT,
    build_sql,
    resolve_question,
)

SCHEMA = {
    "table": "uploaded_data",
    "row_count": 400,
    "fields": [
        {"name": n} for n in ["order_id", "order_date", "region", "category", "revenue", "units"]
    ],
    "measures": ["revenue", "units"],
    "dimensions": ["region", "category"],
    "time_fields": ["order_date"],
    "identifiers": ["order_id"],
}

ONE_MEASURE = SCHEMA | {"measures": ["revenue"], "fields": SCHEMA["fields"]}


@pytest.mark.parametrize(
    ("question", "operation", "measure", "dimension"),
    [
        ("What is the total revenue by region?", "sum", "revenue", "region"),
        ("total revenue for each region", "sum", "revenue", "region"),
        ("What is the average units by category?", "average", "units", "category"),
        ("How many orders per category?", "count", None, "category"),
        ("Which region has the highest revenue?", "rank", "revenue", "region"),
        ("lowest revenue by region", "rank", "revenue", "region"),
        ("Show revenue over time", "trend", "revenue", None),
        ("revenue by month", "trend", "revenue", None),
    ],
)
def test_a_question_that_names_its_columns_is_mapped(
    question: str, operation: str, measure: str | None, dimension: str | None
) -> None:
    mapping = resolve_question(question, SCHEMA)
    assert mapping.confident, mapping.explanation
    assert mapping.operation == operation
    assert mapping.measure == measure
    assert mapping.dimension == dimension
    assert build_sql(mapping) is not None


@pytest.mark.parametrize(
    "question",
    [
        "Explain what drives customer churn",
        "Why did things get worse?",
        "What should we do next quarter?",
        "Is this dataset any good?",
    ],
)
def test_a_question_with_no_recognised_operation_is_refused(question: str) -> None:
    """No keyword, no answer. The alternative is inventing an intent."""
    mapping = resolve_question(question, SCHEMA)
    assert not mapping.confident
    assert mapping.operation == "profile"
    assert build_sql(mapping) is None
    assert mapping.explanation


def test_an_unnamed_measure_is_refused_when_there_is_a_choice() -> None:
    """Two numeric columns and no hint is a guess, so it is refused."""
    mapping = resolve_question("What is the total?", SCHEMA)
    assert not mapping.confident
    assert "does not name which" in mapping.explanation


def test_an_unnamed_measure_is_accepted_when_there_is_only_one() -> None:
    """With a single numeric column there is nothing to guess between."""
    mapping = resolve_question("What is the total?", ONE_MEASURE)
    assert mapping.confident
    assert mapping.measure == "revenue"
    assert mapping.dimension is None


def test_plurals_are_matched() -> None:
    """People write "top categories" for a column called `category`."""
    mapping = resolve_question("top categories", SCHEMA)
    assert mapping.confident
    assert mapping.dimension == "category"
    # No measure named and none forced, so this is a frequency ranking.
    assert mapping.measure is None


def test_a_column_name_is_not_matched_inside_another_word() -> None:
    """`units` must not match "opportunities"."""
    schema = SCHEMA | {"measures": ["units", "revenue"]}
    mapping = resolve_question("count the opportunities", schema)
    assert mapping.operation == "count"
    assert mapping.dimension is None


def test_a_bare_grouping_is_read_as_a_breakdown() -> None:
    """ "revenue by region" names no verb but is unambiguous all the same."""
    mapping = resolve_question("revenue by region", SCHEMA)
    assert mapping.confident
    assert mapping.operation == "sum"
    assert mapping.measure == "revenue"
    assert mapping.dimension == "region"

    # Without a measure it is a count of rows, not a sum of something guessed.
    counted = resolve_question("orders by region", SCHEMA)
    assert counted.confident
    assert counted.operation == "count"
    assert counted.measure is None


def test_a_bare_grouping_by_an_unknown_column_is_still_refused() -> None:
    mapping = resolve_question("revenue by salesperson", SCHEMA)
    assert not mapping.confident


def test_a_trend_needs_a_date_column() -> None:
    schema = SCHEMA | {"time_fields": []}
    mapping = resolve_question("revenue over time", schema)
    assert not mapping.confident
    assert "date column" in mapping.explanation


def test_describe_is_an_honest_profile_not_a_refusal() -> None:
    """Asking what is in the file is answerable, and is not a failure."""
    mapping = resolve_question("Describe this dataset", SCHEMA)
    assert mapping.confident
    assert mapping.operation == "profile"
    assert build_sql(mapping) is None


def test_generated_sql_is_read_only_and_bounded() -> None:
    for question in (
        "total revenue by region",
        "average units by category",
        "how many per region",
        "top categories by revenue",
        "revenue over time",
    ):
        sql = build_sql(resolve_question(question, SCHEMA))
        assert sql is not None
        assert sql.lstrip().upper().startswith("SELECT")
        assert "LIMIT" in sql
        for banned in ("INSERT", "UPDATE", "DELETE", "COPY", "ATTACH", ";"):
            assert banned not in sql.upper()


def test_rank_limits_are_tighter_than_group_limits() -> None:
    """A "top" question should return a short list, not a full breakdown."""
    assert RANK_LIMIT < GROUP_LIMIT
    ranked = str(build_sql(resolve_question("top regions by revenue", SCHEMA)))
    grouped = str(build_sql(resolve_question("total revenue by region", SCHEMA)))
    assert f"LIMIT {RANK_LIMIT}" in ranked
    assert f"LIMIT {GROUP_LIMIT}" in grouped


def test_output_columns_use_the_dataset_vocabulary() -> None:
    """`total_revenue by region` beats `total_value by segment` for a reader."""
    sql = str(build_sql(resolve_question("total revenue by region", SCHEMA)))
    assert "AS region" in sql
    assert "AS total_revenue" in sql


def test_awkward_column_names_still_produce_a_valid_alias() -> None:
    schema = {
        "table": "uploaded_data",
        "fields": [{"name": "2024 sales ($)"}, {"name": "store #"}],
        "measures": ["2024 sales ($)"],
        "dimensions": ["store #"],
        "time_fields": [],
    }
    sql = str(build_sql(resolve_question("total 2024 sales ($) by store #", schema)))
    # The source column is quoted exactly as it is spelled in the file.
    assert '"2024 sales ($)"' in sql
    assert '"store #"' in sql
    # The output names are identifiers: no punctuation, no leading digit.
    assert "AS store" in sql
    assert "AS total_2024_sales" in sql


def test_a_column_name_starting_with_a_digit_gets_a_usable_alias() -> None:
    """`2024` is not an identifier, so grouping by it needs a prefix."""
    schema = {
        "table": "uploaded_data",
        "fields": [{"name": "2024"}, {"name": "amount"}],
        "measures": ["amount"],
        "dimensions": ["2024"],
        "time_fields": [],
    }
    sql = str(build_sql(resolve_question("count by 2024", schema)))
    assert "AS value_2024" in sql


def test_ascending_is_set_only_for_bottom_questions() -> None:
    assert resolve_question("lowest revenue by region", SCHEMA).ascending is True
    assert resolve_question("highest revenue by region", SCHEMA).ascending is False


def test_the_mapping_is_serialisable_and_says_it_is_rule_based() -> None:
    payload = resolve_question("total revenue by region", SCHEMA).as_dict()
    assert payload["interpretation"] == "rule-based"
    assert payload["confident"] is True
    assert payload["explanation"]


def test_resolution_is_deterministic() -> None:
    """Same question, same schema, same plan -- every time."""
    first = resolve_question("total revenue by region", SCHEMA).as_dict()
    for _ in range(20):
        assert resolve_question("total revenue by region", SCHEMA).as_dict() == first
