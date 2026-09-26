"""Behavioural evaluation against a real language model.

This is **not** the deterministic benchmark and does not replace it. That one
runs the scripted provider over one warehouse with a known answer key, is
reproducible, and belongs in CI as regression safety. This one runs an actual
model, is explicitly non-deterministic, is opt-in, and never runs in CI.

The question it answers is different too. The deterministic benchmark asks
"does the engine still compute the right numbers". This asks "can a real
model *operate* this architecture safely" -- which is about whether it
produces parseable structured output, selects tools that exist, plans
something executable, and whether the verification pipeline catches it when
it does not.

There is deliberately no pass mark. A model is not required to reproduce the
scripted plan; there are many reasonable ways to answer "what is the total
revenue by region", and picking a different one is not a failure. What *is*
recorded, per question:

* whether each agent role returned output the schema accepted;
* what the planner produced, and whether any of it was executable;
* which tools were chosen, and which calls failed;
* how many findings were proposed, published and withheld, and which
  verification rule withheld them;
* runtime, provider calls and token usage.

A withheld finding is a success, not a failure. It means a model proposed
something the evidence did not support and the engine caught it, which is
exactly the property the architecture exists to provide.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import subprocess
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agentic_analytics.agents.base import StructuredCall, observe_structured_calls
from agentic_analytics.config import Settings
from agentic_analytics.evaluation.datasets import (
    DATASETS,
    SEED,
    WAREHOUSE_QUESTIONS,
    EvalDataset,
    EvalQuestion,
)
from agentic_analytics.graph.runner import RunResult, run_analysis
from agentic_analytics.llm.registry import build_provider
from agentic_analytics.logging import get_logger
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.warehouse.session import (
    SessionManager,
    open_demo_session,
    open_upload_session,
)

log = get_logger(__name__)

#: Bumped when the outcome schema changes, so a checkpoint from an older
#: harness is not silently merged into a newer report.
HARNESS_SCHEMA_VERSION = 2


@dataclass
class QuestionOutcome:
    """Everything observed for one question. No pass/fail verdict."""

    dataset: str
    question: str
    kind: str
    expectation: str

    completed: bool = False
    stopped_reason: str = ""
    error: str = ""
    #: Cut off by the harness rather than by the engine. A per-call timeout
    #: does not bound a question: forty calls at three minutes each is two
    #: hours for one answer.
    question_timeout: bool = False

    # Where a run broke down, by role and by *stage*. Watching only the
    # provider call cannot distinguish "returned a dict the schema rejected"
    # from "the HTTP request timed out", and those say opposite things about
    # a model.
    roles_called: dict[str, int] = field(default_factory=dict)
    roles_failed: dict[str, int] = field(default_factory=dict)
    failures_by_stage: dict[str, int] = field(default_factory=dict)
    schema_validation_failures: int = 0
    json_parse_failures: int = 0
    transport_failures: int = 0
    timeout_failures: int = 0
    budget_failures: int = 0

    # What the model planned, and what the engine had to do about it.
    raw_model_tasks_returned: int = 0
    raw_model_tools_requested: list[str] = field(default_factory=list)
    raw_model_tasks: list[dict[str, Any]] = field(default_factory=list)
    cleaned_tasks: list[dict[str, Any]] = field(default_factory=list)
    planner_tasks_after_cleanup: int = 0
    tasks_redirected_by_engine: int = 0
    redirect_reasons: list[str] = field(default_factory=list)
    fallback_plan_used: bool = False
    model_plan_directly_executable: bool = False

    planned_tasks: int = 0
    tools_selected: list[str] = field(default_factory=list)
    tool_calls: int = 0
    tool_call_failures: int = 0
    generated_sql_calls: int = 0
    statistical_tests: int = 0

    candidate_findings: int = 0
    published_findings: int = 0
    withheld_findings: int = 0
    withheld_rules: dict[str, int] = field(default_factory=dict)

    report_written: bool = False
    report_sections: int = 0
    #: Every candidate finding with its verdict, for human review. Counts
    #: alone cannot tell you whether a published finding answered the
    #: question or was merely true and useless.
    claims: list[dict[str, Any]] = field(default_factory=list)

    #: Orthogonal descriptive flags rather than one winner/loser enum.
    #: A refusal on an unanswerable question is the desired behaviour.
    outcome_flags: list[str] = field(default_factory=list)

    structured_agent_calls: int = 0
    provider_request_attempts: int = 0
    provider_successful_responses: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    runtime_seconds: float = 0.0


def _observe(
    outcome: QuestionOutcome,
    result: RunResult,
    calls: list[StructuredCall],
    telemetry: dict[str, Any],
) -> QuestionOutcome:
    """Fill an outcome from a completed run."""
    outcome.stopped_reason = result.stopped_reason
    outcome.completed = not result.stopped_reason

    # --- what the model planned, and what the engine did about it
    outcome.raw_model_tasks_returned = int(telemetry.get("raw_model_tasks_returned", 0))
    outcome.raw_model_tools_requested = list(telemetry.get("raw_model_tools_requested", []))
    outcome.raw_model_tasks = list(telemetry.get("raw_model_tasks", []))
    outcome.cleaned_tasks = list(telemetry.get("cleaned_tasks", []))
    outcome.planner_tasks_after_cleanup = int(telemetry.get("planner_tasks_after_cleanup", 0))
    outcome.tasks_redirected_by_engine = int(telemetry.get("tasks_redirected_by_engine", 0))
    outcome.redirect_reasons = list(telemetry.get("redirect_reasons", []))
    outcome.fallback_plan_used = bool(telemetry.get("fallback_plan_used", False))
    outcome.model_plan_directly_executable = bool(
        telemetry.get("model_plan_directly_executable", False)
    )

    outcome.planned_tasks = len(result.tasks)
    for call in result.mcp_trace:
        name = str(call.get("tool_name", ""))
        if name and name not in outcome.tools_selected:
            outcome.tools_selected.append(name)
        outcome.tool_calls += 1
        if not call.get("ok"):
            outcome.tool_call_failures += 1
        if name == "run_readonly_sql":
            outcome.generated_sql_calls += 1
    outcome.statistical_tests = sum(
        1 for s in result.results.values() if s.statistical_result is not None
    )

    outcome.published_findings = len(result.published)
    outcome.withheld_findings = len(result.rejected)
    outcome.candidate_findings = outcome.published_findings + outcome.withheld_findings
    for verdict in result.rejected:
        rule = verdict.rule or "unspecified"
        outcome.withheld_rules[rule] = outcome.withheld_rules.get(rule, 0) + 1

    # --- every claim, published or withheld, for human review.
    #
    # A withheld claim used to be stored with an empty text, which made the
    # most interesting artifact in the run -- the thing a model said that
    # the evidence did not support -- impossible to review. The candidate
    # is recovered from the task outcomes by id. Synthetic data, so finding
    # text is safe to keep; raw rows and prompts are not stored anywhere.
    candidates = {finding.finding_id: finding for task in result.tasks for finding in task.findings}

    def claim(
        finding_id: str,
        *,
        published: bool,
        status: str,
        rule: str,
        reason: str,
        text: str = "",
        kind: str = "",
        task_id: str | None = None,
        result_ids: list[str] | None = None,
        evidence_cells: list[dict[str, Any]] | None = None,
        metric_ids: list[str] | None = None,
        claimed_change: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source = candidates.get(finding_id)
        return {
            "finding_id": finding_id,
            "text": text or (source.text if source else ""),
            "kind": kind or (source.kind if source else ""),
            "task_id": task_id or (source.task_id if source else None),
            "result_ids": result_ids
            if result_ids is not None
            else (list(source.result_ids) if source else []),
            "evidence_cells": evidence_cells
            if evidence_cells is not None
            else ([c.model_dump() for c in source.evidence_cells] if source else []),
            "metric_ids": metric_ids
            if metric_ids is not None
            else (list(source.metric_ids) if source else []),
            "claimed_change": claimed_change
            if claimed_change is not None
            else (source.claimed_change if source else None),
            "published": published,
            "status": status,
            "rule": rule,
            "reason": reason[:300],
        }

    for finding in result.published:
        outcome.claims.append(
            claim(
                finding.finding_id,
                published=True,
                status=finding.verification_status,
                rule=finding.verifier_rule,
                reason=finding.verifier_reason,
                text=finding.text,
                kind=finding.kind,
                task_id=finding.task_id,
                result_ids=list(finding.result_ids),
                evidence_cells=[c.model_dump() for c in finding.evidence_cells],
                metric_ids=list(finding.metric_ids),
                claimed_change=finding.claimed_change,
            )
        )
    for verdict in result.rejected:
        outcome.claims.append(
            claim(
                verdict.finding_id,
                published=False,
                status=verdict.status,
                rule=verdict.rule,
                reason=verdict.reason,
            )
        )

    report = result.report
    outcome.report_written = report is not None and bool(report.key_findings)
    outcome.report_sections = len(report.sections) if report else 0

    _record_calls(outcome, calls)
    outcome.outcome_flags = _flags(outcome)
    return outcome


def _record_calls(outcome: QuestionOutcome, calls: list[StructuredCall]) -> None:
    """Attribute each agent call to a role and a failure stage."""
    for call in calls:
        outcome.roles_called[call.role] = outcome.roles_called.get(call.role, 0) + 1
        if call.stage == "success":
            continue
        outcome.roles_failed[call.role] = outcome.roles_failed.get(call.role, 0) + 1
        outcome.failures_by_stage[call.stage] = outcome.failures_by_stage.get(call.stage, 0) + 1
    outcome.schema_validation_failures = outcome.failures_by_stage.get("schema_validation_error", 0)
    outcome.json_parse_failures = outcome.failures_by_stage.get("json_parse_error", 0)
    outcome.transport_failures = outcome.failures_by_stage.get("transport_error", 0)
    outcome.timeout_failures = outcome.failures_by_stage.get("timeout", 0)
    outcome.budget_failures = outcome.failures_by_stage.get("budget_exhausted", 0)
    # Structured *agent* calls, which is not the same thing as network
    # requests to a provider: one agent call is one ask-and-validate, and a
    # request that never returned is still a request. Cloud cost accounting
    # needs the attempt count, not this one.
    outcome.structured_agent_calls = len(calls)


def _flags(outcome: QuestionOutcome) -> list[str]:
    """Descriptive, orthogonal, and deliberately not a score.

    A refusal on a question the data cannot answer is the behaviour we want,
    so it gets its own flag rather than being folded into "failed".
    """
    flags: list[str] = []
    if outcome.question_timeout:
        flags.append("question_timeout")
    if outcome.error:
        flags.append("harness_error")
    if outcome.timeout_failures or outcome.transport_failures:
        flags.append("provider_failure")
    if outcome.schema_validation_failures or outcome.json_parse_failures:
        flags.append("schema_failure")
    if outcome.tool_call_failures:
        flags.append("tool_failure")
    if outcome.completed and outcome.published_findings:
        flags.append("completed_with_published_findings")
    if outcome.completed and not outcome.candidate_findings:
        flags.append("completed_no_candidate_findings")
    if outcome.completed and outcome.candidate_findings and not outcome.published_findings:
        flags.append("completed_all_findings_withheld")
    # Publishing nothing is only a *refusal* when the run got there cleanly.
    # A timeout, a schema failure or a failed tool call also produce zero
    # findings, and calling those a refusal would describe a breakage as an
    # intention. The wording is deliberately cautious too: the architecture
    # has no explicit "I decline" signal, so this says what was observed.
    clean_path = (
        outcome.completed
        and not outcome.question_timeout
        and not outcome.error
        and not outcome.transport_failures
        and not outcome.timeout_failures
        and not outcome.json_parse_failures
        and not outcome.schema_validation_failures
        and not outcome.tool_call_failures
    )
    if (
        outcome.kind in {"ambiguous", "unsupported"}
        and clean_path
        and not outcome.published_findings
    ):
        flags.append("no_published_finding_on_unanswerable_question")
    if outcome.fallback_plan_used or outcome.tasks_redirected_by_engine:
        flags.append("engine_rescued")
    if outcome.model_plan_directly_executable:
        flags.append("direct_model_plan")
    return flags


async def evaluate_question(
    question: EvalQuestion,
    dataset_id: str,
    session_factory: Any,
    cfg: Settings,
    *,
    question_timeout_seconds: float = 600.0,
) -> QuestionOutcome:
    """Run one question and record what happened. Never raises."""
    outcome = QuestionOutcome(
        dataset=dataset_id,
        question=question.text,
        kind=question.kind,
        expectation=question.expectation,
    )
    manager = SessionManager(max_sessions=4)
    provider = build_provider(cfg)
    calls: list[StructuredCall] = []
    telemetry: dict[str, Any] = {}
    started = time.monotonic()
    try:
        session = manager.add(session_factory())
        server = build_server(manager, cfg)
        with observe_structured_calls(calls.append):
            # A whole-question ceiling. The per-call timeout bounds one
            # request; nothing bounded the question, and a sweep lost two
            # hours to that.
            async with asyncio.timeout(question_timeout_seconds):
                result = await run_analysis(
                    question.text,
                    session,
                    server,
                    settings=cfg,
                    provider=provider,
                    telemetry=telemetry,
                )
        _observe(outcome, result, calls, telemetry)
    except TimeoutError:
        outcome.question_timeout = True
        log.warning(
            "real_model_question_timeout",
            dataset=dataset_id,
            seconds=question_timeout_seconds,
        )
        _record_calls(outcome, calls)
        outcome.outcome_flags = _flags(outcome)
    except Exception as exc:
        # A crash is a result too, and finding them is the point.
        outcome.error = f"{type(exc).__name__}: {exc}"[:300]
        log.warning("real_model_run_crashed", dataset=dataset_id, error=outcome.error)
        log.debug("real_model_traceback", trace=traceback.format_exc()[:2000])
        _record_calls(outcome, calls)
        outcome.outcome_flags = _flags(outcome)
    finally:
        outcome.runtime_seconds = round(time.monotonic() - started, 2)
        outcome.input_tokens = int(getattr(provider.usage, "input_tokens", 0))
        outcome.output_tokens = int(getattr(provider.usage, "output_tokens", 0))
        outcome.provider_request_attempts = int(getattr(provider.usage, "attempts", 0))
        outcome.provider_successful_responses = int(getattr(provider.usage, "successes", 0))
        await provider.aclose()
        manager.close_all()
    return outcome


def question_key(dataset_id: str, index: int, text: str) -> str:
    """A stable identity for one question, for checkpointing.

    Includes a hash of the text so that editing a question invalidates its
    checkpoint rather than silently resuming past a different question.
    """
    digest = hashlib.sha256(text.encode()).hexdigest()[:8]
    return f"{dataset_id}:{index:02d}:{digest}"


def environment_fingerprint(cfg: Settings) -> dict[str, Any]:
    """Enough provenance to know what was actually evaluated.

    A report saying only `model = qwen3:4b` cannot be reproduced or trusted;
    quantization and digest change what a "model" is. Every field is
    best-effort: a missing one is recorded as unknown rather than failing
    the evaluation.
    """
    model = cfg.ollama_model if cfg.provider_mode == "local" else cfg.cloud_model
    info: dict[str, Any] = {
        "harness_schema": HARNESS_SCHEMA_VERSION,
        "provider_mode": cfg.provider_mode,
        "model": model,
        "think": cfg.ollama_think if cfg.provider_mode == "local" else None,
        "temperature": 0.0,
        "dataset_seed": SEED,
        "max_llm_calls": cfg.budgets.max_llm_calls,
        "max_analysis_tasks": cfg.budgets.max_analysis_tasks,
        "max_tool_calls_per_task": cfg.budgets.max_tool_calls_per_task,
        "max_runtime_seconds": cfg.budgets.max_runtime_seconds,
    }
    with contextlib.suppress(Exception):
        info["git_sha"] = (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout.strip()
            or "unknown"
        )
    if cfg.provider_mode == "local":
        with contextlib.suppress(Exception):
            import httpx

            tags = httpx.get(f"{cfg.ollama_base_url}/api/tags", timeout=10).json()
            for entry in tags.get("models", []):
                if entry.get("name") == model:
                    details = entry.get("details", {})
                    info["model_digest"] = str(entry.get("digest", ""))[:16]
                    info["quantization"] = details.get("quantization_level")
                    info["parameter_size"] = details.get("parameter_size")
                    info["model_bytes"] = entry.get("size")
        with contextlib.suppress(Exception):
            info["ollama_version"] = (
                subprocess.run(
                    ["ollama", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                ).stdout.strip()
                or "unknown"
            )
    return info


def _atomic_write(path: Path, text: str) -> None:
    """Write completely or not at all.

    A sweep that dies mid-write must not leave a half-parsed checkpoint
    behind; the next run would refuse to resume from it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _append_outcome(path: Path, key: str, outcome: QuestionOutcome) -> None:
    """Persist one completed question immediately, durably.

    The first sweep wrote its report only at the end and lost nearly two
    hours of finished work when it had to be killed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps({"key": key, "outcome": asdict(outcome)}) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _load_checkpoint(path: Path) -> dict[str, QuestionOutcome]:
    """Completed outcomes from a previous run, keyed by question."""
    if not path.exists():
        return {}
    done: dict[str, QuestionOutcome] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        with contextlib.suppress(Exception):
            record = json.loads(line)
            done[record["key"]] = QuestionOutcome(**record["outcome"])
    return done


def _write_status(path: Path, status: dict[str, Any]) -> None:
    """A stalled run must be diagnosable while it is still running."""
    with contextlib.suppress(OSError):
        _atomic_write(path, json.dumps(status, indent=2, default=str))


async def run_real_model_evaluation(
    cfg: Settings,
    data_dir: Path,
    *,
    dataset_ids: list[str] | None = None,
    include_warehouse: bool = True,
    max_questions: int | None = None,
    question_timeout_seconds: float = 600.0,
    checkpoint_dir: Path | None = None,
    resume: bool = True,
    resume_incompatible_ok: bool = False,
) -> dict[str, Any]:
    """Evaluate a real provider across the datasets. Returns a report."""
    from agentic_analytics.evaluation.datasets import build_all

    built = build_all(data_dir)
    selected: list[EvalDataset] = [
        d for d in DATASETS if dataset_ids is None or d.dataset_id in dataset_ids
    ]
    fingerprint = environment_fingerprint(cfg)

    checkpoint_dir = checkpoint_dir or (data_dir.parent / "real-model-checkpoint")
    outcomes_path = checkpoint_dir / "outcomes.jsonl"
    status_path = checkpoint_dir / "status.json"
    meta_path = checkpoint_dir / "meta.json"

    done: dict[str, QuestionOutcome] = {}
    if resume and meta_path.exists():
        previous = json.loads(meta_path.read_text())
        differing = {
            k
            for k in ("model", "provider_mode", "git_sha", "harness_schema", "think")
            if previous.get("environment", {}).get(k) != fingerprint.get(k)
        }
        if differing and not resume_incompatible_ok:
            # Mixing runs from different models or builds silently would
            # produce a report about nothing in particular.
            log.warning("real_model_checkpoint_incompatible", differing=sorted(differing))
            raise RuntimeError(
                "the checkpoint was written by a different configuration "
                f"({', '.join(sorted(differing))}); pass --no-resume to start "
                "fresh or --resume-incompatible to continue anyway"
            )
        done = _load_checkpoint(outcomes_path)
        if done:
            log.info("real_model_resuming", completed=len(done))
    elif not resume:
        with contextlib.suppress(OSError):
            outcomes_path.unlink()

    _atomic_write(
        meta_path,
        json.dumps({"environment": fingerprint, "started_at": time.time()}, indent=2),
    )

    # Build the work list first, so it can be reported and resumed against.
    work: list[tuple[str, str, EvalQuestion, Any]] = []
    if include_warehouse and (dataset_ids is None or "warehouse" in dataset_ids):
        for index, question in enumerate(
            list(WAREHOUSE_QUESTIONS)[: max_questions or len(WAREHOUSE_QUESTIONS)]
        ):
            work.append(
                (
                    question_key("warehouse", index, question.text),
                    "warehouse",
                    question,
                    lambda: open_demo_session(cfg.demo_warehouse_dir),
                )
            )
    for dataset in selected:
        path = built[dataset.dataset_id]
        for index, question in enumerate(
            list(dataset.questions)[: max_questions or len(dataset.questions)]
        ):
            work.append(
                (
                    question_key(dataset.dataset_id, index, question.text),
                    dataset.dataset_id,
                    question,
                    lambda p=path, f=dataset.file_format: open_upload_session(
                        p, p.name, f, max_rows=cfg.budgets.max_upload_rows
                    ),
                )
            )

    outcomes: list[QuestionOutcome] = []
    started = time.monotonic()
    started_wall = time.time()

    for position, (key, dataset_id, question, factory) in enumerate(work, start=1):
        if key in done:
            outcomes.append(done[key])
            continue
        _write_status(
            status_path,
            {
                "environment": fingerprint,
                "evaluation_started_at": started_wall,
                "question_started_at": time.time(),
                "current_question": {"key": key, "dataset": dataset_id, "text": question.text},
                "completed": len(outcomes),
                "total": len(work),
                "last_completed": outcomes[-1].question if outcomes else None,
            },
        )
        log.info(
            "real_model_question",
            dataset=dataset_id,
            position=f"{position}/{len(work)}",
            question=question.text[:60],
        )
        outcome = await evaluate_question(
            question,
            dataset_id,
            factory,
            cfg,
            question_timeout_seconds=question_timeout_seconds,
        )
        _append_outcome(outcomes_path, key, outcome)
        outcomes.append(outcome)

    _write_status(
        status_path,
        {
            "environment": fingerprint,
            "evaluation_started_at": started_wall,
            "completed": len(outcomes),
            "total": len(work),
            "finished": True,
        },
    )
    report = _summarise(cfg, outcomes, round(time.monotonic() - started, 1))
    report["environment"] = fingerprint
    report["question_timeout_seconds"] = question_timeout_seconds
    report["checkpoint_dir"] = str(checkpoint_dir)
    return report


def _summarise(cfg: Settings, outcomes: list[QuestionOutcome], wall_clock: float) -> dict[str, Any]:
    """Aggregate, without inventing a score."""
    total = len(outcomes)

    def count(predicate: Any) -> int:
        return sum(1 for o in outcomes if predicate(o))

    by_kind: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        bucket = by_kind.setdefault(
            outcome.kind, {"asked": 0, "completed": 0, "published_any": 0, "crashed": 0}
        )
        bucket["asked"] += 1
        bucket["completed"] += int(outcome.completed)
        bucket["published_any"] += int(outcome.published_findings > 0)
        bucket["crashed"] += int(bool(outcome.error))

    withheld_rules: dict[str, int] = {}
    for outcome in outcomes:
        for rule, n in outcome.withheld_rules.items():
            withheld_rules[rule] = withheld_rules.get(rule, 0) + n

    tools: dict[str, int] = {}
    for outcome in outcomes:
        for tool in outcome.tools_selected:
            tools[tool] = tools.get(tool, 0) + 1

    return {
        "evaluation_kind": "real-model behavioural evaluation",
        "is_deterministic": False,
        "note": (
            "Opt-in, not run in CI, and not a pass/fail benchmark. It records "
            "whether a real model can operate this architecture: parseable "
            "structured output, executable plans, tools that exist, and "
            "whether verification catches what the model got wrong. A "
            "withheld finding is the pipeline working, not a failure."
        ),
        "provider_mode": cfg.provider_mode,
        "model": (cfg.ollama_model if cfg.provider_mode == "local" else cfg.cloud_model),
        "questions_asked": total,
        "runs_completed": count(lambda o: o.completed),
        "runs_stopped_early": count(lambda o: not o.completed and not o.error),
        "runs_crashed": count(lambda o: bool(o.error)),
        "runs_publishing_at_least_one_finding": count(lambda o: o.published_findings > 0),
        # Split by stage, because "the schema rejected it" and "the request
        # timed out" say opposite things about a model and were previously
        # the same number.
        "runs_with_a_schema_validation_failure": count(lambda o: o.schema_validation_failures > 0),
        "runs_with_a_json_parse_failure": count(lambda o: o.json_parse_failures > 0),
        "runs_with_a_transport_failure": count(lambda o: o.transport_failures > 0),
        "runs_with_a_provider_timeout": count(lambda o: o.timeout_failures > 0),
        "runs_hitting_the_question_timeout": count(lambda o: o.question_timeout),
        "failures_by_stage": _merge_counts(o.failures_by_stage for o in outcomes),
        # Engine intervention, so a rescued run is not read as a planning
        # success. This is the distinction the evaluation exists to make.
        "runs_with_direct_model_plan": count(lambda o: o.model_plan_directly_executable),
        "runs_requiring_task_redirect": count(lambda o: o.tasks_redirected_by_engine > 0),
        "runs_requiring_engine_fallback": count(lambda o: o.fallback_plan_used),
        "runs_safely_refusing": count(lambda o: "safe_refusal" in o.outcome_flags),
        "outcome_flag_counts": _merge_counts(dict.fromkeys(o.outcome_flags, 1) for o in outcomes),
        "runs_with_a_failed_tool_call": count(lambda o: o.tool_call_failures > 0),
        # Three different things, previously one. An agent call is an
        # ask-and-validate; an attempt is a request that left the process
        # whether or not it came back; a success is one that answered.
        # Cloud cost follows attempts.
        "total_structured_agent_calls": sum(o.structured_agent_calls for o in outcomes),
        "total_provider_request_attempts": sum(o.provider_request_attempts for o in outcomes),
        "total_provider_successful_responses": sum(
            o.provider_successful_responses for o in outcomes
        ),
        "total_input_tokens": sum(o.input_tokens for o in outcomes),
        "total_output_tokens": sum(o.output_tokens for o in outcomes),
        "total_candidate_findings": sum(o.candidate_findings for o in outcomes),
        "total_published_findings": sum(o.published_findings for o in outcomes),
        "total_withheld_findings": sum(o.withheld_findings for o in outcomes),
        "withheld_by_rule": dict(sorted(withheld_rules.items())),
        "tools_selected": dict(sorted(tools.items(), key=lambda kv: -kv[1])),
        "generated_sql_calls": sum(o.generated_sql_calls for o in outcomes),
        "statistical_tests_run": sum(o.statistical_tests for o in outcomes),
        "by_question_kind": by_kind,
        "median_runtime_seconds": _median([o.runtime_seconds for o in outcomes]),
        "wall_clock_seconds": wall_clock,
        "outcomes": [asdict(o) for o in outcomes],
    }


def _merge_counts(dicts: Any) -> dict[str, int]:
    merged: dict[str, int] = {}
    for item in dicts:
        for key, value in item.items():
            merged[key] = merged.get(key, 0) + int(value)
    return dict(sorted(merged.items()))


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 2)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 2)


def write_report(report: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str))
    return path
