"""A plan the dataset cannot execute must not end the run silently.

Found by running a real model. Asked "what is the total net value by
territory?" about an uploaded CSV, qwen3:4b returned a perfectly sensible
task -- objective, dimension, table, columns -- but omitted `preferred_tool`.
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


async def test_no_fallback_when_the_dataset_has_a_metric_layer() -> None:
    """A governed dataset with an unexecutable plan is a different problem.

    There, a task naming a metric that does not exist genuinely cannot run,
    and inventing an upload-shaped fallback would be wrong.
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
    assert tasks == []


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
