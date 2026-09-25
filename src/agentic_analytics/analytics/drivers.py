"""Deterministic change decomposition.

"Revenue fell; what drove it?" is the question an analyst is actually asked,
and it is arithmetic, not language. This module answers it two ways depending
on what kind of metric is involved, and both reconcile exactly to the observed
change -- which is the property that makes a decomposition trustworthy rather
than suggestive.

**Additive metrics** (revenue, units, orders). The total change is the sum of
the per-segment changes, so each segment's contribution is its own change and
the shares sum to 100%.

**Ratio metrics** (gross margin percent, return rate). A weighted average does
not decompose additively, so a shift-share decomposition is used:

    overall change = rate effect + mix effect + interaction

    rate effect  = sum_s  w0_s * (r1_s - r0_s)     within-segment movement
    mix effect   = sum_s  (w1_s - w0_s) * r0_s     weight shifting between segments
    interaction  = sum_s  (w1_s - w0_s) * (r1_s - r0_s)

where ``w`` is a segment's share of the denominator and ``r`` its own rate.
This separates "each segment got worse" from "volume moved toward the worse
segments" -- the distinction the Q3 margin question turns on.

If the components do not reconcile to the observed change within tolerance,
the result is marked ``reconciled: false`` and the caller must not publish it
as a complete explanation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

# A decomposition is accepted as complete only if the components reproduce
# the observed change to within this relative tolerance.
RECONCILE_RTOL = 1e-6
RECONCILE_ATOL = 1e-9

DecompositionKind = Literal["additive", "shift_share"]


@dataclass
class SegmentContribution:
    """One segment's part of the total change."""

    segment: str
    baseline: float
    current: float
    change: float
    contribution_pct: float | None
    #: Populated only for a shift-share decomposition.
    rate_effect: float | None = None
    mix_effect: float | None = None
    interaction: float | None = None

    def as_row(self, kind: DecompositionKind) -> list[Any]:
        if kind == "additive":
            return [
                self.segment,
                round(self.baseline, 6),
                round(self.current, 6),
                round(self.change, 6),
                None if self.contribution_pct is None else round(self.contribution_pct, 4),
            ]
        return [
            self.segment,
            round(self.baseline, 6),
            round(self.current, 6),
            round(self.change, 6),
            None if self.rate_effect is None else round(self.rate_effect, 6),
            None if self.mix_effect is None else round(self.mix_effect, 6),
            None if self.interaction is None else round(self.interaction, 6),
        ]


@dataclass
class Decomposition:
    """The full answer to "what drove this change?"."""

    kind: DecompositionKind
    metric: str
    dimension: str
    baseline_total: float
    current_total: float
    observed_change: float
    explained_change: float
    residual: float
    reconciled: bool
    contributions: list[SegmentContribution] = field(default_factory=list)
    #: Only for shift-share.
    rate_effect_total: float | None = None
    mix_effect_total: float | None = None
    interaction_total: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def columns(self) -> list[str]:
        if self.kind == "additive":
            return ["segment", "baseline", "current", "change", "contribution_pct"]
        return [
            "segment",
            "baseline_rate",
            "current_rate",
            "rate_change",
            "rate_effect",
            "mix_effect",
            "interaction",
        ]

    def rows(self) -> list[list[Any]]:
        return [c.as_row(self.kind) for c in self.contributions]

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "method": self.kind,
            "metric": self.metric,
            "dimension": self.dimension,
            "baseline_total": round(self.baseline_total, 6),
            "current_total": round(self.current_total, 6),
            "observed_change": round(self.observed_change, 6),
            "explained_change": round(self.explained_change, 6),
            "residual": round(self.residual, 9),
            "reconciled": self.reconciled,
        }
        if self.kind == "shift_share":
            payload |= {
                "rate_effect_total": round(self.rate_effect_total or 0.0, 6),
                "mix_effect_total": round(self.mix_effect_total or 0.0, 6),
                "interaction_total": round(self.interaction_total or 0.0, 6),
            }
        if self.warnings:
            payload["warnings"] = self.warnings
        return payload


def _reconciles(observed: float, explained: float) -> bool:
    return math.isclose(observed, explained, rel_tol=RECONCILE_RTOL, abs_tol=RECONCILE_ATOL)


def decompose_additive(
    metric: str,
    dimension: str,
    baseline: dict[str, float],
    current: dict[str, float],
) -> Decomposition:
    """Decompose a summable metric into per-segment contributions."""
    segments = sorted(set(baseline) | set(current))
    contributions: list[SegmentContribution] = []

    baseline_total = float(sum(baseline.values()))
    current_total = float(sum(current.values()))
    observed = current_total - baseline_total

    for segment in segments:
        before = float(baseline.get(segment, 0.0))
        after = float(current.get(segment, 0.0))
        change = after - before
        share = (change / observed * 100.0) if observed != 0 else None
        contributions.append(
            SegmentContribution(
                segment=segment,
                baseline=before,
                current=after,
                change=change,
                contribution_pct=share,
            )
        )

    # Largest mover first: that is the ordering the question implies.
    contributions.sort(key=lambda c: abs(c.change), reverse=True)
    explained = float(sum(c.change for c in contributions))

    warnings: list[str] = []
    appeared = [s for s in segments if s not in baseline]
    vanished = [s for s in segments if s not in current]
    if appeared:
        warnings.append(
            f"{len(appeared)} segment(s) appear only in the later period: "
            + ", ".join(appeared[:5])
        )
    if vanished:
        warnings.append(
            f"{len(vanished)} segment(s) appear only in the earlier period: "
            + ", ".join(vanished[:5])
        )
    if observed == 0:
        warnings.append("the total did not change, so contribution shares are undefined")

    return Decomposition(
        kind="additive",
        metric=metric,
        dimension=dimension,
        baseline_total=baseline_total,
        current_total=current_total,
        observed_change=observed,
        explained_change=explained,
        residual=observed - explained,
        reconciled=_reconciles(observed, explained),
        contributions=contributions,
        warnings=warnings,
    )


def decompose_shift_share(
    metric: str,
    dimension: str,
    baseline: dict[str, tuple[float, float]],
    current: dict[str, tuple[float, float]],
) -> Decomposition:
    """Decompose a ratio metric into rate, mix and interaction effects.

    Each mapping is ``segment -> (numerator, denominator)``. For gross margin
    percent that is (gross profit, revenue); for return rate, (returns, items).
    """
    segments = sorted(set(baseline) | set(current))

    base_den = float(sum(d for _, d in baseline.values()))
    curr_den = float(sum(d for _, d in current.values()))
    base_num = float(sum(n for n, _ in baseline.values()))
    curr_num = float(sum(n for n, _ in current.values()))

    warnings: list[str] = []
    if base_den <= 0 or curr_den <= 0:
        warnings.append("a period has a zero denominator, so the ratio is undefined")
        return Decomposition(
            kind="shift_share",
            metric=metric,
            dimension=dimension,
            baseline_total=0.0,
            current_total=0.0,
            observed_change=0.0,
            explained_change=0.0,
            residual=0.0,
            reconciled=False,
            warnings=warnings,
        )

    baseline_rate = base_num / base_den
    current_rate = curr_num / curr_den
    observed = current_rate - baseline_rate

    contributions: list[SegmentContribution] = []
    rate_total = mix_total = interaction_total = 0.0

    for segment in segments:
        b_num, b_den = baseline.get(segment, (0.0, 0.0))
        c_num, c_den = current.get(segment, (0.0, 0.0))
        w0 = b_den / base_den
        w1 = c_den / curr_den
        # A segment absent from a period has no rate of its own; using the
        # other period's rate would invent movement that did not happen.
        r0 = (b_num / b_den) if b_den > 0 else 0.0
        r1 = (c_num / c_den) if c_den > 0 else 0.0

        rate_effect = w0 * (r1 - r0)
        mix_effect = (w1 - w0) * r0
        interaction = (w1 - w0) * (r1 - r0)

        rate_total += rate_effect
        mix_total += mix_effect
        interaction_total += interaction

        contributions.append(
            SegmentContribution(
                segment=segment,
                baseline=r0,
                current=r1,
                change=r1 - r0,
                contribution_pct=None,
                rate_effect=rate_effect,
                mix_effect=mix_effect,
                interaction=interaction,
            )
        )

    # Order by total influence on the overall rate, not by own rate change:
    # a small segment that moved a lot matters less than a large one that
    # drifted.
    contributions.sort(
        key=lambda c: abs((c.rate_effect or 0) + (c.mix_effect or 0) + (c.interaction or 0)),
        reverse=True,
    )
    explained = rate_total + mix_total + interaction_total

    if not _reconciles(observed, explained):
        warnings.append(
            "the components do not reconcile to the observed change; this "
            "decomposition is not a complete explanation"
        )

    return Decomposition(
        kind="shift_share",
        metric=metric,
        dimension=dimension,
        baseline_total=baseline_rate,
        current_total=current_rate,
        observed_change=observed,
        explained_change=explained,
        residual=observed - explained,
        reconciled=_reconciles(observed, explained),
        contributions=contributions,
        rate_effect_total=rate_total,
        mix_effect_total=mix_total,
        interaction_total=interaction_total,
        warnings=warnings,
    )
