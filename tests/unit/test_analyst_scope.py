"""Reading a period out of a question.

A question naming two quarters is asking for a comparison. Reading only
the first one turns it into a question about a single period -- which,
with no year attached, cannot be resolved at all, so the run answered as
though no period had been named and presented unrelated breakdowns as the
answer. Every number in that report was arithmetically correct and none of
them was what had been asked for, which is the worst shape a wrong answer
can take.
"""

from __future__ import annotations

import pytest

from agentic_analytics.agents.analyst import analyze_question
from agentic_analytics.agents.schemas import QuestionAnalysis
from agentic_analytics.agents.timescope import comparison_window, parse_time_scope
from agentic_analytics.llm.fake import FakeProvider

#: The shape the analyst is given. Enough of a catalog and metric list for
#: the scripted provider to resolve names against; the period reading under
#: test does not depend on which dataset it is.
CATALOG = {
    "tables": [
        {
            "name": "orders",
            "row_count": 100,
            "columns": [
                {"name": "order_date", "type": "DATE"},
                {"name": "category", "type": "VARCHAR"},
            ],
        }
    ]
}
METRICS = [
    {
        "name": "revenue",
        "description": "Net merchandise revenue.",
        "valid_dimensions": ["category", "region"],
        "time_field": "order_date",
        "format": "currency",
    }
]


async def _analyse(question: str) -> QuestionAnalysis:
    """The real entry point, so the test exercises the path a run takes."""
    provider = FakeProvider()
    try:
        return await analyze_question(provider, question, CATALOG, METRICS)
    finally:
        await provider.aclose()


async def test_two_quarters_are_read_as_a_comparison() -> None:
    analysis = await _analyse("total revenue in q3 and q2 and percentage change?")
    assert analysis.time_scope == "Q3 vs Q2"


async def test_two_quarters_with_a_year_resolve_to_real_periods() -> None:
    """The case that has to actually work, end to end through the parser."""
    analysis = await _analyse("total revenue in q3 2025 and q2 2025 and percentage change?")
    assert analysis.time_scope == "2025 Q3 vs 2025 Q2"
    periods = comparison_window(analysis.time_scope)
    assert periods is not None
    assert periods.baseline == ("2025-04-01", "2025-06-30")
    assert periods.current == ("2025-07-01", "2025-09-30")


async def test_a_percentage_change_question_is_not_profiling() -> None:
    """Asking for a change and being handed a profile of everything is the
    wrong answer, not a partial one."""
    analysis = await _analyse("total revenue in q3 and q2 and percentage change?")
    assert analysis.analysis_type == "timeseries"


async def test_a_quarter_without_a_year_says_what_to_type() -> None:
    """The refusal has to be actionable.

    Guessing a year would analyse data nobody asked about, so the engine
    does not -- which makes it essential that it says so, and says how to
    fix it, rather than quietly widening to the whole dataset.
    """
    analysis = await _analyse("total revenue in q3 and q2 and percentage change?")
    joined = " ".join(analysis.ambiguities).lower()
    assert "without a year" in joined
    assert "whole dataset" in joined
    assert "2025" in joined, "the hint should show a concrete example"


async def test_a_single_quarter_with_a_year_still_works() -> None:
    analysis = await _analyse("what was revenue in q3 2025?")
    assert analysis.time_scope == "2025 Q3"
    window = parse_time_scope(analysis.time_scope)
    assert window is not None
    assert window.focus_period == "2025-07-01"


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("Q3 2025 vs Q2 2025", ("2025-04-01", "2025-06-30")),
        ("2025 Q3 vs 2025 Q2", ("2025-04-01", "2025-06-30")),
    ],
)
def test_comparison_windows_are_stable_across_phrasings(
    scope: str, expected: tuple[str, str]
) -> None:
    periods = comparison_window(scope)
    assert periods is not None
    assert periods.baseline == expected


def test_a_yearless_quarter_still_resolves_to_nothing() -> None:
    """Unchanged on purpose.

    Resolving "Q3" to a guessed year would silently analyse a period the
    visitor did not ask about. The fix for that case is the ambiguity
    message, not a guess.
    """
    assert parse_time_scope("Q3") is None
    assert comparison_window("Q3 vs Q2") is None
