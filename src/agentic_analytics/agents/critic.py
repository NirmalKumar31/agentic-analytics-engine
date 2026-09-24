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

from agentic_analytics.agents.base import ask, parse_into, schema_of
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


class CriticVerdict(Verdict):
    """What the critic model returns (finding_id is filled in by the engine)."""

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
) -> tuple[Verdict, dict[str, Any] | None]:
    """Run all three gates on one finding."""
    cited = [results[r] for r in finding.result_ids if r in results]

    shape = check_claim(finding.text, finding.kind, cited, bool(finding.result_ids))
    if not shape.ok:
        verdict = Verdict(
            finding_id=finding.finding_id,
            status="unsupported",
            reason=shape.reason,
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
            numeric_check=numeric.as_dict(),
        )
        _emit(events, finding, verdict)
        return verdict, numeric.as_dict()

    try:
        payload = await ask(
            provider,
            role="critic",
            system=CRITIC,
            user=_critic_prompt(finding, cited),
            schema=schema_of(CriticVerdict),
            context={
                "finding": json.loads(finding.model_dump_json()),
                "results": [s.compact() for s in cited],
            },
        )
        critic = parse_into(CriticVerdict, payload, "critic")
        status = critic.status
        reason = critic.reason
    except LLMError as exc:
        # A critic that cannot answer must not wave a finding through.
        status = "partially_supported"
        reason = f"The verifier could not complete its check ({exc})."

    verdict = Verdict(
        finding_id=finding.finding_id,
        status=status,
        reason=reason,
        numeric_check=numeric.as_dict(),
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
        numeric_check=verdict.numeric_check,
    )


def _critic_prompt(finding: CandidateFinding, cited: list[ResultSnapshot]) -> str:
    blocks: list[str] = []
    for snapshot in cited:
        lines = [
            f"result_id: {snapshot.result_id} (tool: {snapshot.tool_name})",
            f"sql: {snapshot.sql}",
            f"columns: {snapshot.columns}",
        ]
        for index, row in enumerate(snapshot.rows[:40]):
            lines.append(f"  row {index}: {row}")
        if snapshot.statistical_result is not None:
            lines.append(
                "  statistical_result: "
                + json.dumps(snapshot.statistical_result.model_dump(exclude_none=True))
            )
        blocks.append("\n".join(lines))

    cells = "\n".join(
        f"- {c.result_id}[{c.row}].{c.column} = {c.value} ({c.label})"
        for c in finding.evidence_cells
    )
    return f"""\
PROPOSED FINDING
text: {finding.text}
kind: {finding.kind}
cites: {", ".join(finding.result_ids)}

REFERENCED CELLS
{cells or "(none)"}

RESULTS
{chr(10).join(blocks) or "(none)"}

The arithmetic in this finding has already been checked and is correct.
Judge only whether the wording fairly describes the result."""
