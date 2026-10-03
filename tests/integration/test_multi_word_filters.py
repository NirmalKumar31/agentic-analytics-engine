"""A category whose name has a space in it.

`where chronotype is Night Owl` bound only `Night`, because the value
class in the equality grammar had no space in it. The failure was safe --
nothing matches `Night`, so the empty-population guard declined the run --
but it declined a question the product should answer, and it never said
the value was the problem.

Two things had to change together. The grammar now reads a quoted value
whole, and an unquoted one up to the first word that begins another clause,
so `by region` is a grouping rather than part of a category. And the value
is settled against the column's own values server-side, which is what lets
an unknown value be refused by name instead of silently selecting nothing.

Everything here runs the real path -- a loaded DuckDB table, the real
`infer_schema`, the real resolver with a real value lookup, the SQL the
engine would execute -- and checks the rows against an independent query.
Three unrelated domains, because a filter grammar that works on one
vocabulary and not another is a rule about that vocabulary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest

from agentic_analytics.analytics.semantic import category_value_lookup, infer_schema
from agentic_analytics.analytics.upload_plan import build_sql, resolve_question
from agentic_analytics.warehouse.session import open_upload_session

# --------------------------------------------------------------- fixtures

SLEEP = (
    "chronotype,mood,age,sleep_hours",
    [
        ("Night Owl", "Very Good", 31, 6.5),
        ("Night Owl", "Poor", 44, 5.0),
        ("Early Bird", "Very Good", 29, 7.5),
        ("Early Bird", "Fair", 52, 8.0),
        ("Night Hawk", "Poor", 38, 6.0),
    ]
    * 20,
)

OFFICE = (
    "product_category,region,revenue",
    [
        ("Home Office", "South", 120.0),
        ("Home Office", "North", 80.0),
        ("Office Supplies", "South", 45.0),
        ("Technology", "North", 300.0),
    ]
    * 25,
)

TRANSIT = (
    "line_name,weather,delay_minutes",
    [
        ("Coast Express", "Heavy Rain", 12.0),
        ("Coast Express", "Clear", 2.0),
        ("Valley Local", "Heavy Rain", 20.0),
        ("Valley Local", "Clear", 1.0),
    ]
    * 25,
)


def _load(tmp_path: Path, name: str, corpus: tuple[str, list[tuple[Any, ...]]]) -> Any:
    header, rows = corpus
    path = tmp_path / f"{name}.csv"
    path.write_text("\n".join([header, *[",".join(str(v) for v in r) for r in rows]]) + "\n")
    return open_upload_session(path, f"{name}.csv", "csv", scratch_dir=tmp_path / name)


def _oracle(corpus: tuple[str, list[tuple[Any, ...]]], sql: str) -> list[tuple[Any, ...]]:
    """The answer computed outside the engine."""
    header, rows = corpus
    con = duckdb.connect()
    names = header.split(",")
    types = []
    for value in rows[0]:
        types.append(
            "DOUBLE"
            if isinstance(value, float)
            else "BIGINT"
            if isinstance(value, int)
            else "VARCHAR"
        )
    con.execute(
        f"CREATE TABLE t({', '.join(f'{n} {t}' for n, t in zip(names, types, strict=True))})"
    )
    con.executemany(f"INSERT INTO t VALUES ({', '.join('?' * len(names))})", rows)
    return con.execute(sql).fetchall()


def _resolve(session: Any, question: str) -> Any:
    schema = infer_schema(session, "uploaded_data")
    return resolve_question(
        question,
        schema.as_dict(),
        value_lookup=category_value_lookup(session, "uploaded_data"),
    )


def _filters(mapping: Any) -> list[tuple[str, str]]:
    return [(f.column, getattr(f, "value", "")) for f in (mapping.filters or ())]


# ------------------------------------------------------- the defect itself


def test_a_two_word_category_binds_whole(tmp_path: Path) -> None:
    session = _load(tmp_path, "sleep", SLEEP)
    mapping = _resolve(session, "What is the average sleep hours where chronotype is Night Owl?")

    assert mapping.confident, mapping.explanation
    assert ("chronotype", "Night Owl") in _filters(mapping), _filters(mapping)
    # Not the first token. This is the whole defect in one assertion.
    assert ("chronotype", "Night") not in _filters(mapping)


def test_a_grouping_after_the_value_is_not_swallowed(tmp_path: Path) -> None:
    session = _load(tmp_path, "sleep", SLEEP)
    mapping = _resolve(
        session, "What is the average sleep hours where chronotype is Night Owl by mood?"
    )

    assert mapping.confident, mapping.explanation
    assert ("chronotype", "Night Owl") in _filters(mapping)
    # `by mood` is a grouping. A greedy value would have eaten it, and the
    # filter would have been for a category nobody has.
    assert "mood" in mapping.dimensions


def test_the_filter_and_the_grouping_both_reach_sql(tmp_path: Path) -> None:
    session = _load(tmp_path, "office", OFFICE)
    mapping = _resolve(
        session, "What is total revenue where product_category is Home Office by region?"
    )
    assert mapping.confident, mapping.explanation

    sql = build_sql(mapping)
    assert sql is not None
    _, rows = __import__("agentic_analytics.analytics.execute", fromlist=["fetch_rows"]).fetch_rows(
        session, sql
    )

    produced = {str(r[0]): round(float(r[1]), 2) for r in rows}
    expected = {
        str(r[0]): round(float(r[1]), 2)
        for r in _oracle(
            OFFICE,
            "SELECT region, sum(revenue) FROM t WHERE product_category = 'Home Office' GROUP BY region",
        )
    }
    assert produced == expected


def test_a_quoted_value_is_taken_whole(tmp_path: Path) -> None:
    session = _load(tmp_path, "sleep", SLEEP)
    for question in (
        'What is the average sleep hours where chronotype is "Night Owl"?',
        "What is the average sleep hours where chronotype equals 'Night Owl'?",
    ):
        mapping = _resolve(session, question)
        assert mapping.confident, (question, mapping.explanation)
        assert ("chronotype", "Night Owl") in _filters(mapping)


def test_a_second_filter_after_a_two_word_value_survives(tmp_path: Path) -> None:
    session = _load(tmp_path, "sleep", SLEEP)
    mapping = _resolve(
        session,
        "What is the average sleep hours where mood is Very Good and age is at least 30?",
    )
    assert mapping.confident, mapping.explanation
    assert ("mood", "Very Good") in _filters(mapping)
    # The numeric restriction is still its own filter: `and age…` must not
    # have been absorbed into the category.
    assert any(getattr(f, "column", "") == "age" for f in mapping.filters)


def test_a_single_word_value_still_works(tmp_path: Path) -> None:
    """The boundary: nothing about the old behaviour may change."""
    session = _load(tmp_path, "office", OFFICE)
    mapping = _resolve(session, "What is total revenue where region is South?")
    assert mapping.confident, mapping.explanation
    assert ("region", "South") in _filters(mapping)


# ------------------------------------------------------------- refusals


def test_a_value_the_column_does_not_have_is_refused_by_name(tmp_path: Path) -> None:
    session = _load(tmp_path, "sleep", SLEEP)
    mapping = _resolve(session, "What is the average sleep hours where chronotype is Day Sleeper?")

    assert not mapping.confident
    assert "Day Sleeper" in mapping.explanation
    assert "chronotype" in mapping.explanation.replace("_", " ")


def test_a_prefix_shared_by_two_values_is_refused_as_ambiguous(tmp_path: Path) -> None:
    """`Night` begins both `Night Owl` and `Night Hawk`.

    Completing it would pick one of two populations on the reader's behalf,
    which is the failure this engine refuses everywhere else.
    """
    session = _load(tmp_path, "sleep", SLEEP)
    mapping = _resolve(session, "What is the average sleep hours where chronotype is Night?")

    assert not mapping.confident
    assert "Night Owl" in mapping.explanation
    assert "Night Hawk" in mapping.explanation


def test_matching_is_case_insensitive_and_executes_the_stored_spelling(
    tmp_path: Path,
) -> None:
    session = _load(tmp_path, "transit", TRANSIT)
    mapping = _resolve(session, "What is the average delay minutes where weather is heavy rain?")

    assert mapping.confident, mapping.explanation
    # Bound to what the data holds, not to what the question typed.
    assert ("weather", "Heavy Rain") in _filters(mapping)


def test_an_injection_shaped_value_never_reaches_the_statement(tmp_path: Path) -> None:
    session = _load(tmp_path, "transit", TRANSIT)
    mapping = _resolve(
        session, 'What is the average delay minutes where weather is "Clear\'; DROP TABLE t;--"?'
    )

    assert not mapping.confident
    sql = build_sql(mapping)
    assert sql is None or "DROP TABLE" not in sql.upper()


@pytest.mark.parametrize(
    ("name", "corpus", "column", "value"),
    [
        ("sleep", SLEEP, "chronotype", "Night Owl"),
        ("office", OFFICE, "product_category", "Home Office"),
        ("transit", TRANSIT, "weather", "Heavy Rain"),
    ],
)
def test_the_same_grammar_holds_in_three_unrelated_domains(
    tmp_path: Path, name: str, corpus: tuple[str, list[tuple[Any, ...]]], column: str, value: str
) -> None:
    session = _load(tmp_path, name, corpus)
    measure = {"sleep": "sleep hours", "office": "revenue", "transit": "delay minutes"}[name]
    mapping = _resolve(session, f"What is the average {measure} where {column} is {value}?")

    assert mapping.confident, mapping.explanation
    assert (column, value) in _filters(mapping)
