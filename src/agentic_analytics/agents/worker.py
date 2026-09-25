"""The analysis worker.

One worker executes one task. It runs a bounded tool loop through the MCP
client, then states what the results show. It cannot run more tool calls than
its budget allows, and a failure here is contained: the task is marked failed
and the run continues with whatever other tasks succeeded.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from agentic_analytics.agents.base import ask, parse_into, schema_of
from agentic_analytics.agents.prompts import WORKER_FINDINGS, WORKER_TOOL_CHOICE
from agentic_analytics.agents.schemas import (
    AnalysisTask,
    CandidateFinding,
    TaskOutcome,
    TaskStatus,
)
from agentic_analytics.analytics.corrections import (
    apply_family_correction_to_payloads,
)
from agentic_analytics.events import EventBus, EventType
from agentic_analytics.llm.base import LLMError, LLMProvider
from agentic_analytics.logging import get_logger
from agentic_analytics.mcp_layer.client import (
    AnalyticsToolset,
    BudgetExceeded,
    ToolCallFailed,
)

log = get_logger(__name__)


class ToolChoice(BaseModel):
    """The worker's next move."""

    done: bool = False
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class FindingList(BaseModel):
    findings: list[CandidateFinding] = Field(default_factory=list)


def _results_digest(payloads: list[dict[str, Any]]) -> str:
    """Compact rendering of results for the prompt.

    Rows are included -- a worker cannot cite a number it was not shown -- but
    the row count is already bounded by the tool layer.
    """
    if not payloads:
        return "(no results yet)"
    blocks: list[str] = []
    for payload in payloads:
        lines = [
            f"result_id: {payload['result_id']}  (tool: {payload['tool_name']})",
            f"columns: {payload['columns']}",
        ]
        for index, row in enumerate(payload["rows"][:40]):
            lines.append(f"  row {index}: {row}")
        if payload.get("truncated"):
            lines.append("  (result truncated)")
        if payload.get("statistical_result"):
            lines.append(f"  statistical_result: {json.dumps(payload['statistical_result'])}")
        if payload.get("decomposition"):
            lines.append(f"  decomposition: {json.dumps(payload['decomposition'])}")
        for warning in payload.get("warnings", []):
            lines.append(f"  warning: {warning}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def run_task(
    task: AnalysisTask,
    *,
    provider: LLMProvider,
    toolset: AnalyticsToolset,
    events: EventBus | None = None,
    max_tool_calls: int = 6,
) -> TaskOutcome:
    """Execute one analysis task. Never raises for an ordinary failure."""
    if events:
        events.emit(
            EventType.ANALYSIS_TASK_STARTED,
            task_id=task.task_id,
            objective=task.objective,
            analysis_type=task.analysis_type,
            preferred_tool=task.preferred_tool,
            metrics=task.required_metrics,
            dimensions=task.dimensions,
        )

    payloads: list[dict[str, Any]] = []
    notes: list[str] = []
    calls = 0
    task_json = json.loads(task.model_dump_json())

    while calls < max_tool_calls:
        try:
            choice_payload = await ask(
                provider,
                role="worker_next_tool",
                system=WORKER_TOOL_CHOICE,
                user=_tool_choice_prompt(task, payloads, calls, max_tool_calls, toolset),
                schema=schema_of(ToolChoice),
                context={
                    "task": task_json,
                    "calls_made": calls,
                    "max_calls": max_tool_calls,
                    "available_tools": toolset.available_tools,
                    "results": payloads,
                },
            )
            choice = parse_into(ToolChoice, choice_payload, "worker_next_tool")
        except LLMError as exc:
            notes.append(str(exc))
            break

        if choice.done or not choice.tool:
            break

        try:
            payload = await toolset.call(
                choice.tool, choice.arguments, task_id=task.task_id, agent="analysis_worker"
            )
            payloads.append(payload)
            calls += 1
        except BudgetExceeded as exc:
            notes.append(str(exc))
            if events:
                events.emit(EventType.BUDGET_EXCEEDED, task_id=task.task_id, detail=str(exc))
            break
        except ToolCallFailed as exc:
            calls += 1
            notes.append(f"{choice.tool} failed: {exc}")
            # One failed call is recoverable: the loop continues so the
            # worker can try a different tool within its budget.
            continue

    if not payloads:
        outcome = TaskOutcome(
            task_id=task.task_id,
            status="failed",
            tool_calls=calls,
            error=notes[-1] if notes else "the task produced no results",
            notes=notes,
        )
        if events:
            events.emit(
                EventType.ANALYSIS_TASK_FAILED,
                task_id=task.task_id,
                objective=task.objective,
                error=outcome.error,
            )
        return outcome

    # One task is one family of related hypotheses. Correcting before the
    # findings are written means a worker never claims a significance that
    # the adjustment removes.
    family_size = apply_family_correction_to_payloads(payloads)
    if family_size > 1:
        notes.append(
            f"{family_size} related significance tests in this task were "
            "Holm-corrected for multiple comparisons"
        )

    try:
        findings_payload = await ask(
            provider,
            role="worker_findings",
            system=WORKER_FINDINGS,
            user=_findings_prompt(task, payloads),
            schema=schema_of(FindingList),
            context={"task": task_json, "results": payloads},
        )
        findings = parse_into(FindingList, findings_payload, "worker_findings").findings
    except LLMError as exc:
        notes.append(str(exc))
        findings = []

    for finding in findings:
        finding.task_id = task.task_id
        if events:
            events.emit(
                EventType.FINDING_PROPOSED,
                finding_id=finding.finding_id,
                task_id=task.task_id,
                kind=finding.kind,
                text=finding.text,
                result_ids=finding.result_ids,
            )

    status: TaskStatus = "succeeded" if findings else "warning"
    if notes and findings:
        status = "warning"
    outcome = TaskOutcome(
        task_id=task.task_id,
        status=status,
        findings=findings,
        result_ids=[p["result_id"] for p in payloads],
        tool_calls=calls,
        notes=notes,
    )
    if events:
        events.emit(
            EventType.ANALYSIS_TASK_COMPLETED,
            task_id=task.task_id,
            objective=task.objective,
            status=status,
            findings=len(findings),
            tool_calls=calls,
            result_ids=outcome.result_ids,
        )
    return outcome


def _tool_choice_prompt(
    task: AnalysisTask,
    payloads: list[dict[str, Any]],
    calls: int,
    max_calls: int,
    toolset: AnalyticsToolset,
) -> str:
    return f"""\
TASK
objective: {task.objective}
analysis_type: {task.analysis_type}
required_metrics: {", ".join(task.required_metrics) or "(none)"}
dimensions: {", ".join(task.dimensions) or "(none)"}
filters: {json.dumps(task.filters)}
preferred_tool: {task.preferred_tool}

TOOLS AVAILABLE
{", ".join(toolset.available_tools)}

TOOL CALLS USED
{calls} of {max_calls}

RESULTS SO FAR
{_results_digest(payloads)}

Choose the next tool call, or set done to true if the objective is met."""


def _findings_prompt(task: AnalysisTask, payloads: list[dict[str, Any]]) -> str:
    return f"""\
TASK
objective: {task.objective}
required_metrics: {", ".join(task.required_metrics) or "(none)"}

RESULTS
{_results_digest(payloads)}

State what these results show. Cite result_id and the specific cells for every
claim. Do not state a number that is not in the results above."""
