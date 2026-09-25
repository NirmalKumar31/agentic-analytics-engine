"""Multiple-comparison correction.

A task that runs several related significance tests and reports every p-value
below 0.05 will produce a false positive roughly one time in twenty per test.
Holm's step-down method controls the family-wise error rate without assuming
the tests are independent, which is the right default here: segment
comparisons on the same dataset are usually correlated.

The correction is applied per analytical task, because a task is the unit in
which a worker asks a family of related questions. The publication gate reads
``p_value_adjusted``, so an uncorrected p-value can never be the basis for a
published significance claim.
"""

from __future__ import annotations

from agentic_analytics.analytics.results import StatisticalResult

METHOD = "holm"


def holm_adjust(p_values: list[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values, in the input order.

    Adjusted values are made monotone non-decreasing in rank order, which is
    what makes the step-down procedure coherent: a test cannot be judged more
    significant than one with a smaller raw p-value.
    """
    n = len(p_values)
    if n <= 1:
        return list(p_values)

    ordered = sorted(range(n), key=lambda i: p_values[i])
    adjusted = [0.0] * n
    running = 0.0
    for rank, index in enumerate(ordered):
        value = min(1.0, (n - rank) * p_values[index])
        running = max(running, value)
        adjusted[index] = running
    return adjusted


def apply_family_correction(results: list[StatisticalResult]) -> None:
    """Correct a family of results in place.

    A family of one is left alone: there is nothing to correct, and marking it
    corrected would overstate what was done.
    """
    tests = [r for r in results if r is not None]
    if len(tests) <= 1:
        for result in tests:
            result.family_size = 1
            result.p_value_adjusted = None
            result.correction_method = None
        return

    adjusted = holm_adjust([r.p_value for r in tests])
    for result, value in zip(tests, adjusted, strict=True):
        result.p_value_adjusted = value
        result.correction_method = METHOD
        result.family_size = len(tests)
        if result.p_value < 0.05 <= value:
            result.warnings.append(
                f"significant before correction (p={result.p_value:.3g}) but not "
                f"after adjusting for {len(tests)} related tests "
                f"(Holm-adjusted p={value:.3g})"
            )
