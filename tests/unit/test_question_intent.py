"""What the deterministic planner understands a question to be asking.

The home page advertised "Which customer segments are driving the increase in
return rate?" as *"Segmentation with a chi-square test of independence"*, and
the planner classified it as a **correlation**. The branch that tests
`affect|impact|relate|associat|correlat|driv` ran before the one that looks
for a named grouping, so `driving` won and `customer_segment` was never
consulted.

What a reader got for it, in a run that cost real money and is preserved as
evidence: a correlation between `return_rate` and `refund_amount`, a headline
about a refund trend, and the segment comparison that actually answered the
question ranked second behind it.

So intent is pinned here. These are assertions about *classification*, which
is the decision everything downstream inherits -- the tasks dispatched, the
metrics reached for, and which finding becomes the answer.

`test_segment_return_rates_match_an_independent_query` is deliberately not
written against engine output. It computes the same figures from the
warehouse's Parquet with its own SQL, so a change in the metric layer that
silently altered the numbers would fail here rather than agree with itself.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from agentic_analytics.llm.fake import FakeProvider

#: The warehouse's own dimensions, as the analyst role receives them.
DIMENSIONS = [
    "category",
    "brand",
    "region",
    "customer_segment",
    "acquisition_channel",
]
METRICS = ["return_rate", "refund_amount", "revenue", "gross_margin_pct"]

#: The question as it was asked in the run preserved as evidence.
AMBIGUOUS_DRIVER_QUESTION = (
    "Which customer segments are driving the increase in return rate?"
)


def analyse(question: str) -> dict[str, object]:
    provider = FakeProvider()
    return provider._role_question_analyst(
        {"question": question, "dimensions": DIMENSIONS, "metrics": METRICS}
    )


# ----------------------------------------------------------------- intent


def test_a_named_grouping_outranks_a_relationship_verb() -> None:
    """The regression. `driving` must not beat `customer segments`."""
    analysis = analyse(AMBIGUOUS_DRIVER_QUESTION)
    assert analysis["analysis_type"] == "segmentation"
    assert "customer_segment" in analysis["dimensions"]
    assert "return_rate" in analysis["target_metrics"]
    # And not the second metric a correlation reaches for, which is how
    # `refund_amount` came to lead a report about customer segments.
    assert "refund_amount" not in analysis["target_metrics"]


def test_a_relationship_question_with_no_grouping_is_still_a_correlation() -> None:
    """The branch is narrowed, not removed.

    This is the home page's other example, advertised as "a rejected causal
    claim". It names no grouping, so it is still read as a relationship
    question -- and the scripted provider still proposes the causal
    interpretation that the critic then rejects.
    """
    analysis = analyse("Do shipping delays appear to affect repeat purchasing?")
    assert analysis["analysis_type"] == "correlation"


def test_the_advertised_segmentation_example_is_a_segmentation() -> None:
    """A suggested question is a promise about what the engine will do."""
    analysis = analyse(
        "Which customer segment has the highest return rate, and are the "
        "differences statistically significant?"
    )
    assert analysis["analysis_type"] == "segmentation"
    assert analysis["dimensions"] == ["customer_segment"]
    assert analysis["target_metrics"] == ["return_rate"]
    # Unambiguous, so it says nothing about what it had to guess.
    assert analysis["ambiguities"] == []


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Show the monthly trend of revenue", "timeseries"),
        ("What is the total revenue by region?", "segmentation"),
        ("Which region had the highest total revenue?", "segmentation"),
        ("Which acquisition channel has the weakest contribution margin?", "segmentation"),
    ],
)
def test_other_questions_keep_their_classification(question: str, expected: str) -> None:
    """The narrowing must not reclassify anything else."""
    assert analyse(question)["analysis_type"] == expected


# ------------------------------------------------------------- ambiguity


def test_an_underspecified_driver_question_says_what_it_is_missing() -> None:
    """Two readings and no period, both stated rather than chosen.

    The cloud planner detected exactly this and then picked windows anyway:
    its headline covered January to April 2024 while its own decomposition
    used November to December 2025. Naming the gap is the alternative to
    inventing an answer for it.
    """
    notes = " ".join(str(note) for note in analyse(AMBIGUOUS_DRIVER_QUESTION)["ambiguities"])
    assert "baseline or comparison period" in notes
    assert "two readings" in notes
    # Actionable, not merely apologetic.
    assert "October to December 2025" in notes


def test_a_driver_question_with_a_period_does_not_claim_one_is_missing() -> None:
    analysis = analyse(
        "From October to December 2025, which customer segments contributed "
        "most to the increase in return rate?"
    )
    notes = " ".join(str(note) for note in analysis["ambiguities"])
    assert "baseline or comparison period" not in notes


def test_a_metric_named_contribution_is_not_a_driver_question() -> None:
    """`contribution margin` is a metric, not an attribution question.

    The first version of the driver check matched a bare `contribut`, so
    "the weakest contribution margin" was told that "driving" had two
    readings it had never used.
    """
    notes = " ".join(
        str(note)
        for note in analyse(
            "Which acquisition channel has the weakest contribution margin?"
        )["ambiguities"]
    )
    assert "two readings" not in notes


# ------------------------------------------- the figures, computed afresh


def test_segment_return_rates_match_an_independent_query(warehouse_dir: Path) -> None:
    """The same numbers, from the Parquet, with SQL written here.

    `return_rate` is `100.0 * SUM(is_returned) / COUNT(*)` over the order-item
    grain. This rebuilds that join rather than asking the metric layer, so the
    two have to agree for the test to pass -- and a metric definition that
    drifted would be caught by something that does not share its code.
    """
    con = duckdb.connect(":memory:")
    try:
        rows = con.execute(
            """
            SELECT c.customer_segment AS segment,
                   100.0 * SUM(CASE WHEN r.return_id IS NULL THEN 0 ELSE 1 END)
                         / NULLIF(COUNT(*), 0) AS return_rate
            FROM read_parquet(? || '/orders.parquet') o
            JOIN read_parquet(? || '/order_items.parquet') i ON i.order_id = o.order_id
            JOIN read_parquet(? || '/customers.parquet') c ON c.customer_id = o.customer_id
            LEFT JOIN read_parquet(? || '/returns.parquet') r
                   ON r.order_item_id = i.order_item_id
            GROUP BY 1
            ORDER BY 2 DESC
            """,
            [str(warehouse_dir)] * 4,
        ).fetchall()
    finally:
        con.close()

    assert rows, "the warehouse produced no segments"
    segments = {segment for segment, _ in rows}
    assert {"loyal", "new", "returning", "vip"} <= segments

    # Every rate is a percentage, and the ordering is total -- the claim a
    # segmentation answer makes is "this group is highest", so there has to
    # be a highest.
    for segment, rate in rows:
        assert 0.0 <= float(rate) <= 100.0, f"{segment} is not a percentage"
    assert float(rows[0][1]) > float(rows[-1][1]), "no segment is highest"
