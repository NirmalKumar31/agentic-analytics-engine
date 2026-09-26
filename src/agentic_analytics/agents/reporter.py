"""Report assembly from verified findings only.

The model here is an **organiser, not an author**. It chooses which verified
findings appear in the summary, how they are grouped under headings, and in
what order. Every factual sentence in the finished report is then written by
this module, from the exact text of a `PublishedFinding` that already passed
claim-shape checking, numeric verification and the critic.

That division is the point. The previous design let the model write the
executive summary and section bodies as free prose and then stripped any
*number* those contained that no finding supported. A sentence with no number
in it -- "New customers are the primary cause of the Electronics decline" --
went straight through, which meant the run's central invariant ("nothing
reaches the reader that verification did not approve") held for figures and
not for claims. Checking prose afterwards means catching the failures someone
thought to look for; not asking for prose means there is nothing to catch.

What the model still decides, and why that is safe:

* **Selection and ordering** -- cannot introduce a claim, because every
  candidate is already verified.
* **Section grouping** -- which findings belong together. The *heading* is
  not the model's: a label short enough to look like a label is still long
  enough to assert something, so the engine derives it from the kind of
  evidence in the group.
* **Next questions** -- questions rather than assertions, and filtered for
  numbers and causal phrasing so one cannot smuggle in a conclusion.

Limitations are engine-owned. The model is not asked for them at all.
"""

from __future__ import annotations

import json

from agentic_analytics.agents.base import ask_into, bullet_list
from agentic_analytics.agents.prompts import REPORTER
from agentic_analytics.agents.schemas import (
    AnalysisReport,
    PublishedFinding,
    ReportPlan,
    ReportPlanSection,
    ReportSection,
)
from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.llm.base import LLMError, LLMProvider
from agentic_analytics.logging import get_logger
from agentic_analytics.verification.claims import is_causal
from agentic_analytics.verification.numeric import extract_numbers

log = get_logger(__name__)

#: How many findings the engine will put in an executive summary when the
#: model selected none, or selected only ones that no longer exist.
DEFAULT_SUMMARY_FINDINGS = 3
#: Ceiling on how many findings one section renders, so a plan naming every
#: finding in every section cannot produce an unreadable wall of repetition.
MAX_SECTION_FINDINGS = 8
#: Headings, owned by the engine and chosen from what a section contains.
#: `EvidenceKind` is set when a finding is created and is checked by the
#: verification pipeline, so labelling a group by its dominant kind states
#: nothing that was not already established.
KIND_HEADINGS: dict[str, str] = {
    "calculated_fact": "What the numbers show",
    "statistical_result": "Statistical comparisons",
    "interpretation": "Reading of the results",
}
FALLBACK_HEADING = "Findings"


async def write_report(
    question: str,
    findings: list[PublishedFinding],
    results: dict[str, ResultSnapshot],
    limitations: list[str],
    provider: LLMProvider,
) -> AnalysisReport:
    """Ask for a plan, then build the report from verified text."""
    if not findings:
        return _empty_report(question, limitations)

    try:
        payload = await ask_into(
            provider,
            ReportPlan,
            role="reporter",
            system=REPORTER,
            user=_prompt(question, findings, limitations),
            context={
                "question": question,
                "findings": [json.loads(f.model_dump_json()) for f in findings],
                "limitations": limitations,
            },
            max_tokens=1024,
        )
        plan = payload
    except LLMError as exc:
        log.warning("report_plan_failed", error=str(exc))
        return _assemble(question, findings, _default_plan(findings), [*limitations, str(exc)])

    return _assemble(question, findings, plan, limitations)


def _default_plan(findings: list[PublishedFinding]) -> ReportPlan:
    """A plan the engine produces when no model is available.

    Groups by evidence kind, which is a property of the finding rather than a
    judgement about it, so the fallback needs no model and asserts nothing.
    """
    by_kind: dict[str, list[str]] = {}
    for finding in findings:
        by_kind.setdefault(finding.kind, []).append(finding.finding_id)
    return ReportPlan(
        executive_finding_ids=[f.finding_id for f in findings[:DEFAULT_SUMMARY_FINDINGS]],
        sections=[ReportPlanSection(finding_ids=ids) for ids in by_kind.values()],
    )


def _assemble(
    question: str,
    findings: list[PublishedFinding],
    plan: ReportPlan,
    limitations: list[str],
) -> AnalysisReport:
    """Build the report. Every factual sentence comes from a finding.

    The plan is treated as untrusted input: ids that name nothing verified
    are dropped, headings that assert a figure are replaced, and questions
    that state rather than ask are removed.
    """
    by_id = {f.finding_id: f for f in findings}

    selected = _resolve(plan.executive_finding_ids, by_id)
    if not selected:
        selected = findings[:DEFAULT_SUMMARY_FINDINGS]
    executive_summary = " ".join(f.text for f in selected)

    sections: list[ReportSection] = []
    used: set[str] = set()
    for planned in plan.sections:
        members = [f for f in _resolve(planned.finding_ids, by_id) if f.finding_id not in used][
            :MAX_SECTION_FINDINGS
        ]
        if not members:
            continue
        used.update(f.finding_id for f in members)
        sections.append(
            ReportSection(
                heading=_heading_for(members),
                # The body is the findings' own text, joined. Nothing else.
                body=" ".join(f.text for f in members),
                finding_ids=[f.finding_id for f in members],
            )
        )

    return AnalysisReport(
        question=question,
        executive_summary=executive_summary,
        # Every verified finding, in order, verbatim. Not the model's choice:
        # a reader should see everything that survived verification.
        key_findings=[f.text for f in findings],
        sections=sections,
        # Engine-owned. The model is never asked for a limitation, so it
        # cannot state one that is really a conclusion.
        limitations=list(dict.fromkeys(limitations)),
        next_questions=_safe_questions(plan.next_questions),
    )


def _resolve(finding_ids: list[str], by_id: dict[str, PublishedFinding]) -> list[PublishedFinding]:
    """Findings for these ids, de-duplicated, skipping anything unknown."""
    seen: set[str] = set()
    out: list[PublishedFinding] = []
    for finding_id in finding_ids:
        finding = by_id.get(finding_id)
        if finding is not None and finding_id not in seen:
            seen.add(finding_id)
            out.append(finding)
    return out


def _heading_for(members: list[PublishedFinding]) -> str:
    """Label a section by the kind of evidence it holds.

    Deterministic, and derived from a field the verification pipeline already
    validated, so the heading asserts nothing new. Mixed sections take the
    most common kind, ties broken by the first finding's, so the same group
    always produces the same label.
    """
    counts: dict[str, int] = {}
    for finding in members:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
    dominant = max(counts, key=lambda kind: (counts[kind], kind == members[0].kind))
    return KIND_HEADINGS.get(dominant, FALLBACK_HEADING)


def _safe_questions(questions: list[str]) -> list[str]:
    """Keep questions that ask something; drop ones that assert something.

    "Why did late delivery cause churn?" presupposes the causal claim the
    critic exists to reject, so the same detector is applied here.
    """
    kept: list[str] = []
    for question in questions[:5]:
        text = " ".join(question.split())
        if not text:
            continue
        if extract_numbers(text) or is_causal(text):
            log.info("report_question_dropped", question=text)
            continue
        kept.append(text[:200])
    return kept


def _empty_report(question: str, limitations: list[str]) -> AnalysisReport:
    """Nothing survived verification, so the report says exactly that."""
    return AnalysisReport(
        question=question,
        executive_summary=(
            "No finding survived verification, so this report states no conclusion. "
            "The limitations below explain what the analysis could not establish."
        ),
        key_findings=[],
        sections=[],
        limitations=list(dict.fromkeys(limitations))
        or ["Every proposed finding failed verification."],
        next_questions=[],
    )


def _prompt(question: str, findings: list[PublishedFinding], limitations: list[str]) -> str:
    lines = [f"- [{f.finding_id}] ({f.kind}) {f.text}" for f in findings]
    return f"""\
QUESTION
{question}

VERIFIED FINDINGS
{chr(10).join(lines)}

KNOWN LIMITATIONS OF THIS RUN
{bullet_list(limitations)}

Return a plan: which finding ids belong in the executive summary, what
sections to group them under, and the order. You are not writing the report
text -- the engine writes it from these findings' own wording."""
