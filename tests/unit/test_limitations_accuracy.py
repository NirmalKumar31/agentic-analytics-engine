"""Limitations must describe the answer, not the question.

A report on "the average annual revenue by region for people aged 30 to
40" carried two caveats: that no time range was given, and that no
breakdown was named. The question is not temporal, and it says "by
region". Both statements were false, and both appeared on every uploaded
dataset because the checks that produced them asked nothing about the
question actually being answered.

A caveat a reader cannot act on is worse than none: it trains them to
skip the section where a real limitation would appear.
"""

from __future__ import annotations

import pytest

from agentic_analytics.llm.fake import FakeProvider


def ambiguities(question: str, dimensions: list[str] | None = None) -> list[str]:
    provider = FakeProvider()
    payload = provider._role_question_analyst(
        {
            "question": question,
            "metrics": ["revenue", "orders"],
            "dimensions": dimensions if dimensions is not None else [],
        }
    )
    return list(payload["ambiguities"])


@pytest.mark.parametrize(
    "question",
    [
        "What is the average annual revenue by region for people aged 30 to 40?",
        "average spend per store",
        "total units for each product line",
        "revenue broken down by channel",
        "margin across segments",
    ],
)
def test_a_question_that_names_a_breakdown_is_not_told_it_did_not(question: str) -> None:
    """`dimensions` is drawn from the warehouse's known dimensions, so an
    uploaded column can never appear in it. The check has to read the
    question."""
    assert not any("No breakdown named" in a for a in ambiguities(question))


@pytest.mark.parametrize(
    "question",
    [
        "What is the average annual revenue by region for people aged 30 to 40?",
        "which region has the highest revenue",
        "total revenue by product line",
    ],
)
def test_a_non_temporal_question_is_not_told_it_lacks_a_time_range(question: str) -> None:
    assert not any("time range" in a for a in ambiguities(question))


def test_a_temporal_question_without_a_period_still_says_so() -> None:
    """The caveat is real when the question is about change over time and
    names no period -- that genuinely affects which rows were read."""
    said = ambiguities("how did revenue change over time")
    assert any("time range" in a for a in said), said


def test_a_question_naming_no_breakdown_still_says_so() -> None:
    said = ambiguities("what is the total revenue")
    assert any("No breakdown named" in a for a in said), said


def test_a_quarter_without_a_year_keeps_its_specific_caveat() -> None:
    """The actionable one: it names what to type instead."""
    said = ambiguities("what was revenue in Q3")
    assert any("without a year" in a for a in said), said


def test_the_regression_question_produces_neither_false_caveat() -> None:
    """Both of the strings the released report showed."""
    said = ambiguities("What is the average annual revenue by region for people aged 30 to 40?")
    assert not any("No breakdown named" in a for a in said), said
    assert not any("No explicit time range" in a for a in said), said
