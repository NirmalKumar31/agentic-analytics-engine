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
from agentic_analytics.presentation.schemas import PresentationChart


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
) -> PresentationChart:
    """Restate one chart decision in the presentation contract's terms."""
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
