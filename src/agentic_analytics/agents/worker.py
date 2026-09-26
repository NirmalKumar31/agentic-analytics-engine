"""The analysis worker.

One worker executes one task. It runs a bounded tool loop through the MCP
client, then states what the results show. It cannot run more tool calls than
its budget allows, and a failure here is contained: the task is marked failed
and the run continues with whatever other tasks succeeded.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from pydantic import BaseModel, Field

from agentic_analytics.agents.base import ask_into
from agentic_analytics.agents.execution import (
    ExecutionContract,
    render_contract,
    signature,
)
from agentic_analytics.agents.preflight import preflight
from agentic_analytics.agents.prompts import (
    FINDINGS_FIELD_GUIDE,
    WORKER_FINDINGS,
    WORKER_TOOL_CHOICE,
)
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


#: How many previous failed attempts a worker is shown. Enough to stop it
#: repeating itself, small enough that the feedback does not crowd out the
#: contract it is supposed to be reading.
MAX_FEEDBACK_SHOWN = 4


@dataclass
class FailedAttempt:
    """One rejected or failed call, in the form the worker is shown next."""

    tool: str
    arguments: dict[str, Any]
    category: str
    message: str
    #: True when the engine refused it without calling MCP.
    preflight: bool = False

    def render(self) -> str:
        arguments = ", ".join(f"{k}={v!r}" for k, v in sorted(self.arguments.items())) or "(none)"
        return f"tool: {self.tool}\narguments: {arguments}\nerror: {self.message}"


@dataclass
class ToolLoopTelemetry:
    """What happened in one task's tool loop, for the evaluation harness."""

    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    preflight_rejections: int = 0
    duplicates_suppressed: int = 0
    mcp_failures: int = 0
    by_category: dict[str, int] = dc_field(default_factory=dict)
    contract_characters: int = 0

    def record_category(self, category: str) -> None:
        self.by_category[category] = self.by_category.get(category, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_calls_attempted": self.attempted,
            "tool_calls_succeeded": self.succeeded,
            "tool_calls_failed": self.failed,
            "preflight_rejections": self.preflight_rejections,
            "duplicate_failed_calls_suppressed": self.duplicates_suppressed,
            "mcp_failures": self.mcp_failures,
            "failures_by_category": dict(sorted(self.by_category.items())),
            "execution_contract_characters": self.contract_characters,
        }


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
    tables: list[str] | None = None,
    has_metrics: bool = True,
    contract: ExecutionContract | None = None,
    out_of_time: Callable[[], bool] | None = None,
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
    telemetry = ToolLoopTelemetry()
    # Every failed attempt, and the signatures of the calls already known to
    # be bad. Without the second, a worker that cannot see why its call
    # failed proposes the same one until its budget is gone -- which is
    # exactly how one real run spent 36 attempts on 36 failures.
    attempts: list[FailedAttempt] = []
    failed_signatures: dict[str, FailedAttempt] = {}

    while calls < max_tool_calls:
        # The run-level time budget used to be read once, when the task
        # started, and never again. Six tasks each taking six model-latency
        # decisions could therefore overrun it by the whole length of a tool
        # loop -- observed as a 554-second question under a 300-second
        # budget. Checking here stops the run at its own limit instead of at
        # whatever outer timeout the caller happens to impose.
        if out_of_time is not None and out_of_time():
            notes.append("the run reached its time budget before this task finished")
            telemetry.record_category("run_time_budget_reached")
            break

        rendered_contract = render_contract(contract, task) if contract is not None else ""
        telemetry.contract_characters = len(rendered_contract)
        try:
            choice_payload = await ask_into(
                provider,
                ToolChoice,
                role="worker_next_tool",
                system=WORKER_TOOL_CHOICE,
                user=_tool_choice_prompt(
                    task,
                    payloads,
                    calls,
                    max_tool_calls,
                    toolset,
                    tables,
                    has_metrics,
                    execution_contract=rendered_contract,
                    attempts=attempts,
                ),
                context={
                    "task": task_json,
                    "calls_made": calls,
                    "max_calls": max_tool_calls,
                    "available_tools": toolset.available_tools,
                    "results": payloads,
                },
            )
            choice = choice_payload
        except LLMError as exc:
            notes.append(str(exc))
            break

        if choice.done or not choice.tool:
            break

        arguments = dict(choice.arguments)
        # Each branch below consumes one decision, so a worker that keeps
        # proposing bad calls still terminates.
        calls += 1
        telemetry.attempted += 1

        proposed = signature(choice.tool, arguments)
        known_bad = failed_signatures.get(proposed)
        if known_bad is not None:
            telemetry.duplicates_suppressed += 1
            telemetry.record_category("duplicate_failed_call_suppressed")
            notes.append(
                f"{choice.tool} was proposed again unchanged after failing; "
                "not dispatched a second time"
            )
            _remember(attempts, known_bad)
            if events:
                events.emit(
                    EventType.MCP_TOOL_FAILED,
                    tool_name=choice.tool,
                    task_id=task.task_id,
                    ok=False,
                    error=known_bad.message,
                    suppressed=True,
                )
            continue

        if contract is not None:
            rejection = preflight(choice.tool, arguments, contract)
            if rejection is not None:
                telemetry.failed += 1
                telemetry.preflight_rejections += 1
                telemetry.record_category(rejection.category)
                attempt = FailedAttempt(
                    tool=choice.tool,
                    arguments=arguments,
                    category=rejection.category,
                    message=rejection.message,
                    preflight=True,
                )
                failed_signatures[proposed] = attempt
                _remember(attempts, attempt)
                notes.append(f"{choice.tool} was not run: {rejection.message}")
                log.info(
                    "tool_call_rejected_before_dispatch",
                    task_id=task.task_id,
                    tool=choice.tool,
                    category=rejection.category,
                )
                if events:
                    events.emit(
                        EventType.MCP_TOOL_FAILED,
                        tool_name=choice.tool,
                        task_id=task.task_id,
                        ok=False,
                        error=rejection.message,
                        preflight=True,
                    )
                continue

        try:
            payload = await toolset.call(
                choice.tool, arguments, task_id=task.task_id, agent="analysis_worker"
            )
            payloads.append(payload)
            telemetry.succeeded += 1
        except BudgetExceeded as exc:
            notes.append(str(exc))
            if events:
                events.emit(EventType.BUDGET_EXCEEDED, task_id=task.task_id, detail=str(exc))
            break
        except ToolCallFailed as exc:
            telemetry.failed += 1
            telemetry.mcp_failures += 1
            telemetry.record_category("mcp_tool_failed")
            attempt = FailedAttempt(
                tool=choice.tool,
                arguments=arguments,
                category="mcp_tool_failed",
                message=str(exc),
            )
            failed_signatures[proposed] = attempt
            _remember(attempts, attempt)
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
            tool_telemetry=telemetry.as_dict(),
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

    if out_of_time is not None and out_of_time():
        # The tool loop stops at the budget but this call did not, so a run
        # could overrun by one findings request per task -- six of them on
        # the demo warehouse, which is most of the overrun that pushed a
        # question past its ceiling. Stopping here costs claims, never
        # correctness: nothing is published that was not verified.
        notes.append("the run reached its time budget before findings were written")
        telemetry.record_category("findings_skipped_out_of_time")
        return TaskOutcome(
            task_id=task.task_id,
            status="warning",
            result_ids=[p["result_id"] for p in payloads],
            tool_calls=calls,
            notes=notes,
            tool_telemetry=telemetry.as_dict(),
        )

    try:
        findings_payload = await ask_into(
            provider,
            FindingList,
            role="worker_findings",
            system=WORKER_FINDINGS,
            user=_findings_prompt(task, payloads),
            context={"task": task_json, "results": payloads},
        )
        findings = findings_payload.findings
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
        tool_telemetry=telemetry.as_dict(),
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


def _remember(attempts: list[FailedAttempt], attempt: FailedAttempt) -> None:
    """Keep the most recent failures, newest last, without unbounded growth."""
    attempts.append(attempt)
    del attempts[:-MAX_FEEDBACK_SHOWN]


def _tool_choice_prompt(
    task: AnalysisTask,
    payloads: list[dict[str, Any]],
    calls: int,
    max_calls: int,
    toolset: AnalyticsToolset,
    tables: list[str] | None = None,
    has_metrics: bool = True,
    execution_contract: str = "",
    attempts: list[FailedAttempt] | None = None,
) -> str:
    """Tell the worker what it is allowed to name.

    The table names and the metric-layer flag are here because a real model
    otherwise invents them: asked about `sales.csv` it called
    `profile_table(table="sales")` when the only table is `uploaded_data`,
    and reached for `compute_metric` on a dataset with no metric layer.
    Both are refusals the guard handles correctly and neither produces an
    answer, so the run burns its whole tool budget on failed calls. Naming
    the real tables costs nothing and removes the guess.
    """
    known_tables = ", ".join(tables or []) or "(none reported)"
    if has_metrics:
        guidance = (
            "Prefer `compute_metric`, `compare_segments` and `analyze_timeseries`: "
            "the metric layer defines these correctly. Use `run_readonly_sql` only "
            "for a shape it cannot express."
        )
    else:
        guidance = (
            "This dataset has NO metric layer, so `compute_metric`, "
            "`compare_segments`, `analyze_timeseries`, `decompose_change`, "
            "`compare_periods` and `rank_contributors` will all fail. Use "
            "`aggregate_for_question` (pass `table` and `question`) to answer "
            "the question, `profile_table` to describe the columns, "
            "`statistical_test` to compare two groups, or `run_readonly_sql` "
            "for a shape none of those express."
        )
    question = str(task.variables.get("question", "")) if task.variables else ""
    return f"""\
TASK
objective: {task.objective}
analysis_type: {task.analysis_type}
required_metrics: {", ".join(task.required_metrics) or "(none)"}
dimensions: {", ".join(task.dimensions) or "(none)"}
filters: {json.dumps(task.filters)}
preferred_tool: {task.preferred_tool}
table: {task.table or "(not set)"}
{f"original question: {question}" if question else ""}

TABLES IN THIS DATASET (use these names exactly; no others exist)
{known_tables}

TOOLS AVAILABLE
{", ".join(toolset.available_tools)}

{guidance}

{execution_contract}

TOOL CALLS USED
{calls} of {max_calls}
{_render_attempts(attempts)}
RESULTS SO FAR
{_results_digest(payloads)}

Choose the next tool call, or set done to true if the objective is met."""


def _render_attempts(attempts: list[FailedAttempt] | None) -> str:
    """Show the worker what it already tried and why it did not work.

    The loop used to put a failure in `notes` -- read by the report, never
    by the model -- so the next decision was made with no knowledge that the
    previous one had failed, let alone why. A worker with a wrong assumption
    about the schema therefore made the same wrong call until its budget ran
    out. The error message is the only thing that can change its mind.
    """
    if not attempts:
        return ""
    blocks = [a.render() for a in attempts[-MAX_FEEDBACK_SHOWN:]]
    return (
        "\nPREVIOUS ATTEMPTS THAT FAILED\n"
        + "\n\n".join(blocks)
        + "\n\nDo not repeat a failed call unchanged. Use the error and the "
        "contract above to choose a different call, or set done to true if "
        "nothing here can be answered.\n"
    )


def _findings_prompt(
    task: AnalysisTask,
    payloads: list[dict[str, Any]],
    field_guide: str = FINDINGS_FIELD_GUIDE,
) -> str:
    """The findings request. `field_guide` is a parameter so the diagnostic
    probe can compare wordings against the one production runs, rather than
    against a copy of it that has since drifted."""
    return f"""\
TASK
objective: {task.objective}
required_metrics: {", ".join(task.required_metrics) or "(none)"}

RESULTS
{_results_digest(payloads)}

State what these results show. Cite result_id and the specific cells for every
claim. Do not state a number that is not in the results above.

{field_guide}"""
