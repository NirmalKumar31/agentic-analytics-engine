"""The worker's tool loop: what it learns from a failure, and what it repeats.

A real run against the demo warehouse made 36 tool calls and failed all 36.
The plan was fine. What went wrong is that a failure was written to `notes`
-- read by the report, never by the model -- so each of the six decisions in
a task was taken with no knowledge that the previous five had failed, let
alone why. A wrong assumption about the schema therefore survived the whole
budget.

Two changes are tested here. The error now reaches the next prompt, and a
call already known to fail is not dispatched a second time.
"""

from __future__ import annotations

from typing import Any

from agentic_analytics.agents.execution import (
    ExecutionContract,
    MetricContract,
    TableContract,
    ToolContract,
)
from agentic_analytics.agents.schemas import AnalysisTask
from agentic_analytics.agents.worker import FailedAttempt, run_task
from agentic_analytics.llm.base import LLMProvider, LLMRequest
from agentic_analytics.mcp_layer.client import ToolCallFailed

REVENUE = MetricContract(
    name="revenue",
    description="Net revenue.",
    valid_dimensions=["acquisition_channel"],
    time_field="order_date",
    format="currency",
)
ORDERS = TableContract(name="orders", row_count=10, columns=[("order_id", "VARCHAR")])
CUSTOMERS = TableContract(name="customers", row_count=5, columns=[("customer_id", "VARCHAR")])
CONTRACT = ExecutionContract(
    metrics=[REVENUE],
    tables=[ORDERS, CUSTOMERS],
    tools=[
        ToolContract(
            name="compare_segments",
            description="Compare a metric across segments.",
            required={"metric": "string", "dimension": "string"},
        ),
        ToolContract(name="profile_table", description="", required={"table": "string"}),
    ],
    grains=["month"],
    has_metrics=True,
)


class ScriptedWorker(LLMProvider):
    """Replays a fixed sequence of tool choices and records every prompt."""

    name = "scripted"
    requires_credentials = False

    def __init__(self, choices: list[dict[str, Any]]) -> None:
        super().__init__(max_calls=64)
        self.choices = list(choices)
        self.prompts: list[str] = []

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        self._check_budget(request.role)
        self.usage.record(request.role)
        if request.role == "worker_findings":
            return {"findings": []}
        self.prompts.append(request.user)
        if self.choices:
            return self.choices.pop(0)
        return {"done": True}


class FakeToolset:
    """Counts what actually reached MCP."""

    def __init__(self, behaviour: Any = None) -> None:
        self.available_tools = ["compare_segments", "profile_table"]
        self.tool_contracts = list(CONTRACT.tools)
        self.dispatched: list[tuple[str, dict[str, Any]]] = []
        self._behaviour = behaviour

    async def call(
        self, tool: str, arguments: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        self.dispatched.append((tool, dict(arguments or {})))
        if self._behaviour is not None:
            return self._behaviour(tool, arguments or {})
        return {
            "result_id": f"res_{len(self.dispatched)}",
            "tool_name": tool,
            "columns": ["a"],
            "rows": [[1]],
            "row_count": 1,
        }


async def _run(choices: list[dict[str, Any]], toolset: Any, **kwargs: Any) -> Any:
    provider = ScriptedWorker(choices)
    outcome = await run_task(
        AnalysisTask(task_id="task_1", objective="o", preferred_tool="compare_segments"),
        provider=provider,
        toolset=toolset,  # type: ignore[arg-type]
        max_tool_calls=6,
        tables=["orders"],
        contract=CONTRACT,
        **kwargs,
    )
    return outcome, provider, toolset


# ------------------------------------------------- the error reaches the model
async def test_a_failed_call_is_shown_to_the_worker_next_time() -> None:
    """The change the 36-failure run needed."""

    def fails(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise ToolCallFailed("table 'sales' does not exist; tables are: orders")

    outcome, provider, _ = await _run(
        [
            {"tool": "profile_table", "arguments": {"table": "orders"}},
            {"tool": "profile_table", "arguments": {"table": "customers"}},
        ],
        FakeToolset(fails),
    )

    assert len(provider.prompts) >= 2
    second = provider.prompts[1]
    assert "PREVIOUS ATTEMPTS THAT FAILED" in second
    assert "table 'sales' does not exist" in second
    assert "Do not repeat a failed call unchanged" in second
    assert outcome.status == "failed"


async def test_a_preflight_refusal_is_shown_to_the_worker_next_time() -> None:
    outcome, provider, toolset = await _run(
        [
            {"tool": "compare_segments", "arguments": {"metric": "sales", "dimension": "x"}},
            {"tool": "compare_segments", "arguments": {"metric": "revenue", "dimension": "x"}},
        ],
        FakeToolset(),
    )
    second = provider.prompts[1]
    assert "metric 'sales' is not defined" in second
    assert "revenue" in second
    # Refused before dispatch, so MCP never saw it.
    assert toolset.dispatched == []
    assert outcome.tool_telemetry["preflight_rejections"] >= 1


async def test_the_feedback_history_is_bounded() -> None:
    from agentic_analytics.agents.worker import MAX_FEEDBACK_SHOWN

    choices = [{"tool": "profile_table", "arguments": {"table": f"missing_{i}"}} for i in range(6)]
    _, provider, _ = await _run(choices, FakeToolset())
    last = provider.prompts[-1]
    assert last.count("tool: profile_table") <= MAX_FEEDBACK_SHOWN


async def test_a_clean_first_call_shows_no_failure_section() -> None:
    _, provider, _ = await _run(
        [{"tool": "profile_table", "arguments": {"table": "orders"}}], FakeToolset()
    )
    assert "PREVIOUS ATTEMPTS THAT FAILED" not in provider.prompts[0]


# ------------------------------------------------------ duplicate suppression
async def test_the_same_failing_call_is_not_dispatched_twice() -> None:
    """Six identical bad calls must not consume a whole task."""

    def fails(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise ToolCallFailed("nope")

    call = {"tool": "profile_table", "arguments": {"table": "orders"}}
    outcome, _, toolset = await _run([dict(call) for _ in range(6)], FakeToolset(fails))

    assert len(toolset.dispatched) == 1, "the repeat reached MCP again"
    assert outcome.tool_telemetry["duplicate_failed_calls_suppressed"] == 5


async def test_argument_order_does_not_defeat_suppression() -> None:
    def fails(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise ToolCallFailed("nope")

    outcome, _, toolset = await _run(
        [
            {
                "tool": "compare_segments",
                "arguments": {"metric": "revenue", "dimension": "acquisition_channel"},
            },
            {
                "tool": "compare_segments",
                "arguments": {"dimension": "acquisition_channel", "metric": "revenue"},
            },
        ],
        FakeToolset(fails),
    )
    assert len(toolset.dispatched) == 1
    assert outcome.tool_telemetry["duplicate_failed_calls_suppressed"] == 1


async def test_a_suppressed_call_still_consumes_an_iteration() -> None:
    """Otherwise the loop never ends."""

    def fails(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise ToolCallFailed("nope")

    call = {"tool": "profile_table", "arguments": {"table": "orders"}}
    outcome, provider, _ = await _run([dict(call) for _ in range(50)], FakeToolset(fails))
    assert outcome.tool_calls == 6
    assert len(provider.prompts) == 6


async def test_a_changed_call_is_dispatched() -> None:
    """Suppression must not block a worker that actually learned something."""

    def fails_only_customers(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if arguments.get("table") == "customers":
            raise ToolCallFailed("no such table")
        return {
            "result_id": "res_1",
            "tool_name": tool,
            "columns": ["a"],
            "rows": [[1]],
            "row_count": 1,
        }

    outcome, _, toolset = await _run(
        [
            {"tool": "profile_table", "arguments": {"table": "customers"}},
            {"tool": "profile_table", "arguments": {"table": "orders"}},
        ],
        FakeToolset(fails_only_customers),
    )
    assert [t for t, _ in toolset.dispatched] == ["profile_table", "profile_table"]
    assert outcome.tool_telemetry["tool_calls_succeeded"] == 1


async def test_a_successful_call_is_not_suppressed_when_repeated() -> None:
    """Only *failed* signatures are remembered."""
    call = {"tool": "profile_table", "arguments": {"table": "orders"}}
    _, _, toolset = await _run([dict(call), dict(call)], FakeToolset())
    assert len(toolset.dispatched) == 2


# ---------------------------------------------------------------- telemetry
async def test_the_loop_separates_the_two_kinds_of_failure() -> None:
    """ "The model chose an invalid call" and "the tool broke" differ."""

    def fails(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise ToolCallFailed("the tool itself failed")

    outcome, _, _ = await _run(
        [
            {"tool": "compare_segments", "arguments": {"metric": "sales", "dimension": "x"}},
            {"tool": "profile_table", "arguments": {"table": "orders"}},
        ],
        FakeToolset(fails),
    )
    telemetry = outcome.tool_telemetry
    assert telemetry["preflight_rejections"] == 1
    assert telemetry["mcp_failures"] == 1
    assert telemetry["tool_calls_attempted"] == 2
    assert telemetry["tool_calls_succeeded"] == 0
    assert telemetry["failures_by_category"]["unknown_metric"] == 1
    assert telemetry["failures_by_category"]["mcp_tool_failed"] == 1


async def test_the_contract_size_is_recorded() -> None:
    outcome, _, _ = await _run(
        [{"tool": "profile_table", "arguments": {"table": "orders"}}], FakeToolset()
    )
    assert outcome.tool_telemetry["execution_contract_characters"] > 0


async def test_a_task_with_no_contract_still_runs() -> None:
    """The contract is optional, so nothing that omits it breaks."""
    provider = ScriptedWorker([{"tool": "profile_table", "arguments": {"table": "orders"}}])
    outcome = await run_task(
        AnalysisTask(task_id="t", objective="o"),
        provider=provider,
        toolset=FakeToolset(),  # type: ignore[arg-type]
        max_tool_calls=2,
    )
    assert outcome.tool_telemetry["tool_calls_succeeded"] == 1


def test_a_failed_attempt_renders_its_arguments() -> None:
    attempt = FailedAttempt(
        tool="compare_segments",
        arguments={"metric": "sales", "dimension": "x"},
        category="unknown_metric",
        message="metric 'sales' is not defined",
    )
    rendered = attempt.render()
    assert "tool: compare_segments" in rendered
    assert "metric='sales'" in rendered
    assert "metric 'sales' is not defined" in rendered


def test_a_failed_attempt_with_no_arguments_renders() -> None:
    assert "(none)" in FailedAttempt("t", {}, "c", "m").render()


# ------------------------------------------------------- the time budget
async def test_the_tool_loop_stops_when_the_run_is_out_of_time() -> None:
    """The run budget used to be read once, at task start, and never again.

    Six tasks each taking six model-latency decisions could then overrun it
    by a whole tool loop. A 300-second budget produced a 554-second question
    on the demo warehouse, and what actually ended it was the harness's
    outer timeout, so the engine's own limit meant nothing.
    """
    calls = {"n": 0}

    def out_of_time() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    provider = ScriptedWorker(
        [{"tool": "profile_table", "arguments": {"table": "orders"}} for _ in range(6)]
    )
    toolset = FakeToolset()
    outcome = await run_task(
        AnalysisTask(task_id="t", objective="o", preferred_tool="profile_table"),
        provider=provider,
        toolset=toolset,  # type: ignore[arg-type]
        max_tool_calls=6,
        contract=CONTRACT,
        out_of_time=out_of_time,
    )

    assert len(toolset.dispatched) == 2, "the loop ran past the budget"
    assert any("time budget" in note for note in outcome.notes)
    assert outcome.tool_telemetry["failures_by_category"].get("run_time_budget_reached") == 1


async def test_a_task_already_out_of_time_makes_no_model_call() -> None:
    provider = ScriptedWorker([{"tool": "profile_table", "arguments": {"table": "orders"}}])
    toolset = FakeToolset()
    outcome = await run_task(
        AnalysisTask(task_id="t", objective="o"),
        provider=provider,
        toolset=toolset,  # type: ignore[arg-type]
        max_tool_calls=6,
        contract=CONTRACT,
        out_of_time=lambda: True,
    )
    assert provider.prompts == []
    assert toolset.dispatched == []
    assert outcome.status == "failed"


async def test_a_run_with_time_to_spare_is_unaffected() -> None:
    provider = ScriptedWorker([{"tool": "profile_table", "arguments": {"table": "orders"}}])
    toolset = FakeToolset()
    outcome = await run_task(
        AnalysisTask(task_id="t", objective="o"),
        provider=provider,
        toolset=toolset,  # type: ignore[arg-type]
        max_tool_calls=6,
        contract=CONTRACT,
        out_of_time=lambda: False,
    )
    assert outcome.tool_telemetry["tool_calls_succeeded"] == 1
    assert "run_time_budget_reached" not in outcome.tool_telemetry["failures_by_category"]


async def test_no_new_claim_is_written_once_the_run_is_out_of_time() -> None:
    """The tool loop stopped at the budget; the findings call did not.

    One findings request per task is six more model calls on the demo
    warehouse, and that tail is most of what pushed a question past its
    ceiling after the tools had already stopped. Stopping here costs claims,
    never correctness.
    """
    state = {"n": 0}

    def out_of_time() -> bool:
        state["n"] += 1
        # False for the first loop check, true afterwards.
        return state["n"] > 1

    provider = ScriptedWorker([{"tool": "profile_table", "arguments": {"table": "orders"}}])
    outcome = await run_task(
        AnalysisTask(task_id="t", objective="o", preferred_tool="profile_table"),
        provider=provider,
        toolset=FakeToolset(),  # type: ignore[arg-type]
        max_tool_calls=6,
        contract=CONTRACT,
        out_of_time=out_of_time,
    )

    assert outcome.findings == []
    assert outcome.result_ids, "the results it did obtain are still reported"
    assert any("time budget" in note for note in outcome.notes)
    assert "findings_skipped_out_of_time" in outcome.tool_telemetry["failures_by_category"]
    assert "worker_findings" not in provider.usage.by_role
