"""The chart decision, as the presentation contract states it.

This does not choose the chart. `analytics.charts.chart_for` already does
that as a pure function of the contract and the result shape, and having
two selectors would let the report and the chart disagree about what was
drawn. This reads that decision and names its parts -- which field is on
which axis, what the series is -- so the frontend does not have to
re-derive them by inspecting a Vega specification.

It also carries the reason when there is no chart. An empty panel tells a
reader nothing; "48 categories is more than a readable bar chart shows"
tells them to look at the table.
"""

from __future__ import annotations

from typing import Any

from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.presentation.schemas import (
    DisplayField,
    PresentationChart,
    SemanticKind,
)

# ----------------------------------------------------------------- formats
#
# A chart is a reader surface, so the figures on it have to read the way
# the figures beside it do. The axis said `2025-01` where the headline said
# "Oct 2025", and the tooltip said `2025-10-01T00:00:00` -- a stored
# instant, on hover, on the primary surface.
#
# Written as **declarative format strings only**. Vega-Lite can suffix a
# tick through `labelExpr`, and that is deliberately not used: `expr` is on
# the forbidden-key list in `chartSafety.ts` and in the server's own
# validator, because a specification that can carry an expression is a
# specification that can carry code. Improving a tick label is not worth
# widening that.
#
# So a unit that d3-format cannot express goes in the axis **title**
# instead -- "Return rate (%)", "Difference from the highest (pp)" -- which
# is declarative, is announced to a screen reader, and says it once rather
# than on every tick.

#: How a date reads on an axis, per grain. d3-time-format.
_TIME_FORMAT = {
    "day": "%b %-d, %Y",
    "week": "%b %-d, %Y",
    "month": "%b %Y",
    "quarter": "%b %Y",
    "year": "%Y",
}

#: Units d3-format writes itself. A currency prefix is one of them; `%` is
#: not, because d3's `%` multiplies by a hundred and these values are
#: already on a 0-100 scale.
_CURRENCY = frozenset({"$", "£", "€", "¥"})


def _number_format(field: DisplayField) -> str:
    if field.precision == 0 or field.semantic_kind is SemanticKind.COUNT:
        return ",d"
    if field.unit in _CURRENCY:
        return f"{field.unit},.2f"
    return ",.2f"


def _titled(field: DisplayField) -> str:
    """The axis title, carrying any unit d3-format cannot write."""
    label = field.display_label
    if field.unit and field.unit not in _CURRENCY:
        return f"{label} ({field.unit})"
    return label


def apply_display_formats(
    spec: dict[str, Any], fields: list[DisplayField] | None
) -> dict[str, Any]:
    """Write the display contract onto a Vega-Lite specification.

    Pure: returns a new spec and does not touch the one passed in. The
    `field` of every encoding stays raw, because that is the key Vega looks
    up in the data -- only titles and formats change.
    """
    if not fields:
        return spec
    by_name = {field.source_name: field for field in fields}
    out = dict(spec)
    encoding = out.get("encoding")
    if not isinstance(encoding, dict):
        return out
    encoding = {
        channel: dict(entry) if isinstance(entry, dict) else entry
        for channel, entry in encoding.items()
    }

    for channel in ("x", "y", "color"):
        entry = encoding.get(channel)
        if not isinstance(entry, dict):
            continue
        field = by_name.get(str(entry.get("field") or ""))
        if field is None:
            continue
        entry["title"] = _titled(field)
        axis = dict(entry.get("axis") or {}) if channel != "color" else None
        if entry.get("type") == "temporal":
            fmt = _TIME_FORMAT.get(field.time_grain or "", "%b %Y")
            if axis is not None:
                axis["format"] = fmt
        elif entry.get("type") == "quantitative" and axis is not None:
            axis["format"] = _number_format(field)
        if axis is not None:
            entry["axis"] = axis

    tooltip = encoding.get("tooltip")
    if isinstance(tooltip, list):
        written = []
        for raw in tooltip:
            if not isinstance(raw, dict):
                written.append(raw)
                continue
            item = dict(raw)
            field = by_name.get(str(item.get("field") or ""))
            if field is not None:
                item["title"] = _titled(field)
                if item.get("type") == "temporal" or field.semantic_kind is SemanticKind.TIME:
                    # The stored instant, on hover, was the worst of the
                    # three: a reader has to ask for it, so it reads as the
                    # precise answer.
                    item["type"] = "temporal"
                    item["format"] = _TIME_FORMAT.get(field.time_grain or "", "%b %Y")
                elif item.get("type") == "quantitative":
                    item["format"] = _number_format(field)
            written.append(item)
        encoding["tooltip"] = written

    out["encoding"] = encoding
    return out


def _axis_field(spec: dict[str, Any], channel: str) -> str | None:
    encoding = spec.get("encoding")
    if not isinstance(encoding, dict):
        return None
    entry = encoding.get(channel)
    if isinstance(entry, dict):
        name = entry.get("field")
        return str(name) if isinstance(name, str) else None
    return None


def presentation_chart(
    decision: dict[str, Any] | None,
    snapshot: ResultSnapshot | None,
    *,
    chart_id: str | None = None,
    fields: list[DisplayField] | None = None,
) -> PresentationChart:
    """Restate one chart decision in the presentation contract's terms.

    `fields` is the result's display metadata. Given it, the axis titles,
    the axis formats and the tooltip formats are written from the same
    contract the table and the headline use, so a figure cannot be worded
    one way on the chart and another beneath it.
    """
    if not decision:
        return PresentationChart(
            kind="none",
            no_chart_reason="no chart decision was recorded for this result",
        )

    kind = str(decision.get("kind") or "none")
    reason = decision.get("no_chart_reason")
    if kind == "none":
        return PresentationChart(
            kind="none",
            result_id=snapshot.result_id if snapshot else None,
            no_chart_reason=str(reason or "a chart would not help read this result"),
        )

    spec = decision.get("spec")
    spec = dict(spec) if isinstance(spec, dict) else None
    if spec is not None:
        spec = apply_display_formats(spec, fields)

    if kind == "kpi":
        # A single figure. A one-bar chart adds nothing a number does not,
        # so the KPI carries no Vega specification at all.
        value_column = None
        if spec:
            value_column = spec.get("value_column")
        return PresentationChart(
            chart_id=chart_id,
            result_id=snapshot.result_id if snapshot else None,
            kind="kpi",
            title=str(decision.get("title") or "") or None,
            y_field=str(value_column) if value_column else None,
            spec=None,
        )

    return PresentationChart(
        chart_id=chart_id,
        result_id=snapshot.result_id if snapshot else None,
        kind=kind,
        title=str(decision.get("title") or "") or None,
        x_field=_axis_field(spec or {}, "x"),
        y_field=_axis_field(spec or {}, "y"),
        series_field=_axis_field(spec or {}, "color"),
        spec=spec,
    )
