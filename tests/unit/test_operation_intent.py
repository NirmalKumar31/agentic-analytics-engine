"""A word in a column's name is not a request for that analysis.

`Weekly_Sales` is a measure. Asked "what is the average weekly sales by
holiday flag?", a released build answered with a monthly trend of it --
confidently, with `dimension=None` -- because the operation detector saw
"weekly" and the trend pattern is matched before the average one. The
same question phrased with "total" failed the same way.

So a report said its calculation was `trend` over a question that asked
for an average grouped by a flag, and the reader was told the question
asked about behaviour "over time".

The rule these tests hold to: analytical intent comes from what the
question asks for, not from the vocabulary of the columns it names. A
trend requires temporal language of its own.
"""

from __future__ import annotations

import pytest

from agentic_analytics.analytics import upload_plan


def schema(**columns: str) -> dict[str, object]:
    return {
        "table": "uploaded_data",
        "fields": [{"name": n, "data_type": t} for n, t in columns.items()],
        "measures": [n for n, t in columns.items() if t in ("DOUBLE", "BIGINT")],
        "dimensions": [n for n, t in columns.items() if t == "VARCHAR"],
        "time_fields": [n for n, t in columns.items() if t == "DATE"],
        "aggregatable_if_named": [],
    }


#: A retail week-grained table: the measure's name contains a time word,
#: and there is a real date column for a genuine trend to use.
SALES = schema(
    Store="BIGINT",
    Date="DATE",
    Weekly_Sales="DOUBLE",
    Holiday_Flag="VARCHAR",
    Temperature="DOUBLE",
)


@pytest.mark.parametrize(
    ("question", "operation"),
    [
        ("What is the average weekly sales by holiday flag?", "average"),
        ("What is the total weekly sales by holiday flag?", "sum"),
        ("average weekly sales by holiday flag", "average"),
        ("total Weekly_Sales by Holiday_Flag", "sum"),
        ("mean weekly sales by holiday flag", "average"),
    ],
)
def test_a_time_word_inside_a_measure_name_is_not_a_trend_request(
    question: str, operation: str
) -> None:
    """The reported failure, both phrasings."""
    mapping = upload_plan.resolve_question(question, SALES)
    assert mapping.operation == operation, mapping.explanation
    assert mapping.measure == "Weekly_Sales"
    assert mapping.dimension == "Holiday_Flag"
    assert mapping.confident


@pytest.mark.parametrize(
    "question",
    [
        "How did weekly sales change over time?",
        "weekly sales trend",
        "weekly sales by month",
        "show the monthly trend of weekly sales",
        "weekly sales year over year",
    ],
)
def test_genuine_temporal_language_still_asks_for_a_trend(question: str) -> None:
    """The fix must not cost the feature. A question that really is about
    time still gets one -- and only when a date column exists to use."""
    mapping = upload_plan.resolve_question(question, SALES)
    assert mapping.operation == "trend", mapping.explanation
    assert mapping.time_field == "Date"


def test_a_trend_is_refused_when_no_date_column_exists() -> None:
    """Temporal language with nothing to apply it to is a refusal, not a
    silent aggregate over everything."""
    undated = schema(Weekly_Sales="DOUBLE", Holiday_Flag="VARCHAR")
    mapping = upload_plan.resolve_question("weekly sales over time", undated)
    assert mapping.operation != "trend" or not mapping.confident


@pytest.mark.parametrize(
    ("column", "question", "operation"),
    [
        # Other measure names carrying a time word, so the rule is not
        # special-cased to this dataset.
        ("monthly_ad_spend", "total monthly ad spend by channel", "sum"),
        ("daily_active_users", "average daily active users by plan", "average"),
        ("quarterly_revenue", "total quarterly revenue by region", "sum"),
        ("annual_salary", "average annual salary by department", "average"),
        ("hourly_rate", "average hourly rate by grade", "average"),
    ],
)
def test_the_rule_holds_for_any_measure_named_after_a_period(
    column: str, question: str, operation: str
) -> None:
    sch = schema(
        **{
            column: "DOUBLE",
            "channel": "VARCHAR",
            "plan": "VARCHAR",
            "region": "VARCHAR",
            "department": "VARCHAR",
            "grade": "VARCHAR",
            "Date": "DATE",
        }
    )
    mapping = upload_plan.resolve_question(question, sch)
    assert mapping.operation == operation, f"{question}: {mapping.explanation}"
    assert mapping.measure == column
    assert mapping.dimension is not None


def test_an_explicit_grouping_is_not_discarded() -> None:
    """`dimension=None` on a question that says "by holiday flag" is the
    other half of the same defect: the grouping was dropped as well as
    the operation being wrong."""
    mapping = upload_plan.resolve_question(
        "What is the average weekly sales by holiday flag?", SALES
    )
    assert mapping.dimension == "Holiday_Flag"
