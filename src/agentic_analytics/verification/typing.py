"""Reading a number from a result cell, strictly.

A profile statistic reached the verifier as the string ``"44.2128"`` and
was rejected as unsupported, because the arithmetic check only accepts
``int`` and ``float``. The claim was exactly right; the cast was to blame.
The first fix that suggests itself -- coerce anything that looks numeric --
is worse than the defect. ``"0012345"`` is an order reference, ``"2025"``
is a year, ``"1,234"`` means one thousand in one locale and one point two
in another, and ``True`` is not one.

So coercion is narrow and declared. A cell is read as a number only when
the result says that column holds a numeric statistic, and only when the
text is a canonical finite decimal. Everything else stays what it was and
the arithmetic check treats it as it always did.

`Decimal` throughout: a claim of ``176851.26`` is checked against the
digits the engine produced, and binary floating point does not represent
that value exactly.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

#: Canonical decimal text. A leading sign, digits, at most one point, and
#: an optional exponent. No spaces, no separators, no currency, no percent
#: -- each of those means something the cell does not say.
_CANONICAL = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")

#: A redundant leading zero: `0012345`. A numeric column never produces
#: one -- the engine writes 12345 -- so text that carries them came from
#: somewhere that was keeping them, which is what an identifier does.
#: `0` and `0.5` are unaffected.
_PADDED = re.compile(r"^[+-]?0\d")

#: Type names whose values are numeric statistics. Matched against the
#: declared type of the column the cell came from, not against the text.
NUMERIC_TYPE_NAMES = frozenset(
    {
        "BIGINT",
        "DOUBLE",
        "DECIMAL",
        "FLOAT",
        "REAL",
        "HUGEINT",
        "INTEGER",
        "SMALLINT",
        "TINYINT",
        "UBIGINT",
        "UINTEGER",
        "USMALLINT",
        "UTINYINT",
        "NUMERIC",
    }
)


def is_numeric_type(declared: str | None) -> bool:
    """Whether a declared column type holds numbers.

    Matched on the base name, so ``DECIMAL(18,2)`` counts.
    """
    if not declared:
        return False
    base = declared.split("(")[0].strip().upper()
    return base in NUMERIC_TYPE_NAMES


def as_number(value: Any, *, declared_type: str | None = None) -> Decimal | None:
    """The cell's numeric value, or ``None`` when it does not have one.

    Real numbers are returned as they are. Text is read only when
    `declared_type` says the column is numeric, which is why a bare
    reference like ``"0012345"`` in a VARCHAR column is never a number
    however much it looks like one.
    """
    # `bool` is an `int` in Python and is not a measurement.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # NaN and the infinities are not values a claim can cite.
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return Decimal(str(value))
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, str):
        if not is_numeric_type(declared_type):
            return None
        text = value.strip()
        if not _CANONICAL.match(text) or _PADDED.match(text):
            return None
        try:
            parsed = Decimal(text)
        except InvalidOperation:
            return None
        return parsed if parsed.is_finite() else None
    return None
