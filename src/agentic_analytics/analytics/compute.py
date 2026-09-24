"""Metric computation.

The SQL for a metric is assembled here from the definition in ``metrics.yml``,
the validated dimension list and parameter-bound filters. A model chooses
*what* to compute; it never writes the aggregate.
"""

from __future__ import annotations

from typing import Any

from agentic_analytics.analytics.execute import QueryError, run_query
from agentic_analytics.analytics.filters import Filter, build_where
from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.warehouse.metrics import MetricRegistry, grain_expression
from agentic_analytics.warehouse.session import AnalysisSession

MAX_DIMENSIONS = 3
MAX_SEGMENTS = 20


class MetricError(ValueError):
    """The requested metric, dimension or grain is not valid."""


def _registry(session: AnalysisSession) -> MetricRegistry:
    if session.registry is None:
        raise MetricError(
            "this dataset has no metric layer (it is a single uploaded table); "
            "use profile_table and run_readonly_sql instead"
        )
    return session.registry


def _render(
    session: AnalysisSession,
    metric: str,
    dimensions: list[str],
    filters: list[Filter],
    time_grain: str | None,
) -> tuple[str, list[Any], list[str]]:
    """Build the metric query. Returns (sql, params, output column names)."""
    reg = _registry(session)
    try:
        metric_def = reg.metric(metric)
    except KeyError as exc:
        raise MetricError(str(exc)) from None
    model = reg.model_for(metric)

    if len(dimensions) > MAX_DIMENSIONS:
        raise MetricError(
            f"at most {MAX_DIMENSIONS} dimensions may be requested, got {len(dimensions)}"
        )

    select_parts: list[str] = []
    group_parts: list[str] = []
    out_columns: list[str] = []

    if time_grain:
        expr = grain_expression(model.time_field, time_grain)
        select_parts.append(f"{expr} AS period")
        group_parts.append(expr)
        out_columns.append("period")

    for dim in dimensions:
        try:
            column = reg.resolve_dimension(metric, dim)
        except KeyError as exc:
            raise MetricError(str(exc)) from None
        select_parts.append(f'"{column}" AS "{dim}"')
        group_parts.append(f'"{column}"')
        out_columns.append(dim)

    select_parts.append(f'{metric_def.sql.strip()} AS "{metric}"')
    out_columns.append(metric)

    where, params = build_where(filters, reg.filterable_columns(metric), model.dimensions)

    sql = f"SELECT {', '.join(select_parts)}\nFROM (\n{model.sql.strip()}\n) AS m{where}"
    if group_parts:
        sql += f"\nGROUP BY {', '.join(group_parts)}"
        sql += f"\nORDER BY {', '.join(group_parts)}"
    return sql, params, out_columns


def compute_metric(
    session: AnalysisSession,
    metric: str,
    dimensions: list[str] | None = None,
    filters: list[Filter] | None = None,
    time_grain: str | None = None,
    *,
    task_id: str | None = None,
    max_rows: int = 500,
    timeout_seconds: float = 20.0,
) -> ResultSnapshot:
    """Compute one metric, optionally cut by dimensions and time grain."""
    dims = list(dimensions or [])
    sql, params, _ = _render(session, metric, dims, list(filters or []), time_grain)
    return _run_with_params(
        session,
        sql,
        params,
        tool_name="compute_metric",
        parameters={
            "metric": metric,
            "dimensions": dims,
            "time_grain": time_grain,
            "filters": [f.model_dump() for f in (filters or [])],
        },
        task_id=task_id,
        max_rows=max_rows,
        timeout_seconds=timeout_seconds,
    )


def compare_segments(
    session: AnalysisSession,
    metric: str,
    dimension: str,
    segments: list[str] | None = None,
    filters: list[Filter] | None = None,
    *,
    task_id: str | None = None,
    max_rows: int = 500,
    timeout_seconds: float = 20.0,
) -> ResultSnapshot:
    """Compare one metric across the values of one dimension.

    Also returns each segment's share of the total and its difference from
    the best segment, so the ranking an agent describes is in the result
    rather than computed in its head.
    """
    _registry(session)
    all_filters = list(filters or [])
    if segments:
        if len(segments) > MAX_SEGMENTS:
            raise MetricError(f"at most {MAX_SEGMENTS} segments may be compared")
        all_filters.append(Filter(column=dimension, op="in", value=list(segments)))

    inner, params, _ = _render(session, metric, [dimension], all_filters, None)
    quoted_metric = f'"{metric}"'
    sql = (
        f"WITH base AS (\n{inner}\n)\n"
        f'SELECT "{dimension}", {quoted_metric},\n'
        # A share of total is only meaningful when every segment shares a
        # sign. For a metric like contribution margin that can go negative,
        # returning NULL is better than returning a number a model might cite.
        f"  CASE WHEN MIN({quoted_metric}) OVER () >= 0 THEN\n"
        f"    ROUND(100.0 * {quoted_metric} / NULLIF(SUM({quoted_metric}) OVER (), 0), 4)\n"
        f"  END AS share_of_total_pct,\n"
        f"  ROUND({quoted_metric} - MAX({quoted_metric}) OVER (), 6) AS diff_vs_best,\n"
        f"  ROW_NUMBER() OVER (ORDER BY {quoted_metric} DESC NULLS LAST) AS rank_desc\n"
        f"FROM base\nORDER BY {quoted_metric} DESC NULLS LAST"
    )
    return _run_with_params(
        session,
        sql,
        params,
        tool_name="compare_segments",
        parameters={
            "metric": metric,
            "dimension": dimension,
            "segments": segments or [],
            "filters": [f.model_dump() for f in (filters or [])],
        },
        task_id=task_id,
        max_rows=max_rows,
        timeout_seconds=timeout_seconds,
    )


def analyze_timeseries(
    session: AnalysisSession,
    metric: str,
    grain: str = "month",
    time_column: str | None = None,
    filters: list[Filter] | None = None,
    *,
    task_id: str | None = None,
    max_rows: int = 500,
    timeout_seconds: float = 20.0,
) -> ResultSnapshot:
    """Metric over time, with period-over-period change already computed."""
    reg = _registry(session)
    try:
        model = reg.model_for(metric)
    except KeyError as exc:
        raise MetricError(str(exc)) from None
    if time_column and time_column != model.time_field:
        raise MetricError(
            f"metric {metric!r} is measured over {model.time_field!r}, not {time_column!r}"
        )

    inner, params, _ = _render(session, metric, [], list(filters or []), grain)
    q = f'"{metric}"'
    sql = (
        f"WITH base AS (\n{inner}\n)\n"
        f"SELECT period, {q},\n"
        f"  LAG({q}) OVER (ORDER BY period) AS prev_period,\n"
        f"  ROUND({q} - LAG({q}) OVER (ORDER BY period), 6) AS change_abs,\n"
        f"  ROUND(100.0 * ({q} - LAG({q}) OVER (ORDER BY period))\n"
        f"        / NULLIF(ABS(LAG({q}) OVER (ORDER BY period)), 0), 4) AS change_pct\n"
        f"FROM base\nORDER BY period"
    )
    return _run_with_params(
        session,
        sql,
        params,
        tool_name="analyze_timeseries",
        parameters={
            "metric": metric,
            "grain": grain,
            "time_column": model.time_field,
            "filters": [f.model_dump() for f in (filters or [])],
        },
        task_id=task_id,
        max_rows=max_rows,
        timeout_seconds=timeout_seconds,
    )


def _run_with_params(
    session: AnalysisSession,
    sql: str,
    params: list[Any],
    *,
    tool_name: str,
    parameters: dict[str, Any],
    task_id: str | None,
    max_rows: int,
    timeout_seconds: float,
) -> ResultSnapshot:
    """Inline bound parameters, then run through the single execution path.

    The snapshot has to record the literal SQL that produced the numbers,
    because that text is what a reviewer reads in the provenance drawer. The
    values here are already validated -- they came from a model-supplied
    filter that passed :func:`build_where` -- and are re-quoted defensively.
    """
    final_sql = _inline_params(sql, params)
    try:
        return run_query(
            session,
            final_sql,
            tool_name=tool_name,
            parameters=parameters,
            task_id=task_id,
            max_rows=max_rows,
            timeout_seconds=timeout_seconds,
            guard=False,  # composed from validated metric definitions
        )
    except QueryError:
        raise


def _inline_params(sql: str, params: list[Any]) -> str:
    """Replace each ``?`` with a safely quoted literal."""
    if not params:
        return sql
    out: list[str] = []
    index = 0
    in_string = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            in_string = not in_string
            out.append(ch)
        elif ch == "?" and not in_string:
            if index >= len(params):
                raise MetricError("internal error: more placeholders than parameters")
            out.append(_quote_literal(params[index]))
            index += 1
        else:
            out.append(ch)
        i += 1
    if index != len(params):
        raise MetricError("internal error: unused filter parameters")
    return "".join(out)


def _quote_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(value)
    text = str(value).replace("'", "''")
    if "\x00" in text:
        raise MetricError("filter value contains a null byte")
    return f"'{text}'"
