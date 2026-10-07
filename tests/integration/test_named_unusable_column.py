"""A column the reader named must not be swapped for another one.

Found on the deployed service. `What is the total returns_value?` on a
table whose only measure was `revenue` answered **total revenue**,
confidently, while recording that the question had named `returns_value`.
A column that does not exist at all was already refused correctly; a real
column that cannot serve as a measure was not.

`_pick` reasoned that when a table offers exactly one candidate there is
nothing to choose. That holds only when the reader did not choose. Here
they did, and their choice cannot do the job, so the engine must say so
rather than publish a different column's number under their question.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentic_analytics.analytics.semantic import infer_schema
from agentic_analytics.analytics.upload_plan import resolve_question
from agentic_analytics.warehouse.session import open_upload_session

#: `notes` is entirely empty, so DuckDB types it VARCHAR and the profiler
#: classifies it as serving no analytical role. `order_ref` is an
#: identifier. Both are real columns a reader could reasonably name.
#: `revenue` repeats across rows and is fractional, so it profiles as a
#: measure. A near-unique integer would be read as an identifier, and then
#: the table would have no measure at all and these tests would pass for
#: the wrong reason, which is what the first test below checks.
CSV = "region,revenue,notes,order_ref\n" + "\n".join(
    f"{['North', 'South'][i % 2]},{100 + (i % 12) * 10}.50,,REF-{1000 + i}" for i in range(60)
)


@pytest.fixture
def schema(tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "t.csv"
    path.write_text(CSV + "\n")
    session = open_upload_session(path, "t.csv", "csv", scratch_dir=tmp_path / "s")
    return infer_schema(session, "uploaded_data").as_dict()


def test_the_fixture_really_has_an_unusable_column(schema: dict[str, Any]) -> None:
    """Guards the guard: if `notes` became a measure, the tests below would
    pass for the wrong reason."""
    assert "notes" not in schema["measures"]
    assert "notes" not in schema["dimensions"]
    assert "revenue" in schema["measures"]


def test_a_named_unusable_column_refuses_rather_than_substituting(
    schema: dict[str, Any],
) -> None:
    mapping = resolve_question("What is the total notes?", schema)

    assert not mapping.confident, (
        f"answered with measure {mapping.measure!r} for a question about 'notes'"
    )
    assert "notes" in mapping.explanation
    # And specifically not the silent swap that was happening.
    assert mapping.measure != "revenue"


def test_a_named_identifier_is_refused_too(schema: dict[str, Any]) -> None:
    mapping = resolve_question("What is the total order_ref?", schema)
    assert not mapping.confident
    assert "order_ref" in mapping.explanation


def test_the_sole_measure_fallback_still_works(schema: dict[str, Any]) -> None:
    """The boundary. When the reader names no measure there is genuinely
    nothing to choose, and the one measure is the answer."""
    mapping = resolve_question("What is the average by region?", schema)
    assert mapping.confident, mapping.explanation
    assert mapping.measure == "revenue"


def test_naming_the_measure_and_a_grouping_still_works(schema: dict[str, Any]) -> None:
    """A dimension named beside the measure is not an unusable column."""
    mapping = resolve_question("What is the total revenue by region?", schema)
    assert mapping.confident, mapping.explanation
    assert mapping.measure == "revenue"
    assert mapping.dimensions == ("region",)
