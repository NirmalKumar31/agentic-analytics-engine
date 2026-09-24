"""The whole workflow, end to end, on the demo warehouse with the scripted provider.

These assert properties of a real run: the graph terminates, parallel tasks
actually produce independent results, published findings trace to results, and
nothing unverified reaches the report.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_analytics.config import Settings
from agentic_analytics.events import EventType
from agentic_analytics.graph.runner import RunResult, run_analysis
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.verification.claims import is_causal
from agentic_analytics.verification.numeric import extract_numbers
from agentic_analytics.warehouse.session import SessionManager, open_demo_session

MARGIN_QUESTION = "Revenue increased in Q3 2025, but gross margin fell. What caused it?"
RETURNS_QUESTION = "Which customer segments are driving the increase in return rate?"
SHIPPING_QUESTION = "Do shipping delays appear to affect repeat purchasing?"


@pytest.fixture(scope="module")
def full_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A larger warehouse: the injected patterns need volume to be visible."""
    from agentic_analytics.data.generator import GeneratorConfig, generate_warehouse

    out = tmp_path_factory.mktemp("full")
    generate_warehouse(out, GeneratorConfig(n_customers=12_000, n_products=400, seed=99))
    return out


@pytest.fixture
def runner(full_warehouse: Path):  # type: ignore[no-untyped-def]
    manager = SessionManager()
    server = build_server(manager)

    async def run(question: str, **kwargs: object) -> RunResult:
        session = manager.add(open_demo_session(full_warehouse))
        return await run_analysis(question, session, server, **kwargs)  # type: ignore[arg-type]

    try:
        yield run
    finally:
        manager.close_all()


@pytest.fixture(scope="module")
async def margin_run(full_warehouse: Path) -> RunResult:
    manager = SessionManager()
    session = manager.add(open_demo_session(full_warehouse))
    try:
        return await run_analysis(MARGIN_QUESTION, session, build_server(manager))
    finally:
        manager.close_all()


async def test_run_completes_and_reports(margin_run: RunResult) -> None:
    assert margin_run.report is not None
    assert margin_run.stopped_reason == ""
    assert margin_run.metrics["tasks_failed"] == 0
    assert margin_run.metrics["findings_published"] > 0


async def test_plan_dispatches_several_parallel_tasks(margin_run: RunResult) -> None:
    assert len(margin_run.tasks) >= 3
    # Every task produced its own results; a shared or overwritten channel
    # would show up as duplicate result ids across tasks.
    all_ids = [rid for task in margin_run.tasks for rid in task.result_ids]
    assert len(all_ids) == len(set(all_ids))


async def test_task_outcomes_are_ordered_deterministically(margin_run: RunResult) -> None:
    task_ids = [t.task_id for t in margin_run.tasks]
    assert task_ids == sorted(task_ids)


async def test_every_published_finding_traces_to_a_real_result(
    margin_run: RunResult,
) -> None:
    assert margin_run.published
    for finding in margin_run.published:
        assert finding.result_ids, finding.text
        for result_id in finding.result_ids:
            assert result_id in margin_run.results, finding.text
        for cell in finding.evidence_cells:
            snapshot = margin_run.results[cell.result_id]
            assert snapshot.cell(cell.row, cell.column) == cell.value


async def test_every_published_finding_is_supported(margin_run: RunResult) -> None:
    assert all(f.verification_status == "supported" for f in margin_run.published)
    assert all(f.verifier_reason for f in margin_run.published)


async def test_report_states_no_number_outside_the_findings(
    margin_run: RunResult,
) -> None:
    allowed: list[float] = []
    for finding in margin_run.published:
        allowed.extend(extract_numbers(finding.text))
        for result_id in finding.result_ids:
            for row in margin_run.results[result_id].rows:
                allowed.extend(
                    float(v) for v in row if isinstance(v, int | float) and not isinstance(v, bool)
                )
    assert margin_run.report is not None
    texts = [
        margin_run.report.executive_summary,
        *margin_run.report.key_findings,
        *[s.body for s in margin_run.report.sections],
    ]
    for text in texts:
        for stated in extract_numbers(text):
            assert any(
                abs(stated - value) <= max(0.005, abs(value) * 0.005) for value in allowed
            ), f"{stated} in report is not supported: {text[:90]}"


async def test_no_published_finding_asserts_causation(margin_run: RunResult) -> None:
    for finding in margin_run.published:
        assert not is_causal(finding.text), finding.text


async def test_charts_reference_only_verified_results(margin_run: RunResult) -> None:
    published_ids = {f.finding_id for f in margin_run.published}
    for chart in margin_run.charts:
        assert chart.result_id in margin_run.results
        assert chart.finding_ids
        assert set(chart.finding_ids) <= published_ids
        fields = {
            enc.get("field") for enc in chart.spec["encoding"].values() if isinstance(enc, dict)
        }
        columns = set(margin_run.results[chart.result_id].columns)
        assert {f for f in fields if f} <= columns


async def test_every_sql_statement_is_read_only(margin_run: RunResult) -> None:
    forbidden = ("insert", "update", "delete", "drop", "alter", "create", "copy", "attach")
    for snapshot in margin_run.results.values():
        if not snapshot.sql:
            continue
        lowered = snapshot.sql.lower()
        assert lowered.lstrip().startswith(("select", "with")), snapshot.sql[:80]
        for word in forbidden:
            assert f" {word} " not in f" {lowered} ", (word, snapshot.sql[:120])


async def test_dataset_fingerprint_is_recorded_everywhere(margin_run: RunResult) -> None:
    fingerprint = margin_run.dataset["dataset_fingerprint"]
    assert fingerprint.startswith("sha256:")
    for snapshot in margin_run.results.values():
        assert snapshot.dataset_fingerprint == fingerprint


async def test_events_cover_the_whole_pipeline(margin_run: RunResult) -> None:
    kinds = {e["type"] for e in margin_run.events}
    assert {
        EventType.RUN_STARTED,
        EventType.DATASET_LOADED,
        EventType.QUESTION_ANALYZED,
        EventType.PLAN_GENERATED,
        EventType.ANALYSIS_TASK_STARTED,
        EventType.MCP_TOOL_CALLED,
        EventType.MCP_TOOL_COMPLETED,
        EventType.ANALYSIS_TASK_COMPLETED,
        EventType.FINDING_PROPOSED,
        EventType.FINDING_VERIFIED,
        EventType.REPORT_STARTED,
        EventType.REPORT_COMPLETED,
        EventType.RUN_COMPLETED,
    } <= kinds


async def test_events_are_sequential(margin_run: RunResult) -> None:
    seqs = [e["seq"] for e in margin_run.events]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))


async def test_mcp_trace_is_real_and_matches_the_results(margin_run: RunResult) -> None:
    assert margin_run.mcp_trace
    traced = {c["result_id"] for c in margin_run.mcp_trace if c["result_id"]}
    assert traced <= set(margin_run.results)
    assert all("session_id" not in c["arguments"] for c in margin_run.mcp_trace)


async def test_budgets_are_respected(margin_run: RunResult) -> None:
    budgets = Settings().budgets
    assert margin_run.metrics["analysis_tasks"] <= budgets.max_analysis_tasks
    assert margin_run.metrics["mcp_tool_calls"] <= budgets.max_total_tool_calls
    assert margin_run.metrics["llm_calls"] <= budgets.max_llm_calls
    for task in margin_run.tasks:
        assert task.tool_calls <= budgets.max_tool_calls_per_task


async def test_the_margin_question_recovers_the_injected_story(
    margin_run: RunResult,
) -> None:
    """The agents are not told the answer; this checks they found it."""
    text = " ".join(f.text.lower() for f in margin_run.published)
    assert "gross_margin_pct" in text and "fell" in text
    assert "revenue" in text and "rose" in text
    assert "discount" in text or "electronics" in text


async def test_causal_overclaim_is_rejected_on_the_shipping_question(runner) -> None:  # type: ignore[no-untyped-def]
    result = await runner(SHIPPING_QUESTION)
    assert result.rejected, "the scripted worker proposes a causal claim here"
    reasons = " ".join(v.reason.lower() for v in result.rejected)
    assert "causation" in reasons
    assert all(not is_causal(f.text) for f in result.published)


async def test_shipping_question_runs_a_real_statistical_test(runner) -> None:  # type: ignore[no-untyped-def]
    result = await runner(SHIPPING_QUESTION)
    stats = [s for s in result.results.values() if s.statistical_result is not None]
    assert stats, "a question about effect should produce a test"
    test = stats[0].statistical_result
    assert test is not None
    assert 0.0 <= test.p_value <= 1.0
    assert test.sample_sizes
    assert any("causation" in w.lower() for w in test.warnings)


async def test_returns_question_finds_the_segment(runner) -> None:  # type: ignore[no-untyped-def]
    result = await runner(RETURNS_QUESTION)
    text = " ".join(f.text.lower() for f in result.published)
    assert "return_rate" in text
    assert "customer_segment" in text or "new" in text


async def test_run_is_reproducible(runner) -> None:  # type: ignore[no-untyped-def]
    """Same question, same data, same scripted provider -> same conclusions.

    The trace is kept in real completion order, and workers run concurrently,
    so the order tool calls appear in is genuinely not fixed. What is fixed is
    the analysis: the same tasks, the same calls per task, and the same
    findings. Asserting a fixed global trace order would be asserting
    something the system does not provide.
    """
    first = await runner(MARGIN_QUESTION)
    second = await runner(MARGIN_QUESTION)

    assert [f.text for f in first.published] == [f.text for f in second.published]
    assert first.metrics["mcp_tool_calls"] == second.metrics["mcp_tool_calls"]
    assert sorted(c["tool_name"] for c in first.mcp_trace) == sorted(
        c["tool_name"] for c in second.mcp_trace
    )

    def by_task(result: RunResult) -> dict[str | None, list[str]]:
        grouped: dict[str | None, list[str]] = {}
        for call in result.mcp_trace:
            grouped.setdefault(call["task_id"], []).append(call["tool_name"])
        return grouped

    assert by_task(first) == by_task(second)
