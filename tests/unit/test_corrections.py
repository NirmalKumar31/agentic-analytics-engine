"""Multiple-comparison correction, and that it is actually applied.

An implemented-but-uncalled correction is worse than none: the code reads as
though significance is adjusted while every published claim uses a raw
p-value. These assert the wiring as well as the arithmetic.
"""

from __future__ import annotations

import pytest

from agentic_analytics.analytics.corrections import (
    apply_family_correction,
    apply_family_correction_to_payloads,
    holm_adjust,
)
from agentic_analytics.analytics.results import StatisticalResult


def _result(p: float) -> StatisticalResult:
    return StatisticalResult(test_name="t", statistic=1.0, p_value=p)


def test_holm_matches_the_step_down_definition() -> None:
    raw = [0.001, 0.008, 0.039, 0.041, 0.042, 0.6]
    got = holm_adjust(raw)

    n = len(raw)
    order = sorted(range(n), key=lambda i: raw[i])
    expected = [0.0] * n
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (n - rank) * raw[index]))
        expected[index] = running

    assert got == pytest.approx(expected)


def test_adjusted_values_are_monotone_in_rank() -> None:
    raw = [0.04, 0.001, 0.2, 0.03]
    adjusted = holm_adjust(raw)
    pairs = sorted(zip(raw, adjusted, strict=True))
    values = [a for _, a in pairs]
    assert values == sorted(values), "a smaller raw p must not adjust higher"


def test_adjustment_never_decreases_a_p_value() -> None:
    raw = [0.01, 0.02, 0.5]
    for before, after in zip(raw, holm_adjust(raw), strict=True):
        assert after >= before


def test_a_single_test_is_left_alone() -> None:
    """Marking a family of one as corrected would overstate what was done."""
    assert holm_adjust([0.03]) == [0.03]
    single = [_result(0.03)]
    apply_family_correction(single)
    assert single[0].p_value_adjusted is None
    assert single[0].correction_method is None
    assert single[0].family_size == 1
    assert single[0].effective_p_value == 0.03


def test_a_family_is_corrected_and_records_how() -> None:
    family = [_result(0.001), _result(0.04), _result(0.041)]
    apply_family_correction(family)
    for result in family:
        assert result.correction_method == "holm"
        assert result.family_size == 3
        assert result.p_value_adjusted is not None


def test_borderline_significance_is_withdrawn_by_correction() -> None:
    """Three tests at p about 0.04 are not three discoveries."""
    family = [_result(0.039), _result(0.041), _result(0.042)]
    assert all(r.is_significant for r in family), "significant before correction"
    apply_family_correction(family)
    assert not any(r.is_significant for r in family), "and not after"
    assert all("before correction" in " ".join(r.warnings) for r in family)


def test_a_strong_result_survives_correction() -> None:
    family = [_result(1e-9), _result(0.4), _result(0.6)]
    apply_family_correction(family)
    assert family[0].is_significant
    assert not family[1].is_significant


def test_the_publication_gate_reads_the_adjusted_value() -> None:
    result = _result(0.01)
    assert result.effective_p_value == 0.01
    result.p_value_adjusted = 0.3
    assert result.effective_p_value == 0.3
    assert not result.is_significant


def test_payload_correction_matches_snapshot_correction() -> None:
    """The worker corrects payloads; the graph corrects snapshots.

    They must agree exactly, or the finding text and the provenance drawer
    would disagree about whether something was significant.
    """
    raw = [0.002, 0.03, 0.045, 0.7]
    payloads = [
        {"result_id": f"r{i}", "statistical_result": {"p_value": p}} for i, p in enumerate(raw)
    ]
    snapshots = [_result(p) for p in raw]

    apply_family_correction_to_payloads(payloads)
    apply_family_correction(snapshots)

    for payload, snapshot in zip(payloads, snapshots, strict=True):
        assert payload["statistical_result"]["p_value_adjusted"] == pytest.approx(
            snapshot.p_value_adjusted
        )
        assert payload["statistical_result"]["family_size"] == snapshot.family_size


def test_payload_correction_ignores_results_without_a_test() -> None:
    payloads: list[dict[str, object]] = [
        {"result_id": "a"},
        {"result_id": "b", "statistical_result": None},
        {"result_id": "c", "statistical_result": {"p_value": 0.01}},
    ]
    assert apply_family_correction_to_payloads(payloads) == 1
