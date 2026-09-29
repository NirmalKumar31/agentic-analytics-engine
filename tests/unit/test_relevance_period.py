"""Whether a finding answers the question, not merely whether it is true.

Three gates, added after a run answered "total revenue in q3 and q2 and
percentage change?" with a revenue move from October to November plus two
breakdowns nobody asked for. Every figure was exact. The metric was the one
asked about. Nothing checked the period, the granularity, or whether a
level had been asked for and a change supplied -- so all three published,
and the report looked like an answer.

That is the worst shape a wrong answer can take, and these are the three
shapes it took.
"""

from __future__ import annotations

import pytest

from agentic_analytics.verification.period import (
    check_period,
    dates_in,
    quarters_named,
    resolve_span,
)


# ------------------------------------------------------------- the parts
def test_dates_are_read_out_of_a_claim() -> None:
    text = "revenue rose from 679,839 in 2025-10-01 to 1,121,124 in 2025-11-01."
    assert dates_in(text) == [(2025, 10), (2025, 11)]


def test_a_claim_with_no_date_reads_as_no_dates() -> None:
    assert dates_in("Apparel has the highest revenue at 2,116,165.") == []


def test_quarters_are_read_with_or_without_a_year() -> None:
    assert quarters_named("Q3 vs Q2") == {2, 3}
    assert quarters_named("2025 Q3 vs 2025 Q2") == {2, 3}
    assert quarters_named("no period here") == set()


def test_a_comparison_scope_spans_both_halves() -> None:
    """A claim about either period is on topic, so the span covers both."""
    assert resolve_span("2025 Q3 vs 2025 Q2") == ("2025-04-01", "2025-09-30")


def test_a_yearless_scope_resolves_to_no_span() -> None:
    assert resolve_span("Q3 vs Q2") is None


# --------------------------------------------------------- the period gate
def test_the_original_failure_is_caught() -> None:
    """The exact claim that shipped as an answer to a Q3-and-Q2 question."""
    verdict = check_period(
        "revenue rose from 679,839 in 2025-10-01 to 1,121,124 in 2025-11-01.",
        "Q3 vs Q2",
    )
    assert not verdict.aligned
    assert "Q4" in verdict.reason
    assert "Q2, Q3" in verdict.reason


def test_a_claim_inside_the_named_quarters_is_aligned() -> None:
    verdict = check_period(
        "revenue rose from 1,412,235 in 2025-04-01 to 1,710,010 in 2025-07-01.",
        "2025 Q3 vs 2025 Q2",
        resolve_span("2025 Q3 vs 2025 Q2"),
    )
    assert verdict.aligned


def test_a_claim_outside_a_resolved_window_is_refused() -> None:
    verdict = check_period(
        "revenue was 500,000 in 2024-02-01.",
        "2025 Q3 vs 2025 Q2",
        resolve_span("2025 Q3 vs 2025 Q2"),
    )
    assert not verdict.aligned
    assert "2024-02" in verdict.reason


def test_a_yearless_quarter_is_still_checkable() -> None:
    """No year means no dates, but Q3 still means months 7 to 9.

    October is Q4 whatever year it is, so the check that matters most --
    the one the original failure needed -- works without a year.
    """
    assert not check_period("revenue in 2019-10-01 was 10.", "Q3").aligned
    assert check_period("revenue in 2019-08-01 was 10.", "Q3").aligned


def test_nothing_is_checked_when_the_question_names_no_period() -> None:
    """Most questions name no period. A gate that rejected on no evidence
    would be worse than no gate."""
    assert check_period("revenue rose from 1 in 2025-10-01 to 2 in 2025-11-01.", None).aligned
    assert check_period("anything at all", "").aligned


def test_a_claim_with_no_dates_cannot_contradict_a_period() -> None:
    """A ranking is about whatever window produced it; the window came from
    the task, not from the sentence."""
    assert check_period("Apparel has the highest revenue.", "2025 Q3").aligned


# ------------------------------------------------- through the real engine
from agentic_analytics.agents.analyst import analyze_question  # noqa: E402
from agentic_analytics.llm.fake import FakeProvider, _answers_question  # noqa: E402

CATALOG = {
    "tables": [
        {"name": "orders", "row_count": 9, "columns": [{"name": "order_date", "type": "DATE"}]}
    ]
}
METRICS = [
    {
        "name": "revenue",
        "description": "Revenue.",
        "valid_dimensions": ["category", "region"],
        "time_field": "order_date",
        "format": "currency",
    }
]


@pytest.mark.parametrize(
    ("question", "claim", "expected", "why"),
    [
        (
            "total revenue in q3 and q2 and percentage change?",
            "revenue rose from 679,839 in 2025-10-01 to 1,121,124 in 2025-11-01.",
            False,
            "wrong quarter",
        ),
        (
            "total revenue in q3 2025 and q2 2025 and percentage change?",
            "revenue rose from 1,412,235 in 2025-04-01 to 1,710,010 in 2025-07-01.",
            True,
            "the periods asked about",
        ),
        (
            "how much revenue did we make in 2025?",
            "Across category, Apparel has the highest revenue at 2,116,165.",
            False,
            "a breakdown for an overall question",
        ),
        (
            "how much revenue did we make in 2025?",
            "revenue rose from 1,710,010 in 2025-07-01 to 3,184,323 in 2025-10-01.",
            False,
            "a change for a level question",
        ),
        (
            "What is the total revenue by region?",
            "Across region, West has the highest revenue at 2,389,883.",
            True,
            "a breakdown was explicitly asked for",
        ),
        (
            "Why did gross margin change in Q3 2025?",
            "gross_margin_pct fell from 40.87% in 2025-04-01 to 33.24% in 2025-07-01.",
            True,
            "a change was asked about",
        ),
    ],
)
async def test_relevance_through_the_scripted_gate(
    question: str, claim: str, expected: bool, why: str
) -> None:
    """The rules together, on the questions that motivated each of them.

    Both directions matter: turning away the answer is as bad as publishing
    the wrong one, and the aggregate rule nearly did exactly that to "total
    revenue by region".
    """
    provider = FakeProvider()
    try:
        brief = await analyze_question(provider, question, CATALOG, METRICS)
    finally:
        await provider.aclose()
    relevant, reason = _answers_question(
        claim,
        ["revenue"] if "revenue" in claim else ["gross_margin_pct"],
        question,
        list(brief.target_metrics),
        list(brief.dimensions),
        brief.time_scope,
    )
    assert relevant is expected, f"{why}: {reason}"
