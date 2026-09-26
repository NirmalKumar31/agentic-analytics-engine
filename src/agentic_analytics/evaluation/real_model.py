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

import json
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agentic_analytics.config import Settings
from agentic_analytics.evaluation.datasets import (
    DATASETS,
    WAREHOUSE_QUESTIONS,
    EvalDataset,
    EvalQuestion,
)
from agentic_analytics.graph.runner import RunResult, run_analysis
from agentic_analytics.llm.base import LLMProvider, LLMRequest
from agentic_analytics.llm.registry import build_provider
from agentic_analytics.logging import get_logger
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.warehouse.session import (
    SessionManager,
    open_demo_session,
    open_upload_session,
)

log = get_logger(__name__)


class RecordingProvider(LLMProvider):
    """Wraps a real provider and records every exchange's *shape*.

    Only shapes: the role, whether the reply parsed, how long it took, and
    token counts. Prompt and response bodies are not stored -- the report is
    an artifact that may be committed or shared, and it should not carry a
    dataset's contents.
    """

    requires_credentials = False

    def __init__(self, inner: LLMProvider) -> None:
        super().__init__(max_calls=inner.max_calls)
        self.inner = inner
        self.name = inner.name
        self.exchanges: list[dict[str, Any]] = []

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        started = time.monotonic()
        record: dict[str, Any] = {"role": request.role, "ok": False}
        try:
            payload = await self.inner.complete_json(request)
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            record["error"] = str(exc)[:200]
            record["seconds"] = round(time.monotonic() - started, 2)
            self.exchanges.append(record)
            raise
        record["ok"] = True
        record["seconds"] = round(time.monotonic() - started, 2)
        record["keys"] = sorted(payload)[:12]
        self.exchanges.append(record)
        return payload

    @property
    def usage(self) -> Any:
        return self.inner.usage

    @usage.setter
    def usage(self, value: Any) -> None:
        # `LLMProvider.__init__` assigns this; the inner provider owns the
        # real counter, so the assignment on the wrapper is discarded.
        return

    async def aclose(self) -> None:
        await self.inner.aclose()


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

    # Where a run broke down, by role.
    roles_called: dict[str, int] = field(default_factory=dict)
    roles_failed: dict[str, int] = field(default_factory=dict)
    provider_format_failures: int = 0

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

    provider_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    runtime_seconds: float = 0.0


def _observe(
    outcome: QuestionOutcome, result: RunResult, provider: RecordingProvider
) -> QuestionOutcome:
    """Fill an outcome from a completed run."""
    outcome.stopped_reason = result.stopped_reason
    outcome.completed = not result.stopped_reason

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

    report = result.report
    outcome.report_written = report is not None and bool(report.key_findings)
    outcome.report_sections = len(report.sections) if report else 0

    for exchange in provider.exchanges:
        role = exchange["role"]
        outcome.roles_called[role] = outcome.roles_called.get(role, 0) + 1
        if not exchange["ok"]:
            outcome.roles_failed[role] = outcome.roles_failed.get(role, 0) + 1
            # A reply the schema could not accept is the failure mode that
            # matters most: it means the model cannot hold the contract.
            if exchange.get("error_type") in {"LLMError", "ValidationError"}:
                outcome.provider_format_failures += 1
    outcome.provider_calls = len(provider.exchanges)
    outcome.input_tokens = int(getattr(provider.usage, "input_tokens", 0))
    outcome.output_tokens = int(getattr(provider.usage, "output_tokens", 0))
    return outcome


async def evaluate_question(
    question: EvalQuestion,
    dataset_id: str,
    session_factory: Any,
    cfg: Settings,
) -> QuestionOutcome:
    """Run one question and record what happened. Never raises."""
    outcome = QuestionOutcome(
        dataset=dataset_id,
        question=question.text,
        kind=question.kind,
        expectation=question.expectation,
    )
    manager = SessionManager(max_sessions=4)
    provider = RecordingProvider(build_provider(cfg))
    started = time.monotonic()
    try:
        session = manager.add(session_factory())
        server = build_server(manager, cfg)
        result = await run_analysis(question.text, session, server, settings=cfg, provider=provider)
        _observe(outcome, result, provider)
    except Exception as exc:
        # A crash is a result too, and the point of the exercise is to find
        # them, so it is recorded rather than propagated.
        outcome.error = f"{type(exc).__name__}: {exc}"[:300]
        log.warning("real_model_run_crashed", dataset=dataset_id, error=outcome.error)
        log.debug("real_model_traceback", trace=traceback.format_exc()[:2000])
        outcome.provider_calls = len(provider.exchanges)
        for exchange in provider.exchanges:
            role = exchange["role"]
            outcome.roles_called[role] = outcome.roles_called.get(role, 0) + 1
            if not exchange["ok"]:
                outcome.roles_failed[role] = outcome.roles_failed.get(role, 0) + 1
                outcome.provider_format_failures += 1
    finally:
        outcome.runtime_seconds = round(time.monotonic() - started, 2)
        await provider.aclose()
        manager.close_all()
    return outcome


async def run_real_model_evaluation(
    cfg: Settings,
    data_dir: Path,
    *,
    dataset_ids: list[str] | None = None,
    include_warehouse: bool = True,
    max_questions: int | None = None,
) -> dict[str, Any]:
    """Evaluate a real provider across the datasets. Returns a report."""
    from agentic_analytics.evaluation.datasets import build_all

    built = build_all(data_dir)
    selected: list[EvalDataset] = [
        d for d in DATASETS if dataset_ids is None or d.dataset_id in dataset_ids
    ]

    outcomes: list[QuestionOutcome] = []
    started = time.monotonic()

    if include_warehouse and (dataset_ids is None or "warehouse" in dataset_ids):
        questions = list(WAREHOUSE_QUESTIONS)[: max_questions or len(WAREHOUSE_QUESTIONS)]
        for question in questions:
            log.info("real_model_question", dataset="warehouse", question=question.text[:60])
            outcomes.append(
                await evaluate_question(
                    question,
                    "warehouse",
                    lambda: open_demo_session(cfg.demo_warehouse_dir),
                    cfg,
                )
            )

    for dataset in selected:
        path = built[dataset.dataset_id]
        questions = list(dataset.questions)[: max_questions or len(dataset.questions)]
        for question in questions:
            log.info("real_model_question", dataset=dataset.dataset_id, question=question.text[:60])
            outcomes.append(
                await evaluate_question(
                    question,
                    dataset.dataset_id,
                    lambda p=path, f=dataset.file_format: open_upload_session(
                        p, p.name, f, max_rows=cfg.budgets.max_upload_rows
                    ),
                    cfg,
                )
            )

    return _summarise(cfg, outcomes, round(time.monotonic() - started, 1))


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
        "runs_with_a_provider_format_failure": count(lambda o: o.provider_format_failures > 0),
        "runs_with_a_failed_tool_call": count(lambda o: o.tool_call_failures > 0),
        "total_provider_calls": sum(o.provider_calls for o in outcomes),
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
