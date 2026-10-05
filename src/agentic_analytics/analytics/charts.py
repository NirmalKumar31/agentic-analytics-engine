"""Chart selection as a pure function of the contract and the result.

Chart choice used to be a model call, which is why the same verified data
could produce a chart in one pane, a different encoding in the other, and
none at all in a third run. A chart is a rendering of a result the engine
already computed and verified; nothing about that decision needs judgement,
and a model asked for it introduces variance with no authority.

So: the contract says what was asked for, the snapshot says what came back,
and the pair determines one specification. Identical contracts over
identical results produce identical charts, which is what lets Compare Both
render one shared chart instead of two that disagree.

When no chart would help, that is stated as a reason rather than left as an
empty panel.
"""

from __future__ import annotations

from typing import Any, Literal

from agentic_analytics.analytics.labels import output_label
from agentic_analytics.analytics.results import ResultSnapshot

ChartKind = Literal["bar", "line", "grouped_bar", "ranked_bar", "kpi", "none"]

#: Categories a bar chart can show before it stops being readable. Above
#: this the table is the honest presentation and the chart is declined.
MAX_BAR_CATEGORIES = 60
#: Series in a multi-series line. More lines than this is a tangle.
MAX_SERIES = 8
#: Cells in a grouped/stacked chart, as categories x series.
MAX_GROUPED_CELLS = 240


def _numeric_column(snapshot: ResultSnapshot, exclude: set[str]) -> str | None:
    """The measure column, found through lineage rather than guessed."""
    for column, origin in (snapshot.column_lineage or {}).items():
        if origin.get("kind") == "aggregate" and column in snapshot.columns:
            return column
    for column in snapshot.columns:
        if column not in exclude and column != "row_count":
            return column
    return None


def _label(column: str) -> str:
    """An axis, legend or tooltip title a reader can act on.

    The declared label comes first. `rate_effect` separated into "rate
    effect" is still the arithmetic's name for itself, and a chart axis is
    a primary surface -- the published PDF carried `rate_effect` down its
    y-axis, under a title that said the same thing.

    Falling back to word-separation keeps every other column working and
    claims nothing: an uploaded file's `branch_no` becomes "branch no",
    not "branch number".
    """
    declared = output_label(column)
    if declared is not None:
        return declared
    return column.replace("_", " ")


def _measure_format(snapshot: ResultSnapshot, measure: str) -> str:
    """The d3 number format for a measure, matching how the table shows it.

    A revenue total printed as ``83,373,290.48`` in the table and ``83M`` on
    the axis beside it reads as two different figures. The table shows whole
    numbers without decimals and fractional numbers to two places, so the
    axis is decided the same way, from the values actually returned.
    """
    if measure not in snapshot.columns:  # pragma: no cover - callers check first
        return ","
    position = snapshot.columns.index(measure)
    fractional = False
    for row in snapshot.rows:
        value = row[position] if position < len(row) else None
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int):
            continue
        if isinstance(value, float) and value != int(value):
            fractional = True
            break
    return ",.2f" if fractional else ","


def _tooltip(
    fields: list[tuple[str, str]], measure: str, value_format: str
) -> list[dict[str, Any]]:
    """Hover detail: the cuts that identify a mark, then its measured value."""
    entries: list[dict[str, Any]] = [
        {"field": field, "type": kind, "title": _label(field)} for field, kind in fields
    ]
    entries.append(
        {
            "field": measure,
            "type": "quantitative",
            "title": _label(measure),
            "format": value_format,
        }
    )
    return entries


def chart_for(mapping: Any, snapshot: ResultSnapshot) -> dict[str, Any]:
    """The chart specification for one governed result.

    Returns a dict with ``kind``, ``title``, ``spec`` and, when no chart is
    appropriate, ``no_chart_reason``. The caller turns it into a
    :class:`ChartSpec`; this function stays free of the report machinery so
    it can be tested as the pure function it is.
    """
    if mapping is None or not getattr(mapping, "confident", False):
        return {"kind": "none", "no_chart_reason": "the question was not resolved"}

    operation = str(getattr(mapping, "operation", ""))
    dimensions = [str(d) for d in (getattr(mapping, "dimensions", ()) or ())]
    resolved = [
        column
        for column in snapshot.columns
        if column != "row_count" and column in {_alias_of(d) for d in dimensions} | {"period"}
    ]
    has_period = "period" in snapshot.columns
    measure = _numeric_column(snapshot, exclude=set(resolved) | {"period"})
    rows = len(snapshot.rows)

    if measure is None:
        return {"kind": "none", "no_chart_reason": "the result has no measured column to plot"}

    value_format = _measure_format(snapshot, measure)

    if not resolved:
        # A single figure. A one-bar chart adds nothing a number does not.
        return {
            "kind": "kpi",
            "title": f"{_label(measure)}",
            "spec": {"kind": "kpi", "value_column": measure},
        }

    if rows == 0:
        return {"kind": "none", "no_chart_reason": "no rows matched the requested population"}

    # One cut only.
    if len(resolved) == 1:
        column = resolved[0]
        if has_period:
            return _line(column, measure, value_format)
        if operation == "rank":
            return {
                "kind": "ranked_bar",
                "title": f"{_label(measure)} by {_label(column)}, ranked",
                "spec": _bar_spec(column, measure, sort="-y", value_format=value_format),
            }
        if rows > MAX_BAR_CATEGORIES:
            return {
                "kind": "none",
                "no_chart_reason": (
                    f"{rows} categories is more than a readable bar chart shows; "
                    "the complete result is in the table"
                ),
            }
        return {
            "kind": "bar",
            "title": f"{_label(measure)} by {_label(column)}",
            "spec": _bar_spec(column, measure, value_format=value_format),
        }

    # Two cuts. Time plus a category is a multi-series line; two categories
    # are grouped bars. Both are declined when they would be unreadable.
    category = next((c for c in resolved if c != "period"), resolved[0])
    series_count = len({row[snapshot.columns.index(category)] for row in snapshot.rows})
    if has_period:
        if series_count > MAX_SERIES:
            return {
                "kind": "none",
                "no_chart_reason": (
                    f"{series_count} series over time is more than a readable chart shows; "
                    "the complete result is in the table"
                ),
            }
        return {
            "kind": "line",
            "title": f"{_label(measure)} over time by {_label(category)}",
            "spec": _line_spec("period", measure, colour=category, value_format=value_format),
        }

    other = next(c for c in resolved if c != category)
    other_count = len({row[snapshot.columns.index(other)] for row in snapshot.rows})
    if series_count * other_count > MAX_GROUPED_CELLS:
        return {
            "kind": "none",
            "no_chart_reason": (
                f"{series_count} x {other_count} groups is more than a readable chart "
                "shows; the complete result is in the table"
            ),
        }
    return {
        "kind": "grouped_bar",
        "title": f"{_label(measure)} by {_label(category)} and {_label(other)}",
        "spec": _bar_spec(category, measure, colour=other, value_format=value_format),
    }


def _line(column: str, measure: str, value_format: str) -> dict[str, Any]:
    return {
        "kind": "line",
        "title": f"{_label(measure)} over time",
        "spec": _line_spec(column, measure, value_format=value_format),
    }


def _bar_spec(
    category: str,
    measure: str,
    *,
    colour: str | None = None,
    sort: str | None = None,
    value_format: str = ",",
) -> dict[str, Any]:
    encoding: dict[str, Any] = {
        "x": {"field": category, "type": "nominal", "title": _label(category)},
        "y": {
            "field": measure,
            "type": "quantitative",
            "title": _label(measure),
            "axis": {"format": value_format},
        },
    }
    if sort:
        encoding["x"]["sort"] = sort
    fields = [(category, "nominal")]
    if colour:
        encoding["color"] = {"field": colour, "type": "nominal", "title": _label(colour)}
        fields.append((colour, "nominal"))
    encoding["tooltip"] = _tooltip(fields, measure, value_format)
    return {"mark": "bar", "encoding": encoding}


def _line_spec(
    axis: str, measure: str, *, colour: str | None = None, value_format: str = ","
) -> dict[str, Any]:
    encoding: dict[str, Any] = {
        "x": {"field": axis, "type": "ordinal", "title": _label(axis)},
        "y": {
            "field": measure,
            "type": "quantitative",
            "title": _label(measure),
            "axis": {"format": value_format},
        },
    }
    fields = [(axis, "ordinal")]
    if colour:
        encoding["color"] = {"field": colour, "type": "nominal", "title": _label(colour)}
        fields.append((colour, "nominal"))
    encoding["tooltip"] = _tooltip(fields, measure, value_format)
    return {"mark": {"type": "line", "point": True}, "encoding": encoding}


def _alias_of(column: str) -> str:
    from agentic_analytics.analytics.upload_plan import alias_for

    return alias_for(column)
