"""Deterministic arithmetic checking.

If a finding says gross margin fell 7.67 percentage points and cites two
cells, the engine recomputes the subtraction and compares. No model is asked
whether the arithmetic is right, because a model is exactly the wrong tool for
that question.

Two checks run:

1. **Stated change.** When a worker declares ``claimed_change``, the value is
   recomputed from the cited cells and compared within a formatting tolerance.
2. **Loose numbers.** Every number in the finding text must either appear in a
   cited result, or be derivable from two cited cells by subtraction, ratio or
   percentage change. A number that matches nothing is unsupported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agentic_analytics.analytics.results import ResultSnapshot

# Numbers that are obviously not measurements: ISO dates, quarter and month
# labels, and bare years. Stripped before extraction so "2025-07-01" does not
# read as the three numbers 2025, 7 and 1.
_DATE_LIKE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{4}-\d{2}\b"
    r"|\b[Qq][1-4]\s*(?:20\d{2})?\b"
    r"|\b20\d{2}\b"
)
_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")

# Statistical boilerplate states a threshold, not a measurement. "significant
# at the 5% level" is not a claim that something equals 5, and treating it as
# one rejects correct findings.
_THRESHOLD = re.compile(
    r"\b\d+(?:\.\d+)?\s*%?\s*"
    r"(?:significance\s+)?(?:level|confidence(?:\s+interval)?)"
    r"|\bconfidence\s+(?:level|interval)\s+of\s+\d+(?:\.\d+)?\s*%?"
    r"|\balpha\s*=\s*\d*\.?\d+",
    re.IGNORECASE,
)

# A number in prose is rounded for display. A match is accepted when it is
# within half of the least significant digit shown, or within 0.5% -- whichever
# is larger -- of a value that actually exists in a result.
REL_TOLERANCE = 0.005
ABS_FLOOR = 0.005


@dataclass
class NumericCheck:
    """One number in a finding and what it was matched against."""

    stated: float
    matched: bool
    source: str = ""
    computed: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stated": self.stated,
            "matched": self.matched,
            "source": self.source,
            "computed": self.computed,
        }


@dataclass
class NumericVerdict:
    """Outcome of checking every number in a finding."""

    ok: bool
    reason: str = ""
    checks: list[NumericCheck] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "checks": [c.as_dict() for c in self.checks],
        }


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(ABS_FLOOR, abs(b) * REL_TOLERANCE)


def extract_numbers(text: str) -> list[float]:
    """Numbers stated in prose, with date-like tokens removed first."""
    cleaned = _THRESHOLD.sub(" ", _DATE_LIKE.sub(" ", text))
    out: list[float] = []
    for match in _NUMBER.finditer(cleaned):
        token = match.group(0).replace(",", "")
        try:
            out.append(float(token))
        except ValueError:
            continue
    return out


def _result_values(results: list[ResultSnapshot]) -> list[tuple[float, str]]:
    """Every numeric cell in every cited result, with a human label."""
    values: list[tuple[float, str]] = []
    for snapshot in results:
        for row_index, row in enumerate(snapshot.rows):
            for col_index, value in enumerate(row):
                if isinstance(value, bool) or not isinstance(value, int | float):
                    continue
                column = (
                    snapshot.columns[col_index]
                    if col_index < len(snapshot.columns)
                    else f"col{col_index}"
                )
                values.append((float(value), f"{snapshot.result_id}[{row_index}].{column}"))
        stat = snapshot.statistical_result
        if stat is not None:
            values.append((float(stat.statistic), f"{snapshot.result_id}.statistic"))
            values.append((float(stat.p_value), f"{snapshot.result_id}.p_value"))
            if stat.effect_size is not None:
                values.append((float(stat.effect_size), f"{snapshot.result_id}.effect_size"))
            if stat.confidence_interval is not None:
                low, high = stat.confidence_interval
                values.append((float(low), f"{snapshot.result_id}.ci_low"))
                values.append((float(high), f"{snapshot.result_id}.ci_high"))
            for name, size in stat.sample_sizes.items():
                values.append((float(size), f"{snapshot.result_id}.n[{name}]"))
        decomposition = snapshot.decomposition
        if decomposition is not None:
            # Totals a decomposition computed: the observed change and the
            # effect split. Engine-computed and part of the result, so a
            # finding may cite them exactly as it cites a test statistic.
            for key, value in decomposition.items():
                if isinstance(value, bool) or not isinstance(value, int | float):
                    continue
                values.append((float(value), f"{snapshot.result_id}.{key}"))
    return values


def _derived_values(cells: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Differences, ratios and percentage changes between cited cells.

    A finding that says "fell 7.67 percentage points" states a number that is
    in no cell; it is the difference of two. Deriving these explicitly is what
    lets the check stay strict about everything else.
    """
    derived: list[tuple[float, str]] = []
    for i, (a, label_a) in enumerate(cells):
        for j, (b, label_b) in enumerate(cells):
            if i == j:
                continue
            derived.append((b - a, f"{label_b} - {label_a}"))
            if a != 0:
                derived.append((100.0 * (b - a) / abs(a), f"pct change {label_a} -> {label_b}"))
                derived.append((b / a, f"ratio {label_b} / {label_a}"))
    return derived


def verify_numbers(
    text: str,
    claimed_change: dict[str, Any] | None,
    evidence_cells: list[tuple[float, str]],
    results: list[ResultSnapshot],
) -> NumericVerdict:
    """Check every number a finding states against the results it cites."""
    available = _result_values(results)
    if not available and extract_numbers(text):
        return NumericVerdict(
            ok=False, reason="The finding states numbers but cites no usable result."
        )

    pool: list[tuple[float, str]] = list(available)
    pool.extend(_derived_values(evidence_cells))
    # Percentages of a total, and the absolute value of any cell, are both
    # legitimate ways for a number to appear in prose.
    pool.extend((abs(v), f"|{label}|") for v, label in available)

    checks: list[NumericCheck] = []
    unmatched: list[float] = []
    for stated in extract_numbers(text):
        match = next(((v, label) for v, label in pool if _close(stated, v)), None)
        if match is None:
            checks.append(NumericCheck(stated=stated, matched=False))
            unmatched.append(stated)
        else:
            value, label = match
            checks.append(NumericCheck(stated=stated, matched=True, source=label, computed=value))

    if claimed_change:
        verdict = _check_claimed_change(claimed_change, checks)
        if verdict is not None:
            return verdict

    if unmatched:
        formatted = ", ".join(f"{n:g}" for n in unmatched[:3])
        return NumericVerdict(
            ok=False,
            reason=(
                f"The value(s) {formatted} do not appear in, and cannot be derived "
                "from, the cited results."
            ),
            checks=checks,
        )
    return NumericVerdict(
        ok=True, reason="Every stated number traces to a cited result.", checks=checks
    )


def _check_claimed_change(
    claimed: dict[str, Any], checks: list[NumericCheck]
) -> NumericVerdict | None:
    """Recompute a declared change. Returns a verdict only on failure."""
    kind = str(claimed.get("type", "difference"))
    try:
        start = float(claimed["from"])
        end = float(claimed["to"])
        stated = float(claimed["stated"])
    except (KeyError, TypeError, ValueError):
        return NumericVerdict(
            ok=False,
            reason="The stated change is malformed and cannot be checked.",
            checks=checks,
        )

    if kind == "percent_change":
        if start == 0:
            return NumericVerdict(
                ok=False,
                reason="A percentage change from zero is undefined.",
                checks=checks,
            )
        computed = 100.0 * (end - start) / abs(start)
    elif kind == "ratio":
        if start == 0:
            return NumericVerdict(
                ok=False,
                reason="A ratio with a zero denominator is undefined.",
                checks=checks,
            )
        computed = end / start
    else:
        computed = end - start

    checks.append(
        NumericCheck(
            stated=stated,
            matched=_close(stated, computed),
            source=f"recomputed {kind} from cited cells",
            computed=computed,
        )
    )
    if not _close(stated, computed):
        return NumericVerdict(
            ok=False,
            reason=(
                f"The stated {kind.replace('_', ' ')} of {stated:g} does not match "
                f"{computed:g} recomputed from the cited cells."
            ),
            checks=checks,
        )
    if (computed > 0) != (stated > 0) and computed != 0 and stated != 0:
        return NumericVerdict(
            ok=False,
            reason="The stated direction is opposite to the cited results.",
            checks=checks,
        )
    return None
