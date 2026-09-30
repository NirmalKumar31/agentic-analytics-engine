"""The answer-shape check must recognise the column it asked for.

The engine aliases its output columns to lowercase slugs: a grouping by
`Holiday_Flag` is returned as `holiday_flag`. The coverage gate compared
the mapping's dimension against the result's column names literally, so
`"Holiday_Flag" not in {"holiday_flag", ...}` and the finding was withheld
as not having "the requested answer shape".

The numbers were right and sitting in the result store. The reader got a
completed report with nothing in it.

Every fixture in the committed corpus uses lowercase column names, so
alias and original coincided and nothing caught this. It appears the
moment an uploaded file capitalises a header -- which most exported files
do.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agentic_analytics.analytics import upload_plan
from agentic_analytics.analytics.execute import run_query
from agentic_analytics.analytics.semantic import infer_schema
from agentic_analytics.verification.coverage import check_answer_coverage
from agentic_analytics.warehouse.session import SessionManager, open_upload_session

#: 120 rows per group, and a capitalised header -- the condition that
#: distinguishes this from every corpus fixture.
ROWS = [
    (store, f"2010-{(index % 12) + 1:02d}-05", value, flag)
    for index, (store, value, flag) in enumerate(
        (s, v, f)
        for s in range(1, 41)
        for v, f in ((100.0, 0), (300.0, 0), (500.0, 0), (200.0, 1), (400.0, 1), (600.0, 1))
    )
]


def _run(question: str, header: str):
    body = "\n".join(",".join(str(v) for v in row) for row in ROWS)
    manager = SessionManager()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fixture.csv"
        path.write_text(f"{header}\n{body}\n")
        try:
            session = manager.add(open_upload_session(path, path.name, "csv"))
            schema = infer_schema(session, "uploaded_data").as_dict()
            mapping = upload_plan.resolve_question(question, schema)
            sql = upload_plan.build_sql(mapping)
            assert sql is not None, mapping.explanation
            snapshot = run_query(
                session,
                sql,
                tool_name="aggregate_for_question",
                parameters={"table": "uploaded_data"} | mapping.as_dict(),
                guard=False,
            )
            return mapping, snapshot
        finally:
            manager.close_all()


@pytest.mark.parametrize(
    ("header", "question"),
    [
        # Capitalised, as exported files usually are.
        (
            "Store,Date,Weekly_Sales,Holiday_Flag",
            "What is the average Weekly_Sales by Holiday_Flag?",
        ),
        # Spoken lower case against a capitalised schema.
        ("Store,Date,Weekly_Sales,Holiday_Flag", "average weekly sales by holiday flag"),
        # Mixed case.
        ("Store,Date,weeklySales,holidayFlag", "average weeklySales by holidayFlag"),
        # All lower case, which already worked and must keep working.
        ("store,date,weekly_sales,holiday_flag", "average weekly_sales by holiday_flag"),
    ],
)
def test_a_grouped_answer_is_publishable_whatever_the_header_case(
    header: str, question: str
) -> None:
    mapping, snapshot = _run(question, header)
    assert mapping.dimension is not None, mapping.explanation
    coverage = check_answer_coverage(mapping, [snapshot])
    assert coverage.complete, f"{coverage.rule}: {coverage.reason}"
    assert coverage.output_shape


def test_the_numbers_are_there_either_way() -> None:
    """The figures were never wrong -- only unpublishable. Worth pinning,
    so a future shape fix cannot be mistaken for an arithmetic one."""
    _, snapshot = _run(
        "What is the average Weekly_Sales by Holiday_Flag?",
        "Store,Date,Weekly_Sales,Holiday_Flag",
    )
    values = {str(r[0]): round(float(r[1]), 2) for r in snapshot.rows}
    assert values == {"0": 300.0, "1": 400.0}
