"""The benchmark harness.

Every case runs the real workflow and is then scored against the injected
ground truth, which the run never saw. Scoring is mechanical: substring and
direction checks on published findings, arithmetic re-verification of every
number, and structural checks on SQL, provenance and charts.
"""

from __future__ import annotations

import time
from collections.abc import Callable
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


# Tools that own computation. A run resolved through these rather than
# through model-written SQL is the difference between an analytics engine and
# a text-to-SQL wrapper, so the split is measured rather than asserted.
DETERMINISTIC_TOOLS = frozenset(
    {
        "compute_metric",
        "compare_segments",
        "compare_periods",
        "analyze_timeseries",
        "decompose_change",
        "rank_contributors",
        "correlation_matrix",
        "statistical_test",
        "profile_dataset",
        "profile_table",
        "aggregate_for_question",
        "describe_table",
        "list_tables",
        "list_metrics",
        "sample_rows",
        "get_result",
    }
)
STATISTICAL_TOOLS = frozenset({"statistical_test", "correlation_matrix"})
DECOMPOSITION_TOOLS = frozenset({"decompose_change", "rank_contributors", "compare_periods"})


def score_case(case: BenchmarkCase, result: RunResult) -> CaseResult:
    """Score one run against what the generator actually injected."""
    scored = CaseResult(
        case_id=case.case_id,
        question=case.question,
        pattern_id=case.pattern_id,
        llm_calls=int(result.metrics.get("llm_calls", 0)),
        runtime_seconds=float(result.metrics.get("runtime_seconds", 0.0)),
    )

    # --- findings, split by denominator
    scored.published_findings = len(result.published)
    scored.withheld_findings = len(result.rejected)
    scored.candidate_findings = scored.published_findings + scored.withheld_findings
    scored.supported_candidate_findings = sum(
        1 for f in result.published if f.verification_status == "supported"
    )
    scored.unsupported_published_findings = sum(
        1 for f in result.published if f.verification_status != "supported"
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
    if scored.unsupported_published_findings:
        scored.failures.append(
            f"{scored.unsupported_published_findings} unsupported finding(s) were published"
        )

    scored.pattern_found = scored.metric_found and scored.entity_found and scored.direction_found

    # --- numeric accuracy: re-verify every published number independently
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
        scored.numeric_assertions += 1
        if verdict.ok:
            scored.numeric_assertions_correct += 1
        else:
            scored.failures.append(f"numeric check failed: {verdict.reason}")

    # --- SQL validity
    statements = [s.sql for s in result.results.values() if s.sql]
    scored.sql_statements = len(statements)
    scored.sql_statements_valid = sum(
        1 for sql in statements if sql.lstrip().lower().startswith(("select", "with"))
    )

    # --- tool calls, and how many were governed rather than generated SQL
    scored.tool_calls = len(result.mcp_trace)
    scored.tool_calls_ok = sum(1 for c in result.mcp_trace if c["ok"])
    for call in result.mcp_trace:
        name = str(call["tool_name"])
        if name == "run_readonly_sql":
            scored.generated_sql_calls += 1
        elif name in DETERMINISTIC_TOOLS:
            scored.deterministic_tool_calls += 1
        if name in STATISTICAL_TOOLS:
            scored.statistical_tool_calls += 1
        if name in DECOMPOSITION_TOOLS:
            scored.decomposition_tool_calls += 1

    # --- provenance completeness
    scored.provenance_complete = sum(
        1
        for f in result.published
        if f.result_ids and all(r in result.results for r in f.result_ids) and f.verifier_reason
    )

    # --- chart field validity
    for chart in result.charts:
        snapshot = result.results.get(chart.result_id)
        for encoding in chart.spec.get("encoding", {}).values():
            entries = encoding if isinstance(encoding, list) else [encoding]
            for entry in entries:
                if not isinstance(entry, dict) or "field" not in entry:
                    continue
                scored.chart_fields += 1
                if snapshot is not None and entry["field"] in snapshot.columns:
                    scored.chart_fields_valid += 1

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
    provider_mode = cfg.provider_mode
    probe = open_demo_session(directory)
    fingerprint = probe.dataset_fingerprint
    probe.close()

    try:
        for case in CASES:
            session = manager.add(open_demo_session(directory))
            result = await run_analysis(case.question, session, server, settings=cfg)
            scored.append(score_case(case, result))
            manager.drop(session.session_id)
    finally:
        manager.close_all()

    found_patterns = {s.pattern_id for s in scored if s.pattern_found and s.pattern_id}

    def total(attribute: str) -> int:
        return sum(getattr(s, attribute) for s in scored)

    def ratio(numerator: str, denominator: str) -> float | None:
        """A pooled rate, so the denominator is explicit and auditable.

        Pooled rather than a mean of per-case rates: averaging ratios weights
        a case with two findings the same as one with ten.
        """
        bottom = total(denominator)
        return round(total(numerator) / bottom, 6) if bottom else None

    candidates = total("candidate_findings")
    published = total("published_findings")
    unsupported_published = total("unsupported_published_findings")

    summary: dict[str, Any] = {
        # --- what the benchmark is, stated in the artefact itself
        "benchmark_kind": "deterministic end-to-end engine benchmark",
        "provider_mode": provider_mode,
        "measures": (
            "graph execution, MCP tool execution, SQL and statistical "
            "correctness, provenance, deterministic verification and "
            "publication behaviour"
        ),
        "does_not_measure": (
            "language-model question understanding, planning quality or tool-selection reliability"
        ),
        # --- cases
        "cases": len(scored),
        "cases_passed": sum(1 for s in scored if not s.failures),
        "patterns_expected": len(EXPECTED_PATTERNS),
        "patterns_found": len(found_patterns),
        "patterns_missed": sorted(EXPECTED_PATTERNS - found_patterns),
        # --- findings, each with its denominator named
        "candidate_findings": candidates,
        "supported_candidate_findings": total("supported_candidate_findings"),
        "withheld_findings": total("withheld_findings"),
        "candidate_support_rate": ratio("supported_candidate_findings", "candidate_findings"),
        "published_findings": published,
        "unsupported_published_findings": unsupported_published,
        "published_support_rate": (
            round((published - unsupported_published) / published, 6) if published else None
        ),
        # --- verification, each with its denominator named
        "numeric_assertions": total("numeric_assertions"),
        "numeric_assertions_correct": total("numeric_assertions_correct"),
        "numeric_accuracy": ratio("numeric_assertions_correct", "numeric_assertions"),
        "sql_statements": total("sql_statements"),
        "sql_validity": ratio("sql_statements_valid", "sql_statements"),
        "tool_calls": total("tool_calls"),
        "tool_call_validity": ratio("tool_calls_ok", "tool_calls"),
        "provenance_completeness": ratio("provenance_complete", "published_findings"),
        "chart_fields": total("chart_fields"),
        "chart_field_validity": ratio("chart_fields_valid", "chart_fields"),
        # --- correctness against the injected patterns
        "task_completion": ratio_of_cases(scored, lambda s: bool(s.published_findings)),
        "metric_correctness": ratio_of_cases(scored, lambda s: s.metric_found),
        "directional_correctness": ratio_of_cases(scored, lambda s: s.direction_found),
        # --- tool dependence: is this an engine or a SQL-writing wrapper?
        "deterministic_tool_calls": total("deterministic_tool_calls"),
        "generated_sql_calls": total("generated_sql_calls"),
        "resolved_without_generated_sql": ratio("deterministic_tool_calls", "tool_calls"),
        "statistical_tool_calls": total("statistical_tool_calls"),
        "decomposition_tool_calls": total("decomposition_tool_calls"),
        # --- cost, named so it cannot be read as model latency
        "provider_calls": total("llm_calls"),
        "deterministic_engine_seconds_mean": round(
            sum(s.runtime_seconds for s in scored) / len(scored), 4
        )
        if scored
        else None,
        "deterministic_engine_seconds_note": (
            "engine runtime with the scripted provider; excludes model inference latency entirely"
        ),
        "wall_clock_seconds": round(time.monotonic() - started, 2),
        "dataset_fingerprint": fingerprint,
    }

    return {
        "summary": summary,
        "cases": [
            case.as_dict()
            | {"pattern_title": (PATTERNS_BY_ID[case.pattern_id].title if case.pattern_id else "")}
            for case in scored
        ],
    }


def ratio_of_cases(
    scored: list[CaseResult], predicate: Callable[[CaseResult], bool]
) -> float | None:
    """Share of cases satisfying a predicate. The denominator is the cases."""
    if not scored:
        return None
    return round(sum(1 for s in scored if predicate(s)) / len(scored), 6)
