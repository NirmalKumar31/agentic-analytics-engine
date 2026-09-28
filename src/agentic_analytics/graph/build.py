"""The bounded analysis workflow.

START -> dataset_context -> analyze_question -> plan_analysis
      -> dispatch (Send, fan-out) -> analysis_worker (parallel)
      -> aggregate_results -> critique_findings
      -> [at most one follow-up round] -> build_visualizations
      -> write_report -> verify_publication -> finalize -> END

There is exactly one conditional loop edge, and it is guarded by a counter
that the graph state carries, so the workflow terminates by construction
rather than by hoping a model says it is done.
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agentic_analytics.agents import analyst, critic, reporter, visualizer
from agentic_analytics.agents.execution import (
    ExecutionContract,
    build_execution_contract,
    select_tools,
)
from agentic_analytics.agents.schemas import (
    AnalysisPlan,
    AnalysisTask,
    PublishedFinding,
    TaskOutcome,
    Verdict,
)
from agentic_analytics.agents.scope import dataset_vocabulary
from agentic_analytics.agents.worker import run_task
from agentic_analytics.analytics.corrections import apply_family_correction
from agentic_analytics.analytics.results import ResultSnapshot, StatisticalResult
from agentic_analytics.config import Budgets
from agentic_analytics.events import EventBus, EventType
from agentic_analytics.graph.state import AnalysisState, WorkerInput
from agentic_analytics.llm.base import BudgetError, LLMError, LLMProvider
from agentic_analytics.logging import get_logger
from agentic_analytics.mcp_layer.client import AnalyticsToolset
from agentic_analytics.verification.claims import collapse_exact_duplicates
from agentic_analytics.verification.limitations import rejection_limitations
from agentic_analytics.verification.period import dataset_dimensions
from agentic_analytics.warehouse.metrics import VALID_GRAINS
from agentic_analytics.warehouse.session import AnalysisSession

log = get_logger(__name__)


class RunContext:
    """Everything a node needs that is not graph state.

    Held outside the state because none of it is serialisable and none of it
    should be checkpointed: a live MCP connection, a provider, an event bus.
    """

    def __init__(
        self,
        session: AnalysisSession,
        toolset: AnalyticsToolset,
        provider: LLMProvider,
        events: EventBus,
        budgets: Budgets,
        telemetry: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.toolset = toolset
        self.provider = provider
        self.events = events
        self.budgets = budgets
        #: Optional dict the real-model evaluation passes in to record where
        #: the engine intervened. `None` in production, and every write is
        #: guarded, so a normal run does none of this work.
        self.telemetry = telemetry
        self.started_at = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def out_of_time(self) -> bool:
        return self.elapsed > self.budgets.max_runtime_seconds

    def results(self) -> dict[str, ResultSnapshot]:
        """The snapshots this run may read.

        Scoped to the run's own toolset, not to the session. The session
        store is shared by every run on the connection, so reading it whole
        would let one half of a comparison cite the other half's numbers --
        and would hand a cloud run the cells a local run was allowed to
        compute. A dropped id is skipped: the store is bounded.
        """
        store = self.session.results
        return {rid: store.get(rid) for rid in self.toolset.result_ids if store.has(rid)}


def build_graph(ctx: RunContext) -> Any:
    """Compile the workflow with its context bound into the nodes."""
    graph: StateGraph[AnalysisState, None, AnalysisState, AnalysisState] = StateGraph(AnalysisState)

    # ------------------------------------------------------ dataset_context
    async def dataset_context(state: AnalysisState) -> dict[str, Any]:
        catalog = ctx.session.catalog()
        metrics = ctx.session.registry.describe_all() if ctx.session.registry else []
        models = sorted(ctx.session.registry.models) if ctx.session.registry else []
        ctx.events.emit(
            EventType.DATASET_LOADED,
            dataset_kind=catalog["dataset_kind"],
            source=catalog["source"],
            dataset_fingerprint=catalog["dataset_fingerprint"],
            tables=[{"name": t["name"], "row_count": t["row_count"]} for t in catalog["tables"]],
            metrics=[m["name"] for m in metrics],
        )
        return {
            "dataset_catalog": catalog,
            "metric_catalog": metrics,
            "model_names": models,
            "followup_rounds": 0,
        }

    # ----------------------------------------------------- analyze_question
    async def analyze_question(state: AnalysisState) -> dict[str, Any]:
        try:
            analysis = await analyst.analyze_question(
                ctx.provider,
                state["question"],
                state["dataset_catalog"],
                state["metric_catalog"],
            )
        except (LLMError, BudgetError) as exc:
            return _abort("the question could not be analysed", exc, ctx)

        ctx.events.emit(
            EventType.QUESTION_ANALYZED,
            intent=analysis.intent,
            analysis_type=analysis.analysis_type,
            target_metrics=analysis.target_metrics,
            dimensions=analysis.dimensions,
            time_scope=analysis.time_scope,
            ambiguities=analysis.ambiguities,
        )
        limitations = [f"Ambiguity: {a}" for a in analysis.ambiguities]
        return {"analysis": analysis, "limitations": limitations}

    # -------------------------------------------------------- plan_analysis
    async def plan_analysis(state: AnalysisState) -> dict[str, Any]:
        if state.get("stopped_reason"):
            return {}
        try:
            tasks = await analyst.plan_analysis(
                ctx.provider,
                state["question"],
                state["analysis"],
                state["metric_catalog"],
                state.get("model_names", []),
                max_tasks=ctx.budgets.max_analysis_tasks,
                tables=state["dataset_catalog"].get("tables", []),
                telemetry=ctx.telemetry,
            )
        except (LLMError, BudgetError) as exc:
            return _abort("no analysis plan could be produced", exc, ctx)

        if not tasks:
            return _abort("the planner produced no executable task for this dataset", None, ctx)

        ctx.events.emit(
            EventType.PLAN_GENERATED,
            task_count=len(tasks),
            tasks=[
                {
                    "task_id": t.task_id,
                    "objective": t.objective,
                    "analysis_type": t.analysis_type,
                    "preferred_tool": t.preferred_tool,
                    "metrics": t.required_metrics,
                    "dimensions": t.dimensions,
                    "priority": t.priority,
                }
                for t in tasks
            ],
        )
        return {"plan": AnalysisPlan(tasks=tasks), "pending_tasks": tasks}

    # ------------------------------------------------------------- dispatch
    def dispatch(state: AnalysisState) -> Any:
        """Fan out one Send per pending task, or skip straight to aggregation."""
        if state.get("stopped_reason"):
            return "aggregate_results"
        tasks = state.get("pending_tasks") or []
        if not tasks:
            return "aggregate_results"
        return [Send("analysis_worker", {"task": t}) for t in tasks]

    # ------------------------------------------------------ analysis_worker
    async def analysis_worker(state: WorkerInput) -> dict[str, Any]:
        """Runs once per Send, concurrently with its siblings."""
        task: AnalysisTask = state["task"]
        if ctx.out_of_time():
            return {
                "task_outcomes": [
                    TaskOutcome(
                        task_id=task.task_id,
                        status="failed",
                        error="the run exceeded its time budget before this task started",
                    )
                ],
            }
        outcome = await run_task(
            task,
            provider=ctx.provider,
            toolset=ctx.toolset,
            events=ctx.events,
            max_tool_calls=ctx.budgets.max_tool_calls_per_task,
            tables=sorted(ctx.session.table_names),
            has_metrics=ctx.session.has_metrics,
            contract=_execution_contract(ctx, task),
            out_of_time=ctx.out_of_time,
        )
        return {"task_outcomes": [outcome]}

    def _execution_contract(ctx: RunContext, task: AnalysisTask) -> ExecutionContract:
        """What this worker may name, from what the session already knows.

        Assembled per task rather than per run so the tool schemas shown can
        be narrowed to the ones this task could plausibly use. Everything in
        it is read from the session catalog, the metric registry and the MCP
        listing; none of it is written down a second time here.
        """
        catalog = ctx.session.catalog()
        wanted = select_tools(
            ctx.toolset.available_tools,
            task,
            has_metrics=ctx.session.has_metrics,
        )
        return build_execution_contract(
            catalog=catalog,
            registry=ctx.session.registry,
            tool_contracts=[c for c in ctx.toolset.tool_contracts if c.name in wanted],
            grains=sorted(VALID_GRAINS),
            available_tools=list(ctx.toolset.available_tools),
            task=task,
        )

    # ----------------------------------------------------- aggregate_results
    async def aggregate_results(state: AnalysisState) -> dict[str, Any]:
        outcomes = state.get("task_outcomes", [])
        failed = [o for o in outcomes if o.status == "failed"]
        warned = [o for o in outcomes if o.status == "warning"]
        limitations: list[str] = []
        for outcome in failed:
            limitations.append(
                f"Analysis task {outcome.task_id} failed and contributed no evidence"
                + (f": {outcome.error}" if outcome.error else ".")
            )
        for outcome in warned:
            for note in outcome.notes[:1]:
                limitations.append(f"Analysis task {outcome.task_id} was degraded: {note}")

        succeeded = [o for o in outcomes if o.status != "failed" and o.findings]
        if outcomes and not succeeded:
            return {
                "limitations": limitations,
                "stopped_reason": "no analysis task produced a usable result",
            }
        return {"limitations": limitations}

    # ---------------------------------------------------- critique_findings
    async def critique_findings(state: AnalysisState) -> dict[str, Any]:
        results = ctx.results()
        # Apply the same family correction to the stored snapshots that the
        # workers applied to their payloads, so the numbers a reader sees in
        # the provenance drawer match the ones the findings were written
        # from. Holm is deterministic, so correcting the same family twice
        # produces the same values.
        by_task: dict[str | None, list[StatisticalResult]] = {}
        for snapshot in results.values():
            if snapshot.statistical_result is not None:
                by_task.setdefault(snapshot.task_id, []).append(snapshot.statistical_result)
        for family in by_task.values():
            apply_family_correction(family)
        rejected: list[Verdict] = []
        verdicts: list[Verdict] = []
        supported: list[PublishedFinding] = []
        # The objective is the sub-question a task was given, and a finding
        # that answers it answers part of the question even when it does not
        # restate the whole one.
        plan = state.get("plan")
        objectives = {task.task_id: task.objective for task in (plan.tasks if plan else [])}
        # The brief is the question decomposed into metrics and dimensions,
        # which is a sturdier statement of "what was asked" than the
        # question's wording: "shipping delays" and `late_delivery_rate`
        # share no word at all.
        brief = state.get("analysis")
        brief_metrics = list(brief.target_metrics) if brief else []
        brief_dimensions = list(brief.dimensions) if brief else []
        brief_time_scope = (brief.time_scope or "") if brief else ""
        # Derived from this dataset, not from the demo warehouse's registry.
        dataset_dims = sorted(
            dataset_dimensions(
                ctx.session.catalog(),
                ctx.session.registry.describe_all() if ctx.session.registry else [],
            )
        )

        for outcome in state.get("task_outcomes", []):
            for finding in outcome.findings:
                if ctx.out_of_time():
                    # Verification costs a model call per claim, and that
                    # tail is how a run overruns its budget after the tool
                    # loop has already stopped. A claim that cannot be
                    # checked is withheld -- never waved through, which is
                    # the one outcome that would make the budget matter
                    # more than the invariant.
                    verdict = Verdict(
                        finding_id=finding.finding_id,
                        status="unsupported",
                        reason=(
                            "The run reached its time budget before this claim "
                            "could be verified, so it was not published."
                        ),
                        rule="verification_budget_exhausted",
                    )
                    verdicts.append(verdict)
                    rejected.append(verdict)
                    continue
                try:
                    verdict, _ = await critic.verify_finding(
                        finding,
                        results,
                        ctx.provider,
                        ctx.events,
                        question=state.get("question", ""),
                        objective=objectives.get(finding.task_id or "", ""),
                        target_metrics=brief_metrics,
                        target_dimensions=brief_dimensions,
                        time_scope=brief_time_scope,
                        available_dimensions=dataset_dims,
                    )
                except (LLMError, BudgetError) as exc:
                    verdict = Verdict(
                        finding_id=finding.finding_id,
                        status="partially_supported",
                        reason=f"Verification could not be completed ({exc}).",
                    )
                verdicts.append(verdict)
                # Only `supported` is published. `partially_supported` is
                # retained for the audit metrics but kept out of the report.
                if verdict.status == "supported":
                    supported.append(critic.publish(finding, verdict))
                else:
                    rejected.append(verdict)

        # Exact duplicates, collapsed after verification rather than before.
        # A real run published "The product family 'Home' has the highest
        # total net value of 40189.25." twice, from two tasks that had found
        # it independently. Both were true and both passed every gate;
        # printing the same sentence twice still inflates the finding count,
        # the report length and the apparent breadth of the analysis.
        #
        # After the verdicts, because letting an unverified candidate decide
        # which verified one survives would put the collapse upstream of the
        # thing that makes publication safe.
        published, duplicate_records = collapse_exact_duplicates(
            supported,
            text_of=lambda f: f.text,
            id_of=lambda f: f.finding_id,
            task_of=lambda f: f.task_id or "",
            results_of=lambda f: list(f.result_ids),
        )
        duplicates = [d.as_dict() for d in duplicate_records]
        for record in duplicate_records:
            ctx.events.emit(
                EventType.FINDING_REJECTED,
                finding_id=record.finding_id,
                reason="an identical finding was already published",
                duplicate_of=record.duplicate_of,
            )

        if duplicates and ctx.telemetry is not None:
            ctx.telemetry["duplicate_published_findings_removed"] = len(duplicates)
            ctx.telemetry["duplicate_published_findings"] = duplicates

        # One sentence per reason, not one sentence for every rejection.
        # A single blanket line told a reader the engine had found a data
        # problem when it had found a relevance problem, or none at all.
        limitations: list[str] = rejection_limitations(v.rule for v in rejected)
        if duplicates:
            limitations.extend(rejection_limitations(["duplicate_finding"] * len(duplicates)))
        return {
            "verdicts": verdicts,
            "published": published,
            "rejected": rejected,
            "limitations": limitations,
        }

    # -------------------------------------------------------- follow-up gate
    def needs_followup(state: AnalysisState) -> str:
        """At most one extra round, and only when it can still help."""
        if state.get("stopped_reason"):
            return "build_visualizations"
        if state.get("followup_rounds", 0) >= ctx.budgets.max_followup_rounds:
            return "build_visualizations"
        if ctx.out_of_time():
            return "build_visualizations"
        if ctx.toolset.budget.total_used >= ctx.toolset.budget.max_total:
            return "build_visualizations"
        # A follow-up is worth a round only when nothing survived but some
        # task did return results to work from.
        published = state.get("published", [])
        outcomes = state.get("task_outcomes", [])
        if published:
            return "build_visualizations"
        if any(o.result_ids for o in outcomes):
            return "followup_round"
        return "build_visualizations"

    async def followup_round(state: AnalysisState) -> dict[str, Any]:
        """One narrower retry, using the simplest tool that can succeed."""
        rounds = state.get("followup_rounds", 0) + 1
        ctx.events.emit(
            EventType.FOLLOWUP_ROUND_STARTED,
            round=rounds,
            reason="no finding survived verification in the first round",
        )
        analysis = state.get("analysis")
        metrics = list(analysis.target_metrics) if analysis else []
        if not metrics and state.get("metric_catalog"):
            metrics = [state["metric_catalog"][0]["name"]]
        tasks = (
            [
                AnalysisTask(
                    task_id=f"task_followup_{rounds}",
                    objective=f"Recompute {metrics[0]} directly as a single value",
                    analysis_type="profiling",
                    required_metrics=metrics[:1],
                    preferred_tool="compute_metric",
                    priority=1,
                )
            ]
            if metrics
            else []
        )
        return {"followup_rounds": rounds, "pending_tasks": tasks}

    # --------------------------------------------------- build_visualizations
    async def build_visualizations(state: AnalysisState) -> dict[str, Any]:
        published = state.get("published", [])
        if not published:
            return {"charts": []}
        if ctx.out_of_time():
            # A chart is decoration. Spending a model call on one after the
            # analytical deadline is the clearest case of presentation work
            # competing with the budget, and nothing is lost by skipping it.
            return {
                "charts": [],
                "limitations": ["Charts were skipped: the run reached its time budget."],
            }
        try:
            charts = await visualizer.build_charts(
                published,
                ctx.results(),
                ctx.provider,
                max_chart_rows=ctx.budgets.max_chart_rows,
                events=ctx.events,
            )
        except (LLMError, BudgetError) as exc:
            return {
                "charts": [],
                "limitations": [f"Charts were not produced: {exc}"],
            }
        return {"charts": charts}

    # ---------------------------------------------------------- write_report
    async def write_report(state: AnalysisState) -> dict[str, Any]:
        ctx.events.emit(EventType.REPORT_STARTED, finding_count=len(state.get("published", [])))
        limitations = list(dict.fromkeys(state.get("limitations", [])))
        if state.get("stopped_reason"):
            limitations.append(f"The run stopped early: {state['stopped_reason']}.")
        if ctx.out_of_time():
            # Organising is the only thing the model does here, so a run
            # that is out of time still gets its report -- written by the
            # engine from the findings that passed verification. Losing
            # verified findings because the *presentation* step had no
            # budget left would be the wrong trade in both directions: it
            # discards real work and reports a failure that did not happen.
            report = reporter.assemble_without_model(
                state["question"],
                state.get("published", []),
                [*limitations, "The run reached its time budget before the report was organised."],
            )
            return {"report": report}
        try:
            report = await reporter.write_report(
                state["question"],
                state.get("published", []),
                ctx.results(),
                limitations,
                ctx.provider,
                dataset_vocabulary(
                    ctx.session.catalog(),
                    ctx.session.registry.describe_all() if ctx.session.registry else [],
                ),
            )
        except LLMError as exc:
            # Includes `BudgetError`. Same reasoning as above: fall back to
            # the deterministic organisation rather than to a stub.
            report = reporter.assemble_without_model(
                state["question"],
                state.get("published", []),
                [*limitations, str(exc)],
            )
        return {"report": report}

    # ---------------------------------------------------- verify_publication
    async def verify_publication(state: AnalysisState) -> dict[str, Any]:
        """Last gate: nothing unverified may be referenced by the report."""
        report = state.get("report")
        published = {f.finding_id for f in state.get("published", [])}
        if report is None:
            return {}
        dropped = 0
        for section in report.sections:
            before = len(section.finding_ids)
            section.finding_ids = [f for f in section.finding_ids if f in published]
            dropped += before - len(section.finding_ids)
        charts = [
            c
            for c in state.get("charts", [])
            if all(f in published for f in c.finding_ids) and c.finding_ids
        ]
        extra: list[str] = []
        if dropped:
            extra.append(f"{dropped} report reference(s) to unverified findings were removed.")
        return {"report": report, "charts": charts, "limitations": extra}

    # --------------------------------------------------------------- finalize
    async def finalize(state: AnalysisState) -> dict[str, Any]:
        report = state.get("report")
        ctx.events.emit(
            EventType.REPORT_COMPLETED,
            finding_count=len(state.get("published", [])),
            rejected_count=len(state.get("rejected", [])),
            chart_count=len(state.get("charts", [])),
            sections=[s.heading for s in (report.sections if report else [])],
        )
        return {}

    graph.add_node("dataset_context", dataset_context)
    graph.add_node("analyze_question", analyze_question)
    graph.add_node("plan_analysis", plan_analysis)
    graph.add_node("analysis_worker", analysis_worker, input_schema=WorkerInput)
    graph.add_node("aggregate_results", aggregate_results)
    graph.add_node("critique_findings", critique_findings)
    graph.add_node("followup_round", followup_round)
    graph.add_node("build_visualizations", build_visualizations)
    graph.add_node("write_report", write_report)
    graph.add_node("verify_publication", verify_publication)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "dataset_context")
    graph.add_edge("dataset_context", "analyze_question")
    graph.add_edge("analyze_question", "plan_analysis")
    graph.add_conditional_edges("plan_analysis", dispatch, ["analysis_worker", "aggregate_results"])
    graph.add_edge("analysis_worker", "aggregate_results")
    graph.add_edge("aggregate_results", "critique_findings")
    graph.add_conditional_edges(
        "critique_findings", needs_followup, ["followup_round", "build_visualizations"]
    )
    # The single loop edge. `followup_round` increments a counter that
    # `needs_followup` checks, so this can be taken at most once.
    graph.add_conditional_edges(
        "followup_round", dispatch, ["analysis_worker", "aggregate_results"]
    )
    graph.add_edge("build_visualizations", "write_report")
    graph.add_edge("write_report", "verify_publication")
    graph.add_edge("verify_publication", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(name="agentic-analytics")


def _abort(reason: str, exc: BaseException | None, ctx: RunContext) -> dict[str, Any]:
    """Stop the run cleanly, with a reason a user can read."""
    detail = f"{reason}: {exc}" if exc else reason
    log.warning("run_aborted", reason=reason, error=str(exc) if exc else None)
    ctx.events.emit(EventType.RUN_FAILED, reason=detail)
    return {"stopped_reason": reason, "errors": [detail], "limitations": [detail]}
