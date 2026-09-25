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

from agentic_analytics.agents.schemas import PublishedFinding
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
from agentic_analytics.verification.claims import CAUSAL_RULE, is_causal
from agentic_analytics.verification.numeric import verify_numbers
from agentic_analytics.verification.sql import is_read_only
from agentic_analytics.warehouse.session import SessionManager, open_demo_session

DOWN_WORDS = ("fell", "declin", "decreas", "lower", "worst", "lowest", "down", "weakest")
UP_WORDS = ("rose", "increas", "higher", "highest", "best", "up", "grew", "strongest")


def _direction_present(text: str, direction: str | None) -> bool:
    if direction is None:
        return True
    words = DOWN_WORDS if direction == "down" else UP_WORDS
    return any(w in text for w in words)


def _findings_reporting(published: list[PublishedFinding], metric: str) -> list[PublishedFinding]:
    """Findings that actually report this metric.

    Preferring the structured `metric_ids` over the prose, and falling back
    to the text only when a finding recorded no metric id. Without this
    scoping, a direction check reads the whole run: a case expecting margin
    *down* and revenue *up* would pass on a run that said "fell" once about
    anything at all.
    """
    tagged = [f for f in published if metric in f.metric_ids]
    if tagged:
        return tagged
    return [f for f in published if metric in f.text.lower()]


#: Tools whose results are a movement through time. A `claimed_change` on
#: anything else is a difference between two segments -- "Apparel exceeds
#: Toys" -- which has a sign but is not a direction of travel.
TEMPORAL_TOOLS = frozenset({"analyze_timeseries", "compare_periods", "decompose_change"})


def _provenance_resolves(finding: PublishedFinding, results: dict[str, Any]) -> bool:
    """Every link from a finding back to the data must actually resolve.

    One definition, shared with the recording validator: the finding cites
    results, those results exist, each evidence cell names a real row and
    column, the value recorded on the cell still matches the stored
    snapshot, and a verifier reason was written. Checking only that the
    result ids exist would call a finding "fully traceable" while its cited
    cell pointed at a column that is not there.
    """
    if not finding.result_ids or not finding.verifier_reason:
        return False
    if not all(rid in results for rid in finding.result_ids):
        return False
    for cell in finding.evidence_cells:
        snapshot = results.get(cell.result_id)
        if snapshot is None:
            return False
        try:
            stored = snapshot.cell(cell.row, cell.column)
        except (KeyError, IndexError):
            return False
        if cell.value is None:
            continue
        if isinstance(stored, int | float) and isinstance(cell.value, int | float):
            if abs(float(stored) - float(cell.value)) > max(1e-6, abs(float(stored)) * 1e-9):
                return False
        elif stored != cell.value:
            return False
    return True


def _signed_change(finding: PublishedFinding, results: dict[str, Any]) -> str | None:
    """Up, down, or None, from the finding's recorded arithmetic.

    `claimed_change` is the structured from/to the worker stated and the
    verifier recomputed, so its sign is a fact about the run rather than a
    word that happened to appear in a sentence.

    Only counted for a finding whose evidence came from a temporal tool. A
    segment comparison also records a from/to -- "Apparel 1,522,302 versus
    Toys 825,693" -- and reading that as "revenue rose" is how a
    cross-sectional result gets mistaken for a trend.
    """
    temporal = any(
        getattr(results.get(rid), "tool_name", None) in TEMPORAL_TOOLS for rid in finding.result_ids
    )
    if not temporal:
        return None
    change = finding.claimed_change or {}
    before, after = change.get("from"), change.get("to")
    if not isinstance(before, int | float) or not isinstance(after, int | float):
        return None
    if after == before:
        return None
    return "up" if after > before else "down"


def _worded_direction(text: str) -> str | None:
    """A direction from wording, but only when the wording is unambiguous.

    "Apparel has the highest revenue and Toys the lowest" contains both an up
    word and a down word: it is a cross-sectional comparison, not a movement,
    and reading either direction out of it is how a check for "revenue rose"
    passed on a run that never said so.
    """
    low = text.lower()
    up = any(w in low for w in UP_WORDS)
    down = any(w in low for w in DOWN_WORDS)
    if up == down:
        return None
    return "up" if up else "down"


def _metric_directions_hold(
    published: list[PublishedFinding],
    expectations: tuple[tuple[str, str], ...],
    results: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Check each metric's direction against the findings reporting it.

    Structured arithmetic first, wording only as a fallback for findings
    that state a direction without recording a from/to -- a statistical
    result, for instance.
    """
    failures: list[str] = []
    for metric, direction in expectations:
        reporting = _findings_reporting(published, metric)
        if not reporting:
            failures.append(f"no published finding reports {metric}")
            continue

        signed = [d for d in (_signed_change(f, results) for f in reporting) if d is not None]
        if signed:
            if direction not in signed:
                failures.append(
                    f"{metric} was reported as {sorted(set(signed))}, expected {direction}"
                )
            continue

        worded = [d for d in (_worded_direction(f.text) for f in reporting) if d is not None]
        if not worded:
            failures.append(f"no finding states a direction for {metric}")
        elif direction not in worded:
            failures.append(
                f"{metric} was described as {sorted(set(worded))}, expected {direction}"
            )
    return (not failures), failures


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

    def reports(metric: str) -> bool:
        return metric in metric_ids or metric in corpus

    # Every metric in `all`, any one in `any`. A question asking about two
    # things is not answered by recovering one of them.
    missing_all = [m for m in case.expect_metrics_all if not reports(m)]
    any_satisfied = not case.expect_metrics_any or any(reports(m) for m in case.expect_metrics_any)
    scored.metric_found = not missing_all and any_satisfied

    scored.entity_found = (
        any(e in corpus for e in case.expect_entity_any) if case.expect_entity_any else True
    )

    # Directions are checked per metric, against the findings that report
    # that metric, and only fall back to the whole corpus for a case that
    # names a single bare direction.
    directions_hold, direction_failures = _metric_directions_hold(
        result.published, case.expect_metric_directions, result.results
    )
    scored.direction_found = directions_hold and _direction_present(corpus, case.expect_direction)

    tests = [
        s.statistical_result for s in result.results.values() if s.statistical_result is not None
    ]
    scored.statistical_test_run = bool(tests)

    # The sign of a SciPy-computed effect size, for a pattern whose signature
    # is a between-group difference rather than a trend.
    if case.expect_effect_sign is None:
        scored.effect_sign_correct = True
    else:
        signs = {
            ("negative" if t.effect_size < 0 else "positive")
            for t in tests
            if t.effect_size is not None and t.effect_size != 0
        }
        scored.effect_sign_correct = case.expect_effect_sign in signs
        if not scored.effect_sign_correct:
            scored.failures.append(
                f"no test reported a {case.expect_effect_sign} effect; "
                f"saw {sorted(signs) or 'none'}"
            )
    scored.rejection_observed = bool(result.rejected)
    # Not "something was withheld" but "the causal guard withheld something".
    # An unrelated numeric mismatch must not satisfy a case that exists to
    # prove causal over-claims are caught.
    scored.causal_rejection_observed = any(v.rule == CAUSAL_RULE for v in result.rejected)

    if missing_all:
        scored.failures.append(f"no published finding reports {missing_all}")
    if not any_satisfied:
        scored.failures.append(f"none of {case.expect_metrics_any} appears in the findings")
    if not scored.entity_found:
        scored.failures.append(f"none of {case.expect_entity_any} appears in the findings")
    scored.failures.extend(direction_failures)
    if case.expect_direction and not _direction_present(corpus, case.expect_direction):
        scored.failures.append(f"the {case.expect_direction} direction was not stated")
    if case.expect_statistical_test and not scored.statistical_test_run:
        scored.failures.append("no statistical test was run")
    if case.expect_causal_rejection and not scored.causal_rejection_observed:
        rules = sorted({v.rule for v in result.rejected}) or ["(nothing was withheld)"]
        scored.failures.append(f"no causal over-claim was caught; rejection rules seen: {rules}")
    if scored.unsupported_published_findings:
        scored.failures.append(
            f"{scored.unsupported_published_findings} unsupported finding(s) were published"
        )

    scored.pattern_found = (
        scored.metric_found
        and scored.entity_found
        and scored.direction_found
        and scored.effect_sign_correct
        and (not case.expect_causal_rejection or scored.causal_rejection_observed)
        and (not case.expect_statistical_test or scored.statistical_test_run)
    )

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
        scored.published_findings_numeric_checked += 1
        if verdict.ok:
            scored.published_findings_numeric_valid += 1
        else:
            scored.failures.append(f"numeric check failed: {verdict.reason}")

    # --- SQL validity, decided by the guard that protects the database.
    # Not by a prefix check: "does it start with SELECT" would score a
    # statement the runtime would have refused as valid.
    statements = [s.sql for s in result.results.values() if s.sql]
    scored.sql_statements = len(statements)
    for sql in statements:
        accepted, why = is_read_only(sql)
        if accepted:
            scored.sql_statements_valid += 1
        else:
            scored.failures.append(f"SQLGuard would refuse a recorded statement: {why}")

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

    # --- provenance completeness, down to the cell
    for finding in result.published:
        if _provenance_resolves(finding, result.results):
            scored.provenance_complete += 1
        else:
            scored.failures.append(f"provenance does not resolve for finding {finding.finding_id}")

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
        "published_with_supported_verdict": published - unsupported_published,
        "unsupported_published_findings": unsupported_published,
        # Renamed from `published_support_rate`, which read like an accuracy
        # score. It is a consistency check on the publication gate: did the
        # gate emit anything its own pipeline rejected? 1.0 by construction
        # unless the gate leaks. Not an independent semantic estimate.
        "publication_gate_integrity": (
            round((published - unsupported_published) / published, 6) if published else None
        ),
        "publication_gate_integrity_note": (
            "verifies the publication gate emitted no finding its verification "
            "pipeline rejected; not an independent human semantic accuracy estimate"
        ),
        # --- verification, each with its denominator named
        # Per published finding, not per numeric literal: `verify_numbers`
        # checks every figure in a finding and returns one verdict.
        "published_findings_numeric_checked": total("published_findings_numeric_checked"),
        "published_findings_numeric_valid": total("published_findings_numeric_valid"),
        "published_finding_numeric_verification_rate": ratio(
            "published_findings_numeric_valid", "published_findings_numeric_checked"
        ),
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
