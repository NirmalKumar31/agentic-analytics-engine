"""Chart construction from verified results only."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from agentic_analytics.agents.base import ask_into
from agentic_analytics.agents.prompts import VISUALIZER
from agentic_analytics.agents.schemas import ChartSpec, PublishedFinding
from agentic_analytics.analytics.labels import column_label
from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.events import EventBus, EventType
from agentic_analytics.llm.base import LLMError, LLMProvider
from agentic_analytics.logging import get_logger
from agentic_analytics.verification.charts import ChartError, ChartRequest, build_chart

log = get_logger(__name__)


#: The marks and encoding types a chart may use.
#:
#: Declared as `Literal` rather than as `str`, so the provider's structured
#: output is constrained to them and a model cannot return a value the
#: validator will refuse.
#:
#: It did. A live run's visualiser chose `categorical` -- a reasonable word
#: for a nominal axis, and not one Vega-Lite has -- and
#: `verification/charts.py` rejected the specification:
#: "encoding type 'categorical' is not allowed". The refusal was correct;
#: the chart was still lost. Constraining the schema prevents the mistake
#: instead of repairing its output, which is the same rule `preflight`
#: follows for tool arguments.
ChartMark = Literal["bar", "line", "area", "point", "scatter"]
EncodingType = Literal["quantitative", "nominal", "ordinal", "temporal"]


class ChartChoice(BaseModel):
    """The encoding a model is allowed to choose."""

    skip: bool = False
    title: str = ""
    mark: ChartMark = "bar"
    x: str = ""
    x_type: EncodingType = "nominal"
    y: str = ""
    y_type: EncodingType = "quantitative"


async def build_charts(
    findings: list[PublishedFinding],
    results: dict[str, ResultSnapshot],
    provider: LLMProvider,
    *,
    max_charts: int = 4,
    max_chart_rows: int = 200,
    events: EventBus | None = None,
) -> list[ChartSpec]:
    """One chart per distinct verified result, up to the limit.

    Only results cited by a published finding are eligible, so a chart cannot
    show something the verification step rejected.
    """
    charts: list[ChartSpec] = []
    seen: set[str] = set()

    for finding in findings:
        for result_id in finding.result_ids:
            if result_id in seen or len(charts) >= max_charts:
                continue
            snapshot = results.get(result_id)
            if snapshot is None or snapshot.row_count < 2:
                continue
            seen.add(result_id)

            supporting = [f.finding_id for f in findings if result_id in f.result_ids]
            try:
                payload = await ask_into(
                    provider,
                    ChartChoice,
                    role="visualizer",
                    system=VISUALIZER,
                    user=_prompt(snapshot, finding.text),
                    context={
                        "result": snapshot.compact(),
                        "title": _default_title(snapshot),
                        "finding_text": finding.text,
                    },
                )
                choice = payload
            except LLMError as exc:
                log.warning("chart_choice_failed", result_id=result_id, error=str(exc))
                continue

            if choice.skip:
                continue

            try:
                spec = build_chart(
                    ChartRequest(
                        mark=choice.mark,
                        x=choice.x,
                        y=choice.y,
                        x_type=choice.x_type,
                        y_type=choice.y_type,
                        title=choice.title or _default_title(snapshot),
                    ),
                    snapshot,
                    max_rows=max_chart_rows,
                )
            except ChartError as exc:
                log.info("chart_rejected", result_id=result_id, reason=str(exc))
                if events:
                    events.emit(EventType.CHART_REJECTED, result_id=result_id, reason=str(exc))
                continue

            chart = ChartSpec(
                title=str(spec["title"]),
                result_id=result_id,
                finding_ids=supporting,
                spec=spec,
            )
            charts.append(chart)
            if events:
                events.emit(
                    EventType.CHART_CREATED,
                    chart_id=chart.chart_id,
                    result_id=result_id,
                    title=chart.title,
                    mark=spec["mark"]["type"],
                    finding_ids=supporting,
                )
    return charts


def _default_title(snapshot: ResultSnapshot) -> str:
    """A chart title in the reader's words.

    Built from the engine's own names, which is why it has to be labelled:
    a title reading `return_rate by customer_segment` describes the query
    rather than the chart, and a chart title is the first thing read on a
    primary surface. `column_label` is the same source the axis titles and
    the table headers use, so they cannot disagree.
    """
    params = snapshot.parameters
    metric = params.get("metric")
    dimension = params.get("dimension") or (params.get("dimensions") or [None])[0]
    if metric and dimension:
        return f"{column_label(str(metric))} by {column_label(str(dimension)).lower()}"
    if metric and params.get("grain"):
        return f"{column_label(str(metric))} by {str(params['grain']).lower()}"
    if metric:
        return column_label(str(metric))
    # Hand-written SQL and statistical tests carry no metric parameters, so
    # the result's own columns are the only honest description of what it
    # shows -- labelled, because "arrived_late by customer_segment" is a
    # chart title a reader was actually shown.
    if len(snapshot.columns) >= 2:
        return f"{column_label(snapshot.columns[1])} by {column_label(snapshot.columns[0]).lower()}"
    return column_label(snapshot.tool_name)


def _prompt(snapshot: ResultSnapshot, finding_text: str) -> str:
    preview = "\n".join(f"  row {i}: {row}" for i, row in enumerate(snapshot.agent_rows()[:10]))
    return f"""\
RESULT {snapshot.result_id} (tool: {snapshot.tool_name})
columns: {snapshot.columns}
{preview}

THE FINDING THIS SUPPORTS
{finding_text}

Choose a mark and the x and y fields. Both fields must be column names above."""
