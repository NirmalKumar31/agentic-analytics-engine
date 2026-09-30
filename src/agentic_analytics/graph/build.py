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


def _canonical_for(mapping: Any, results: dict[str, Any], already: list[Any]) -> Any:
    """The engine's direct answer, when there is one to give.

    Composed from the executed result, so its figures carry the precision
    the result carries. A model restating the same figure is removed rather
    than preferred; see the note at the return below.
    """
    del already  # kept for callers; the duplicate is removed downstream
    if mapping is None or not getattr(mapping, "confident", False):
        return None
    from agentic_analytics.verification.canonical import canonical_answer

    canonical: Any = getattr(mapping, "canonical_dict", lambda: {})()
    metric_contract = isinstance(canonical, dict) and canonical.get("kind") == "metric_registry"
    for snapshot in reversed(list(results.values())):
        expected_tool = "compute_metric" if metric_contract else "aggregate_for_question"
        if snapshot.tool_name != expected_tool:
            continue
        finding = canonical_answer(mapping, snapshot)
        if finding is None:
            continue
        # A model summary of one or more rows is not equivalent to the
        # registry contract's complete answer.  It may cite the same cells
        # (for example, only the largest region), so evidence overlap cannot
        # suppress the canonical grouped result.
        # The engine's own answer wins, whichever shape it is.
        #
        # This used to bail out for a scalar when a published claim already
        # cited the same cell, so the report would not say one number in
        # two voices. The goal was right and the choice of voice was
        # backwards: the model's sentence said "165,414,408" where the
        # engine's said "165,414,407.58 across 518 rows", both citing the
        # same cell, and numeric verification accepts a rounded figure. The
        # rounding was the only difference, and it is the published total a
        # reader takes away. The duplicate is removed below instead.
        return finding
    return None


def _verify_without_a_model(finding: Any, results: dict[str, Any], mapping: Any) -> Verdict:
    """Deterministic gates only, for a claim the engine wrote itself.

    Every gate a model-proposed claim faces except the model: the cells
    must resolve, the arithmetic must check, and it must answer the
    resolved intent. Asking a model to approve the engine's own arithmetic
    would reintroduce exactly the failure this exists to prevent.
    """
    from agentic_analytics.agents.critic import _resolve_cells
    from agentic_analytics.verification.coverage import check_answer_coverage
    from agentic_analytics.verification.intent import check_intent
    from agentic_analytics.verification.numeric import verify_numbers

    cells = _resolve_cells(finding, results)
    cited = [results[r] for r in finding.result_ids if r in results]
    numeric = verify_numbers(finding.text, finding.claimed_change, cells, cited)
    if not numeric.ok:
        return Verdict(
            finding_id=finding.finding_id,
            status="unsupported",
            reason=numeric.reason,
            rule="numeric_mismatch",
            numeric_check=numeric.as_dict(),
        )
    coverage = check_answer_coverage(mapping, cited)
    if coverage.applicable and not coverage.complete:
        return Verdict(
            finding_id=finding.finding_id,
            status="unsupported",
            reason=coverage.reason,
            rule=coverage.rule,
            numeric_check=numeric.as_dict() | {"answer_coverage": coverage.as_dict()},
            evidence_supported=True,
            answers_question=False,
            relevance_reason=coverage.reason,
        )
    intent = check_intent(finding.text, mapping)
    if intent.applicable and not intent.answers:
        return Verdict(
            finding_id=finding.finding_id,
            status="unsupported",
            reason=intent.reason,
            rule="irrelevant_to_question",
            numeric_check=numeric.as_dict(),
        )
    return Verdict(
        finding_id=finding.finding_id,
        status="supported",
        reason=(
            "Composed by the engine from the executed aggregate and its result "
            "lineage, and checked against the cells it cites."
        ),
        rule="engine_canonical",
        numeric_check=numeric.as_dict(),
        evidence_supported=True,
        answers_question=True,
    )


def _resolve_intent(ctx: Any, question: str) -> Any:
    """The question mapped onto an uploaded table, or `None`.

    Only for a single uploaded table: the governed warehouse answers
    through its metric registry, where the brief already carries the
    target metrics and this adds nothing. Any failure here returns `None`
    and the intent check abstains -- a verification must not fall over
    because a question could not be parsed.
    """
    if not question:
        return None
    try:
        from agentic_analytics.analytics import upload_plan
        from agentic_analytics.analytics.semantic import infer_schema

        if ctx.session.registry is not None:
            return None
        tables = list(ctx.session.table_names)
        if len(tables) != 1:
            return None
        schema = infer_schema(ctx.session, tables[0])
        return upload_plan.resolve_question(question, schema.as_dict())
    except Exception:  # pragma: no cover - never break verification
        return None


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
        query_mapping: Any = None
        if ctx.session.registry is None and len(ctx.session.table_names) == 1:
            from agentic_analytics.analytics.semantic import infer_schema

            table = next(iter(ctx.session.table_names))
            schema = infer_schema(ctx.session, table).as_dict()
            try:
                query_mapping = await analyst.resolve_upload_query(
                    ctx.provider, state["question"], schema
                )
            except (LLMError, BudgetError) as exc:
                return _abort("the uploaded-data question could not be grounded", exc, ctx)
            analysis = analyst.analysis_from_upload_mapping(state["question"], query_mapping)
        elif ctx.session.registry is not None:
            # A direct metric question is resolved against the registry before
            # any model sees it.  The old generic planner could transform
            # "total revenue by region" into a trend or one chosen region;
            # both outputs were cited yet did not answer the question.
            from agentic_analytics.analytics.metric_plan import resolve_question

            query_mapping = resolve_question(state["question"], ctx.session.registry)
            if query_mapping is not None:
                analysis = analyst.analysis_from_metric_mapping(state["question"], query_mapping)
            else:
                try:
                    analysis = await analyst.analyze_question(
                        ctx.provider,
                        state["question"],
                        state["dataset_catalog"],
                        state["metric_catalog"],
                    )
                except (LLMError, BudgetError) as exc:
                    return _abort("the question could not be analysed", exc, ctx)
        else:
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
        if query_mapping is not None and not query_mapping.confident:
            limitations = [f"The question was not executed because {query_mapping.explanation}."]
        return {
            "analysis": analysis,
            "query_mapping": query_mapping,
            "limitations": limitations,
        }

    # -------------------------------------------------------- plan_analysis
    async def plan_analysis(state: AnalysisState) -> dict[str, Any]:
        if state.get("stopped_reason"):
            return {}
        mapping = state.get("query_mapping")
        if mapping is not None:
            if not mapping.confident:
                return _abort(
                    f"the question could not be mapped safely: {mapping.explanation}", None, ctx
                )
            metric_contract = mapping.canonical_dict().get("kind") == "metric_registry"
            task = AnalysisTask(
                task_id="task_01",
                objective="Compute the accepted query contract",
                analysis_type=state["analysis"].analysis_type,
                preferred_tool="compute_metric" if metric_contract else "aggregate_for_question",
                priority=1,
                required_metrics=[mapping.metric] if metric_contract and mapping.metric else [],
                dimensions=list(mapping.dimensions) if metric_contract else [],
                table=getattr(mapping, "table", None),
                variables={
                    "question": state["question"],
                    "metric_query_contract"
                    if metric_contract
                    else "query_contract": mapping.as_dict(),
                },
            )
            tasks = [task]
            ctx.events.emit(
                EventType.PLAN_GENERATED,
                task_count=1,
                contract_hash=mapping.contract_hash,
                interpretation=mapping.interpretation,
                tasks=[
                    {
                        "task_id": task.task_id,
                        "objective": task.objective,
                        "analysis_type": task.analysis_type,
                        "preferred_tool": task.preferred_tool,
                        "metrics": [mapping.measure] if mapping.measure else [],
                        "dimensions": [mapping.dimension] if mapping.dimension else [],
                        "priority": task.priority,
                    }
                ],
            )
            return {"plan": AnalysisPlan(tasks=tasks), "pending_tasks": tasks}
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
        # The engine's own reading of the question, resolved once for the
        # run and handed to every verification. For an uploaded table this
        # names the operation, measure, dimension and period the question
        # asked for, and a claim that speaks to none of them does not
        # answer it whatever the model says. `None` for the governed
        # warehouse and for anything that cannot be mapped, where the
        # intent check abstains.
        question_mapping = state.get("query_mapping") or _resolve_intent(
            ctx, state.get("question", "")
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
                        mapping=question_mapping,
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

        # The engine's own answer to a question it mapped confidently.
        #
        # Added after the model's claims and verified without asking a
        # model anything: the sentence is generated from the executed
        # query and its lineage, so its numbers are its cells by
        # construction. A real run proposed the correct total and the
        # critic withheld it, unsure whether `total_net_value` named a
        # column of the uploaded file or the engine's own alias. It was
        # the alias, the doubt was fair, and the wording caused it. This
        # says "the total net value is ..." instead, naming the visitor's
        # column, and a model cannot discard it over a name the engine
        # chose.
        canonical = _canonical_for(question_mapping, results, supported)
        if canonical is not None:
            verdict = _verify_without_a_model(canonical, results, question_mapping)
            verdicts.append(verdict)
            if verdict.status == "supported":
                supported.insert(0, critic.publish(canonical, verdict))
            else:
                rejected.append(verdict)

        # A direct metric contract is the engine's full answer.  Model prose
        # may summarize a best segment or a trend, but neither is permitted
        # to replace a requested grouped aggregate.  Keep the deterministic
        # complete result rather than mixing it with partial model claims.
        question_contract: Any = getattr(question_mapping, "canonical_dict", lambda: {})()
        # Whenever the engine composed its own answer, not only for a
        # grouped one. A scalar total published the model's restatement
        # instead: "165,414,408" where the engine's own sentence said
        # "165,414,407.58 across 518 rows". Both cite the same cell and
        # numeric verification accepts a rounded figure, so the rounding
        # was the only difference -- and it is the published total that a
        # reader takes away.
        grouped_contract = bool(
            (
                isinstance(question_contract, dict)
                and question_contract.get("kind") == "metric_registry"
            )
            or getattr(question_mapping, "dimension", None)
        )
        if grouped_contract or getattr(question_mapping, "confident", False):
            canonical_ids = {
                item.finding_id for item in supported if item.verifier_rule == "engine_canonical"
            }
            if canonical_ids:
                for item in supported[:]:
                    if item.finding_id not in canonical_ids:
                        supported.remove(item)
                        rejected.append(
                            Verdict(
                                finding_id=item.finding_id,
                                status="unsupported",
                                reason=(
                                    "The engine published the complete "
                                    "registry-grounded answer instead of a partial "
                                    "summary."
                                    if grouped_contract
                                    else "The engine published its own answer, computed "
                                    "from the executed result, instead of a restatement."
                                ),
                                rule=(
                                    "partial_metric_answer"
                                    if grouped_contract
                                    else "restated_engine_answer"
                                ),
                                evidence_supported=item.evidence_supported,
                                answers_question=False,
                            )
                        )

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
        # A resolved contract has exactly one permitted computation.  A
        # follow-up cannot safely broaden, regroup or substitute it when the
        # resulting claim is withheld; it would recreate the partial-answer
        # failure this contract prevents.
        if state.get("query_mapping") is not None:
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
        stopped = str(state.get("stopped_reason") or "")
        if stopped:
            # A refusal is stated once. The mapping stage already writes a
            # precise, actionable sentence -- "the question restricts to
            # '3 to 9' but does not say which column that applies to; name
            # the column, for example ..." -- and a generic "the run
            # stopped early" on top of it adds nothing a reader can use.
            core = stopped.split(":", 1)[-1].strip() or stopped
            if not any(core and core in existing for existing in limitations):
                limitations.append(f"The question was not answered: {stopped}.")
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
    """Stop the graph cleanly, with a reason a user can read.

    This node still lets the graph assemble its report.  Emitting
    ``RUN_FAILED`` here made the browser treat the stream as terminal before
    :func:`run_analysis` had stored that report, so a safe refusal could leave
    the visitor on an empty screen.  ``RUN_FAILED`` is reserved for a run
    which actually crashed; a graceful stop is represented in the timeline as
    a failed analysis stage and ends with the runner's single terminal event.
    """
    detail = f"{reason}: {exc}" if exc else reason
    log.warning("run_aborted", reason=reason, error=str(exc) if exc else None)
    ctx.events.emit(EventType.ANALYSIS_TASK_FAILED, reason=detail)
    # The detail belongs in `errors`, which is diagnostic, and not in
    # `limitations`, which a visitor reads. Adding it here was one of three
    # places that described a single refusal, so a report on an unmappable
    # question said the same thing three times in three phrasings.
    return {"stopped_reason": reason, "errors": [detail]}
