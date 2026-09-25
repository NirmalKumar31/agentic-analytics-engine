"""A deterministic scripted provider.

This is not a language model. It is a rule-based stand-in that produces the
same structured outputs a model would, so that tests, CI, the recorded demos
and the public deployment all run with zero credentials and identical results
on identical input.

Two properties matter and are tested:

* **It never invents a number.** Every figure it writes into a finding is read
  out of a :class:`ResultSnapshot` that a real query produced, and every
  finding carries the exact ``(result_id, row, column)`` cells it came from.
* **It behaves like a model, including badly.** On correlation tasks it
  proposes a causal interpretation, because that is the failure real models
  make most often. The critic rejects it with a deterministic rule, so the
  verification path is exercised by a genuine mistake rather than a staged one.
"""

from __future__ import annotations

import re
from typing import Any

from agentic_analytics.llm.base import LLMProvider, LLMRequest

# Question keyword -> metrics the analyst should target.
_METRIC_HINTS: list[tuple[str, tuple[str, ...]]] = [
    (r"revenue|sales|top ?line", ("revenue",)),
    (r"gross margin|margin|profitab", ("gross_margin_pct",)),
    (r"discount|promo", ("avg_discount_rate",)),
    (r"return|refund", ("return_rate", "refund_amount")),
    (r"repeat|retention|churn|come back", ("repeat_purchase_rate",)),
    (r"ship|deliver|late|delay|carrier", ("late_delivery_rate", "avg_delivery_days")),
    (r"channel|acquisition|marketing|roas|spend|cac", ("contribution_margin_pct", "roas")),
    (r"order value|aov|basket", ("average_order_value",)),
    (r"units|volume|quantity", ("units",)),
]

_DIMENSION_HINTS: list[tuple[str, str]] = [
    (r"categor|product", "category"),
    (r"segment", "customer_segment"),
    (r"region|geograph", "region"),
    (r"channel|acquisition|marketing", "acquisition_channel"),
    (r"brand", "brand"),
    (r"carrier", "carrier"),
]

_QUARTER = re.compile(r"\bq([1-4])\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(20\d{2})\b")


def _matches(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


class FakeProvider(LLMProvider):
    """Scripted, deterministic, credential-free."""

    name = "fake"
    requires_credentials = False

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        self._check_budget()
        handler = getattr(self, f"_role_{request.role}", None)
        if handler is None:
            raise KeyError(f"scripted provider has no handler for role {request.role!r}")
        payload: dict[str, Any] = handler(request.context)
        # Token counts are approximations of prompt size, not billing figures.
        self.usage.record(
            request.role,
            input_tokens=(len(request.system) + len(request.user)) // 4,
            output_tokens=len(str(payload)) // 4,
        )
        return payload

    # ------------------------------------------------------------- analyst

    def _role_question_analyst(self, ctx: dict[str, Any]) -> dict[str, Any]:
        question: str = ctx.get("question", "")
        available: list[str] = list(ctx.get("metrics", []))
        available_dims: list[str] = list(ctx.get("dimensions", []))

        metrics: list[str] = []
        for pattern, names in _METRIC_HINTS:
            if _matches(pattern, question):
                metrics.extend(n for n in names if n in available and n not in metrics)
        if not metrics:
            metrics = [str(m) for m in ("revenue", "orders") if m in available][:2]

        dimensions = [
            dim
            for pattern, dim in _DIMENSION_HINTS
            if _matches(pattern, question) and dim in available_dims
        ]

        quarter = _QUARTER.search(question)
        year = _YEAR.search(question)
        time_scope = None
        if quarter and year:
            time_scope = f"{year.group(1)} Q{quarter.group(1)}"
        elif quarter:
            time_scope = f"Q{quarter.group(1)}"
        elif year:
            time_scope = year.group(1)

        if _matches(r"affect|impact|relate|associat|correlat|driv", question):
            analysis_type = "correlation"
        elif _matches(r"trend|over time|month|quarter|increase|decrease|fell|rose", question):
            analysis_type = "timeseries"
        elif dimensions:
            analysis_type = "segmentation"
        else:
            analysis_type = "profiling"

        ambiguities: list[str] = []
        if not time_scope:
            ambiguities.append("No explicit time range; the full dataset period is used.")
        if not dimensions:
            ambiguities.append(
                "No breakdown named in the question; likely drivers are chosen by the planner."
            )

        return {
            "intent": question.strip() or "Describe the dataset",
            "analysis_type": analysis_type,
            "target_metrics": metrics[:4],
            "dimensions": dimensions[:3],
            "time_scope": time_scope,
            "comparison_groups": [],
            "ambiguities": ambiguities,
        }

    # ------------------------------------------------------------- planner

    def _role_planner(self, ctx: dict[str, Any]) -> dict[str, Any]:
        analysis: dict[str, Any] = ctx.get("analysis", {})
        metric_dims: dict[str, list[str]] = ctx.get("metric_dimensions", {})
        available: list[str] = list(ctx.get("metrics", []))
        max_tasks = int(ctx.get("max_tasks", 6))
        filters: list[dict[str, Any]] = list(ctx.get("default_filters", []))

        if not available:
            # No semantic metric layer, which is the case for an uploaded
            # file. Profile the table and aggregate it directly instead.
            return self._plan_without_metrics(ctx, max_tasks)

        targets: list[str] = [m for m in analysis.get("target_metrics", []) if m in available]
        if not targets:
            targets = [m for m in ("revenue",) if m in available]
        named_dims: list[str] = list(analysis.get("dimensions", []))

        tasks: list[dict[str, Any]] = []

        def add(
            objective: str,
            analysis_type: str,
            metrics: list[str],
            dimensions: list[str],
            tool: str,
            priority: int,
            extra: dict[str, Any] | None = None,
        ) -> None:
            if len(tasks) >= max_tasks or not metrics:
                return
            task: dict[str, Any] = {
                "task_id": f"task_{len(tasks) + 1:02d}",
                "objective": objective,
                "analysis_type": analysis_type,
                "required_metrics": metrics,
                "dimensions": dimensions,
                "filters": list(filters),
                "preferred_tool": tool,
                "priority": priority,
                "depends_on": [],
            }
            if extra:
                task.update(extra)
            tasks.append(task)

        primary = targets[0]

        # 1. Always establish the trend of the headline metric first.
        add(
            f"Track {primary} over time to locate when it moved",
            "timeseries",
            [primary],
            [],
            "analyze_timeseries",
            1,
        )

        # 2. The second target metric, if the question named one, over the
        #    same period -- this is what makes "revenue up, margin down"
        #    visible as two series rather than one claim.
        if len(targets) > 1:
            add(
                f"Track {targets[1]} over the same period for comparison",
                "timeseries",
                [targets[1]],
                [],
                "analyze_timeseries",
                1,
            )

        # 3. Break the headline metric down by whatever dimensions are
        #    available, preferring any the question named.
        candidate_dims = named_dims or _default_dimensions(primary, metric_dims)
        for dim in candidate_dims[:2]:
            if dim in metric_dims.get(primary, []):
                add(
                    f"Compare {primary} across {dim} to find where the change concentrates",
                    "segmentation",
                    [primary],
                    [dim],
                    "compare_segments",
                    2,
                )

        # 4. A driver decomposition, when the question asks what caused a
        #    change and the window is known. This is the tool that actually
        #    answers "why", so it is planned before the supporting cuts.
        window = ctx.get("comparison_window") or {}
        if (
            window.get("baseline")
            and window.get("current")
            and _matches(r"caus|why|driv|explain|contribut|decompos", str(ctx.get("question", "")))
        ):
            # Every metric the question named, not just the first: "revenue
            # rose but margin fell" is two changes to attribute, and the
            # interesting one is usually the second.
            for target in targets[:2]:
                dimensions_for_target = _default_dimensions(target, metric_dims)
                if not dimensions_for_target:
                    continue
                add(
                    f"Attribute the change in {target} across {dimensions_for_target[0]}",
                    "composition",
                    [target],
                    [dimensions_for_target[0]],
                    "decompose_change",
                    1,
                    {
                        "baseline_start": window["baseline"][0],
                        "baseline_end": window["baseline"][1],
                        "current_start": window["current"][0],
                        "current_end": window["current"][1],
                    },
                )

        # 5. Mix and discount checks, when margin is in play.
        if "gross_margin_pct" in targets or _matches(r"margin", str(analysis.get("intent", ""))):
            if "avg_discount_rate" in available:
                add(
                    "Measure how the discount rate changed over the same periods",
                    "timeseries",
                    ["avg_discount_rate"],
                    [],
                    "analyze_timeseries",
                    2,
                )
            if "units" in available and "category" in metric_dims.get("units", []):
                add(
                    "Check whether product mix shifted between categories",
                    "composition",
                    ["units"],
                    ["category"],
                    "compute_metric",
                    2,
                    {"time_grain": "quarter"},
                )

        # 5. A real statistical comparison when the question asks whether
        #    one thing affects another.
        if analysis.get("analysis_type") == "correlation":
            stat = _statistical_task(ctx)
            if stat and len(tasks) < max_tasks:
                stat["task_id"] = f"task_{len(tasks) + 1:02d}"
                stat["filters"] = []
                tasks.append(stat)

        if not tasks:
            add(
                f"Compute {primary} for the dataset",
                "profiling",
                [primary],
                [],
                "compute_metric",
                3,
            )
        return {"tasks": tasks[:max_tasks]}

    def _plan_without_metrics(self, ctx: dict[str, Any], max_tasks: int) -> dict[str, Any]:
        """Plan for a dataset that has no metric layer.

        An uploaded file is one arbitrary table, so there is nothing to look
        a metric up in. Two tasks, both bounded: ask the engine to map the
        question onto the table's inferred schema, and describe the table's
        shape regardless. The mapping is the engine's work, not this
        provider's -- `aggregate_for_question` resolves the columns and
        composes the SQL -- so the same plan is right whether the caller is
        a rule or a model.
        """
        tables: list[dict[str, Any]] = list(ctx.get("tables", []))
        question = str(ctx.get("question", ""))
        name = next((str(t.get("name", "")) for t in tables if t.get("name")), "")
        if not name:
            return {"tasks": []}

        tasks: list[dict[str, Any]] = [
            {
                "task_id": "task_01",
                "objective": f"Answer the question from {name} if it maps to the columns",
                "analysis_type": "profiling",
                "required_metrics": [],
                "dimensions": [],
                "filters": [],
                "preferred_tool": "aggregate_for_question",
                "priority": 1,
                "depends_on": [],
                "table": name,
                "variables": {"question": question},
            }
        ]
        if max_tasks > 1:
            tasks.append(
                {
                    "task_id": "task_02",
                    "objective": f"Describe the shape of {name}",
                    "analysis_type": "profiling",
                    "required_metrics": [],
                    "dimensions": [],
                    "filters": [],
                    "preferred_tool": "profile_table",
                    "priority": 2,
                    "depends_on": [],
                    "table": name,
                }
            )
        return {"tasks": tasks}

    # -------------------------------------------------------------- worker

    def _role_worker_next_tool(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Choose the next tool call for a task, or stop."""
        task: dict[str, Any] = ctx.get("task", {})
        calls_made = int(ctx.get("calls_made", 0))

        # Profiling is the one task that genuinely needs a second call: the
        # aggregate can only be written once the profile has reported which
        # columns exist and how many distinct values they hold.
        if task.get("preferred_tool") == "aggregate_for_question":
            # One attempt. If the engine refuses the mapping the task ends
            # with no result, and the report says so rather than substituting
            # some other number for the answer.
            if calls_made == 0:
                return {
                    "done": False,
                    "tool": "aggregate_for_question",
                    "arguments": {
                        "table": task.get("table"),
                        "question": str(task.get("variables", {}).get("question", "")),
                    },
                }
            return {"done": True, "tool": None, "arguments": {}}

        if task.get("preferred_tool") == "profile_table":
            # Profile and stop. This task describes the table; it does not
            # answer the question, and following it with an aggregate over
            # whichever column happened to look groupable produced a number
            # that read like an answer without being one. Answering is
            # `aggregate_for_question`'s job, and it refuses rather than
            # guesses.
            if calls_made == 0:
                return {
                    "done": False,
                    "tool": "profile_table",
                    "arguments": {"table": task.get("table")},
                }
            return {"done": True, "tool": None, "arguments": {}}

        if calls_made > 0:
            return {"done": True, "tool": None, "arguments": {}}

        tool = task.get("preferred_tool", "compute_metric")
        metrics: list[str] = list(task.get("required_metrics", []))
        dimensions: list[str] = list(task.get("dimensions", []))
        filters: list[dict[str, Any]] = list(task.get("filters", []))
        metric = metrics[0] if metrics else "revenue"

        if tool == "analyze_timeseries":
            args: dict[str, Any] = {
                "metric": metric,
                "grain": task.get("time_grain") or "month",
                "filters": filters,
            }
        elif tool == "compare_segments":
            args = {
                "metric": metric,
                "dimension": dimensions[0] if dimensions else "region",
                "filters": filters,
            }
        elif tool == "statistical_test":
            args = {
                "test_type": task.get("test_type", "two_proportion_z"),
                "variables": task.get("variables", {}),
                "filters": filters,
            }
        elif tool == "decompose_change":
            args = {
                "metric": metric,
                "dimension": dimensions[0] if dimensions else "category",
                "baseline_start": task.get("baseline_start"),
                "baseline_end": task.get("baseline_end"),
                "current_start": task.get("current_start"),
                "current_end": task.get("current_end"),
                "filters": [],
            }
        elif tool == "correlation_matrix":
            args = {
                "table": task.get("table", "sales"),
                "columns": task.get("columns", []),
                "filters": filters,
            }
        else:
            args = {"metric": metric, "dimensions": dimensions, "filters": filters}
            if task.get("time_grain"):
                args["time_grain"] = task["time_grain"]
            tool = "compute_metric"
        return {"done": False, "tool": tool, "arguments": args}

    def _role_worker_findings(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Turn result rows into claims, quoting the cells they came from."""
        task: dict[str, Any] = ctx.get("task", {})
        results: list[dict[str, Any]] = list(ctx.get("results", []))
        findings: list[dict[str, Any]] = []
        for result in results:
            findings.extend(_findings_for_result(task, result))
        return {"findings": findings[:4]}

    # -------------------------------------------------------------- critic

    def _role_critic(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Semantic judgement only; arithmetic is settled before this runs."""
        finding: dict[str, Any] = ctx.get("finding", {})
        results: list[dict[str, Any]] = list(ctx.get("results", []))
        text: str = finding.get("text", "")
        kind: str = finding.get("kind", "calculated_fact")

        if not finding.get("result_ids"):
            return {
                "status": "unsupported",
                "reason": "The claim cites no result, so nothing supports it.",
            }
        if not results:
            return {
                "status": "unsupported",
                "reason": "The cited results are not available for checking.",
            }

        has_test = any(r.get("statistical_result") for r in results)
        if _matches(r"\bsignificant", text) and not has_test:
            return {
                "status": "unsupported",
                "reason": (
                    "The claim calls the difference significant but no statistical "
                    "test was run on the cited result."
                ),
            }
        if kind == "interpretation":
            return {
                "status": "supported",
                "reason": "Stated as an interpretation and scoped to the cited result.",
            }
        return {
            "status": "supported",
            "reason": "The wording matches the values in the cited result.",
        }

    # ---------------------------------------------------------- visualizer

    def _role_visualizer(self, ctx: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = ctx.get("result", {})
        columns: list[str] = list(result.get("columns", []))
        if len(columns) < 2:
            return {"skip": True}
        # A profile describes the schema; plotting null counts per column is
        # noise next to the analysis the report is actually about.
        if result.get("tool_name") == "profile_table":
            return {"skip": True}

        if "period" in columns:
            x, mark = "period", "line"
            x_type = "temporal"
        else:
            x, mark = columns[0], "bar"
            x_type = "nominal"
        numeric = _first_numeric_column(result, exclude={x})
        if numeric is None:
            return {"skip": True}

        return {
            "skip": False,
            "title": ctx.get("title") or f"{numeric} by {x}",
            "mark": mark,
            "x": x,
            "x_type": x_type,
            "y": numeric,
            "y_type": "quantitative",
        }

    # ------------------------------------------------------------ reporter

    def _role_reporter(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Plan the report's shape. It does not write any of its prose.

        The engine renders every factual sentence from the findings' own
        text, so what is returned here is selection and grouping only.
        """
        question: str = ctx.get("question", "")
        findings: list[dict[str, Any]] = list(ctx.get("findings", []))
        if not findings:
            return {"executive_finding_ids": [], "sections": [], "next_questions": []}

        facts = [f for f in findings if f.get("kind") == "calculated_fact"]
        stats = [f for f in findings if f.get("kind") == "statistical_result"]
        interpretations = [f for f in findings if f.get("kind") == "interpretation"]

        # Grouped by evidence kind. The engine labels each group; this only
        # says which findings belong together.
        sections: list[dict[str, Any]] = [
            {"finding_ids": [f["finding_id"] for f in group[:cap]]}
            for group, cap in ((facts, 6), (stats, 4), (interpretations, 4))
            if group
        ]

        return {
            "executive_finding_ids": [f["finding_id"] for f in (facts + stats)[:3]]
            or [findings[0]["finding_id"]],
            "sections": sections,
            "next_questions": _next_questions(question, findings),
        }


# --------------------------------------------------------------- helpers


def _default_dimensions(metric: str, metric_dims: dict[str, list[str]]) -> list[str]:
    """Dimensions worth cutting a metric by when the question named none."""
    preferred = ["category", "acquisition_channel", "region", "customer_segment", "carrier"]
    available = metric_dims.get(metric, [])
    return [d for d in preferred if d in available]


def _statistical_task(ctx: dict[str, Any]) -> dict[str, Any] | None:
    """A concrete test for the question, when the dataset supports one."""
    question: str = str(ctx.get("question", "")).lower()
    models: list[str] = list(ctx.get("models", []))
    if "customer_lifecycle" in models and _matches(
        r"ship|deliver|late|delay|repeat|retention", question
    ):
        return {
            "objective": (
                "Test whether customers whose first delivery was late repeat at a different rate"
            ),
            "analysis_type": "statistical_test",
            "required_metrics": ["repeat_purchase_rate"],
            "dimensions": ["first_delivery_status"],
            "preferred_tool": "statistical_test",
            "priority": 1,
            "depends_on": [],
            "test_type": "two_proportion_z",
            "variables": {
                "model": "customer_lifecycle",
                "group_column": "first_delivery_status",
                "value_column": "is_repeat",
                "groups": ["late", "on_time"],
            },
        }
    if "sales" in models and _matches(r"return|refund", question):
        return {
            "objective": "Test whether return rate is independent of customer segment",
            "analysis_type": "statistical_test",
            "required_metrics": ["return_rate"],
            "dimensions": ["customer_segment"],
            "preferred_tool": "statistical_test",
            "priority": 2,
            "depends_on": [],
            "test_type": "chi_square",
            "variables": {
                "model": "sales",
                "row_column": "customer_segment",
                "column_column": "return_reason",
            },
        }
    return None


def _fmt(value: Any, metric: str = "") -> str:
    """Format a value the way the metric's unit reads."""
    if value is None:
        return "n/a"
    if isinstance(value, int | float):
        if metric.endswith("_pct") or metric in {"return_rate", "gross_margin_pct"}:
            return f"{value:,.2f}%"
        if isinstance(value, int) or float(value).is_integer():
            return f"{int(value):,}"
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        return f"{value:,.2f}"
    return str(value)


def _period_label(value: Any) -> str:
    text = str(value)
    return text.split("T")[0] if "T" in text else text


def _first_numeric_column(result: dict[str, Any], exclude: set[str]) -> str | None:
    columns: list[str] = list(result.get("columns", []))
    rows: list[list[Any]] = list(result.get("rows", []))
    if not rows:
        return None
    for index, name in enumerate(columns):
        if name in exclude:
            continue
        if any(
            isinstance(row[index], int | float) and not isinstance(row[index], bool) for row in rows
        ):
            return name
    return None


def _findings_for_result(task: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    tool = result.get("tool_name", "")
    if tool == "analyze_timeseries":
        return _timeseries_findings(task, result)
    if tool == "compare_segments":
        return _segment_findings(task, result)
    if tool == "statistical_test":
        return _statistical_findings(task, result)
    if tool == "correlation_matrix":
        return _correlation_findings(task, result)
    if tool in ("decompose_change", "rank_contributors"):
        return _decomposition_findings(task, result)
    if tool == "compute_metric" and result.get("columns", [])[:1] == ["period"]:
        return _composition_findings(task, result)
    if tool == "profile_table":
        return _profile_findings(task, result)
    if tool in ("run_readonly_sql", "aggregate_for_question"):
        return _adhoc_findings(task, result)
    return _scalar_findings(task, result)


def _cell(result: dict[str, Any], row: int, column: str, label: str) -> dict[str, Any]:
    columns: list[str] = result["columns"]
    value = result["rows"][row][columns.index(column)]
    return {
        "result_id": result["result_id"],
        "row": row,
        "column": column,
        "value": value,
        "label": label,
    }


def _timeseries_findings(task: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    columns: list[str] = result["columns"]
    rows: list[list[Any]] = result["rows"]
    if len(rows) < 2 or "change_abs" not in columns:
        return []
    metric = columns[1]
    i_metric, i_change, i_period = columns.index(metric), columns.index("change_abs"), 0

    candidates = [
        (idx, row[i_change])
        for idx, row in enumerate(rows)
        if isinstance(row[i_change], int | float)
    ]
    if not candidates:
        return []

    # If the question named a period, report the change into that period.
    # Otherwise the largest absolute move is the one worth naming.
    focus = task.get("focus_period")
    focused = (
        next(
            (
                pair
                for pair in candidates
                if _period_label(rows[pair[0]][i_period]) == _period_label(focus)
            ),
            None,
        )
        if focus
        else None
    )
    idx, change = focused or max(candidates, key=lambda pair: abs(pair[1]))
    previous, current = rows[idx - 1], rows[idx]
    direction = "rose" if change > 0 else "fell"
    unit = "percentage points" if metric.endswith("_pct") or metric == "return_rate" else ""
    from_label, to_label = _period_label(previous[i_period]), _period_label(current[i_period])

    text = (
        f"{metric} {direction} from {_fmt(previous[i_metric], metric)} in {from_label} "
        f"to {_fmt(current[i_metric], metric)} in {to_label}"
        + (f", a change of {abs(change):,.2f} {unit}." if unit else f" ({abs(change):,.2f}).")
    )
    return [
        {
            "text": text,
            "kind": "calculated_fact",
            "task_id": task.get("task_id"),
            "result_ids": [result["result_id"]],
            "metric_ids": [metric],
            "evidence_cells": [
                _cell(result, idx - 1, metric, f"{metric} at {from_label}"),
                _cell(result, idx, metric, f"{metric} at {to_label}"),
            ],
            "claimed_change": {
                "type": "difference",
                "from": previous[i_metric],
                "to": current[i_metric],
                "stated": round(float(change), 6),
            },
        }
    ]


def _segment_findings(task: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    columns: list[str] = result["columns"]
    rows: list[list[Any]] = result["rows"]
    if len(rows) < 2:
        return []
    dimension, metric = columns[0], columns[1]
    i_metric = columns.index(metric)
    ranked = [r for r in rows if isinstance(r[i_metric], int | float)]
    if len(ranked) < 2:
        return []
    ranked.sort(key=lambda r: r[i_metric], reverse=True)
    top, bottom = ranked[0], ranked[-1]
    top_index, bottom_index = rows.index(top), rows.index(bottom)

    text = (
        f"Across {dimension}, {top[0]} has the highest {metric} at "
        f"{_fmt(top[i_metric], metric)} and {bottom[0]} the lowest at "
        f"{_fmt(bottom[i_metric], metric)}."
    )
    return [
        {
            "text": text,
            "kind": "calculated_fact",
            "task_id": task.get("task_id"),
            "result_ids": [result["result_id"]],
            "metric_ids": [metric],
            "evidence_cells": [
                _cell(result, top_index, metric, f"{metric} for {top[0]}"),
                _cell(result, bottom_index, metric, f"{metric} for {bottom[0]}"),
            ],
            "claimed_change": {
                "type": "difference",
                "from": bottom[i_metric],
                "to": top[i_metric],
                "stated": round(float(top[i_metric]) - float(bottom[i_metric]), 6),
            },
        }
    ]


def _statistical_findings(task: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    stat: dict[str, Any] | None = result.get("statistical_result")
    if not stat:
        return []
    rows: list[list[Any]] = result["rows"]
    columns: list[str] = result["columns"]
    p_value = stat.get("p_value")
    significant = isinstance(p_value, int | float) and p_value < 0.05
    sizes = stat.get("sample_sizes", {})

    sample_text = ", ".join(f"{name}={count:,}" for name, count in sizes.items())
    parts = []

    # Lead with the measured values. A p-value with no effect stated is the
    # least useful way to report a comparison. The sample-size column is
    # numeric too, so the quantity being compared is named explicitly rather
    # than taken as "the first number".
    value_column = next((c for c in ("rate", "mean") if c in columns), None)
    if value_column and len(rows) >= 2:
        i_value = columns.index(value_column)
        stated = ", ".join(
            f"{row[0]} = {_fmt(row[i_value])}" for row in rows[:2] if row[i_value] is not None
        )
        if stated:
            parts.append(f"Measured {value_column}: {stated}.")

    parts += [
        f"A {stat.get('test_name')} on {sample_text}",
        f"returned p = {p_value:.3g}",
    ]
    if stat.get("effect_size") is not None:
        parts.append(f"with {stat.get('effect_size_name')} = {float(stat['effect_size']):.3f}")
    verdict = (
        "the difference is statistically significant at the 5% level"
        if significant
        else "the difference is not statistically significant at the 5% level"
    )
    text = " ".join(parts) + f"; {verdict}."

    cells: list[dict[str, Any]] = []
    if value_column:
        for row_index in range(min(len(rows), 2)):
            cells.append(
                _cell(result, row_index, value_column, f"{value_column} for {rows[row_index][0]}")
            )

    findings = [
        {
            "text": text,
            "kind": "statistical_result",
            "task_id": task.get("task_id"),
            "result_ids": [result["result_id"]],
            "metric_ids": list(task.get("required_metrics", [])),
            "evidence_cells": cells,
            "claimed_change": None,
        }
    ]

    # A model that has just seen a significant p-value very often reaches
    # for a causal verb. The scripted provider does the same, so the
    # deterministic causal check is exercised by a real mistake.
    if significant and len(rows) >= 2:
        group_a, group_b = rows[0][0], rows[1][0]
        findings.append(
            {
                "text": (
                    f"Being in the {group_a} group causes the observed difference versus {group_b}."
                ),
                "kind": "interpretation",
                "task_id": task.get("task_id"),
                "result_ids": [result["result_id"]],
                "metric_ids": [],
                "evidence_cells": cells[:1],
                "claimed_change": None,
            }
        )
    return findings


def _correlation_findings(task: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    columns: list[str] = result["columns"]
    rows: list[list[Any]] = result["rows"]
    best: tuple[float, int, str] | None = None
    for row_index, row in enumerate(rows):
        for col_index, column in enumerate(columns):
            if col_index == 0 or column == row[0]:
                continue
            value = row[col_index]
            if isinstance(value, int | float) and (best is None or abs(value) > abs(best[0])):
                best = (float(value), row_index, column)
    if best is None:
        return []
    value, row_index, column = best
    row_name = rows[row_index][0]
    return [
        {
            "text": (
                f"{row_name} and {column} have a Pearson correlation of {value:.3f} "
                "across the dataset. Correlation does not establish causation."
            ),
            "kind": "calculated_fact",
            "task_id": task.get("task_id"),
            "result_ids": [result["result_id"]],
            "metric_ids": [],
            "evidence_cells": [_cell(result, row_index, column, f"corr({row_name}, {column})")],
            "claimed_change": None,
        }
    ]


def _composition_findings(task: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    """Find the segment whose share moved most between two periods.

    States the per-segment percentage change rather than a share of total,
    because a percentage change between two cells is something the numeric
    verifier can recompute from the cells the finding cites. A share of total
    is not in any cell and could not be checked the same way.
    """
    columns: list[str] = result["columns"]
    rows: list[list[Any]] = result["rows"]
    if len(columns) < 3 or len(rows) < 4:
        return []
    dimension, metric = columns[1], columns[2]

    periods = sorted({str(row[0]) for row in rows})
    if len(periods) < 2:
        return []
    focus = task.get("focus_period")
    if focus and _period_label(focus) in [_period_label(p) for p in periods]:
        index = [_period_label(p) for p in periods].index(_period_label(focus))
        if index == 0:
            return []
        previous, current = periods[index - 1], periods[index]
    else:
        previous, current = periods[-2], periods[-1]

    by_period: dict[str, dict[str, tuple[int, float]]] = {previous: {}, current: {}}
    for row_index, row in enumerate(rows):
        period, segment, value = str(row[0]), str(row[1]), row[2]
        if period in by_period and isinstance(value, int | float):
            by_period[period][segment] = (row_index, float(value))

    changes: list[tuple[float, str, tuple[int, float], tuple[int, float]]] = []
    for segment, (row_b, value_b) in by_period[current].items():
        if segment not in by_period[previous]:
            continue
        row_a, value_a = by_period[previous][segment]
        if value_a == 0:
            continue
        pct_change = 100.0 * (value_b - value_a) / abs(value_a)
        changes.append((pct_change, segment, (row_a, value_a), (row_b, value_b)))
    if not changes:
        return []
    changes.sort(key=lambda c: c[0], reverse=True)
    pct, segment, (row_a, value_a), (row_b, value_b) = changes[0]

    text = (
        f"{segment} {metric} rose {pct:,.2f}% from {_fmt(value_a, metric)} in "
        f"{_period_label(previous)} to {_fmt(value_b, metric)} in {_period_label(current)}, "
        f"the largest increase across {dimension}."
    )
    return [
        {
            "text": text,
            "kind": "calculated_fact",
            "task_id": task.get("task_id"),
            "result_ids": [result["result_id"]],
            "metric_ids": [metric],
            "evidence_cells": [
                _cell(result, row_a, metric, f"{metric}, {segment}, {_period_label(previous)}"),
                _cell(result, row_b, metric, f"{metric}, {segment}, {_period_label(current)}"),
            ],
            "claimed_change": {
                "type": "percent_change",
                "from": value_a,
                "to": value_b,
                "stated": round(pct, 6),
            },
        }
    ]


# Columns a profile reports, in the order `profile_table` returns them.
_PROFILE_NUMERIC = ("non_null", "null_pct", "distinct_count")


def _quote_ident(name: str) -> str:
    """Double-quote an identifier, doubling any embedded quote."""
    return '"' + str(name).replace('"', '""') + '"'


def _decomposition_findings(task: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    """State what a deterministic decomposition attributed the change to.

    The engine computed the attribution and checked that it reconciles; this
    reports it, citing the per-segment cells it came from.
    """
    decomposition = result.get("decomposition") or {}
    columns: list[str] = result["columns"]
    rows: list[list[Any]] = result["rows"]
    if not decomposition or not rows:
        return []
    if not decomposition.get("reconciled"):
        # An attribution that does not add up is not an explanation.
        return []

    metric = str(decomposition.get("metric", ""))
    dimension = str(decomposition.get("dimension", ""))
    findings: list[dict[str, Any]] = []

    if decomposition.get("method") == "additive":
        i_change = columns.index("change")
        i_share = columns.index("contribution_pct")
        top = rows[0]
        if not isinstance(top[i_share], int | float):
            return []
        findings.append(
            {
                "text": (
                    f"{top[0]} accounts for {top[i_share]:,.1f}% of the change in "
                    f"{metric}, the largest single contribution across {dimension}."
                ),
                "kind": "calculated_fact",
                "task_id": task.get("task_id"),
                "result_ids": [result["result_id"]],
                "metric_ids": [metric],
                "evidence_cells": [
                    _cell(result, 0, "contribution_pct", f"{top[0]} share of the change"),
                    _cell(result, 0, "change", f"{top[0]} change in {metric}"),
                ],
                "claimed_change": None,
            }
        )
        del i_change
    else:
        rate = float(decomposition.get("rate_effect_total", 0.0))
        mix = float(decomposition.get("mix_effect_total", 0.0))
        observed = float(decomposition.get("observed_change", 0.0))
        dominant = (
            "within-segment rate movement"
            if abs(rate) >= abs(mix)
            else (f"a shift in mix between {dimension} values")
        )
        findings.append(
            {
                "text": (
                    f"Of the {observed:,.2f} point change in {metric}, "
                    f"{rate:,.2f} comes from rate movement within {dimension} values "
                    f"and {mix:,.2f} from the mix shifting between them; "
                    f"{dominant} is the larger part."
                ),
                "kind": "calculated_fact",
                "task_id": task.get("task_id"),
                "result_ids": [result["result_id"]],
                "metric_ids": [metric],
                "evidence_cells": [
                    _cell(result, 0, "rate_effect", f"rate effect for {rows[0][0]}"),
                    _cell(result, 0, "mix_effect", f"mix effect for {rows[0][0]}"),
                ],
                "claimed_change": None,
            }
        )
    return findings


def _profile_findings(task: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    """Describe the shape of a table from its profile.

    States only values that are numeric cells of the profile result -- the
    counts and the null rate -- so every number remains checkable.
    """
    columns: list[str] = result["columns"]
    rows: list[list[Any]] = result["rows"]
    if not rows or "column_name" not in columns:
        return []
    table = str(task.get("table") or result.get("parameters", {}).get("table") or "the table")

    idx_name = columns.index("column_name")
    idx_non_null = columns.index("non_null") if "non_null" in columns else None
    idx_distinct = columns.index("distinct_count") if "distinct_count" in columns else None
    if idx_non_null is None or idx_distinct is None:
        return []

    # The column with the most distinct values is the most informative thing
    # a profile can point at.
    candidates = [
        (row_index, row)
        for row_index, row in enumerate(rows)
        if isinstance(row[idx_distinct], int | float)
    ]
    if not candidates:
        return []
    row_index, row = max(candidates, key=lambda pair: float(pair[1][idx_distinct]))

    text = (
        f"{table} has {len(rows)} columns. The column {row[idx_name]} holds "
        f"{_fmt(row[idx_distinct])} distinct values across "
        f"{_fmt(row[idx_non_null])} non-null rows."
    )
    return [
        {
            "text": text,
            "kind": "calculated_fact",
            "task_id": task.get("task_id"),
            "result_ids": [result["result_id"]],
            "metric_ids": [],
            "evidence_cells": [
                _cell(result, row_index, "distinct_count", f"distinct values in {row[idx_name]}"),
                _cell(result, row_index, "non_null", f"non-null rows in {row[idx_name]}"),
            ],
            "claimed_change": None,
        }
    ]


def _adhoc_findings(task: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    """Findings from a hand-written aggregate.

    A two-column grouped result reads the same way as `compare_segments`, so
    it reuses that wording rather than inventing a second phrasing.
    """
    columns: list[str] = result["columns"]
    rows: list[list[Any]] = result["rows"]
    if len(columns) >= 2 and len(rows) >= 2:
        first_numeric = all(
            isinstance(row[1], int | float) and not isinstance(row[1], bool) for row in rows
        )
        if first_numeric:
            return _segment_findings(task, result)
    return _scalar_findings(task, result)


def _scalar_findings(task: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    columns: list[str] = result["columns"]
    rows: list[list[Any]] = result["rows"]
    if not rows:
        return []
    metric = columns[-1]
    value = rows[0][len(columns) - 1]
    if not isinstance(value, int | float):
        return []
    return [
        {
            "text": f"{metric} for the selected scope is {_fmt(value, metric)}.",
            "kind": "calculated_fact",
            "task_id": task.get("task_id"),
            "result_ids": [result["result_id"]],
            "metric_ids": [metric],
            "evidence_cells": [_cell(result, 0, metric, metric)],
            "claimed_change": None,
        }
    ]


def _next_questions(question: str, findings: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    metrics = {m for f in findings for m in f.get("metric_ids", [])}
    if "gross_margin_pct" in metrics:
        out.append("Does the margin move persist after the promotional period, or does it recover?")
    if "return_rate" in metrics:
        out.append("Which return reasons account for the excess in the worst category?")
    if "contribution_margin_pct" in metrics or "roas" in metrics:
        out.append(
            "What would contribution margin look like if the weakest channel's "
            "discount rate matched the average?"
        )
    if not out:
        out.append("Which dimension explains the largest share of the variation seen here?")
    return out[:3]
