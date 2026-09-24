"""The single place a query reaches DuckDB.

Every analytical tool funnels through :func:`run_query`, which guards the SQL,
bounds the runtime, truncates the result, and records a
:class:`~agentic_analytics.analytics.results.ResultSnapshot`. Because there is
exactly one execution path, "was this query checked?" has one answer.
"""

from __future__ import annotations

import datetime as dt
import decimal
import math
import threading
import time
from typing import Any

import duckdb

from agentic_analytics.analytics.results import ResultSnapshot, Scalar
from agentic_analytics.warehouse.session import AnalysisSession
from agentic_analytics.warehouse.sqlguard import SQLGuardError, check_sql


class QueryError(RuntimeError):
    """A query failed. The message is safe to show a user."""


def _coerce(value: Any) -> Scalar:
    """Convert a DuckDB value into something JSON-serialisable.

    Dates become ISO strings and decimals become floats, because the snapshot
    travels over MCP as JSON and then into a chart spec. NaN and infinity
    become ``None``: they are not valid JSON and a chart cannot plot them.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<{len(bytes(value))} bytes>"
    return str(value)


def run_query(
    session: AnalysisSession,
    sql: str,
    *,
    tool_name: str,
    parameters: dict[str, Any] | None = None,
    task_id: str | None = None,
    max_rows: int = 500,
    timeout_seconds: float = 20.0,
    max_sql_length: int = 8000,
    guard: bool = True,
    extra_warnings: list[str] | None = None,
) -> ResultSnapshot:
    """Validate, execute and snapshot a single read-only query.

    ``guard=False`` is reserved for SQL this package composed itself from a
    validated metric definition. Statements that originated with a model are
    always guarded.
    """
    warnings = list(extra_warnings or [])
    if guard:
        try:
            checked = check_sql(
                sql,
                allowed_tables={t.lower() for t in session.table_names},
                max_length=max_sql_length,
            )
        except SQLGuardError as exc:
            raise QueryError(str(exc)) from None
        sql_to_run = checked.sql
    else:
        sql_to_run = sql

    # Fetch one extra row so truncation is detected rather than guessed.
    fetch_limit = max_rows + 1
    wrapped = f"SELECT * FROM (\n{sql_to_run}\n) AS _aae_q LIMIT {fetch_limit}"

    started = time.time()
    timer = threading.Timer(timeout_seconds, session.con.interrupt)
    timed_out = False
    try:
        with session.lock:
            timer.start()
            try:
                cursor = session.con.execute(wrapped)
                description = cursor.description or []
                columns = [str(d[0]) for d in description]
                raw_rows = cursor.fetchall()
            finally:
                timer.cancel()
    except duckdb.InterruptException:
        timed_out = True
        raise QueryError(
            f"query exceeded the {timeout_seconds:g}s time limit and was cancelled"
        ) from None
    except duckdb.Error as exc:
        # DuckDB messages name columns and tables, which is exactly what an
        # agent needs to correct itself, and contains no host detail once
        # external access is off.
        raise QueryError(f"query failed: {exc}") from None
    finally:
        if not timed_out:
            timer.cancel()

    duration_ms = (time.time() - started) * 1000.0
    truncated = len(raw_rows) > max_rows
    if truncated:
        raw_rows = raw_rows[:max_rows]
        warnings.append(f"result truncated to {max_rows} rows; aggregate further or add a filter")

    rows: list[list[Scalar]] = [[_coerce(v) for v in row] for row in raw_rows]
    snapshot = ResultSnapshot(
        tool_name=tool_name,
        task_id=task_id,
        sql=sql_to_run,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        dataset_fingerprint=session.dataset_fingerprint,
        started_at=started,
        duration_ms=round(duration_ms, 3),
        parameters=parameters or {},
        warnings=warnings,
    )
    return session.results.put(snapshot)


def fetch_rows(
    session: AnalysisSession, sql: str, timeout_seconds: float = 20.0
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Run internal SQL and return raw rows without creating a snapshot.

    Used by the statistical tools, which need full-precision values to
    compute from and publish their own snapshot afterwards.
    """
    timer = threading.Timer(timeout_seconds, session.con.interrupt)
    try:
        with session.lock:
            timer.start()
            try:
                cursor = session.con.execute(sql)
                columns = [str(d[0]) for d in (cursor.description or [])]
                return columns, cursor.fetchall()
            finally:
                timer.cancel()
    except duckdb.InterruptException:
        raise QueryError(
            f"query exceeded the {timeout_seconds:g}s time limit and was cancelled"
        ) from None
    except duckdb.Error as exc:
        raise QueryError(f"query failed: {exc}") from None
