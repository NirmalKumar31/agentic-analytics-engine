"""Schema, profile and sample tools.

These are what an agent uses to orient itself in a dataset. They are
deliberately aggregate-first: `profile_table` returns statistics, and
`sample_rows` is capped hard, because raw rows are the only route by which
unaggregated user data can reach a prompt.
"""

from __future__ import annotations

from typing import Any

from agentic_analytics.analytics.execute import run_query
from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.warehouse.session import AnalysisSession

NUMERIC_TYPES = {
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "FLOAT",
    "DOUBLE",
    "REAL",
    "DECIMAL",
}
TEMPORAL_TYPES = {"DATE", "TIMESTAMP", "TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS", "TIME"}


class TableNotFound(KeyError):
    """The named table is not in this session."""


def resolve_table(session: AnalysisSession, table: str) -> str:
    """Case-insensitively resolve a table name, or raise.

    Returns the canonical name. Callers quote this value; it is never the
    raw string the agent supplied.
    """
    for name in session.tables:
        if name.lower() == table.lower():
            return name
    raise TableNotFound(f"unknown table {table!r}; available tables: {sorted(session.tables)}")


def list_tables(session: AnalysisSession) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "row_count": t.row_count, "column_count": len(t.columns)}
        for t in session.tables.values()
    ]


def describe_table(session: AnalysisSession, table: str) -> dict[str, Any]:
    name = resolve_table(session, table)
    info = session.tables[name]
    return {
        "table": name,
        "row_count": info.row_count,
        "columns": info.columns,
        "dataset_fingerprint": session.dataset_fingerprint,
    }


def _base_type(duck_type: str) -> str:
    return duck_type.split("(")[0].strip().upper()


def profile_table(
    session: AnalysisSession, table: str, timeout_seconds: float = 20.0
) -> ResultSnapshot:
    """Per-column statistics: null rate, distinct count, and range.

    One query per table rather than one per column, so profiling a wide table
    stays a single bounded execution.
    """
    name = resolve_table(session, table)
    info = session.tables[name]
    total = max(info.row_count, 1)

    selects: list[str] = []
    for column in info.columns:
        col, dtype = column["name"], _base_type(column["type"])
        q = f'"{col}"'
        selects.append(
            f"SELECT {_lit(col)} AS column_name, {_lit(dtype)} AS data_type, "
            f"COUNT({q}) AS non_null, "
            f"ROUND(100.0 * (1 - COUNT({q}) / {total}.0), 2) AS null_pct, "
            f"COUNT(DISTINCT {q}) AS distinct_count, "
            + (
                f"ROUND(MIN({q})::DOUBLE, 4)::VARCHAR AS min_value, "
                f"ROUND(MAX({q})::DOUBLE, 4)::VARCHAR AS max_value, "
                f"ROUND(AVG({q})::DOUBLE, 4)::VARCHAR AS mean_value"
                if dtype in NUMERIC_TYPES
                else (
                    f"MIN({q})::VARCHAR AS min_value, MAX({q})::VARCHAR AS max_value, "
                    "NULL::VARCHAR AS mean_value"
                    if dtype in TEMPORAL_TYPES
                    else "NULL::VARCHAR AS min_value, NULL::VARCHAR AS max_value, "
                    "NULL::VARCHAR AS mean_value"
                )
            )
            + f' FROM "{name}"'
        )

    sql = "\nUNION ALL\n".join(selects)
    return run_query(
        session,
        sql,
        tool_name="profile_table",
        parameters={"table": name},
        max_rows=len(info.columns),
        timeout_seconds=timeout_seconds,
        guard=False,  # composed here from the session's own catalogue
    )


def sample_rows(
    session: AnalysisSession, table: str, limit: int, max_sample_rows: int = 20
) -> ResultSnapshot:
    """A small, deterministic sample of raw rows.

    Hard-capped: this is the one tool that discloses unaggregated values, so
    the ceiling is enforced here rather than trusted from the caller.
    """
    name = resolve_table(session, table)
    effective = max(1, min(int(limit), max_sample_rows))
    warnings = [f"sample capped at {max_sample_rows} rows"] if int(limit) > max_sample_rows else []
    return run_query(
        session,
        f'SELECT * FROM "{name}" LIMIT {effective}',
        tool_name="sample_rows",
        parameters={"table": name, "limit": effective},
        max_rows=effective,
        guard=False,
        extra_warnings=warnings,
    )


def _lit(value: str) -> str:
    """Single-quoted SQL string literal with quotes doubled."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"
