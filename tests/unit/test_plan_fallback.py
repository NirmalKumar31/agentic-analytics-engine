"""A plan the dataset cannot execute must not end the run silently.

Found by running a real model. Asked "what is the total net value by
territory?" about an uploaded CSV, qwen3:4b returned a perfectly sensible
task -- objective, dimension, table, columns, but omitted `preferred_tool`.
The schema default is `compute_metric`, which needs a governed metric layer
that an upload does not have, so the cleaning step dropped every task and the
run stopped with "the planner produced no executable task for this dataset"
having computed nothing.

Two changes, both in the engine and neither touching verification: a task
aimed at a metric tool on a metric-free dataset is redirected to
`aggregate_for_question` rather than discarded, and if a plan still comes
back empty the engine substitutes its own bounded two-task plan.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentic_analytics.agents import analyst
from agentic_analytics.agents.schemas import QuestionAnalysis
from agentic_analytics.llm.base import LLMProvider, LLMRequest

TABLES = [{"name": "uploaded_data", "row_count": 4000}]
QUESTION = "What is the total net value by territory?"


class ScriptedPlanner(LLMProvider):
    """Returns a fixed planner payload, whatever it is asked."""

    name = "scripted"
    requires_credentials = False

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__()
        self.payload = payload

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        return self.payload


async def _plan(payload: dict[str, Any], metrics: list[dict[str, Any]] | None = None) -> Any:
    return await analyst.plan_analysis(
        ScriptedPlanner(payload),
        QUESTION,
        QuestionAnalysis(intent=QUESTION, analysis_type="segmentation"),
        metrics or [],
        [],
        max_tasks=5,
        tables=TABLES,
    )


async def test_the_real_model_plan_that_used_to_vanish() -> None:
    """Verbatim the payload qwen3:4b produced, minus `preferred_tool`."""
    tasks = await _plan(
        {
            "tasks": [
                {
                    "objective": "Compute the total net value by territory",
                    "dimensions": ["territory"],
                    "filters": [],
                    "table": "uploaded_data",
                    "columns": ["net_value"],
                }
            ]
        }
    )
    assert tasks, "the plan was dropped entirely"
    assert tasks[0].preferred_tool == "aggregate_for_question"
    # The model's intent survives the redirect.
    assert "net value" in tasks[0].objective.lower()
    assert tasks[0].table == "uploaded_data"
    assert tasks[0].variables.get("question") == QUESTION


@pytest.mark.parametrize(
    "tool",
    ["compute_metric", "compare_segments", "analyze_timeseries", "decompose_change"],
)
async def test_any_metric_tool_is_redirected_on_a_metric_free_dataset(tool: str) -> None:
    tasks = await _plan(
        {"tasks": [{"objective": "do the thing", "preferred_tool": tool, "table": "uploaded_data"}]}
    )
    assert len(tasks) == 1
    assert tasks[0].preferred_tool == "aggregate_for_question"


async def test_a_metric_free_tool_is_left_alone() -> None:
    """Redirecting a task that would have worked would be a regression."""
    tasks = await _plan(
        {
            "tasks": [
                {
                    "objective": "describe it",
                    "preferred_tool": "profile_table",
                    "table": "uploaded_data",
                }
            ]
        }
    )
    assert tasks[0].preferred_tool == "profile_table"


async def test_an_empty_plan_falls_back_to_the_engine_plan() -> None:
    """A provider returning nothing usable must not end the run."""
    tasks = await _plan({"tasks": []})
    assert [t.preferred_tool for t in tasks] == [
        "aggregate_for_question",
        "profile_table",
    ]
    assert tasks[0].variables.get("question") == QUESTION
    assert all(t.table == "uploaded_data" for t in tasks)


async def test_the_fallback_respects_the_task_ceiling() -> None:
    tasks = await analyst.plan_analysis(
        ScriptedPlanner({"tasks": []}),
        QUESTION,
        QuestionAnalysis(intent=QUESTION, analysis_type="profiling"),
        [],
        [],
        max_tasks=1,
        tables=TABLES,
    )
    assert len(tasks) == 1


async def test_no_upload_fallback_when_the_dataset_has_a_metric_layer() -> None:
    """A governed dataset never gets the upload-shaped fallback plan.

    The task itself survives. It used to be dropped, on the reasoning that a
    task naming a metric that does not exist cannot run -- true when the
    worker had no way to learn the real names, and false now that it is
    given the catalogue. What must not happen is the *upload* fallback:
    pointing a governed dataset at `aggregate_for_question` would route
    around the metric layer entirely.
    """
    metrics = [
        {
            "name": "revenue",
            "format": "currency",
            "description": "net revenue",
            "valid_dimensions": ["category"],
            "time_field": "ordered_at",
        }
    ]
    tasks = await _plan(
        {
            "tasks": [
                {"objective": "x", "preferred_tool": "compute_metric", "required_metrics": ["nope"]}
            ]
        },
        metrics=metrics,
    )
    assert [t.preferred_tool for t in tasks] == ["compute_metric"]
    assert tasks[0].required_metrics == [], "the metric that does not exist is filtered out"
    assert not any(t.preferred_tool == "aggregate_for_question" for t in tasks)


async def test_no_fallback_without_a_table_to_point_at() -> None:
    tasks = await analyst.plan_analysis(
        ScriptedPlanner({"tasks": []}),
        QUESTION,
        QuestionAnalysis(intent=QUESTION, analysis_type="profiling"),
        [],
        [],
        max_tasks=5,
        tables=[],
    )
    assert tasks == []


def test_the_worker_prompt_names_the_real_tables() -> None:
    """The other half of the same failure.

    With no table names in the prompt, the model called
    `profile_table(table="sales")` -- the filename -- when the only table is
    `uploaded_data`, and every call in the run failed.
    """
    from agentic_analytics.agents.schemas import AnalysisTask
    from agentic_analytics.agents.worker import _tool_choice_prompt

    class _Toolset:
        available_tools = ["profile_table", "aggregate_for_question"]

    prompt = _tool_choice_prompt(
        AnalysisTask(objective="o", table="uploaded_data", variables={"question": QUESTION}),
        [],
        0,
        6,
        _Toolset(),  # type: ignore[arg-type]
        tables=["uploaded_data"],
        has_metrics=False,
    )
    assert "uploaded_data" in prompt
    assert QUESTION in prompt
    # And it says which tools cannot work here, rather than recommending them.
    assert "NO metric layer" in prompt
    assert "aggregate_for_question" in prompt


def test_the_worker_prompt_recommends_metric_tools_when_they_exist() -> None:
    from agentic_analytics.agents.schemas import AnalysisTask
    from agentic_analytics.agents.worker import _tool_choice_prompt

    class _Toolset:
        available_tools = ["compute_metric"]

    prompt = _tool_choice_prompt(
        AnalysisTask(objective="o"),
        [],
        0,
        6,
        _Toolset(),  # type: ignore[arg-type]
        tables=["orders", "order_items"],
        has_metrics=True,
    )
    assert "compute_metric" in prompt
    assert "NO metric layer" not in prompt
    assert "orders" in prompt


# ------------------------------------ the mirror redirect, on a warehouse
WAREHOUSE_METRICS = [
    {
        "name": "revenue",
        "format": "currency",
        "description": "net revenue",
        "valid_dimensions": ["acquisition_channel", "category"],
        "time_field": "order_date",
    }
]


async def test_an_upload_tool_naming_a_metric_is_redirected_to_the_metric_layer() -> None:
    """Found on the demo warehouse: the planner chose an upload tool.

    Redirecting is honest here only because the task already names the
    metric. Choosing one for it would be the engine deciding what the model
    meant, which is the line this layer does not cross.
    """
    telemetry: dict[str, Any] = {}
    tasks = await analyst.plan_analysis(
        ScriptedPlanner(
            {
                "tasks": [
                    {
                        "objective": "Revenue by acquisition channel",
                        "preferred_tool": "aggregate_for_question",
                        "required_metrics": ["revenue"],
                        "dimensions": ["acquisition_channel"],
                        "table": "orders",
                    }
                ]
            }
        ),
        QUESTION,
        QuestionAnalysis(intent=QUESTION, analysis_type="segmentation"),
        WAREHOUSE_METRICS,
        [],
        max_tasks=5,
        tables=[{"name": "orders", "row_count": 10}],
        telemetry=telemetry,
    )
    assert len(tasks) == 1
    assert tasks[0].preferred_tool == "compute_metric"
    assert tasks[0].required_metrics == ["revenue"]
    assert telemetry["tasks_redirected_by_engine"] == 1
    assert "metric layer" in telemetry["redirect_reasons"][0]


async def test_an_upload_tool_naming_no_metric_is_not_redirected_by_guessing() -> None:
    """There is no unambiguous metric to redirect to, so the engine does not.

    Preflight refuses the call with the metric names beside it and the
    model chooses. An engine that picked one would put its own authority
    behind a guess.
    """
    telemetry: dict[str, Any] = {}
    tasks = await analyst.plan_analysis(
        ScriptedPlanner(
            {
                "tasks": [
                    {
                        "objective": "How did things go?",
                        "preferred_tool": "aggregate_for_question",
                        "required_metrics": [],
                        "table": "orders",
                    }
                ]
            }
        ),
        QUESTION,
        QuestionAnalysis(intent=QUESTION, analysis_type="segmentation"),
        WAREHOUSE_METRICS,
        [],
        max_tasks=5,
        tables=[{"name": "orders", "row_count": 10}],
        telemetry=telemetry,
    )
    assert [t.preferred_tool for t in tasks] == ["aggregate_for_question"]
    assert telemetry["tasks_redirected_by_engine"] == 0


def test_the_planner_prompt_states_the_metric_layer_branch() -> None:
    """It documented the metric-free case and not its mirror."""
    from agentic_analytics.agents.prompts import PLANNER

    assert "compare_segments" in PLANNER
    assert "governed metric\nlayer" in PLANNER
    assert "aggregate_for_question` is for datasets that have no metric layer" in PLANNER


async def test_a_metric_task_that_names_no_metric_is_kept_on_a_governed_dataset() -> None:
    """The drop rule predates the execution contract and outlived its reason.

    It cost a whole run: the planner returned six sensible metric tasks --
    "compare gross margin across acquisition channels", `compare_segments`,
    `decompose_change` -- every one with `required_metrics: []`, and the
    cleaner discarded all six without a word. The worker is given the metric
    catalogue now and preflight checks whatever it names, so the objective
    is executable; throwing it away is the loss.
    """
    telemetry: dict[str, Any] = {}
    tasks = await analyst.plan_analysis(
        ScriptedPlanner(
            {
                "tasks": [
                    {
                        "objective": "Compare gross margin across acquisition channels",
                        "preferred_tool": "compare_segments",
                        "required_metrics": [],
                    },
                    {
                        "objective": "Identify the segments driving revenue growth",
                        "preferred_tool": "decompose_change",
                        "required_metrics": [],
                    },
                ]
            }
        ),
        QUESTION,
        QuestionAnalysis(intent=QUESTION, analysis_type="segmentation"),
        WAREHOUSE_METRICS,
        [],
        max_tasks=5,
        tables=[{"name": "orders", "row_count": 10}],
        telemetry=telemetry,
    )
    assert [t.preferred_tool for t in tasks] == ["compare_segments", "decompose_change"]
    # Kept, but counted: this is the planner leaving out a field it was asked for.
    assert telemetry["tasks_without_a_named_metric"] == 2


async def test_a_metric_task_with_no_metric_is_still_dropped_without_a_metric_layer() -> None:
    """There, the drop is right: there is nothing for the worker to name."""
    tasks = await analyst.plan_analysis(
        ScriptedPlanner(
            {
                "tasks": [
                    {
                        "objective": "x",
                        "preferred_tool": "compute_metric",
                        "required_metrics": ["nope"],
                    }
                ]
            }
        ),
        QUESTION,
        QuestionAnalysis(intent=QUESTION, analysis_type="segmentation"),
        [
            {
                "name": "revenue",
                "format": "currency",
                "description": "r",
                "valid_dimensions": ["category"],
                "time_field": "d",
            }
        ],
        [],
        max_tasks=5,
        tables=[{"name": "orders", "row_count": 10}],
    )
    # `nope` is filtered out, the task keeps its metric tool, and the worker
    # names a real metric from the catalogue.
    assert [t.preferred_tool for t in tasks] == ["compute_metric"]
    assert tasks[0].required_metrics == []


def test_the_planner_prompt_asks_for_the_metric_name() -> None:
    from agentic_analytics.agents.prompts import PLANNER

    assert "required_metrics" in PLANNER
    assert "exactly as METRICS AVAILABLE" in PLANNER
