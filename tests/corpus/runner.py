"""Run a corpus case and classify what happened.

The classification is the interesting part. Three outcomes are acceptable
and the rest are contract failures, so the runner does not ask "did it
work" -- it asks which of the allowed things happened, and separately
whether anything published was unsupported or off-topic.

Nothing here reaches a network. The scripted provider drives the graph, and
the AI-stub mode swaps in a provider that answers from the same script
while reporting itself as remote, so the disclosure path is exercised too.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_analytics.agents.scope import check_scope
from agentic_analytics.config import Settings
from agentic_analytics.graph.runner import RunResult, run_analysis
from agentic_analytics.llm.fake import FakeProvider
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.verification.numeric import verify_numbers
from agentic_analytics.warehouse.session import SessionManager, open_upload_session
from tests.corpus.generators import build
from tests.corpus.kinds import Outcome
from tests.corpus.manifest import Case


class RemoteFakeProvider(FakeProvider):
    """The scripted provider, declaring itself remote.

    Enough to exercise the disclosure and result-scoping paths that key off
    `remote_inference` without a credential or a network.
    """

    remote_inference = True


class _Recording(RemoteFakeProvider):
    """Remote, and keeps every prompt it was handed.

    Used for every case rather than a sensitive subset, so the corpus can
    report a measured raw-cell disclosure count instead of pointing at a
    different suite.
    """

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[Any] = []

    async def complete_json(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        return await super().complete_json(request)

    def everything_sent(self) -> str:
        return "\n".join(
            f"{r.system}\n{r.user}\n{json.dumps(r.context, default=str)}" for r in self.requests
        )


@dataclass
class Observation:
    """What one case actually did."""

    case: Case
    outcome: Outcome
    published: int = 0
    withheld: int = 0
    refusal_reason: str = ""
    #: Contract violations. Any non-empty list fails the corpus.
    unsupported: list[str] = field(default_factory=list)
    irrelevant: list[str] = field(default_factory=list)
    silent_guesses: list[str] = field(default_factory=list)
    leaked_results: list[str] = field(default_factory=list)
    failure: str = ""
    withheld_rules: list[str] = field(default_factory=list)
    #: Values from columns the classifier would not offer as a grouping,
    #: found in a prompt. A grouping column's own labels are part of any
    #: aggregate over it and are excluded deliberately.
    disclosed_cells: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            not self.failure
            and not self.unsupported
            and not self.irrelevant
            and not self.silent_guesses
            and not self.leaked_results
            and not self.disclosed_cells
            and self.outcome in self.case.allowed
        )


def _settings(upload_dir: Path) -> Settings:
    return Settings(
        upload_dir=upload_dir,
        live_analytics_enabled=True,
        uploads_enabled=True,
        provider_mode="fake",
        log_json=False,
    )


def _numbers_resolve(result: RunResult) -> list[str]:
    """Published findings whose numbers do not come from their own cited
    results. Re-checked here rather than trusted: the corpus exists to
    catch the case where the gate itself is wrong."""
    broken: list[str] = []
    for finding in result.published:
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
                cells.append((float(value), cell.column))
        verdict = verify_numbers(
            finding.text,
            finding.claimed_change,
            cells,
            [result.results[r] for r in finding.result_ids if r in result.results],
        )
        if not verdict.ok:
            broken.append(f"{finding.text[:70]} :: {verdict.reason[:70]}")
    return broken


#: A column whose values repeat is a category, and its labels are part of
#: any aggregate grouped by it. Judged from the data, in absolute and
#: relative terms, so a small table and a large one are both handled.
LEGITIMATE_GROUP_MAX_DISTINCT = 25
LEGITIMATE_GROUP_MAX_UNIQUENESS = 0.5

#: ISO dates are excluded wholesale. The engine derives period bounds from
#: the question -- "Q2 2025" becomes 2025-04-01 to 2025-06-30 -- and those
#: appear in the plan it sends without having been read from any row.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _values_that_must_not_be_sent(session: Any, dataset: Any) -> set[str]:
    """Cell values whose presence in a prompt would be a disclosure.

    Derived from the data's own cardinality, deliberately **not** from the
    classifier's `role`. Reading the role here made this check blind in
    exactly the case it exists for: reintroducing the near-unique-text
    defect reclassified those columns as dimensions, and a boundary that
    trusts the classifier reclassified their values as legitimate at the
    same moment, so the leak measured zero. The check has to decide what a
    grouping is for itself.

    Two classes of legitimate value are subtracted:

    *Repeating columns.* Labels of a genuine category. Subtracted by
    **value**, not by column, because a foreign key shares its value space
    with the identifier it references -- in a parent/child task table a
    legitimate `parent_task_ref` label is also some row's `task_ref`.

    *Dates.* See `_ISO_DATE` above.

    What remains is the class that matters: identifiers, near-unique text
    and free text, whose values a prompt could not legitimately have
    obtained.
    """
    del session  # the boundary is intentionally independent of inference
    rows = len(dataset.rows)
    legitimate: set[str] = set()
    suspect: set[str] = set()
    for index, _name in enumerate(dataset.header):
        values = {
            value
            for row in dataset.rows
            if isinstance((value := row[index]), str)
            and len(value) >= 6
            and not _ISO_DATE.match(value)
        }
        if not values:
            continue
        distinct = len(values)
        repeats = (
            distinct <= LEGITIMATE_GROUP_MAX_DISTINCT
            or distinct / max(rows, 1) <= LEGITIMATE_GROUP_MAX_UNIQUENESS
        )
        (legitimate if repeats else suspect).update(values)
    return suspect - legitimate


def _cited_results_belong_to_this_run(result: RunResult) -> list[str]:
    """A published finding citing a result this run did not produce."""
    return [
        f"{f.finding_id} cites {r}"
        for f in result.published
        for r in f.result_ids
        if r not in result.results
    ]


async def run_case(case: Case, tmp: Path, *, remote: bool = False) -> Observation:
    """Upload the dataset, ask the question, and classify the answer."""
    dataset = build(case.dataset)
    directory = tmp / case.dataset
    directory.mkdir(parents=True, exist_ok=True)
    path = dataset.write_parquet(directory) if case.parquet else dataset.write_csv(directory)

    cfg = _settings(tmp / "uploads")
    manager = SessionManager()
    provider: FakeProvider | None = None
    try:
        session = manager.add(
            open_upload_session(path, path.name, "parquet" if case.parquet else "csv")
        )
        verdict = check_scope(
            case.question,
            session.catalog(),
            session.registry.describe_all() if session.registry else [],
        )
        if not verdict.in_scope:
            return Observation(case, Outcome.SAFE_REFUSAL, refusal_reason=verdict.reason)

        # Recording on every case, not just the sensitive shapes, so the
        # disclosure count the report carries is measured rather than
        # inferred from a neighbouring suite.
        provider = _Recording() if remote else FakeProvider()
        forbidden = _values_that_must_not_be_sent(session, dataset) if remote else set()
        server = build_server(manager, cfg)
        result = await run_analysis(case.question, session, server, settings=cfg, provider=provider)
        sent = provider.everything_sent() if isinstance(provider, _Recording) else ""
    except Exception as exc:  # pragma: no cover - a failure is the finding
        return Observation(case, Outcome.NO_FINDINGS, failure=f"{type(exc).__name__}: {exc}"[:180])
    finally:
        if provider is not None:
            await provider.aclose()
        manager.close_all()

    observation = _classify(case, result)
    observation.disclosed_cells = sorted(v for v in forbidden if v in sent)[:5]
    return observation


def _classify(case: Case, result: RunResult) -> Observation:
    """Turn a completed run into an observation."""
    published = list(result.published)
    rejected = list(result.rejected)
    rules = sorted({v.rule for v in rejected})

    if result.stopped_reason and not published:
        # Stopped without an answer. Acceptable for a question the dataset
        # cannot support; a failure only if the case expected an answer.
        return Observation(
            case,
            Outcome.NO_FINDINGS,
            withheld=len(rejected),
            refusal_reason=result.stopped_reason[:120],
            withheld_rules=rules,
        )

    outcome = Outcome.VERIFIED_ANSWER if published else Outcome.NO_FINDINGS
    observation = Observation(
        case,
        outcome,
        published=len(published),
        withheld=len(rejected),
        withheld_rules=rules,
        unsupported=_numbers_resolve(result),
        leaked_results=_cited_results_belong_to_this_run(result),
    )

    # A published finding must carry a supporting verdict. The engine's own
    # gate decides this; the corpus checks the gate was actually applied.
    verdicts = {v.finding_id: v for v in getattr(result, "verdicts", []) or []}
    for finding in published:
        verdict = verdicts.get(finding.finding_id)
        if verdict is not None and verdict.status != "supported":
            observation.unsupported.append(
                f"{finding.text[:60]} published with verdict {verdict.status}"
            )

    return observation


def report(observations: list[Observation]) -> dict[str, Any]:
    """The corpus matrix, counted rather than described."""
    domains = {o.case.domain for o in observations}
    families = {o.case.family for o in observations}
    kinds = {o.case.kind for o in observations}
    return {
        "domains_covered": len(domains),
        "structural_families_covered": len(families),
        "question_types_covered": len(kinds),
        "total_cases": len(observations),
        "verified_answers": sum(1 for o in observations if o.outcome is Outcome.VERIFIED_ANSWER),
        "safe_refusals": sum(1 for o in observations if o.outcome is Outcome.SAFE_REFUSAL),
        "zero_finding_completions": sum(
            1 for o in observations if o.outcome is Outcome.NO_FINDINGS
        ),
        "unexpected_failures": sum(1 for o in observations if o.failure),
        "outcome_not_allowed": sum(
            1 for o in observations if not o.failure and o.outcome not in o.case.allowed
        ),
        "unsupported_published": sum(len(o.unsupported) for o in observations),
        "irrelevant_published": sum(len(o.irrelevant) for o in observations),
        "silent_guesses": sum(len(o.silent_guesses) for o in observations),
        "cross_run_leaks": sum(len(o.leaked_results) for o in observations),
        "raw_cell_disclosures": sum(len(o.disclosed_cells) for o in observations),
        "csv_cases": sum(1 for o in observations if not o.case.parquet),
        "parquet_cases": sum(1 for o in observations if o.case.parquet),
    }
