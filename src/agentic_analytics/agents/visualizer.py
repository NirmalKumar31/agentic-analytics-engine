"""Chart construction from verified results only."""

from __future__ import annotations

from pydantic import BaseModel

from agentic_analytics.agents.base import ask_into
from agentic_analytics.agents.prompts import VISUALIZER
from agentic_analytics.agents.schemas import ChartSpec, PublishedFinding
from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.events import EventBus, EventType
from agentic_analytics.llm.base import LLMError, LLMProvider
from agentic_analytics.logging import get_logger
from agentic_analytics.verification.charts import ChartError, ChartRequest, build_chart

log = get_logger(__name__)


class ChartChoice(BaseModel):
    """The encoding a model is allowed to choose."""

    skip: bool = False
    title: str = ""
    mark: str = "bar"
    x: str = ""
    x_type: str = "nominal"
    y: str = ""
    y_type: str = "quantitative"


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
    params = snapshot.parameters
    metric = params.get("metric")
    dimension = params.get("dimension") or (params.get("dimensions") or [None])[0]
    if metric and dimension:
        return f"{metric} by {dimension}"
    if metric and params.get("grain"):
        return f"{metric} by {params['grain']}"
    if metric:
        return str(metric)
    # Hand-written SQL carries no metric parameters, so the result's own
    # column names are the only honest description of what it shows.
    if len(snapshot.columns) >= 2:
        return f"{snapshot.columns[1]} by {snapshot.columns[0]}"
    return snapshot.tool_name


def _prompt(snapshot: ResultSnapshot, finding_text: str) -> str:
    preview = "\n".join(f"  row {i}: {row}" for i, row in enumerate(snapshot.agent_rows()[:10]))
    return f"""\
RESULT {snapshot.result_id} (tool: {snapshot.tool_name})
columns: {snapshot.columns}
{preview}

THE FINDING THIS SUPPORTS
{finding_text}

Choose a mark and the x and y fields. Both fields must be column names above."""
