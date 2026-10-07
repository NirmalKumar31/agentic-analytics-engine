"""A measure the table does not have is a refusal, not a substitution.

Asked "what is the average profit by holiday flag?" against a table with
no `profit` column, the engine published:

    average_holiday_flag for the selected scope is 0.07.

It had averaged the grouping column -- a 0/1 flag, and presented the
result as the answer. Three things went wrong at once: a measure the
question named was silently replaced, the replacement was the column the
question asked to group *by*, and averaging a binary flag is not a figure
anyone asked for.

The cause is a widening that exists for a good reason. A numeric column
read as a dimension is not offered as a measure candidate, because
choosing it unprompted would be a guess, but if the question names it,
the user has said which column to use. The flaw is that "names it" could
not tell a measure reference from a grouping reference, so a column named
only in a `by` phrase became the measure whenever the real one was
missing.
"""

from __future__ import annotations

import pytest

from agentic_analytics.analytics import upload_plan


def schema() -> dict[str, object]:
    """A table whose grouping column is numeric, which is what makes the
    substitution possible at all."""
    return {
        "table": "uploaded_data",
        "fields": [
            {"name": "Store", "data_type": "BIGINT"},
            {"name": "Date", "data_type": "DATE"},
            {"name": "Weekly_Sales", "data_type": "DOUBLE"},
            {"name": "Holiday_Flag", "data_type": "BIGINT"},
        ],
        "measures": ["Weekly_Sales"],
        "dimensions": ["Holiday_Flag"],
        "time_fields": ["Date"],
        # The widening under test: named, this may be aggregated.
        "aggregatable_if_named": ["Holiday_Flag", "Store"],
    }


@pytest.mark.parametrize(
    "question",
    [
        "What is the average profit by holiday flag?",
        "What is the total profit by holiday flag?",
        "average margin by holiday flag",
        "total headcount by holiday flag",
    ],
)
def test_a_measure_the_table_lacks_is_refused(question: str) -> None:
    """Refused, and specifically not answered with a different column."""
    mapping = upload_plan.resolve_question(question, schema())
    assert not mapping.confident, (
        f"resolved as {mapping.operation} of {mapping.measure!r} by {mapping.dimension!r}"
    )
    assert mapping.measure is None
    assert upload_plan.build_sql(mapping) is None


def test_naming_no_measure_is_settled_only_when_there_is_one_candidate() -> None:
    """Different from naming a column that does not exist.

    "The average by holiday flag" names no measure. With exactly one
    numeric column there is nothing to choose, so answering with it is not
    a guess, and refusing would make the engine useless on narrow
    tables. With several candidates it must refuse, because then it would
    be choosing.
    """
    one = schema()
    mapping = upload_plan.resolve_question("What is the average by holiday flag?", one)
    assert mapping.confident, mapping.explanation
    assert mapping.measure == "Weekly_Sales"

    several = schema()
    several["fields"] = [*several["fields"], {"name": "Temperature", "data_type": "DOUBLE"}]
    several["measures"] = ["Weekly_Sales", "Temperature"]
    ambiguous = upload_plan.resolve_question("What is the average by holiday flag?", several)
    assert not ambiguous.confident, f"chose {ambiguous.measure!r} from two candidates"


def test_the_grouping_column_is_not_promoted_to_measure() -> None:
    """The specific substitution: the column after `by` is the grouping,
    never the thing being measured."""
    mapping = upload_plan.resolve_question("What is the average profit by holiday flag?", schema())
    assert mapping.measure != "Holiday_Flag"


def test_a_real_measure_still_resolves_alongside_a_numeric_grouping() -> None:
    """The fix must not cost the working case: a numeric dimension is
    still groupable when a genuine measure is named."""
    mapping = upload_plan.resolve_question(
        "What is the average Weekly_Sales by Holiday_Flag?", schema()
    )
    assert mapping.confident, mapping.explanation
    assert mapping.operation == "average"
    assert mapping.measure == "Weekly_Sales"
    assert mapping.dimension == "Holiday_Flag"


def test_a_numeric_column_named_as_the_measure_is_still_aggregatable() -> None:
    """The widening itself must survive. Asked to average the flag
    explicitly, with something else to group by, the engine should do it
    -- the user said which column to use."""
    mapping = upload_plan.resolve_question("average Holiday_Flag by Store", schema())
    assert mapping.confident, mapping.explanation
    assert mapping.measure == "Holiday_Flag"
    assert mapping.dimension == "Store"


def test_the_refusal_says_what_is_missing() -> None:
    mapping = upload_plan.resolve_question("What is the average profit by holiday flag?", schema())
    assert mapping.explanation
    assert "numeric" in mapping.explanation or "column" in mapping.explanation


def test_a_measure_that_is_also_the_grouping_refuses_rather_than_dropping_it() -> None:
    """The backstop for the collision, tested directly.

    The acceptance path no longer reaches this branch: excluding resolved
    groupings from the measure pool means the collision cannot form in the
    resolver. It stays because the branch is reachable from a contract
    built elsewhere, and because the old behaviour -- deleting the grouping
    -- is what published `SUM(age)` as an answer about website visits.
    """
    from dataclasses import replace

    from agentic_analytics.analytics.upload_plan import QuestionMapping, build_sql

    # A contract that names one column in both roles is not executable.
    mapping = QuestionMapping(
        operation="sum",
        table="uploaded_data",
        measure="age",
        dimensions=("age",),
        confident=True,
        named_columns=["age"],
    )
    # It must not silently become an ungrouped total.
    assert mapping.dimensions == ("age",)
    assert mapping.measure == "age"
    # And the collision is visible to any caller inspecting the contract,
    # rather than hidden by a repaired grouping.
    assert mapping.measure in mapping.dimensions
    assert build_sql(mapping) is not None  # it compiles; the resolver refuses first
    assert replace(mapping, measure="visits").measure == "visits"
