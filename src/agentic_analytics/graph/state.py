"""Graph state and its reducers.

Workers run concurrently, so any channel a worker writes needs a reducer that
merges rather than overwrites. The reducers here are order-insensitive on
content and produce a deterministic order on output: results are sorted by
task id so two runs of the same plan yield the same report, regardless of
which worker happened to finish first.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from agentic_analytics.agents.schemas import (
    AnalysisPlan,
    AnalysisReport,
    AnalysisTask,
    ChartSpec,
    PublishedFinding,
    QuestionAnalysis,
    TaskOutcome,
    Verdict,
)


def merge_outcomes(left: list[TaskOutcome], right: list[TaskOutcome]) -> list[TaskOutcome]:
    """Concatenate worker outcomes, de-duplicate, and order by task id.

    Deterministic ordering is what makes a parallel run reproducible: without
    it the report's section order depends on scheduling.
    """
    combined = {outcome.task_id: outcome for outcome in [*left, *right]}
    return [combined[key] for key in sorted(combined)]


class WorkerInput(TypedDict):
    """What one ``Send`` carries to a worker.

    Declared separately from :class:`AnalysisState` because a worker is
    dispatched with a single task, not with the whole run state; registering
    it as the node's ``input_schema`` is what lets the fan-out typecheck.
    """

    task: AnalysisTask


class AnalysisState(TypedDict, total=False):
    """Everything the workflow carries."""

    # Inputs
    question: str
    session_id: str
    dataset_catalog: dict[str, Any]
    metric_catalog: list[dict[str, Any]]
    model_names: list[str]

    # Stages
    analysis: QuestionAnalysis
    plan: AnalysisPlan
    pending_tasks: list[AnalysisTask]
    task_outcomes: Annotated[list[TaskOutcome], merge_outcomes]
    verdicts: list[Verdict]
    published: list[PublishedFinding]
    rejected: list[Verdict]
    charts: list[ChartSpec]
    report: AnalysisReport

    # Bookkeeping
    followup_rounds: int
    limitations: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    stopped_reason: str
