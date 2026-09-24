"""Vega-Lite specification construction and validation.

A model never emits a chart specification directly. It chooses a mark, an x
field and a y field; this module builds the specification and binds the data
inline from the result snapshot. That closes three holes at once: a chart
cannot reference a field the result does not have, cannot load data from a
URL, and cannot carry an expression or signal that executes in the browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentic_analytics.analytics.results import ResultSnapshot

ALLOWED_MARKS: frozenset[str] = frozenset({"bar", "line", "area", "point", "scatter"})
ALLOWED_TYPES: frozenset[str] = frozenset({"quantitative", "nominal", "ordinal", "temporal"})

# Keys that make a Vega-Lite spec do something other than draw the data it was
# given. Rejected anywhere in the tree.
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "signals",
        "expr",
        "datasets",
        "transform",
        "params",
        "selection",
        "url",
        "loader",
        "usermeta",
        "onerror",
        "hconcat",
        "vconcat",
        "layer",
        "facet",
        "repeat",
        "spec",
        "resolve",
    }
)

VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"


class ChartError(ValueError):
    """The requested chart could not be built safely."""


@dataclass
class ChartRequest:
    """What a visualisation agent is allowed to choose."""

    mark: str
    x: str
    y: str
    x_type: str = "nominal"
    y_type: str = "quantitative"
    title: str = ""
    color: str | None = None


def build_chart(
    request: ChartRequest, snapshot: ResultSnapshot, max_rows: int = 200
) -> dict[str, Any]:
    """Build a validated Vega-Lite spec with the result's data inline."""
    mark = request.mark.strip().lower()
    if mark not in ALLOWED_MARKS:
        raise ChartError(
            f"mark {request.mark!r} is not allowed; use one of {sorted(ALLOWED_MARKS)}"
        )
    # Vega-Lite has no "scatter" mark; it is a point mark.
    vega_mark = "point" if mark == "scatter" else mark

    for field in (request.x, request.y):
        if field not in snapshot.columns:
            raise ChartError(
                f"field {field!r} is not a column of result {snapshot.result_id}; "
                f"available: {snapshot.columns}"
            )
    if request.color is not None and request.color not in snapshot.columns:
        raise ChartError(
            f"color field {request.color!r} is not a column of result {snapshot.result_id}"
        )
    for type_name in (request.x_type, request.y_type):
        if type_name not in ALLOWED_TYPES:
            raise ChartError(f"encoding type {type_name!r} is not allowed")

    records = snapshot.to_records()[:max_rows]
    # Drop rows where the plotted value is missing; a null in a quantitative
    # channel renders as a gap that reads like a real zero.
    records = [r for r in records if r.get(request.y) is not None]
    if not records:
        raise ChartError(f"result {snapshot.result_id} has no plottable rows for {request.y!r}")

    # Axis tuning. Vega-Lite's defaults assume a dense series; an aggregate
    # result has a handful of points, and the default tick density renders a
    # crowded axis that reads as though there were far more data than there is.
    x_axis: dict[str, Any] = {"labelOverlap": "greedy"}
    if request.x_type == "temporal":
        x_axis |= {"tickCount": min(len(records), 12), "format": "%Y-%m", "labelAngle": 0}
    elif request.x_type in ("nominal", "ordinal"):
        x_axis |= {"labelAngle": -30, "labelLimit": 120}

    encoding: dict[str, Any] = {
        "x": {
            "field": request.x,
            "type": request.x_type,
            "title": request.x,
            "axis": x_axis,
        },
        "y": {"field": request.y, "type": request.y_type, "title": request.y},
    }
    if vega_mark == "bar":
        # Full-width bars read as a filled area rather than as a comparison.
        encoding["x"]["scale"] = {"paddingInner": 0.3, "paddingOuter": 0.15}

    if request.color:
        encoding["color"] = {"field": request.color, "type": "nominal"}
    encoding["tooltip"] = [
        {"field": column, "type": "quantitative" if _is_numeric(snapshot, column) else "nominal"}
        for column in snapshot.columns
    ]

    spec: dict[str, Any] = {
        "$schema": VEGA_LITE_SCHEMA,
        # Carried for the card heading and the chart's accessible name; the
        # plot itself does not repeat it.
        "title": (request.title or f"{request.y} by {request.x}")[:120],
        "data": {"values": records},
        "mark": {"type": vega_mark, "tooltip": True},
        "encoding": encoding,
        "width": "container",
        "height": 240,
        "padding": {"left": 4, "right": 8, "top": 4, "bottom": 4},
    }
    validate_chart(spec, snapshot)
    return spec


def validate_chart(spec: dict[str, Any], snapshot: ResultSnapshot) -> None:
    """Re-check a built spec. Raises :class:`ChartError` on any violation.

    Run on specs this module built as well, so a future change to the builder
    cannot quietly widen what ships to the browser.
    """
    _reject_forbidden_keys(spec)

    data = spec.get("data")
    if not isinstance(data, dict) or "values" not in data:
        raise ChartError("chart data must be inline values")
    if not isinstance(data["values"], list):
        raise ChartError("chart data values must be a list")

    mark = spec.get("mark")
    mark_type = mark.get("type") if isinstance(mark, dict) else mark
    if mark_type not in ALLOWED_MARKS:
        raise ChartError(f"mark {mark_type!r} is not allowed")

    encoding = spec.get("encoding")
    if not isinstance(encoding, dict):
        raise ChartError("chart has no encoding")

    columns = set(snapshot.columns)
    for channel, definition in encoding.items():
        entries = definition if isinstance(definition, list) else [definition]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            field = entry.get("field")
            if field is not None and field not in columns:
                raise ChartError(
                    f"encoding {channel} references {field!r}, which is not a column "
                    f"of result {snapshot.result_id}"
                )
            type_name = entry.get("type")
            if type_name is not None and type_name not in ALLOWED_TYPES:
                raise ChartError(f"encoding type {type_name!r} is not allowed")


def _reject_forbidden_keys(node: Any, path: str = "$") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_KEYS:
                raise ChartError(f"chart specification may not contain {key!r} (at {path})")
            _reject_forbidden_keys(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_forbidden_keys(value, f"{path}[{index}]")


def _is_numeric(snapshot: ResultSnapshot, column: str) -> bool:
    index = snapshot.columns.index(column)
    return any(
        isinstance(row[index], int | float) and not isinstance(row[index], bool)
        for row in snapshot.rows
    )
