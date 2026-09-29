"""A finding must answer the question, not merely be correct.

The committed demonstrations carried accurate, off-topic results: a revenue
ranking by acquisition channel under a question about why margin fell, a
month-over-month refund total under a question about which customer segments
drive returns, and channel rankings under a question about shipping delay.
Every number in them verified. None of them answered what was asked.

Relevance is judged separately from arithmetic, so a reader can tell "wrong"
from "not an answer", and it fails closed: a critic that does not say a
finding is relevant has not said it is.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentic_analytics.agents import critic
from agentic_analytics.agents.critic import IRRELEVANT, verify_finding
from agentic_analytics.agents.schemas import CandidateFinding
from agentic_analytics.analytics.results import EvidenceCell, ResultSnapshot
from agentic_analytics.llm.base import LLMError, LLMProvider, LLMRequest
from agentic_analytics.llm.fake import _answers_question

RESULT = ResultSnapshot(
    result_id="res_1",
    tool_name="compare_segments",
    columns=["channel", "revenue"],
    rows=[["affiliate", 1511561.0], ["referral", 832000.0]],
    row_count=2,
)


class ScriptedCritic(LLMProvider):
    name = "scripted"
    requires_credentials = False

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(max_calls=16)
        self.payload = payload
        self.seen: dict[str, Any] = {}

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        self._check_budget(request.role)
        self.usage.record(request.role)
        self.seen = dict(request.context)
        return self.payload


def _finding(text: str = "affiliate has the highest revenue at 1511561.") -> CandidateFinding:
    return CandidateFinding(
        text=text,
        kind="calculated_fact",
        task_id="task_1",
        result_ids=["res_1"],
        evidence_cells=[EvidenceCell(result_id="res_1", row=0, column="revenue")],
    )


async def _verdict(payload: dict[str, Any], **kwargs: Any) -> Any:
    provider = ScriptedCritic(payload)
    verdict, _ = await verify_finding(
        _finding(), {"res_1": RESULT}, provider, question="Why did gross margin fall?", **kwargs
    )
    return verdict, provider


# ------------------------------------------------- the two judgements
async def test_an_accurate_but_off_topic_finding_is_withheld() -> None:
    verdict, _ = await _verdict(
        {
            "status": "supported",
            "reason": "The wording matches the cited result.",
            "answers_question": False,
            "relevance_reason": "A revenue ranking does not explain a margin change.",
        }
    )
    assert verdict.status == "unsupported"
    assert verdict.rule == IRRELEVANT
    # The two judgements stay separable: the evidence did hold.
    assert verdict.evidence_supported is True
    assert verdict.answers_question is False
    assert "margin" in verdict.reason


async def test_a_relevant_supported_finding_publishes() -> None:
    verdict, _ = await _verdict(
        {
            "status": "supported",
            "reason": "Matches the cited result.",
            "answers_question": True,
            "relevance_reason": "Reports the margin change the question asks about.",
        }
    )
    assert verdict.status == "supported"
    assert verdict.rule == "critic"
    assert verdict.evidence_supported is True
    assert verdict.answers_question is True


async def test_relevance_is_not_consulted_when_the_evidence_fails() -> None:
    """A wrong claim is reported as wrong, not as off-topic."""
    verdict, _ = await _verdict(
        {
            "status": "unsupported",
            "reason": "The result does not show that.",
            "answers_question": True,
        }
    )
    assert verdict.status == "unsupported"
    assert verdict.rule == "critic"
    assert verdict.evidence_supported is False


# --------------------------------------------------------- fails closed
async def test_a_critic_that_omits_relevance_does_not_publish() -> None:
    """Silence is not consent."""
    verdict, _ = await _verdict({"status": "supported", "reason": "Matches."})
    assert verdict.status == "unsupported"
    assert verdict.rule == IRRELEVANT
    assert verdict.answers_question is None


async def test_an_unavailable_critic_still_fails_closed() -> None:
    class Broken(LLMProvider):
        name = "broken"
        requires_credentials = False

        async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
            raise LLMError("verifier down")

    verdict, _ = await verify_finding(
        _finding(), {"res_1": RESULT}, Broken(), question="Why did gross margin fall?"
    )
    assert verdict.status != "supported"
    assert verdict.rule == "critic_unavailable"


async def test_the_question_and_objective_reach_the_critic() -> None:
    _, provider = await _verdict(
        {"status": "supported", "reason": "x", "answers_question": True},
        objective="Rank revenue by channel",
    )
    assert provider.seen["question"] == "Why did gross margin fall?"
    assert provider.seen["objective"] == "Rank revenue by channel"


# ------------------------------- the deterministic rule, on the real cases
@pytest.mark.parametrize(
    ("text", "metrics", "question", "targets", "dims", "expected", "why"),
    [
        (
            "Across acquisition_channel, affiliate has the highest revenue at 1511561.",
            ["revenue"],
            "Which customer segments are driving the increase in return rate?",
            ["return_rate", "refund_amount"],
            ["customer_segment"],
            False,
            "a channel revenue ranking does not name customer segments",
        ),
        (
            "refund_amount rose from 49,863 in 2025-10 to 92,372 in 2025-11.",
            ["refund_amount"],
            "Which customer segments are driving the increase in return rate?",
            ["return_rate", "refund_amount"],
            ["customer_segment"],
            True,
            "it reports a metric the question is about",
        ),
        (
            "Across customer_segment, new has the highest return_rate at 9.96%.",
            ["return_rate"],
            "Which customer segments are driving the increase in return rate?",
            ["return_rate"],
            ["customer_segment"],
            True,
            "sliced by exactly the dimension asked about",
        ),
        (
            "late_delivery_rate rose from 8.94 to 14.26.",
            ["late_delivery_rate"],
            "Do shipping delays appear to affect repeat purchasing?",
            ["repeat_purchase_rate", "late_delivery_rate"],
            [],
            True,
            "wording shares no term with the question; the metric does the work",
        ),
    ],
)
def test_the_scripted_relevance_rule_on_the_recorded_cases(
    text: str,
    metrics: list[str],
    question: str,
    targets: list[str],
    dims: list[str],
    expected: bool,
    why: str,
) -> None:
    relevant, reason = _answers_question(text, metrics, question, targets, dims)
    assert relevant is expected, f"{why}: {reason}"
    assert reason


def test_relevance_is_not_assessed_when_no_question_was_recorded() -> None:
    relevant, reason = _answers_question("anything", [], "", [], [])
    assert relevant is True
    assert "not assessed" in reason


def test_the_rejection_rule_is_stable() -> None:
    """Metrics key off the rule, not off wording written for a person."""
    assert IRRELEVANT == "irrelevant_to_question"
    assert critic.IRRELEVANT == IRRELEVANT
