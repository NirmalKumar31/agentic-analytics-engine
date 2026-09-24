"""The benchmark harness.

Every case runs the real workflow and is then scored against the injected
ground truth, which the run never saw. Scoring is mechanical: substring and
direction checks on published findings, arithmetic re-verification of every
number, and structural checks on SQL, provenance and charts.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agentic_analytics.config import Settings, get_settings
from agentic_analytics.data.ground_truth import PATTERNS_BY_ID
from agentic_analytics.evaluation.cases import (
    CASES,
    EXPECTED_PATTERNS,
    BenchmarkCase,
    CaseResult,
)
from agentic_analytics.graph.runner import RunResult, run_analysis
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.verification.claims import is_causal
from agentic_analytics.verification.numeric import verify_numbers
from agentic_analytics.warehouse.session import SessionManager, open_demo_session

DOWN_WORDS = ("fell", "declin", "decreas", "lower", "worst", "lowest", "down", "weakest")
UP_WORDS = ("rose", "increas", "higher", "highest", "best", "up", "grew", "strongest")


def _direction_present(text: str, direction: str | None) -> bool:
    if direction is None:
        return True
    words = DOWN_WORDS if direction == "down" else UP_WORDS
    return any(w in text for w in words)


def score_case(case: BenchmarkCase, result: RunResult) -> CaseResult:
    """Score one run against what the generator actually injected."""
    scored = CaseResult(
        case_id=case.case_id,
        question=case.question,
        pattern_id=case.pattern_id,
        findings_published=len(result.published),
        findings_rejected=len(result.rejected),
        tool_calls=int(result.metrics.get("mcp_tool_calls", 0)),
        llm_calls=int(result.metrics.get("llm_calls", 0)),
        runtime_seconds=float(result.metrics.get("runtime_seconds", 0.0)),
    )
    corpus = " ".join(f.text.lower() for f in result.published)
    metric_ids = {m for f in result.published for m in f.metric_ids}

    scored.metric_found = (
        any(m in metric_ids or m in corpus for m in case.expect_metrics)
        if case.expect_metrics
        else True
    )
    scored.entity_found = (
        any(e in corpus for e in case.expect_entity_any) if case.expect_entity_any else True
    )
    scored.direction_found = _direction_present(corpus, case.expect_direction)
    scored.statistical_test_run = any(
        s.statistical_result is not None for s in result.results.values()
    )
    scored.rejection_observed = bool(result.rejected)

    if not scored.metric_found:
        scored.failures.append(f"no published finding reports {case.expect_metrics}")
    if not scored.entity_found:
        scored.failures.append(f"none of {case.expect_entity_any} appears in the findings")
    if not scored.direction_found:
        scored.failures.append(f"the {case.expect_direction} direction was not stated")
    if case.expect_statistical_test and not scored.statistical_test_run:
        scored.failures.append("no statistical test was run")
    if case.expect_rejection and not scored.rejection_observed:
        scored.failures.append("no over-claim was caught")

    scored.pattern_found = scored.metric_found and scored.entity_found and scored.direction_found

    # --- numeric accuracy: re-verify every published number independently
    numeric_checks = 0
    numeric_ok = 0
    for finding in result.published:
        cited = [result.results[r] for r in finding.result_ids if r in result.results]
        cells: list[tuple[float, str]] = []
        for cell in finding.evidence_cells:
            snapshot = result.results.get(cell.result_id)
            if snapshot is None:
                continue
            try:
                value = snapshot.cell(cell.row, cell.column)
            except (KeyError, IndexError):
                continue
            if isinstance(value, int | float) and not isinstance(value, bool):
                cells.append((float(value), f"{cell.result_id}[{cell.row}].{cell.column}"))
        verdict = verify_numbers(finding.text, finding.claimed_change, cells, cited)
        numeric_checks += 1
        if verdict.ok:
            numeric_ok += 1
        else:
            scored.failures.append(f"numeric check failed: {verdict.reason}")
    scored.numeric_accuracy = numeric_ok / numeric_checks if numeric_checks else 1.0

    # --- SQL validity
    statements = [s.sql for s in result.results.values() if s.sql]
    valid_sql = sum(1 for sql in statements if sql.lstrip().lower().startswith(("select", "with")))
    scored.sql_validity = valid_sql / len(statements) if statements else 1.0

    # --- tool call validity
    calls = result.mcp_trace
    scored.tool_call_validity = sum(1 for c in calls if c["ok"]) / len(calls) if calls else 0.0

    # --- published finding support rate
    proposed = len(result.published) + len(result.rejected)
    scored.support_rate = len(result.published) / proposed if proposed else 0.0

    # --- provenance completeness
    complete = sum(
        1
        for f in result.published
        if f.result_ids and all(r in result.results for r in f.result_ids) and f.verifier_reason
    )
    scored.provenance_completeness = complete / len(result.published) if result.published else 0.0

    # --- chart field validity
    chart_fields = 0
    chart_valid = 0
    for chart in result.charts:
        snapshot = result.results.get(chart.result_id)
        if snapshot is None:
            chart_fields += 1
            continue
        for encoding in chart.spec.get("encoding", {}).values():
            entries = encoding if isinstance(encoding, list) else [encoding]
            for entry in entries:
                if not isinstance(entry, dict) or "field" not in entry:
                    continue
                chart_fields += 1
                if entry["field"] in snapshot.columns:
                    chart_valid += 1
    scored.chart_field_validity = chart_valid / chart_fields if chart_fields else 1.0

    # --- published findings must never assert causation
    for finding in result.published:
        if is_causal(finding.text):
            scored.failures.append(f"a causal claim was published: {finding.text[:70]}")

    return scored


async def run_benchmark(
    warehouse_dir: Path | None = None, settings: Settings | None = None
) -> dict[str, Any]:
    """Run every case and return the scored report."""
    cfg = settings or get_settings()
    directory = warehouse_dir or cfg.demo_warehouse_dir
    manager = SessionManager(max_sessions=len(CASES) + 2)
    server = build_server(manager, cfg)
    started = time.monotonic()
    scored: list[CaseResult] = []

    try:
        for case in CASES:
            session = manager.add(open_demo_session(directory))
            result = await run_analysis(case.question, session, server, settings=cfg)
            scored.append(score_case(case, result))
            manager.drop(session.session_id)
    finally:
        manager.close_all()

    found_patterns = {s.pattern_id for s in scored if s.pattern_found and s.pattern_id}

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    summary = {
        "cases": len(scored),
        "cases_passed": sum(1 for s in scored if not s.failures),
        "patterns_expected": len(EXPECTED_PATTERNS),
        "patterns_found": len(found_patterns),
        "patterns_missed": sorted(EXPECTED_PATTERNS - found_patterns),
        "task_completion": mean([1.0 if s.findings_published else 0.0 for s in scored]),
        "metric_correctness": mean([1.0 if s.metric_found else 0.0 for s in scored]),
        "directional_correctness": mean([1.0 if s.direction_found else 0.0 for s in scored]),
        "numeric_accuracy": mean([s.numeric_accuracy for s in scored]),
        "sql_validity": mean([s.sql_validity for s in scored]),
        "tool_call_validity": mean([s.tool_call_validity for s in scored]),
        "published_support_rate": mean([s.support_rate for s in scored]),
        "provenance_completeness": mean([s.provenance_completeness for s in scored]),
        "chart_field_validity": mean([s.chart_field_validity for s in scored]),
        "unsupported_findings_published": sum(
            1 for s in scored for f in s.failures if "causal claim was published" in f
        ),
        "total_findings_published": sum(s.findings_published for s in scored),
        "total_findings_rejected": sum(s.findings_rejected for s in scored),
        "total_tool_calls": sum(s.tool_calls for s in scored),
        "total_llm_calls": sum(s.llm_calls for s in scored),
        "mean_runtime_seconds": mean([s.runtime_seconds for s in scored]),
        "wall_clock_seconds": round(time.monotonic() - started, 2),
        "dataset_fingerprint": open_demo_session(directory).dataset_fingerprint,
    }

    return {
        "summary": summary,
        "cases": [
            {
                **case.__dict__,
                "pattern_title": (PATTERNS_BY_ID[case.pattern_id].title if case.pattern_id else ""),
            }
            for case in scored
        ],
    }
