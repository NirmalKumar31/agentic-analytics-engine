"""How the run degrades.

A single failed analysis task must not kill the run; a failure in the dataset
layer must stop it; a budget must bite; and nothing may quietly invent a
replacement result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentic_analytics.agents.schemas import AnalysisTask
from agentic_analytics.agents.worker import run_task
from agentic_analytics.config import Budgets, Settings
from agentic_analytics.events import EventBus, EventType
from agentic_analytics.graph.runner import run_analysis
from agentic_analytics.llm.base import LLMError, LLMProvider, LLMRequest
from agentic_analytics.llm.fake import FakeProvider
from agentic_analytics.mcp_layer.client import AnalyticsToolset, ToolBudget
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.warehouse.session import SessionManager, open_demo_session

QUESTION = "Revenue increased in Q3 2025, but gross margin fell. What caused it?"


class BrokenProvider(FakeProvider):
    """Fails for one role, behaves normally for the rest."""

    def __init__(self, broken_role: str, max_calls: int = 60) -> None:
        super().__init__(max_calls=max_calls)
        self.broken_role = broken_role

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        if request.role == self.broken_role:
            raise LLMError("the language model call failed (simulated)")
        return await super().complete_json(request)


class DeadProvider(LLMProvider):
    """Fails for every role."""

    name = "dead"

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        raise LLMError("the language model could not be reached (simulated)")


@pytest.fixture
def setup(warehouse_dir: Path):  # type: ignore[no-untyped-def]
    manager = SessionManager()
    server = build_server(manager)

    def make():  # type: ignore[no-untyped-def]
        return manager.add(open_demo_session(warehouse_dir)), server

    try:
        yield make
    finally:
        manager.close_all()


async def test_a_failing_worker_does_not_kill_the_run(setup) -> None:  # type: ignore[no-untyped-def]
    """One broken task; the others still produce a report."""
    session, server = setup()
    result = await run_analysis(
        QUESTION, session, server, provider=BrokenProvider("worker_findings")
    )
    assert result.report is not None
    assert result.tasks, "tasks still ran"
    assert all(t.status != "succeeded" for t in result.tasks)
    # Nothing was invented to fill the gap.
    assert result.published == []
    assert result.report.limitations


async def test_a_failed_question_analysis_stops_the_run_cleanly(setup) -> None:  # type: ignore[no-untyped-def]
    session, server = setup()
    bus = EventBus()
    result = await run_analysis(QUESTION, session, server, provider=DeadProvider(), events=bus)
    assert result.stopped_reason
    assert result.published == []
    assert EventType.RUN_FAILED in {e.type for e in bus.history}
    # A report is still produced, and it says why there is nothing in it.
    assert result.report is not None
    assert any("could not" in limitation for limitation in result.report.limitations)


async def test_a_failed_planner_stops_before_dispatching(setup) -> None:  # type: ignore[no-untyped-def]
    session, server = setup()
    result = await run_analysis(QUESTION, session, server, provider=BrokenProvider("planner"))
    assert result.stopped_reason
    assert result.tasks == []
    assert result.metrics["mcp_tool_calls"] == 0


async def test_a_failed_reporter_still_returns_the_findings(setup) -> None:  # type: ignore[no-untyped-def]
    session, server = setup()
    result = await run_analysis(QUESTION, session, server, provider=BrokenProvider("reporter"))
    assert result.published, "verification still ran"
    assert result.report is not None
    # The fallback report carries the findings rather than inventing prose.
    assert result.report.key_findings


async def test_a_failed_critic_withholds_rather_than_publishes(setup) -> None:  # type: ignore[no-untyped-def]
    """A verifier that cannot answer must not wave a finding through."""
    session, server = setup()
    result = await run_analysis(QUESTION, session, server, provider=BrokenProvider("critic"))
    assert result.published == []
    assert result.rejected
    assert all(v.status == "partially_supported" for v in result.rejected)


async def test_a_failed_visualizer_does_not_fail_the_run(setup) -> None:  # type: ignore[no-untyped-def]
    session, server = setup()
    result = await run_analysis(QUESTION, session, server, provider=BrokenProvider("visualizer"))
    assert result.report is not None
    assert result.published
    assert result.charts == []


async def test_a_tight_tool_budget_degrades_rather_than_crashes(
    setup, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    session, server = setup()
    settings = Settings(budgets=Budgets(max_total_tool_calls=2, max_tool_calls_per_task=1))
    bus = EventBus()
    result = await run_analysis(
        QUESTION, session, server, settings=settings, events=bus, provider=FakeProvider()
    )
    assert result.metrics["mcp_tool_calls"] <= 2
    assert result.report is not None
    assert any(t.status == "failed" for t in result.tasks)
    assert EventType.BUDGET_EXCEEDED in {e.type for e in bus.history}


async def test_a_worker_whose_tool_call_fails_reports_the_failure(setup) -> None:  # type: ignore[no-untyped-def]
    """A task that cannot run is marked failed, not quietly dropped."""
    session, server = setup()
    bus = EventBus()
    async with AnalyticsToolset(
        server,
        session_id=session.session_id,
        session_key=session.session_key,
        events=bus,
        budget=ToolBudget(),
    ) as toolset:
        task = AnalysisTask(
            task_id="bad_task",
            objective="Compute a metric that does not exist",
            required_metrics=["not_a_real_metric"],
            preferred_tool="compute_metric",
        )
        outcome = await run_task(
            task, provider=FakeProvider(), toolset=toolset, events=bus, max_tool_calls=2
        )
    assert outcome.status == "failed"
    assert outcome.error
    assert outcome.findings == []
    assert EventType.ANALYSIS_TASK_FAILED in {e.type for e in bus.history}


async def test_an_uploaded_table_is_analysed_without_a_metric_layer(
    tmp_path: Path,
) -> None:
    """An upload has no metric layer, so the engine maps the question itself.

    This is the path that used to raise IndexError: the planner indexed into
    an empty metric list. It has to produce a real answer, not merely survive.
    """
    from agentic_analytics.warehouse.session import open_upload_session

    csv = tmp_path / "small.csv"
    csv.write_text(
        "region,channel,amount\n"
        "West,web,10\nEast,web,20\nWest,retail,30\nEast,retail,5\nNorth,web,1\n"
    )
    manager = SessionManager()
    session = manager.add(open_upload_session(csv, "small.csv", "csv"))
    server = build_server(manager)
    try:
        result = await run_analysis("What is the total amount by region?", session, server)
    finally:
        manager.close_all()

    assert result.stopped_reason == ""
    assert result.report is not None
    assert result.published, "the upload produced no finding"

    # Two bounded tasks: describe the table, and answer the question by
    # mapping it onto the inferred schema. Workers run concurrently, so the
    # trace order is not fixed.
    tools = [call["tool_name"] for call in result.mcp_trace]
    assert sorted(tools) == ["aggregate_for_question", "profile_table"], tools

    # The aggregate is correct: West is 10 + 30 = 40, the largest.
    text = " ".join(f.text for f in result.published)
    assert "West" in text and "40" in text, text

    # And every number still traces back to a cited cell.
    for finding in result.published:
        assert finding.result_ids
        for cell in finding.evidence_cells:
            snapshot = result.results[cell.result_id]
            assert snapshot.cell(cell.row, cell.column) == cell.value


async def test_an_upload_still_refuses_write_sql(tmp_path: Path) -> None:
    """The guard applies to the SQL the profiling loop composes, too."""
    from agentic_analytics.warehouse.session import open_upload_session

    csv = tmp_path / "x.csv"
    csv.write_text("a,b\n1,2\n3,4\n")
    manager = SessionManager()
    session = manager.add(open_upload_session(csv, "x.csv", "csv"))
    server = build_server(manager)
    try:
        result = await run_analysis("Summarise this file", session, server)
    finally:
        manager.close_all()
    for snapshot in result.results.values():
        if snapshot.sql:
            assert snapshot.sql.lstrip().lower().startswith(("select", "with"))


async def test_the_followup_round_runs_at_most_once(setup) -> None:  # type: ignore[no-untyped-def]
    """The single loop edge must be bounded by construction."""
    session, server = setup()
    bus = EventBus()
    # Breaking finding generation leaves results but no published findings,
    # which is exactly the condition the follow-up gate looks for.
    result = await run_analysis(
        QUESTION, session, server, provider=BrokenProvider("worker_findings"), events=bus
    )
    followups = [e for e in bus.history if e.type == EventType.FOLLOWUP_ROUND_STARTED]
    assert len(followups) <= 1
    assert result.report is not None
