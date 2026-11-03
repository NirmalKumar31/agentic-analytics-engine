"""Regressions at the SQL and JSON boundaries."""

import decimal
import json

import duckdb
import pytest

from agentic_analytics.analytics.execute import _coerce
from agentic_analytics.analytics.filters import Filter, FilterError, build_where


def test_quoted_filter_identifier_is_literal_and_value_is_bound() -> None:
    column = 'sales"region'
    clause, parameters = build_where([Filter(column=column, value="West' OR 1=1 --")], {column})
    with duckdb.connect() as connection:
        connection.execute('CREATE TABLE sample ("sales""region" VARCHAR)')
        connection.execute("INSERT INTO sample VALUES (?), (?)", ["East", parameters[0]])
        rows = connection.execute("SELECT * FROM sample" + clause, parameters).fetchall()
    assert rows == [("West' OR 1=1 --",)]


def test_mapped_filter_identifier_is_also_escaped() -> None:
    clause, parameters = build_where(
        [Filter(column="region", value="West")], {"region"}, {"region": 'sales"region'}
    )
    assert clause == ' WHERE "sales""region" = ?'
    assert parameters == ["West"]
    with pytest.raises(FilterError):
        build_where([Filter(column="unknown", value="West")], {"region"})


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", "1e9999"])
def test_decimal_nonfinite_or_overflow_is_json_null(value: str) -> None:
    result = _coerce(decimal.Decimal(value))
    assert json.dumps({"value": result}, allow_nan=False) == '{"value": null}'


def test_finite_decimal_remains_a_number() -> None:
    assert _coerce(decimal.Decimal("12.50")) == 12.5
