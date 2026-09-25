"""The patterns deliberately injected into the demo warehouse.

A note on wording. The generator *does* cause these patterns -- it writes the
rows. But the analytics system only ever sees the finished table, and nothing
in it supports a causal identification: there is no randomisation, no
instrument, no natural experiment. So the expectations below are phrased as
associations, matching what an agent could legitimately conclude. Where a
sentence describes what the generator does rather than what the data shows,
it is marked as a generator note.

This module is the answer key for the evaluation suite. It is imported by
`generator` (which injects the patterns) and by `evaluation` (which checks
whether the agents found them). It is never imported by any agent, prompt
builder, or MCP tool, and `tests/unit/test_ground_truth_isolation.py`
enforces that.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

# The warehouse covers two full years so that quarter-over-quarter and
# year-over-year comparisons are both available.
PERIOD_START = date(2024, 1, 1)
PERIOD_END = date(2025, 12, 31)

# The window in which margin is deliberately compressed.
MARGIN_EVENT_START = date(2025, 7, 1)
MARGIN_EVENT_END = date(2025, 9, 30)


class InjectedPattern(BaseModel):
    """One known phenomenon, with the assertion the evaluation makes."""

    pattern_id: str
    title: str
    description: str
    # The dimension value the agent is expected to surface, where the
    # pattern names one.
    expected_entity: str | None = None
    expected_direction: str | None = None
    # Terms the evaluation accepts as evidence the pattern was found. Kept
    # here rather than in the evaluation module so the answer key is one file.
    accept_terms: list[str] = []


PATTERNS: list[InjectedPattern] = [
    InjectedPattern(
        pattern_id="q3_margin_compression",
        title="Q3 2025 revenue rises while gross margin falls",
        description=(
            "During 2025-07-01..2025-09-30 the generator raises order discount "
            "rates and shifts unit volume toward the low-margin Electronics "
            "category. Revenue increases against Q2 2025 as unit volume "
            "grows, while gross margin percent falls by several percentage "
            "points. Both a higher discount rate and a mix shift toward a "
            "lower-margin category accompany the decline; a shift-share "
            "decomposition separates the two without needing a causal design."
        ),
        expected_entity="Electronics",
        expected_direction="down",
        accept_terms=["discount", "mix", "electronics", "margin"],
    ),
    InjectedPattern(
        pattern_id="home_kitchen_returns",
        title="Home & Kitchen return rate is elevated, concentrated in new customers",
        description=(
            "Home & Kitchen carries a base return rate several times the "
            "warehouse average, and the excess is concentrated in customers "
            "whose segment is 'new'. The dominant reason code is "
            "'damaged_in_transit'."
        ),
        expected_entity="Home & Kitchen",
        expected_direction="up",
        accept_terms=["home & kitchen", "home and kitchen", "new", "return"],
    ),
    InjectedPattern(
        pattern_id="northeast_carrier_delay",
        title="RapidPost shipments to the Northeast run later from 2025-05",
        description=(
            "From 2025-05-01 the carrier 'RapidPost' shipping to region "
            "'Northeast' has its delivery time distribution shifted later, "
            "producing a materially higher late-delivery rate than other "
            "carrier/region pairs."
        ),
        expected_entity="RapidPost",
        expected_direction="up",
        accept_terms=["rapidpost", "northeast", "delay", "late"],
    ),
    InjectedPattern(
        pattern_id="late_delivery_repeat_association",
        title="Late first delivery is associated with a lower repeat-purchase rate",
        description=(
            "Customers whose first delivery was late repeat at a materially "
            "lower rate than customers whose first delivery was on time. "
            "Generator note: the effect is written directly into the "
            "repeat-purchase draw, so the association is real rather than "
            "spurious. It is still only an association from the data's point "
            "of view -- the generator also makes late delivery more likely in "
            "one region, so region confounds the comparison and a "
            "two-proportion test cannot separate them. An agent concluding "
            "causation here is overstating its evidence, and the verifier is "
            "expected to withhold that claim."
        ),
        expected_direction="down",
        accept_terms=["late", "delay", "repeat", "lower"],
    ),
    InjectedPattern(
        pattern_id="affiliate_weak_contribution",
        title="Affiliate acquisition ranks high on revenue and last on contribution",
        description=(
            "The 'affiliate' acquisition channel produces high revenue per "
            "customer but the worst contribution margin: it carries the "
            "highest discount rate, the highest marketing spend per acquired "
            "customer, and an above-average return rate. A channel ranking on "
            "revenue puts it near the top; a ranking on contribution margin "
            "puts it last."
        ),
        expected_entity="affiliate",
        expected_direction="down",
        accept_terms=["affiliate", "contribution", "margin", "roas"],
    ),
    InjectedPattern(
        pattern_id="q4_seasonality",
        title="Q4 seasonal demand peak",
        description=(
            "November and December carry roughly 1.6x the baseline order "
            "volume in both years, with a smaller July bump."
        ),
        expected_direction="up",
        accept_terms=["season", "q4", "november", "december", "holiday"],
    ),
]

PATTERNS_BY_ID = {p.pattern_id: p for p in PATTERNS}
