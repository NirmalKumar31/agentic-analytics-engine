"""Change decomposition.

A decomposition is only trustworthy if it adds up. These assert the
reconciliation property directly, and separately that the shift-share split
isolates the two effects it claims to separate.
"""

from __future__ import annotations

import random

import pytest

from agentic_analytics.analytics.drivers import (
    decompose_additive,
    decompose_shift_share,
)


def test_additive_contributions_sum_to_the_observed_change() -> None:
    result = decompose_additive(
        "revenue",
        "category",
        {"Electronics": 100.0, "Apparel": 200.0, "Beauty": 50.0},
        {"Electronics": 180.0, "Apparel": 150.0, "Beauty": 50.0},
    )
    assert result.reconciled
    assert result.observed_change == pytest.approx(30.0)
    assert sum(c.change for c in result.contributions) == pytest.approx(30.0)
    shares = [c.contribution_pct for c in result.contributions if c.contribution_pct]
    assert sum(shares) == pytest.approx(100.0, abs=0.01)
    # Largest mover first: that is the ordering the question implies.
    assert result.contributions[0].segment == "Electronics"


def test_a_pure_mix_shift_shows_no_rate_effect() -> None:
    """Every segment's own rate is unchanged; only the weights move."""
    baseline = {"Electronics": (20.0, 100.0), "Apparel": (60.0, 100.0)}
    current = {"Electronics": (60.0, 300.0), "Apparel": (60.0, 100.0)}
    result = decompose_shift_share("gross_margin", "category", baseline, current)

    assert result.reconciled
    assert result.rate_effect_total == pytest.approx(0.0, abs=1e-12)
    assert result.mix_effect_total == pytest.approx(result.observed_change, abs=1e-12)
    assert result.interaction_total == pytest.approx(0.0, abs=1e-12)


def test_a_pure_rate_change_shows_no_mix_effect() -> None:
    """Weights are unchanged; every segment simply got worse."""
    baseline = {"A": (50.0, 100.0), "B": (30.0, 100.0)}
    current = {"A": (40.0, 100.0), "B": (20.0, 100.0)}
    result = decompose_shift_share("rate", "seg", baseline, current)

    assert result.reconciled
    assert result.mix_effect_total == pytest.approx(0.0, abs=1e-12)
    assert result.rate_effect_total == pytest.approx(result.observed_change, abs=1e-12)


@pytest.mark.parametrize("seed", range(4))
def test_randomised_decompositions_always_reconcile(seed: int) -> None:
    """100 random cases per seed, both methods. 400 cases in total.

    Reconciliation is the property that makes a decomposition an explanation
    rather than a suggestion, so it is asserted on arbitrary input rather
    than on a hand-picked example.
    """
    rng = random.Random(seed)
    for _ in range(100):
        segments = [f"s{i}" for i in range(rng.randint(2, 8))]

        ratio_before = {s: (rng.uniform(0, 500), rng.uniform(1, 1000)) for s in segments}
        ratio_after = {s: (rng.uniform(0, 500), rng.uniform(1, 1000)) for s in segments}
        shift = decompose_shift_share("m", "d", ratio_before, ratio_after)
        assert shift.reconciled, (ratio_before, ratio_after)
        assert shift.explained_change == pytest.approx(shift.observed_change, abs=1e-9)

        add_before = {s: rng.uniform(-100, 100) for s in segments}
        add_after = {s: rng.uniform(-100, 100) for s in segments}
        additive = decompose_additive("m", "d", add_before, add_after)
        assert additive.reconciled
        assert additive.explained_change == pytest.approx(additive.observed_change, abs=1e-9)


def test_a_segment_present_in_only_one_period_is_flagged() -> None:
    result = decompose_additive("revenue", "category", {"A": 100.0}, {"A": 100.0, "B": 50.0})
    assert result.reconciled
    assert any("only in the later period" in w for w in result.warnings)


def test_a_zero_denominator_is_refused_rather_than_divided() -> None:
    result = decompose_shift_share("rate", "seg", {"A": (0.0, 0.0)}, {"A": (1.0, 0.0)})
    assert not result.reconciled
    assert any("zero denominator" in w for w in result.warnings)


def test_no_change_leaves_contribution_shares_undefined() -> None:
    """A share of a zero total is not a number worth reporting."""
    result = decompose_additive("revenue", "d", {"A": 10.0}, {"A": 10.0})
    assert result.observed_change == 0.0
    assert all(c.contribution_pct is None for c in result.contributions)
    assert any("did not change" in w for w in result.warnings)


def test_shift_share_orders_by_influence_not_by_own_rate_change() -> None:
    """A tiny segment that moved a lot matters less than a large one drifting."""
    baseline = {"big": (500.0, 1000.0), "tiny": (5.0, 10.0)}
    current = {"big": (400.0, 1000.0), "tiny": (1.0, 10.0)}
    result = decompose_shift_share("rate", "seg", baseline, current)
    assert result.contributions[0].segment == "big"
    # `tiny` moved further in its own rate.
    by_own_change = sorted(result.contributions, key=lambda c: abs(c.change), reverse=True)
    assert by_own_change[0].segment == "tiny"
