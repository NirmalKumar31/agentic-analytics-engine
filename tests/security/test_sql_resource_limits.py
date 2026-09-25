"""Read-only denial of service.

A query can be a `SELECT`, touch only real tables, and still burn a core for
as long as it is allowed to. These cover the shapes that do that, and assert
the legitimate analytical queries next to them still pass -- a guard that
rejects everything is not a guard.

Two layers are exercised: the static checks in SQLGuard, which reject the
obvious cases before any work starts, and the engine's own cancellation,
which is the backstop for everything else.
"""

from __future__ import annotations

import time

import pytest

from agentic_analytics.analytics.execute import QueryError, run_query
from agentic_analytics.warehouse.session import AnalysisSession
from agentic_analytics.warehouse.sqlguard import (
    MAX_AST_DEPTH,
    MAX_AST_NODES,
    MAX_CTES,
    MAX_GENERATED_ROWS,
    MAX_JOINS,
    SQLGuardError,
    check_sql,
)

TABLES = {
    "orders",
    "order_items",
    "products",
    "customers",
    "returns",
    "shipping_events",
    "marketing_daily",
}


def guard(sql: str) -> str:
    return check_sql(sql, allowed_tables=TABLES, max_length=500_000).sql


# ------------------------------------------------------------ generators

GENERATOR_ABUSE = [
    ("trillion_row_range", "SELECT count(*) FROM range(1000000000000)"),
    ("huge_generate_series", "SELECT count(*) FROM generate_series(1, 999999999999)"),
    ("scientific_notation", "SELECT count(*) FROM range(1e12)"),
    ("negative_huge", "SELECT count(*) FROM range(-999999999999, 0)"),
    (
        "subquery_bound",
        "SELECT * FROM range((SELECT count(*) FROM orders))",
    ),
    ("column_bound", "SELECT * FROM orders, range(orders.order_id)"),
    (
        "generator_cross_join",
        "SELECT count(*) FROM range(50000000) a, range(50000000) b",
    ),
]


@pytest.mark.parametrize("name,sql", GENERATOR_ABUSE, ids=[n for n, _ in GENERATOR_ABUSE])
def test_unbounded_generators_are_refused(name: str, sql: str) -> None:
    with pytest.raises(SQLGuardError):
        guard(sql)


def test_a_bounded_generator_is_still_allowed() -> None:
    """A date spine is a legitimate use and must keep working."""
    assert guard("SELECT * FROM range(1000)")
    assert guard("SELECT * FROM generate_series(1, 365)")
    assert guard(f"SELECT * FROM range({MAX_GENERATED_ROWS})")


def test_the_generator_limit_is_the_boundary() -> None:
    guard(f"SELECT * FROM range({MAX_GENERATED_ROWS})")
    with pytest.raises(SQLGuardError, match="would generate"):
        guard(f"SELECT * FROM range({MAX_GENERATED_ROWS + 1})")


# --------------------------------------------------------------- structure

STRUCTURAL_ABUSE = [
    (
        "cross_join_explosion",
        "SELECT count(*) FROM orders a, orders b, orders c, orders d",
    ),
    (
        "too_many_joins",
        "SELECT 1 FROM orders o "
        + " ".join(
            f"JOIN products p{i} ON p{i}.product_id = o.order_id" for i in range(MAX_JOINS + 3)
        ),
    ),
    (
        "too_many_ctes",
        "WITH " + ", ".join(f"c{i} AS (SELECT 1 AS x)" for i in range(MAX_CTES + 6)) + " SELECT 1",
    ),
    (
        "enormous_projection",
        "SELECT " + ", ".join(f"{i} AS c{i}" for i in range(MAX_AST_NODES)) + " FROM orders",
    ),
    (
        "recursive_cte",
        "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM t) SELECT * FROM t",
    ),
    (
        "bounded_recursive_cte_still_refused",
        "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 5) "
        "SELECT * FROM t",
    ),
]


@pytest.mark.parametrize("name,sql", STRUCTURAL_ABUSE, ids=[n for n, _ in STRUCTURAL_ABUSE])
def test_structurally_expensive_queries_are_refused(name: str, sql: str) -> None:
    with pytest.raises(SQLGuardError):
        guard(sql)


def test_deep_nesting_does_not_crash_the_parser() -> None:
    """sqlglot parses by recursive descent.

    Deep nesting exhausts the Python stack *during parsing*, before any
    structural check could run, so the guard has to catch the RecursionError
    and turn a crash into a refusal.
    """
    for depth in (MAX_AST_DEPTH + 10, 400, 2000):
        sql = "SELECT " + "(" * depth + "1" + ")" * depth
        with pytest.raises(SQLGuardError):
            guard(sql)


def test_deeply_nested_subqueries_are_refused() -> None:
    sql = "SELECT 1"
    for _ in range(MAX_AST_DEPTH + 5):
        sql = f"SELECT * FROM ({sql}) AS t"
    with pytest.raises(SQLGuardError):
        guard(sql)


def test_an_overlong_statement_is_refused() -> None:
    sql = "SELECT " + ", ".join(f"'{i}' AS c{i}" for i in range(5000)) + " FROM orders"
    with pytest.raises(SQLGuardError, match="character limit"):
        check_sql(sql, allowed_tables=TABLES, max_length=8000)


REAL_ANALYTICAL_QUERIES = [
    (
        "three_way_join",
        "SELECT p.category, SUM(i.quantity * i.unit_price) AS revenue "
        "FROM orders o JOIN order_items i USING(order_id) "
        "JOIN products p USING(product_id) GROUP BY 1",
    ),
    (
        "several_ctes",
        "WITH a AS (SELECT 1 AS x), b AS (SELECT 2 AS y), c AS (SELECT 3 AS z) "
        "SELECT * FROM a, b, c",
    ),
    (
        "window_function",
        "SELECT order_id, ROW_NUMBER() OVER (PARTITION BY customer_id "
        "ORDER BY order_date) FROM orders",
    ),
    (
        "union_of_aggregates",
        "SELECT 'a' AS k, COUNT(*) AS n FROM orders UNION ALL SELECT 'b', COUNT(*) FROM returns",
    ),
    ("date_spine_join", "SELECT d.i FROM range(365) d(i) LEFT JOIN orders o ON o.order_id = d.i"),
]


@pytest.mark.parametrize(
    "name,sql", REAL_ANALYTICAL_QUERIES, ids=[n for n, _ in REAL_ANALYTICAL_QUERIES]
)
def test_legitimate_analytical_queries_still_pass(name: str, sql: str) -> None:
    """A guard that rejects real work is not a usable guard."""
    assert guard(sql).strip()


# ------------------------------------------------ the engine-level backstop


def test_a_slow_query_is_cancelled_and_stops_consuming_cpu(
    session: AnalysisSession,
) -> None:
    """The timeout must stop the query, not merely stop waiting for it.

    Composed internal SQL bypasses the guard, which is what makes this a test
    of DuckDB's cancellation rather than of the static generator bound.
    """
    started = time.monotonic()
    with pytest.raises(QueryError, match="time limit"):
        run_query(
            session,
            "SELECT count(*) FROM range(400000000) t(i) WHERE i % 7 = 0",
            tool_name="run_readonly_sql",
            timeout_seconds=1.0,
            guard=False,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 20, f"cancellation took {elapsed:.1f}s"

    # The connection survives, and the next query is unaffected.
    assert run_query(session, "SELECT 1 AS x", tool_name="run_readonly_sql").rows == [[1]]


def test_result_rows_are_capped_before_they_reach_an_agent(
    session: AnalysisSession,
) -> None:
    snapshot = run_query(
        session, "SELECT * FROM order_items", tool_name="run_readonly_sql", max_rows=25
    )
    assert snapshot.row_count == 25
    assert snapshot.truncated is True
    assert any("truncated" in w for w in snapshot.warnings)


def test_a_high_cardinality_group_by_is_bounded_by_the_row_cap(
    session: AnalysisSession,
) -> None:
    """Grouping by a near-unique column cannot flood the caller."""
    snapshot = run_query(
        session,
        "SELECT order_id, COUNT(*) AS n FROM order_items GROUP BY 1",
        tool_name="run_readonly_sql",
        max_rows=50,
    )
    assert snapshot.row_count == 50
    assert snapshot.truncated is True


def test_the_engine_refuses_host_access_even_without_the_guard(
    session: AnalysisSession,
) -> None:
    """Second layer, exercised with the first deliberately bypassed."""
    import duckdb

    for sql in (
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "COPY (SELECT 1) TO '/tmp/leak.csv'",
        "INSTALL httpfs",
    ):
        with pytest.raises(duckdb.Error):
            session.con.execute(sql)
