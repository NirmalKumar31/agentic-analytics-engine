"""Question analyst and analysis planner."""

from __future__ import annotations

from typing import Any

from agentic_analytics.agents.base import ask, bullet_list, parse_into, schema_of
from agentic_analytics.agents.prompts import PLANNER, QUESTION_ANALYST
from agentic_analytics.agents.schemas import AnalysisPlan, AnalysisTask, QuestionAnalysis
from agentic_analytics.agents.timescope import comparison_window as _comparison_window
from agentic_analytics.agents.timescope import parse_time_scope
from agentic_analytics.llm.base import LLMProvider

# Tools that do not require a metric from the semantic layer. A dataset with
# no metric layer is analysed entirely through these.
METRIC_FREE_TOOLS = frozenset(
    {
        "statistical_test",
        "profile_table",
        "profile_dataset",
        "aggregate_for_question",
        "run_readonly_sql",
        "correlation_matrix",
    }
)


def _metric_lines(metrics: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- {m['name']} ({m['format']}): {m['description']} "
        f"[dimensions: {', '.join(m['valid_dimensions']) or 'none'}]"
        for m in metrics
    )


async def analyze_question(
    provider: LLMProvider,
    question: str,
    catalog: dict[str, Any],
    metrics: list[dict[str, Any]],
) -> QuestionAnalysis:
    """Turn a question into a structured brief."""
    all_dimensions = sorted({d for m in metrics for d in m["valid_dimensions"]})
    tables = ", ".join(t["name"] for t in catalog.get("tables", []))

    user = f"""\
QUESTION
{question}

DATASET
kind: {catalog.get("dataset_kind")}
tables: {tables or "(none)"}

METRICS AVAILABLE
{_metric_lines(metrics) or "(this dataset has no metric layer; use SQL and profiling)"}

DIMENSIONS AVAILABLE
{", ".join(all_dimensions) or "(none)"}

Produce the analysis brief."""

    payload = await ask(
        provider,
        role="question_analyst",
        system=QUESTION_ANALYST,
        user=user,
        schema=schema_of(QuestionAnalysis),
        context={
            "question": question,
            "metrics": [m["name"] for m in metrics],
            "dimensions": all_dimensions,
            "tables": [t["name"] for t in catalog.get("tables", [])],
            "dataset_kind": catalog.get("dataset_kind"),
        },
    )
    analysis = parse_into(QuestionAnalysis, payload, "question_analyst")

    # A model may name a metric that does not exist. Drop it and say so,
    # rather than letting a worker fail on it later.
    known = {m["name"] for m in metrics}
    unknown = [m for m in analysis.target_metrics if m not in known]
    if unknown:
        analysis.target_metrics = [m for m in analysis.target_metrics if m in known]
        analysis.ambiguities.append(
            f"Requested metric(s) not defined for this dataset: {', '.join(unknown)}."
        )
    known_dims = set(all_dimensions)
    analysis.dimensions = [d for d in analysis.dimensions if d in known_dims]
    return analysis


async def plan_analysis(
    provider: LLMProvider,
    question: str,
    analysis: QuestionAnalysis,
    metrics: list[dict[str, Any]],
    models: list[str],
    max_tasks: int,
    default_filters: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
) -> list[AnalysisTask]:
    """Turn a brief into independent, executable analytical tasks."""
    metric_dimensions = {m["name"]: m["valid_dimensions"] for m in metrics}

    user = f"""\
QUESTION
{question}

BRIEF
intent: {analysis.intent}
analysis_type: {analysis.analysis_type}
target_metrics: {", ".join(analysis.target_metrics) or "(none)"}
dimensions: {", ".join(analysis.dimensions) or "(none)"}
time_scope: {analysis.time_scope or "(whole dataset)"}
ambiguities:
{bullet_list(analysis.ambiguities)}

METRICS AVAILABLE
{_metric_lines(metrics) or "(none)"}

SEMANTIC MODELS AVAILABLE FOR STATISTICAL TESTS
{", ".join(models) or "(none)"}

TABLES AVAILABLE
{", ".join(t["name"] for t in (tables or [])) or "(none)"}

Emit at most {max_tasks} tasks."""

    payload = await ask(
        provider,
        role="planner",
        system=PLANNER,
        user=user,
        schema=schema_of(AnalysisPlan),
        context={
            "question": question,
            "analysis": analysis.model_dump(),
            "metrics": [m["name"] for m in metrics],
            "metric_dimensions": metric_dimensions,
            "models": models,
            "max_tasks": max_tasks,
            "default_filters": default_filters or [],
            # The two windows a driver decomposition compares, derived from
            # the time scope the question named.
            "comparison_window": (
                {"baseline": list(periods.baseline), "current": list(periods.current)}
                if (periods := _comparison_window(analysis.time_scope))
                else {}
            ),
            # An uploaded file has no metric layer, so the planner falls back
            # to profiling the table it does have.
            "tables": tables or [],
        },
    )
    plan = parse_into(AnalysisPlan, payload, "planner")
    window = parse_time_scope(analysis.time_scope)
    time_fields = {m["name"]: m["time_field"] for m in metrics}

    # Drop tasks that reference metrics this dataset does not define, and
    # dimensions the metric does not support. A task that cannot run is worse
    # than one fewer task.
    known = {m["name"] for m in metrics}
    # A dataset with no metric layer -- any upload -- cannot run the
    # metric-based tools at all. Dropping those tasks silently is what a real
    # model's first plan hits: `preferred_tool` defaults to `compute_metric`
    # when a model omits it, every task is then unexecutable, and the run
    # stops having done nothing. The objective and columns are still good, so
    # the task is redirected to the tool that *can* serve it rather than
    # discarded. Nothing about verification changes; this only decides which
    # governed tool a task reaches.
    metric_free_dataset = not known
    cleaned: list[AnalysisTask] = []
    for task in plan.tasks:
        task.required_metrics = [m for m in task.required_metrics if m in known]
        if metric_free_dataset and task.preferred_tool not in METRIC_FREE_TOOLS:
            task.preferred_tool = "aggregate_for_question"
            task.table = task.table or (tables[0]["name"] if tables else None)
            task.variables = {**task.variables, "question": question}
        if not task.required_metrics and task.preferred_tool not in METRIC_FREE_TOOLS:
            continue
        primary = task.required_metrics[0] if task.required_metrics else None
        if primary:
            allowed = set(metric_dimensions.get(primary, []))
            if task.preferred_tool != "statistical_test":
                task.dimensions = [d for d in task.dimensions if d in allowed]
        # A time scope names a period, not a filter: each metric is measured
        # over its own time field, so the window is resolved per task rather
        # than emitted once by the planner.
        if window is not None and primary:
            time_field = time_fields.get(primary)
            if time_field and not any(f.get("column") == time_field for f in task.filters):
                task.filters = [*task.filters, window.filter_for(time_field)]
            # Any task that cuts by period needs the grain and the named
            # period, not just the ones using analyze_timeseries.
            if task.preferred_tool == "analyze_timeseries" or task.time_grain:
                task.time_grain = task.time_grain or window.grain
                task.focus_period = task.focus_period or window.focus_period

        cleaned.append(task)
        if len(cleaned) >= max_tasks:
            break

    if not cleaned and metric_free_dataset and tables:
        # The engine's own plan, used when a provider returned nothing this
        # dataset can execute. Bounded, deterministic, and the same two tasks
        # the scripted provider would have chosen -- owned here so that every
        # provider gets the fallback rather than each having to implement it.
        cleaned = _fallback_plan(question, str(tables[0]["name"]), max_tasks)
    return cleaned


def _fallback_plan(question: str, table: str, max_tasks: int) -> list[AnalysisTask]:
    """Answer the question if the rules can map it; describe the table either way."""
    tasks = [
        AnalysisTask(
            task_id="task_01",
            objective=f"Answer the question from {table} if it maps to the columns",
            analysis_type="profiling",
            preferred_tool="aggregate_for_question",
            priority=1,
            table=table,
            variables={"question": question},
        )
    ]
    if max_tasks > 1:
        tasks.append(
            AnalysisTask(
                task_id="task_02",
                objective=f"Describe the shape of {table}",
                analysis_type="profiling",
                preferred_tool="profile_table",
                priority=2,
                table=table,
            )
        )
    return tasks
