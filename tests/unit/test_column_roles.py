"""Column roles, from the data rather than from the column name.

The failure this exists for: a `Store` column of 45 values, each recurring
across 143 rows, was classified as a *measure* because the integer-dimension
rule capped distinct values at 12. The engine then averaged store numbers
and answered "the average profit by Store is 23" for a table with no profit
column.

Every case here goes through the real upload and `infer_schema` path. None
of them names a column the way the production file does, because a rule
that only works for a column called `Store` is not a rule.
"""

from __future__ import annotations

import csv
import io
import random
from pathlib import Path

import pytest

from agentic_analytics.analytics.semantic import InferredSchema, infer_schema
from agentic_analytics.warehouse.session import SessionManager, open_upload_session

ROWS = 900


def _csv(header: list[str], rows: list[list[object]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


@pytest.fixture
def profile(tmp_path: Path):
    manager = SessionManager()

    def run(header: list[str], rows: list[list[object]]) -> InferredSchema:
        path = tmp_path / f"f{len(header)}{len(rows)}{header[0]}.csv"
        path.write_text(_csv(header, rows))
        session = manager.add(open_upload_session(path, path.name, "csv"))
        return infer_schema(session, "uploaded_data")

    yield run
    manager.close_all()


def _role(schema: InferredSchema, column: str) -> str:
    return next(f.role for f in schema.fields if f.name == column)


def _ambiguous(schema: InferredSchema, column: str) -> bool:
    return next(f.ambiguous for f in schema.fields if f.name == column)


# ──────────────────────────────────────── numeric keys are not quantities
@pytest.mark.parametrize(
    ("column", "values"),
    [
        # A branch number: few values, each recurring across many rows.
        ("outlet_no", [1 + i % 45 for i in range(ROWS)]),
        # An employee reference, same shape under a different name.
        ("staff_ref", [100 + i % 30 for i in range(ROWS)]),
        # A postal code: numeric, many rows per value.
        ("postcode", [90000 + i % 60 for i in range(ROWS)]),
        # A product code with a wider range.
        ("article", [4000 + i % 150 for i in range(ROWS)]),
    ],
)
def test_a_numeric_code_is_a_grouping_not_a_measure(
    profile, column: str, values: list[int]
) -> None:
    rng = random.Random(4)
    schema = profile(
        [column, "net_value"],
        [[v, round(rng.uniform(5, 900), 2)] for v in values],
    )
    assert _role(schema, column) == "dimension"
    assert column not in schema.measures
    # Still usable as a measure when the question names it outright.
    assert column in schema.aggregatable_if_named


def test_a_row_numbering_sequence_is_an_identifier(profile) -> None:
    rng = random.Random(5)
    schema = profile(
        ["order_ref", "net_value"],
        [[i, round(rng.uniform(5, 900), 2)] for i in range(ROWS)],
    )
    assert _role(schema, "order_ref") == "identifier"
    assert "order_ref" not in schema.measures


def test_a_calendar_year_groups_rather_than_sums(profile) -> None:
    rng = random.Random(6)
    schema = profile(
        ["reported_year", "net_value"],
        [[2015 + i % 8, round(rng.uniform(5, 900), 2)] for i in range(ROWS)],
    )
    assert _role(schema, "reported_year") == "dimension"
    assert "reported_year" not in schema.measures


def test_a_binary_flag_is_a_grouping(profile) -> None:
    rng = random.Random(7)
    schema = profile(
        ["promoted", "net_value"],
        [[i % 2, round(rng.uniform(5, 900), 2)] for i in range(ROWS)],
    )
    assert _role(schema, "promoted") == "dimension"


# ──────────────────────────────────────── quantities stay quantities
@pytest.mark.parametrize(
    ("column", "values"),
    [
        # Currency and continuous measurement: fractional, so unambiguous.
        ("net_value", [round(10 + (i * 7919 % 90000) / 100, 2) for i in range(ROWS)]),
        ("reading_c", [round(-5 + (i * 3571 % 4500) / 100, 2) for i in range(ROWS)]),
        ("share_pct", [round((i * 1327 % 10000) / 100, 2) for i in range(ROWS)]),
    ],
)
def test_a_fractional_quantity_is_a_measure(profile, column: str, values: list[float]) -> None:
    schema = profile([column, "grouping"], [[v, f"g{i % 5}"] for i, v in enumerate(values)])
    assert _role(schema, column) == "measure"
    assert not _ambiguous(schema, column)


def test_a_small_rating_scale_groups_and_a_wider_count_measures(profile) -> None:
    """Both are integers with few distinct values. The repeat rate differs."""
    rng = random.Random(8)
    schema = profile(
        ["score", "units", "net_value"],
        [[1 + i % 5, rng.randint(1, 120), round(rng.uniform(5, 900), 2)] for i in range(ROWS)],
    )
    assert _role(schema, "score") == "dimension"
    assert _role(schema, "units") == "measure"


def test_an_integer_in_the_uncertain_band_is_marked_rather_than_settled(profile) -> None:
    """A code list and a genuine count can look identical in the data.

    Resolving that silently is the defect this module exists for, so the
    role is still usable but the uncertainty is recorded for the schema to
    report instead of presented as settled.
    """
    rng = random.Random(9)
    schema = profile(
        ["basket_size", "net_value"],
        [[rng.randint(1, 60), round(rng.uniform(5, 900), 2)] for i in range(ROWS)],
    )
    assert _role(schema, "basket_size") == "measure"
    assert _ambiguous(schema, "basket_size")
    assert "could be a count or a code" in next(
        f.reason for f in schema.fields if f.name == "basket_size"
    )


def test_a_near_unique_text_column_is_never_a_grouping(profile) -> None:
    """Its values would travel into a prompt as group labels."""
    schema = profile(
        ["comment", "net_value"],
        [[f"free text note number {i}", 1.5] for i in range(ROWS)],
    )
    assert _role(schema, "comment") == "identifier"
    assert "comment" not in schema.dimensions


def test_a_tiny_file_does_not_turn_every_number_into_a_grouping(profile) -> None:
    """Cardinality means nothing before a value has had a chance to repeat."""
    schema = profile(
        ["net_value", "units", "region"],
        [[1.5 * i, i, ["n", "s"][i % 2]] for i in range(8)],
    )
    assert _role(schema, "net_value") == "measure"
    assert _role(schema, "units") == "measure"
