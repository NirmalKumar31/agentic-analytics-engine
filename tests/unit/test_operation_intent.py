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


# ─────────────────────── the same contract, whichever mode resolves it
def _resolved(question: str, rows: list[tuple[object, ...]], header: str):
    """Compile and execute a question against a small real table.

    The mapping is a pure function of the question and the schema, so both
    modes resolve the same contract by construction. What this pins is
    that the *compiled query and its numbers* are the same -- the property
    Compare Both depends on, and the one a future planner change could
    break without any test noticing.
    """
    import tempfile
    from pathlib import Path

    from agentic_analytics.analytics.execute import run_query
    from agentic_analytics.analytics.semantic import infer_schema
    from agentic_analytics.warehouse.session import SessionManager, open_upload_session

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
            snapshot = run_query(
                session,
                sql,
                tool_name="aggregate_for_question",
                parameters={"table": "uploaded_data"} | mapping.as_dict(),
                guard=False,
            )
            return mapping, sql, snapshot
        finally:
            manager.close_all()


#: A week-grained table with a flag to group by, two groups with
#: different means, and a date column a trend could legitimately use.
HEADER = "Store,Date,Weekly_Sales,Holiday_Flag"
#: Sized past the cardinality threshold on purpose. Below it an integer
#: column stays a measure rather than becoming a dimension, so a handful
#: of rows cannot express "group by a flag" at all -- a six-row version of
#: this fixture refused while the real 6,435-row table worked.
#:
#: 120 rows per flag. Means 300 and 400; totals 36,000 and 48,000.
ROWS = [
    (store, f"2010-{(index % 12) + 1:02d}-05", value, flag)
    for index, (store, value, flag) in enumerate(
        (s, v, f)
        for s in range(1, 41)
        for v, f in ((100.0, 0), (300.0, 0), (500.0, 0), (200.0, 1), (400.0, 1), (600.0, 1))
    )
]


def test_the_grouped_average_is_computed_per_group() -> None:
    mapping, sql, snapshot = _resolved(
        "What is the average Weekly_Sales by Holiday_Flag?", ROWS, HEADER
    )
    assert snapshot is not None and sql is not None
    assert mapping.operation == "average"
    assert mapping.dimension == "Holiday_Flag"
    # Every requested group, not a highest/lowest pair.
    assert len(snapshot.rows) == 2
    values = {str(r[0]): round(float(r[1]), 2) for r in snapshot.rows}
    assert values == {"0": 300.0, "1": 400.0}


def test_the_grouped_total_is_computed_per_group() -> None:
    _, _, snapshot = _resolved("What is the total Weekly_Sales by Holiday_Flag?", ROWS, HEADER)
    assert snapshot is not None
    values = {str(r[0]): round(float(r[1]), 2) for r in snapshot.rows}
    assert values == {"0": 36000.0, "1": 48000.0}


def test_the_contract_hash_is_stable_for_one_question() -> None:
    """Both modes resolve the contract from the question and the schema
    alone, so the hash they compare in Compare Both must not depend on
    anything else. Resolved twice, it has to agree with itself."""
    first, _, _ = _resolved("What is the average Weekly_Sales by Holiday_Flag?", ROWS, HEADER)
    second, _, _ = _resolved("What is the average Weekly_Sales by Holiday_Flag?", ROWS, HEADER)
    assert first.contract_hash == second.contract_hash
    assert first.contract_hash


def test_wording_that_means_the_same_thing_hashes_the_same() -> None:
    """`average` and `mean` are one request, so Compare Both must not
    report an interpretation mismatch between them."""
    a, _, _ = _resolved("average Weekly_Sales by Holiday_Flag", ROWS, HEADER)
    b, _, _ = _resolved("mean Weekly_Sales by Holiday_Flag", ROWS, HEADER)
    assert a.contract_hash == b.contract_hash


def test_a_different_operation_hashes_differently() -> None:
    """The hash has to be able to say two interpretations differ, or
    reporting equality with it means nothing."""
    a, _, _ = _resolved("average Weekly_Sales by Holiday_Flag", ROWS, HEADER)
    b, _, _ = _resolved("total Weekly_Sales by Holiday_Flag", ROWS, HEADER)
    assert a.contract_hash != b.contract_hash


def test_a_column_literally_named_trend_does_not_request_one() -> None:
    """The case only the column-name stripping can handle.

    Narrowing the pattern removed the bare period adjectives, but `trend`
    itself has to stay in it -- a user asking for a trend says "trend".
    So a column *called* `trend_score` would still match unless column
    references are removed before intent is read. The two halves of this
    fix are otherwise redundant; this is what makes the stripping
    load-bearing on its own.
    """
    sch = schema(trend_score="DOUBLE", region="VARCHAR", Date="DATE")
    mapping = upload_plan.resolve_question("average trend_score by region", sch)
    assert mapping.operation == "average", mapping.explanation
    assert mapping.measure == "trend_score"
    assert mapping.dimension == "region"


def test_a_column_named_over_time_does_not_request_a_series() -> None:
    sch = schema(time_series_id="BIGINT", amount="DOUBLE", region="VARCHAR", Date="DATE")
    mapping = upload_plan.resolve_question("total amount by region", sch)
    assert mapping.operation == "sum", mapping.explanation
    assert mapping.dimension == "region"
