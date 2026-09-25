"""No factual sentence may reach the reader that verification did not approve.

The finding pipeline is strict: claim shape, then arithmetic against cited
cells, then a critic, and only supported findings publish. The reporter used
to undo that at the last step by writing the executive summary and section
bodies as free prose, with a filter that removed any *number* no finding
supported. A claim with no number in it went straight through.

    "New customers are the primary cause of poor Electronics performance."

Nothing in that sentence is checkable by a numeric filter, and nothing in the
run supports it. These tests drive a reporter that tries to publish exactly
that, through every field it could use, and assert it never appears.

The invariant, stated exactly: every factual sentence in a published report
is either the exact `text` of a `PublishedFinding`, or deterministic
framework text owned by the engine.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentic_analytics.agents.reporter import KIND_HEADINGS, write_report
from agentic_analytics.agents.schemas import EvidenceCell, PublishedFinding
from agentic_analytics.llm.base import LLMError, LLMProvider, LLMRequest

#: The claim under test. No digits, so a numeric filter cannot see it.
FABRICATION = "New customers are the primary cause of poor Electronics performance."


def _finding(finding_id: str, text: str, kind: str = "calculated_fact") -> PublishedFinding:
    return PublishedFinding(
        finding_id=finding_id,
        text=text,
        kind=kind,  # type: ignore[arg-type]
        task_id="task_01",
        result_ids=["res_1"],
        evidence_cells=[
            EvidenceCell(result_id="res_1", row=0, column="revenue", value=100.0, label="revenue")
        ],
        metric_ids=["revenue"],
        verification_status="supported",
        verifier_reason="checked",
    )


FINDINGS = [
    _finding("fin_a", "Revenue fell from 120,000 in Q2 to 100,000 in Q3."),
    _finding("fin_b", "Electronics returned 12.0% of units, the highest of any category."),
    _finding(
        "fin_c",
        "The association between late delivery and repeat purchase is statistically detectable.",
        kind="statistical_result",
    ),
]


class ScriptedReporter(LLMProvider):
    """Returns whatever payload a test hands it, for the reporter role."""

    name = "scripted"
    requires_credentials = False

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__()
        self.payload = payload

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        if request.role != "reporter":
            raise AssertionError(f"unexpected role {request.role}")
        return self.payload


class BrokenReporter(LLMProvider):
    name = "broken"
    requires_credentials = False

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        raise LLMError("the reporter is unavailable")


def _text_of(report: Any) -> str:
    """Every string a reader would see."""
    parts = [report.executive_summary, *report.key_findings, *report.limitations]
    parts += [s.heading for s in report.sections]
    parts += [s.body for s in report.sections]
    parts += report.next_questions
    return "\n".join(parts)


async def _report(payload: dict[str, Any]) -> Any:
    return await write_report(
        "Why did Electronics perform poorly?",
        FINDINGS,
        {},
        ["The dataset covers two years only."],
        ScriptedReporter(payload),
    )


@pytest.mark.parametrize(
    "payload",
    [
        # The fields the old schema had. They are not in ReportPlan any more,
        # so a model still emitting them must have them ignored, not merged.
        {"executive_summary": FABRICATION},
        {"key_findings": [FABRICATION]},
        {"sections": [{"heading": "Cause", "body": FABRICATION, "finding_ids": ["fin_a"]}]},
        {"limitations": [FABRICATION]},
        # And the fields it does have.
        {"sections": [{"heading": FABRICATION, "finding_ids": ["fin_a"]}]},
        {"next_questions": [FABRICATION]},
        {"executive_finding_ids": [FABRICATION]},
        # Everything at once.
        {
            "executive_summary": FABRICATION,
            "key_findings": [FABRICATION],
            "sections": [{"heading": FABRICATION, "body": FABRICATION, "finding_ids": ["fin_b"]}],
            "limitations": [FABRICATION],
            "next_questions": [FABRICATION],
        },
    ],
)
async def test_a_fabricated_claim_never_reaches_the_report(payload: dict[str, Any]) -> None:
    report = await _report(payload)
    assert FABRICATION not in _text_of(report), payload


async def test_every_factual_sentence_comes_from_a_verified_finding() -> None:
    """The invariant itself, not just one attack against it.

    The summary and every section body must be built only from the exact
    text of published findings.
    """
    report = await _report(
        {
            "executive_finding_ids": ["fin_a", "fin_b"],
            "sections": [{"finding_ids": ["fin_a"]}, {"finding_ids": ["fin_c"]}],
        }
    )
    verified = {f.text for f in FINDINGS}

    # Executive summary is a join of exact finding texts.
    remaining = report.executive_summary
    for text in sorted(verified, key=len, reverse=True):
        remaining = remaining.replace(text, "")
    assert remaining.strip() == "", f"summary contains text no finding provided: {remaining!r}"

    # So is every section body.
    for section in report.sections:
        remaining = section.body
        for text in sorted(verified, key=len, reverse=True):
            remaining = remaining.replace(text, "")
        assert remaining.strip() == "", f"section body has unverified text: {remaining!r}"

    # Key findings are the verified texts verbatim.
    assert report.key_findings == [f.text for f in FINDINGS]


async def test_section_headings_are_engine_owned() -> None:
    """A heading is short enough to look like a label and long enough to
    assert something, so the model does not get to write one."""
    report = await _report(
        {"sections": [{"heading": FABRICATION, "finding_ids": ["fin_a", "fin_b"]}]}
    )
    assert [s.heading for s in report.sections] == [KIND_HEADINGS["calculated_fact"]]
    for section in report.sections:
        assert section.heading in KIND_HEADINGS.values()


async def test_a_heading_reflects_the_evidence_in_its_section() -> None:
    report = await _report({"sections": [{"finding_ids": ["fin_c"]}]})
    assert report.sections[0].heading == KIND_HEADINGS["statistical_result"]


async def test_unknown_finding_ids_are_dropped_rather_than_rendered() -> None:
    report = await _report(
        {
            "executive_finding_ids": ["fin_does_not_exist"],
            "sections": [{"finding_ids": ["fin_nope", "fin_a"]}],
        }
    )
    assert "fin_does_not_exist" not in _text_of(report)
    assert report.sections[0].finding_ids == ["fin_a"]
    # An unresolvable selection falls back to real findings, not to nothing.
    assert report.executive_summary


async def test_a_finding_is_not_repeated_across_sections() -> None:
    report = await _report(
        {"sections": [{"finding_ids": ["fin_a"]}, {"finding_ids": ["fin_a", "fin_b"]}]}
    )
    placed = [fid for s in report.sections for fid in s.finding_ids]
    assert len(placed) == len(set(placed)), placed


async def test_limitations_are_engine_owned() -> None:
    """The model is not asked for limitations, so it cannot write one.

    A "limitation" is a good hiding place for a conclusion -- "the decline is
    probably driven by new customers" reads as caution while asserting the
    thing nothing verified.
    """
    report = await _report({"limitations": [FABRICATION, "and another thing"]})
    assert report.limitations == ["The dataset covers two years only."]


async def test_next_questions_cannot_smuggle_a_claim() -> None:
    """A question can presuppose what it pretends to ask."""
    report = await _report(
        {
            "next_questions": [
                "Why did new customers cause the Electronics decline?",  # causal
                "Is the 7.63pp drop concentrated in one region?",  # states a figure
                "Which categories were most affected?",  # a real question
            ]
        }
    )
    assert report.next_questions == ["Which categories were most affected?"]


async def test_the_fallback_report_obeys_the_same_invariant() -> None:
    """When the reporter fails, the engine still writes only verified text."""
    report = await write_report(
        "Why did Electronics perform poorly?",
        FINDINGS,
        {},
        ["The dataset covers two years only."],
        BrokenReporter(),
    )
    verified = {f.text for f in FINDINGS}
    remaining = report.executive_summary
    for text in sorted(verified, key=len, reverse=True):
        remaining = remaining.replace(text, "")
    assert remaining.strip() == ""
    assert report.key_findings == [f.text for f in FINDINGS]
    assert all(s.heading in KIND_HEADINGS.values() for s in report.sections)


async def test_a_run_with_no_verified_finding_states_that_plainly() -> None:
    report = await write_report("Why?", [], {}, ["nothing worked"], ScriptedReporter({}))
    assert report.key_findings == []
    assert report.sections == []
    assert "No finding survived verification" in report.executive_summary


def test_the_plan_schema_has_no_prose_field() -> None:
    """The structural guarantee behind all of the above.

    A model cannot write a factual sentence into a schema with nowhere to
    put one. This fails if a free-text field is ever added back.
    """
    from agentic_analytics.agents.schemas import ReportPlan, ReportPlanSection

    assert set(ReportPlan.model_fields) == {
        "executive_finding_ids",
        "sections",
        "next_questions",
    }
    assert set(ReportPlanSection.model_fields) == {"finding_ids"}
