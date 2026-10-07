"""Question analyst and analysis planner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agentic_analytics.agents.base import ask_into, bullet_list
from agentic_analytics.agents.prompts import PLANNER, QUESTION_ANALYST, UPLOAD_QUERY_PLANNER
from agentic_analytics.agents.schemas import (
    AnalysisPlan,
    AnalysisTask,
    AnalysisType,
    QuestionAnalysis,
    UploadQueryPlan,
)
from agentic_analytics.agents.timescope import comparison_window as _comparison_window
from agentic_analytics.agents.timescope import parse_time_scope
from agentic_analytics.analytics import upload_plan
from agentic_analytics.analytics.metric_plan import MetricQuestionMapping
from agentic_analytics.analytics.resolution import ResolutionAssessment, assess
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


def _upload_plan_fixture(question: str, mapping: upload_plan.QuestionMapping) -> dict[str, Any]:
    """The scripted provider's contract-shaped answer.

    Live providers never see ``context``.  The fixture lets the deterministic
    stand-in exercise the same response schema without reparsing a prose
    prompt, which is the purpose of that provider's structured context.
    """
    return {
        "table": mapping.table,
        "operation": mapping.operation,
        "operation_source": question,
        "measure": mapping.measure,
        "measure_source": question if mapping.measure else "",
        "dimensions": list(mapping.dimensions),
        "dimension_sources": [question for _ in mapping.dimensions],
        # Compatibility projection; ignored when plural fields are present.
        "dimension": mapping.dimension,
        "dimension_source": question if mapping.dimension else "",
        "filters": [
            {
                "column": item["column"],
                "operator": item["operator"],
                "value": "" if item.get("value") is None else str(item.get("value")),
                "source_text": item.get("source_text") or question,
            }
            for item in (f.as_dict() for f in mapping.filters)
        ],
        "time_field": mapping.time_field or mapping.period_field,
        "time_grain": mapping.time_grain,
        "period_start": mapping.period[0] if mapping.period else None,
        "period_end": mapping.period[1] if mapping.period else None,
        "ascending": mapping.ascending,
        "confident": mapping.confident,
        "ambiguity": "" if mapping.confident else mapping.explanation,
    }


async def resolve_upload_query(
    provider: LLMProvider,
    question: str,
    schema: dict[str, Any],
) -> upload_plan.QuestionMapping:
    """Resolve one upload question, using AI for language and rules for authority."""
    deterministic = upload_plan.resolve_question(question, schema)
    if not provider.remote_inference:
        return deterministic

    fields = [
        {
            "name": str(field.get("name", "")),
            "type": str(field.get("data_type") or field.get("type") or ""),
        }
        for field in schema.get("fields", [])
    ]
    roles = {
        "measures": list(schema.get("measures", [])),
        "dimensions": list(schema.get("dimensions", [])),
        "time_fields": list(schema.get("time_fields", [])),
        "aggregatable_if_named": list(schema.get("aggregatable_if_named", [])),
    }
    user = f"""\
QUESTION
{question}

SCHEMA
table: {schema.get("table")}
columns:
{bullet_list([f"{field['name']} ({field['type']})" for field in fields])}
measures: {", ".join(roles["measures"]) or "(none)"}
dimensions: {", ".join(roles["dimensions"]) or "(none)"}
time fields: {", ".join(roles["time_fields"]) or "(none)"}

Return the grounded upload query plan."""
    planned = await ask_into(
        provider,
        UploadQueryPlan,
        role="upload_query_planner",
        system=UPLOAD_QUERY_PLANNER,
        user=user,
        context={
            "question": question,
            "schema": {"table": schema.get("table"), "fields": fields, **roles},
            "candidate_plan": _upload_plan_fixture(question, deterministic),
        },
    )
    proposed = upload_plan.mapping_from_plan(question, schema, planned)
    if proposed.confident:
        return proposed

    # The cloud plan could not be used. Where the rules resolved the
    # question unambiguously, the engine's own contract executes and
    # provenance records that the planner did not decide it.
    #
    # Asked "what is the total Weekly_Sales by Store", a real model
    # returned `profile` with no measure and no grouping. Refusing there
    # fails a routine business question because a model chose a different
    # operation, and the visitor sees an empty pane next to a deterministic
    # answer that worked. The arithmetic is not the model's to own, so
    # there is nothing to protect by refusing.
    #
    # This is deliberately not presented as an independent interpretation:
    # `interpretation` says the rules decided, `planner_note` says why, and
    # the canonical contract is byte-identical to the deterministic one --
    # so Compare Both reports the same governed interpretation, which is
    # the truth.
    if deterministic.confident:
        deterministic.planner_note = (
            "the AI planner did not return a usable contract for this question, "
            "so the engine's own schema-grounded contract was executed"
        )
        return deterministic

    # Neither resolved it. The AI's reason is the more specific one.
    return proposed


#: What decided the contract for one automatic run. Stable identifiers:
#: the UI maps them to copy and the planning audit groups by them.
ROUTE_RULES_EXACT = "rules_exact"
ROUTE_AI_RESOLVED = "ai_resolved"
ROUTE_AI_UNAVAILABLE = "ai_unavailable"
ROUTE_REFUSED = "refused"


@dataclass(frozen=True)
class AutoResolution:
    """The outcome of resolving one question automatically.

    Carries the route and the assessment as well as the contract, because
    "the rules settled it" and "a model settled it" are different facts
    about a run and a reader is entitled to know which happened.
    """

    mapping: upload_plan.QuestionMapping
    assessment: ResolutionAssessment
    route: str
    #: Planning requests actually issued. Zero for every exact question,
    #: which is the property that makes automatic routing affordable.
    planning_calls: int = 0


async def resolve_upload_query_automatically(
    question: str,
    schema: dict[str, Any],
    *,
    open_planner: Callable[[], Awaitable[LLMProvider]] | None = None,
) -> AutoResolution:
    """Resolve one upload question, asking a model only when rules cannot.

    The deterministic resolver runs first, always, and its assessment
    decides whether a provider is needed at all. An exact question
    executes immediately having made no provider call and consulted no
    ledger -- which is the whole point: the common case must not cost
    anything, or automatic routing is just the AI mode with extra steps.

    `open_planner` is a factory rather than a provider because constructing
    the governed cloud provider *is* the ledger admission. Taking a
    durable slot for a question the rules already answered would charge a
    quota against work that never happened, so the provider is built only
    once ambiguity is established, and only then.

    A model is asked only when the question is ambiguous: the dataset can
    answer it and the wording did not say how. A missing column, an
    unimplemented operation and an unsafe grouping are certain refusals,
    and a model cannot overturn any of them -- asking would spend a
    request to be told what the rules already knew.
    """
    assessment = assess(question, schema)
    # `assess` always runs the resolver, so there is always a contract
    # here: an exact one, or the refusing one that carries the reason.
    contract = assessment.deterministic_contract
    assert contract is not None, "assess always runs the resolver"

    if assessment.executable:
        return AutoResolution(
            mapping=contract,
            assessment=assessment,
            route=ROUTE_RULES_EXACT,
            planning_calls=0,
        )

    if not assessment.ai_eligible:
        return AutoResolution(
            mapping=contract,
            assessment=assessment,
            route=ROUTE_REFUSED,
            planning_calls=0,
        )

    if open_planner is None:
        # Ambiguous, and no planner to ask. This is a refusal with a
        # precise reason, not an execution failure: nothing broke, and the
        # question is answerable once the wording names a column.
        return AutoResolution(
            mapping=contract,
            assessment=assessment,
            route=ROUTE_AI_UNAVAILABLE,
            planning_calls=0,
        )

    provider = await open_planner()
    try:
        proposed = await resolve_upload_query(provider, question, schema)
        calls = int(getattr(getattr(provider, "usage", None), "attempts", 0) or 0)
    finally:
        closer = getattr(provider, "aclose", None)
        if closer is not None:
            await closer()

    if proposed.confident:
        return AutoResolution(
            mapping=proposed,
            assessment=assessment,
            route=ROUTE_AI_RESOLVED,
            planning_calls=max(calls, 1),
        )

    # The plan was unusable and the rules had already declined. Neither
    # settled it, so the question is refused with the more specific
    # reason, and never silently answered as a different question.
    return AutoResolution(
        mapping=proposed,
        assessment=assessment,
        route=ROUTE_REFUSED,
        planning_calls=max(calls, 1),
    )


def analysis_from_upload_mapping(
    question: str, mapping: upload_plan.QuestionMapping
) -> QuestionAnalysis:
    """One brief derived from the accepted contract, without reinterpreting it."""
    if mapping.operation == "trend":
        analysis_type: AnalysisType = "timeseries"
    elif mapping.dimensions or mapping.operation == "rank":
        analysis_type = "segmentation"
    elif mapping.operation == "profile":
        analysis_type = "profiling"
    else:
        analysis_type = "composition"
    period = ""
    if mapping.period:
        period = f"{mapping.period[0]} to {mapping.period[1]}"
    ambiguities = [] if mapping.confident else [mapping.explanation]
    return QuestionAnalysis(
        intent=question,
        analysis_type=analysis_type,
        target_metrics=[mapping.measure] if mapping.measure else [],
        dimensions=list(mapping.dimensions),
        time_scope=period or None,
        ambiguities=ambiguities,
    )


def analysis_from_metric_mapping(question: str, mapping: MetricQuestionMapping) -> QuestionAnalysis:
    """Build a brief from an accepted metric-layer contract.

    The registry has already decided the metric, grouping and time grain;
    asking a second model to restate those facts is both wasteful and a route
    for the plan to drift from the question.
    """
    return QuestionAnalysis(
        intent=question,
        analysis_type="timeseries" if mapping.time_grain else "segmentation",
        target_metrics=[mapping.metric] if mapping.metric else [],
        dimensions=list(mapping.dimensions),
        time_scope=mapping.time_grain,
        ambiguities=[] if mapping.confident else [mapping.explanation],
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

    payload = await ask_into(
        provider,
        QuestionAnalysis,
        role="question_analyst",
        system=QUESTION_ANALYST,
        user=user,
        context={
            "question": question,
            "metrics": [m["name"] for m in metrics],
            "dimensions": all_dimensions,
            "tables": [t["name"] for t in catalog.get("tables", [])],
            "dataset_kind": catalog.get("dataset_kind"),
        },
    )
    analysis = payload

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
    telemetry: dict[str, Any] | None = None,
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

    payload = await ask_into(
        provider,
        AnalysisPlan,
        role="planner",
        system=PLANNER,
        user=user,
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
    plan = payload
    window = parse_time_scope(analysis.time_scope)
    time_fields = {m["name"]: m["time_field"] for m in metrics}

    # Drop tasks that reference metrics this dataset does not define, and
    # dimensions the metric does not support. A task that cannot run is worse
    # than one fewer task.
    known = {m["name"] for m in metrics}
    # A dataset with no metric layer (any upload) cannot run the
    # metric-based tools at all. Dropping those tasks silently is what a real
    # model's first plan hits: `preferred_tool` defaults to `compute_metric`
    # when a model omits it, every task is then unexecutable, and the run
    # stops having done nothing. The objective and columns are still good, so
    # the task is redirected to the tool that *can* serve it rather than
    # discarded. Nothing about verification changes; this only decides which
    # governed tool a task reaches.
    metric_free_dataset = not known
    # Telemetry for the real-model evaluation. An engine fallback that
    # rescues a bad plan is good for the product and *hides* the model's
    # failure, so the intervention is recorded rather than left invisible.
    # `plan_telemetry` is a plain dict a caller may pass in; production
    # passes nothing and none of this runs.
    if telemetry is not None:
        telemetry["raw_model_tasks"] = [
            {
                "objective": task.objective[:200],
                "analysis_type": task.analysis_type,
                "required_metrics": list(task.required_metrics),
                "dimensions": list(task.dimensions),
                "preferred_tool": task.preferred_tool,
                "table": task.table,
                "columns": list(task.columns),
            }
            for task in plan.tasks
        ]
        telemetry["raw_model_tasks_returned"] = len(plan.tasks)
        telemetry["raw_model_tools_requested"] = sorted(
            {task.preferred_tool for task in plan.tasks}
        )
        telemetry["tasks_redirected_by_engine"] = 0
        telemetry["redirect_reasons"] = []
        telemetry["fallback_plan_used"] = False

    cleaned: list[AnalysisTask] = []
    for task in plan.tasks:
        task.required_metrics = [m for m in task.required_metrics if m in known]
        if metric_free_dataset and task.preferred_tool not in METRIC_FREE_TOOLS:
            if telemetry is not None:
                telemetry["tasks_redirected_by_engine"] += 1
                telemetry["redirect_reasons"].append(
                    f"{task.preferred_tool} needs a metric layer this dataset has none of"
                )
            task.preferred_tool = "aggregate_for_question"
            task.table = task.table or (tables[0]["name"] if tables else None)
            task.variables = {**task.variables, "question": question}
        # The mirror of the redirect above. A governed dataset asked for
        # with an upload tool cannot work. `aggregate_for_question` maps a
        # question onto one table's raw columns, and on a warehouse that is
        # both ambiguous and outside the metric layer. Redirecting is only
        # honest when the task already names the metric to use: choosing one
        # for it would be the engine deciding what the model meant.
        if (
            not metric_free_dataset
            and task.preferred_tool == "aggregate_for_question"
            and task.required_metrics
        ):
            if telemetry is not None:
                telemetry["tasks_redirected_by_engine"] += 1
                telemetry["redirect_reasons"].append(
                    "aggregate_for_question does not use the metric layer; the task "
                    f"already names {task.required_metrics[0]!r}"
                )
            task.preferred_tool = "compute_metric"

        # A metric task that names no metric used to be dropped here, and
        # that rule is now wrong. It was written when the worker had no idea
        # which metrics existed, so a task saying "compare gross margin
        # across channels" with an empty `required_metrics` really was
        # unexecutable. The worker is given the metric catalogue now, and
        # preflight checks whatever it names, so the task is executable and
        # dropping it throws away a perfectly good objective.
        #
        # It cost a whole run to find: the planner returned six sensible
        # metric tasks, every one with `required_metrics: []`, and the
        # engine discarded all six without a word. The drop survives only
        # where there is genuinely nothing to name.
        if not task.required_metrics and task.preferred_tool not in METRIC_FREE_TOOLS:
            if metric_free_dataset:
                continue
            if telemetry is not None:
                telemetry["tasks_without_a_named_metric"] = (
                    telemetry.get("tasks_without_a_named_metric", 0) + 1
                )
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
        # the scripted provider would have chosen, owned here so that every
        # provider gets the fallback rather than each having to implement it.
        cleaned = _fallback_plan(question, str(tables[0]["name"]), max_tasks)
        if telemetry is not None:
            telemetry["fallback_plan_used"] = True

    if telemetry is not None:
        telemetry["planner_tasks_after_cleanup"] = len(cleaned)
        telemetry["cleaned_tasks"] = [
            {
                "objective": task.objective[:200],
                "preferred_tool": task.preferred_tool,
                "required_metrics": list(task.required_metrics),
                "dimensions": list(task.dimensions),
                "table": task.table,
            }
            for task in cleaned
        ]
        # The distinction the evaluation exists to make: did the model plan
        # something runnable, or did the engine rescue it?
        telemetry["model_plan_directly_executable"] = bool(
            plan.tasks
            and not telemetry["fallback_plan_used"]
            and telemetry["tasks_redirected_by_engine"] == 0
            and cleaned
        )
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
