"""Evidence verification.

Three gates, in order, and the first failure decides:

1. **Claim shape** -- deterministic. Causal language on observational data,
   "significant" with no test, a claim that cites nothing.
2. **Arithmetic** -- deterministic. Every number must trace to a cited result
   or be derivable from two cited cells.
3. **Semantics** -- the critic model. Whether the wording is a fair
   description of the result, which is the only part of this that needs
   judgement.

Ordering matters: a model asked to check arithmetic will sometimes agree with
wrong arithmetic, so the arithmetic is settled before the model is consulted.
"""

from __future__ import annotations

import json
from typing import Any

from agentic_analytics.agents.base import ask_into
from agentic_analytics.agents.prompts import CRITIC
from agentic_analytics.agents.schemas import (
    CandidateFinding,
    PublishedFinding,
    Verdict,
)
from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.events import EventBus, EventType
from agentic_analytics.llm.base import LLMError, LLMProvider
from agentic_analytics.logging import get_logger
from agentic_analytics.verification.claims import check_claim
from agentic_analytics.verification.numeric import verify_numbers

log = get_logger(__name__)


#: Stable rejection rule for a claim that is accurate and off-topic.
IRRELEVANT = "irrelevant_to_question"


class CriticVerdict(Verdict):
    """What the critic model returns (finding_id is filled in by the engine).

    `answers_question` defaults to `None`, which the engine treats as "the
    critic did not answer" and refuses, rather than as consent. A model that
    omits the field must not thereby publish.
    """

    finding_id: str = ""


def _resolve_cells(
    finding: CandidateFinding, results: dict[str, ResultSnapshot]
) -> list[tuple[float, str]]:
    """Numeric values at the cells a finding points to.

    A cell reference that does not resolve is dropped here and shows up as an
    unmatched number in the arithmetic check, which is the correct outcome:
    the claim is citing something that is not there.
    """
    out: list[tuple[float, str]] = []
    for cell in finding.evidence_cells:
        snapshot = results.get(cell.result_id)
        if snapshot is None:
            continue
        try:
            value = snapshot.cell(cell.row, cell.column)
        except (KeyError, IndexError):
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        out.append((float(value), f"{cell.result_id}[{cell.row}].{cell.column}"))
    return out


async def verify_finding(
    finding: CandidateFinding,
    results: dict[str, ResultSnapshot],
    provider: LLMProvider,
    events: EventBus | None = None,
    question: str = "",
    objective: str = "",
    target_metrics: list[str] | None = None,
    target_dimensions: list[str] | None = None,
) -> tuple[Verdict, dict[str, Any] | None]:
    """Run every gate on one finding.

    Four now, not three. Claim shape, arithmetic and evidence support all
    ask whether the claim is *right*; none of them asks whether it is an
    *answer*. A revenue ranking by channel can pass all three and still say
    nothing about why margin fell, and shipping that as the flagship result
    is a product failure even though every number in it is correct.
    """
    cited = [results[r] for r in finding.result_ids if r in results]

    shape = check_claim(finding.text, finding.kind, cited, bool(finding.result_ids))
    if not shape.ok:
        verdict = Verdict(
            finding_id=finding.finding_id,
            status="unsupported",
            reason=shape.reason,
            rule=shape.rule,
            numeric_check={"rule": shape.rule},
        )
        _emit(events, finding, verdict)
        return verdict, {"rule": shape.rule}

    numeric = verify_numbers(
        finding.text,
        finding.claimed_change,
        _resolve_cells(finding, results),
        cited,
    )
    if not numeric.ok:
        verdict = Verdict(
            finding_id=finding.finding_id,
            status="unsupported",
            reason=numeric.reason,
            rule="numeric_mismatch",
            numeric_check=numeric.as_dict(),
        )
        _emit(events, finding, verdict)
        return verdict, numeric.as_dict()

    try:
        payload = await ask_into(
            provider,
            CriticVerdict,
            role="critic",
            system=CRITIC,
            user=_critic_prompt(finding, cited, question, objective),
            context={
                "finding": json.loads(finding.model_dump_json()),
                "results": [s.compact() for s in cited],
                "question": question,
                "objective": objective,
                "target_metrics": list(target_metrics or []),
                "target_dimensions": list(target_dimensions or []),
            },
        )
        critic = payload
        status = critic.status
        reason = critic.reason
        answers = critic.answers_question
        relevance_reason = critic.relevance_reason
    except LLMError as exc:
        # A critic that cannot answer must not wave a finding through.
        status = "partially_supported"
        reason = f"The verifier could not complete its check ({exc})."
        relevance_reason = ""
        answers = None
        rule = "critic_unavailable"
    else:
        rule = "critic"

    evidence_supported = status == "supported"

    # Relevance is only reached when the evidence holds, so a claim is never
    # rejected as off-topic when the real problem is that it is wrong.
    if evidence_supported and answers is not True:
        status = "unsupported"
        rule = IRRELEVANT
        reason = relevance_reason or "The finding does not address the question that was asked."

    verdict = Verdict(
        finding_id=finding.finding_id,
        status=status,
        reason=reason,
        rule=rule,
        numeric_check=numeric.as_dict(),
        evidence_supported=evidence_supported,
        answers_question=answers,
        relevance_reason=relevance_reason,
    )
    _emit(events, finding, verdict)
    return verdict, numeric.as_dict()


def _emit(events: EventBus | None, finding: CandidateFinding, verdict: Verdict) -> None:
    if not events:
        return
    kind = (
        EventType.FINDING_VERIFIED if verdict.status == "supported" else EventType.FINDING_REJECTED
    )
    events.emit(
        kind,
        finding_id=finding.finding_id,
        task_id=finding.task_id,
        status=verdict.status,
        reason=verdict.reason,
        text=finding.text,
    )


def publish(finding: CandidateFinding, verdict: Verdict) -> PublishedFinding:
    """Convert a verified finding into its published form."""
    return PublishedFinding(
        finding_id=finding.finding_id,
        text=finding.text,
        kind=finding.kind,
        task_id=finding.task_id,
        result_ids=finding.result_ids,
        evidence_cells=finding.evidence_cells,
        metric_ids=finding.metric_ids,
        verification_status=verdict.status,
        verifier_reason=verdict.reason,
        verifier_rule=verdict.rule,
        numeric_check=verdict.numeric_check,
        evidence_supported=verdict.evidence_supported,
        answers_question=verdict.answers_question,
        relevance_reason=verdict.relevance_reason,
        # Dropped when the verifier could not read it. An unreadable change
        # asserts nothing, so it does not fail the finding -- but it must
        # not be displayed as a calculation either, because nothing checked
        # it. The provenance drawer shows what was verified or nothing.
        claimed_change=(
            None
            if (verdict.numeric_check or {}).get("claimed_change_discarded")
            else finding.claimed_change
        ),
    )


def _render_cells(finding: CandidateFinding, by_id: dict[str, ResultSnapshot]) -> str:
    """Show the critic what is *in* each cited cell, not what was claimed.

    `EvidenceCell.value` is optional and a proposer often leaves it unset.
    This line used to render it directly, so every unset value reached the
    critic as `= None` -- and the critic, reading exactly what it was shown,
    rejected correct findings on the grounds that the cited cell was empty.
    A real local model lost four true claims that way in one probe: the
    engine held the right value the whole time and never looked it up.

    Resolution goes through `agent_cell`, so a withheld upload cell is still
    withheld here. A claimed value that disagrees with the result is shown
    alongside rather than silently replaced, because a miscopied cell is
    something the critic should see.
    """
    lines: list[str] = []
    for cell in finding.evidence_cells:
        reference = f"{cell.result_id}[{cell.row}].{cell.column}"
        label = f" ({cell.label})" if cell.label else ""
        snapshot = by_id.get(cell.result_id)
        if snapshot is None:
            lines.append(f"- {reference} = (this result was not cited or is unavailable)")
            continue
        try:
            value = snapshot.agent_cell(cell.row, cell.column)
        except (KeyError, IndexError):
            lines.append(f"- {reference} = (no such cell in that result)")
            continue
        line = f"- {reference} = {value}{label}"
        if cell.value is not None and not _same_scalar(cell.value, value):
            line += f"  [the finding states {cell.value} for this cell]"
        lines.append(line)
    return "\n".join(lines)


def _same_scalar(claimed: Any, actual: Any) -> bool:
    if isinstance(claimed, bool) or isinstance(actual, bool):
        return claimed is actual
    if isinstance(claimed, int | float) and isinstance(actual, int | float):
        return abs(float(claimed) - float(actual)) <= max(0.005, abs(float(actual)) * 1e-6)
    return str(claimed).strip() == str(actual).strip()


def _critic_prompt(
    finding: CandidateFinding,
    cited: list[ResultSnapshot],
    question: str = "",
    objective: str = "",
) -> str:
    blocks: list[str] = []
    for snapshot in cited:
        lines = [
            f"result_id: {snapshot.result_id} (tool: {snapshot.tool_name})",
            f"sql: {snapshot.sql}",
            f"columns: {snapshot.columns}",
        ]
        for index, row in enumerate(snapshot.agent_rows()[:40]):
            lines.append(f"  row {index}: {row}")
        if snapshot.statistical_result is not None:
            lines.append(
                "  statistical_result: "
                + json.dumps(snapshot.statistical_result.model_dump(exclude_none=True))
            )
        blocks.append("\n".join(lines))

    cells = _render_cells(finding, {s.result_id: s for s in cited})
    return f"""\
QUESTION
{question or "(not recorded)"}

TASK OBJECTIVE
{objective or "(not recorded)"}

PROPOSED FINDING
text: {finding.text}
kind: {finding.kind}
cites: {", ".join(finding.result_ids)}

REFERENCED CELLS
{cells or "(none)"}

RESULTS
{chr(10).join(blocks) or "(none)"}

The arithmetic in this finding has already been checked and is correct.
Judge two things: whether the wording fairly describes the result, and
whether the finding answers the QUESTION above."""
