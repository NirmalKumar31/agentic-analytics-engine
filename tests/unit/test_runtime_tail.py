"""What may still call a model after the run's time budget is spent.

`max_runtime_seconds` is documented as a hard ceiling. It was not one: the
tool loop stopped, and then findings, verification, charts and the report all
carried on making model calls. A 300-second budget produced a question still
working twenty minutes later.

The rule now is that no *new* work starts after the deadline, and nothing
already verified is lost because the presentation step had no budget left.
A run that is out of time still gets its report -- organised by the engine,
with every factual sentence still a `PublishedFinding.text`.
"""

from __future__ import annotations

import inspect

from agentic_analytics.agents import reporter
from agentic_analytics.agents.schemas import PublishedFinding
from agentic_analytics.graph.build import build_graph

SOURCE = inspect.getsource(build_graph)


def _node(name: str) -> str:
    start = SOURCE.index(f"async def {name}")
    rest = SOURCE[start + 10 :]
    end = rest.find("\n    async def ")
    tail = rest.find("\n    def ")
    if 0 <= tail < (end if end >= 0 else len(rest)):
        end = tail
    return rest[: end if end >= 0 else len(rest)]


def test_every_model_consuming_node_reads_the_budget() -> None:
    """The audit this file exists for. Add a node, add it here."""
    for name in ("analysis_worker", "critique_findings", "build_visualizations", "write_report"):
        assert "out_of_time" in _node(name), f"{name} can call a model past the deadline"


def test_the_followup_round_is_gated_too() -> None:
    assert "out_of_time" in SOURCE[SOURCE.index("def needs_followup") :][:900]


def test_charts_are_skipped_rather_than_paid_for() -> None:
    node = _node("build_visualizations")
    assert 'return {\n                "charts": []' in node or '"charts": []' in node
    assert "time budget" in node


def test_a_run_out_of_time_still_reports_its_verified_findings() -> None:
    """The failure mode this replaced: "The report could not be written."."""
    node = _node("write_report")
    assert "assemble_without_model" in node
    assert "could not be written" not in node


def test_a_provider_failure_in_the_reporter_does_not_discard_findings() -> None:
    node = _node("write_report")
    # `BudgetError` subclasses `LLMError`, so one clause covers both.
    assert "except LLMError" in node
    assert node.count("assemble_without_model") >= 2


# --------------------------------------------- the deterministic report
def _finding(text: str, finding_id: str) -> PublishedFinding:
    return PublishedFinding(
        finding_id=finding_id,
        text=text,
        kind="calculated_fact",
        task_id="t",
        result_ids=["res_1"],
        evidence_cells=[],
        metric_ids=[],
        verification_status="supported",
        verifier_reason="ok",
        verifier_rule="critic",
    )


def test_the_engine_can_write_the_report_with_no_model_at_all() -> None:
    findings = [_finding("Revenue was 128450.75.", "f1"), _finding("Margin was 40.16%.", "f2")]
    report = reporter.assemble_without_model("q", findings, ["ran out of time"])

    assert report.key_findings == [f.text for f in findings]
    assert report.sections, "findings are still organised"
    # Every factual sentence is a finding's own text, unchanged.
    body = " ".join(s.body for s in report.sections)
    for finding in findings:
        assert finding.text in body
    assert "ran out of time" in report.limitations


def test_the_deterministic_report_invents_no_prose() -> None:
    findings = [_finding("Revenue was 128450.75.", "f1")]
    report = reporter.assemble_without_model("Why did revenue move?", findings, [])
    allowed = {f.text for f in findings}
    for section in report.sections:
        assert section.body in {" ".join(allowed), *allowed}
    assert report.executive_summary in {" ".join(allowed), *allowed}
    assert report.next_questions == []


def test_no_findings_still_produces_an_honest_report() -> None:
    report = reporter.assemble_without_model("q", [], [])
    assert report.key_findings == []
    assert "no conclusion" in report.executive_summary.lower()
