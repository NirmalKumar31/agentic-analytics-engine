"""Report writing from verified findings only."""

from __future__ import annotations

import json

from agentic_analytics.agents.base import ask, bullet_list, parse_into, schema_of
from agentic_analytics.agents.prompts import REPORTER
from agentic_analytics.agents.schemas import AnalysisReport, PublishedFinding
from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.llm.base import LLMError, LLMProvider
from agentic_analytics.logging import get_logger
from agentic_analytics.verification.numeric import extract_numbers

log = get_logger(__name__)


class ReportBody(AnalysisReport):
    """What the reporter returns; the question is supplied by the engine."""

    question: str = ""


async def write_report(
    question: str,
    findings: list[PublishedFinding],
    results: dict[str, ResultSnapshot],
    limitations: list[str],
    provider: LLMProvider,
) -> AnalysisReport:
    """Write the report, then strip anything it introduced that findings do not support."""
    try:
        payload = await ask(
            provider,
            role="reporter",
            system=REPORTER,
            user=_prompt(question, findings, limitations),
            schema=schema_of(ReportBody),
            context={
                "question": question,
                "findings": [json.loads(f.model_dump_json()) for f in findings],
                "limitations": limitations,
            },
            max_tokens=2048,
        )
        body = parse_into(ReportBody, payload, "reporter")
    except LLMError as exc:
        log.warning("report_generation_failed", error=str(exc))
        return _fallback_report(question, findings, [*limitations, str(exc)])

    report = AnalysisReport(
        question=question,
        executive_summary=body.executive_summary,
        key_findings=body.key_findings,
        sections=body.sections,
        limitations=list(dict.fromkeys([*body.limitations, *limitations])),
        next_questions=body.next_questions,
    )
    return _strip_unsupported_numbers(report, findings, results)


def _allowed_numbers(
    findings: list[PublishedFinding], results: dict[str, ResultSnapshot]
) -> list[float]:
    """Every number a report is permitted to state."""
    allowed: list[float] = []
    for finding in findings:
        allowed.extend(extract_numbers(finding.text))
        for cell in finding.evidence_cells:
            if isinstance(cell.value, int | float) and not isinstance(cell.value, bool):
                allowed.append(float(cell.value))
    for result_id in {r for f in findings for r in f.result_ids}:
        snapshot = results.get(result_id)
        if snapshot is None:
            continue
        # raw-rows-ok: this builds the set of numbers the report is allowed
        # to state, which has to be checked against the real values. Nothing
        # here is sent to a model.
        for row in snapshot.rows:
            allowed.extend(
                float(v) for v in row if isinstance(v, int | float) and not isinstance(v, bool)
            )
    return allowed


def _strip_unsupported_numbers(
    report: AnalysisReport,
    findings: list[PublishedFinding],
    results: dict[str, ResultSnapshot],
) -> AnalysisReport:
    """Remove report prose that states a number no finding supports.

    The reporter is instructed not to introduce numbers. This enforces it,
    because an instruction a model can ignore is not a control.
    """
    allowed = _allowed_numbers(findings, results)

    def supported(text: str) -> bool:
        for stated in extract_numbers(text):
            if not any(abs(stated - value) <= max(0.005, abs(value) * 0.005) for value in allowed):
                log.info("report_text_dropped", value=stated)
                return False
        return True

    if not supported(report.executive_summary):
        report.executive_summary = (
            "The findings below are stated with their supporting results. "
            "A generated summary was removed because it contained a figure that "
            "no verified finding supports."
        )
    report.key_findings = [k for k in report.key_findings if supported(k)]
    kept_sections = []
    for section in report.sections:
        if supported(section.body):
            kept_sections.append(section)
        else:
            report.limitations.append(
                f"A section titled {section.heading!r} was removed because it "
                "stated a figure no verified finding supports."
            )
    report.sections = kept_sections
    return report


def _fallback_report(
    question: str, findings: list[PublishedFinding], limitations: list[str]
) -> AnalysisReport:
    """A report assembled without a model, used when the reporter fails."""
    return AnalysisReport(
        question=question,
        executive_summary=(
            " ".join(f.text for f in findings[:3])
            if findings
            else "No finding survived verification."
        ),
        key_findings=[f.text for f in findings],
        sections=[],
        limitations=limitations,
        next_questions=[],
    )


def _prompt(question: str, findings: list[PublishedFinding], limitations: list[str]) -> str:
    lines = [
        f"- [{f.finding_id}] ({f.kind}) {f.text}  [results: {', '.join(f.result_ids)}]"
        for f in findings
    ]
    return f"""\
QUESTION
{question}

VERIFIED FINDINGS (the only facts you may use)
{chr(10).join(lines) or "(none survived verification)"}

KNOWN LIMITATIONS OF THIS RUN
{bullet_list(limitations)}

Write the report. Do not state any number that is not above."""
