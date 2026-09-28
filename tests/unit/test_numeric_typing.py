"""What counts as a number in a result cell.

The list of things that must *not* be read as numbers is longer than the
list of things that must, and it is the interesting half: every entry is a
value some dataset really contains.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from agentic_analytics.verification.typing import as_number, is_numeric_type

NUMERIC = "DOUBLE"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("44.2128", Decimal("44.2128")),
        ("176851.26", Decimal("176851.26")),
        ("0", Decimal("0")),
        ("4000", Decimal("4000")),
        ("-12.5", Decimal("-12.5")),
        ("+3", Decimal("3")),
        (".5", Decimal("0.5")),
        ("2.", Decimal("2")),
        ("1e3", Decimal("1000")),
        ("1.5E-2", Decimal("0.015")),
    ],
)
def test_canonical_decimals_in_a_declared_numeric_column_are_read(
    text: str, expected: Decimal
) -> None:
    assert as_number(text, declared_type=NUMERIC) == expected


def test_the_exact_value_survives(y: None = None) -> None:
    """Decimal, not float: the claim is checked against the digits the
    engine produced."""
    assert as_number("176851.26", declared_type=NUMERIC) == Decimal("176851.26")
    assert str(as_number("0.1", declared_type=NUMERIC)) == "0.1"


@pytest.mark.parametrize(
    "text",
    [
        "0012345",  # an order reference that happens to be digits
        "2025-01-01",  # a date
        "2025/01/01",
        "1,234",  # thousands separator in one locale, decimal in another
        "1 234",
        "1.234,56",
        "$40.00",  # currency belongs in metadata, not in the digits
        "40%",
        "NaN",
        "nan",
        "inf",
        "-Infinity",
        "1e",  # malformed exponent
        "--3",
        "3..1",
        "",
        "   ",
        "twelve",
        "0x1F",
        "1_000",
    ],
)
def test_these_are_never_numbers(text: str) -> None:
    assert as_number(text, declared_type=NUMERIC) is None


def test_the_coercion_path_is_defence_in_depth_today() -> None:
    """Worth stating plainly: after the mean was fixed at its creation
    boundary, no production result reaches the string branch.

    Every numeric statistic the profile emits is now numerically typed,
    and the two that remain text -- `min_value` and `max_value` -- are
    declared VARCHAR because one `UNION ALL` spans columns of every type,
    so they are correctly refused rather than coerced. Reverting the
    declared-type read in the critic therefore breaks nothing today,
    which is why it is not counted as a load-bearing fix.

    The consequence is a known gap, not a fixed one: a claim citing the
    minimum or maximum of a numeric column is still reported as
    numerically unsupported. Recorded in docs/LIMITATIONS.md rather than
    quietly coerced, because reading those would mean trusting text whose
    column declares itself VARCHAR.
    """
    assert as_number("44.2128", declared_type="VARCHAR") is None


def test_a_numeric_looking_string_in_an_undeclared_column_is_not_read() -> None:
    """The declaration is what licenses the read. `0012345` is a reference
    in a VARCHAR column and stays one."""
    assert as_number("44.2128") is None
    assert as_number("44.2128", declared_type="VARCHAR") is None
    assert as_number("0012345", declared_type="VARCHAR") is None


def test_real_numbers_need_no_declaration() -> None:
    assert as_number(4000) == Decimal(4000)
    assert as_number(44.2128) == Decimal("44.2128")
    assert as_number(Decimal("1.5")) == Decimal("1.5")


def test_booleans_are_not_numbers() -> None:
    """`bool` is an `int` in Python, and a flag is not a measurement."""
    assert as_number(True) is None
    assert as_number(False) is None
    assert as_number(True, declared_type=NUMERIC) is None


def test_null_is_not_a_number() -> None:
    assert as_number(None) is None
    assert as_number(None, declared_type=NUMERIC) is None


def test_non_finite_floats_are_refused() -> None:
    assert as_number(float("nan")) is None
    assert as_number(float("inf")) is None
    assert as_number(float("-inf")) is None


@pytest.mark.parametrize(
    "declared", ["DOUBLE", "BIGINT", "DECIMAL(18,2)", "decimal(9,3)", "integer"]
)
def test_numeric_type_names_are_recognised(declared: str) -> None:
    assert is_numeric_type(declared)


@pytest.mark.parametrize("declared", ["VARCHAR", "DATE", "TIMESTAMP", "BOOLEAN", "", None])
def test_non_numeric_type_names_are_not(declared: str | None) -> None:
    assert not is_numeric_type(declared)
