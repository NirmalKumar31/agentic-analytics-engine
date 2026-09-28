"""Whether a finding is about the period the question asked about.

The other gates ask whether a claim is *right*. This one asks whether it is
about the right *time*, which is a different question and was the one thing
nothing checked.

The failure it exists to stop: asked for "total revenue in q3 and q2 and
percentage change", a run reported revenue moving from 679,839 in October
to 1,121,124 in November. Both figures were exact, the metric was the one
asked about, and the answer was about the wrong two months. Every gate
passed it, because the relevance check short-circuited to "reports a metric
the question names" and never looked at the dates.

Two levels of strictness, because questions name periods with two levels of
precision:

* **A resolved window** -- "Q3 2025" -- gives real dates, so a finding's
  dates either fall inside it or they do not.
* **A bare quarter** -- "Q3", no year -- names no dates at all, but it does
  name months 7 to 9. A finding dated October is in Q4 whatever year it is,
  so this is still checkable and still worth checking.

Deliberately silent when the question names no period, which is most
questions. A gate that rejected everything whenever it had nothing to go on
would be worse than no gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Dates as the engine renders them in a finding: `2025-07-01`, or a month
#: like `2025-07`. Anything looser is not a date this can reason about.
_DATE = re.compile(r"\b((?:19|20)\d{2})-(0[1-9]|1[0-2])(?:-(\d{2}))?\b")

#: A quarter named in a question, with or without a year.
_QUARTER = re.compile(
    r"\b(?:((?:19|20)\d{2})\s*[- ]?\s*)?[Qq]([1-4])(?:\s*,?\s*((?:19|20)\d{2}))?\b"
)


@dataclass(frozen=True)
class PeriodVerdict:
    """Whether the dates in a claim match the period asked about."""

    aligned: bool
    reason: str = ""


def _quarter_of(month: int) -> int:
    return (month - 1) // 3 + 1


def quarters_named(scope: str) -> set[int]:
    """Quarter numbers a scope names, ignoring the year.

    "Q3 vs Q2" gives {2, 3}. Used when no year is available, where the
    quarter is still the strongest fact on offer.
    """
    return {int(m.group(2)) for m in _QUARTER.finditer(scope or "")}


def dates_in(text: str) -> list[tuple[int, int]]:
    """Every (year, month) pair a claim mentions, in order."""
    return [(int(m.group(1)), int(m.group(2))) for m in _DATE.finditer(text or "")]


def check_period(
    text: str,
    time_scope: str | None,
    window: tuple[str, str] | None = None,
) -> PeriodVerdict:
    """Whether a claim's dates fall in the period the question named.

    `window` is the resolved date range when one exists. `time_scope` is the
    raw phrase, used for the yearless-quarter case where no window can be
    built. Both absent means the question named no period, and nothing is
    checked.
    """
    if not time_scope:
        return PeriodVerdict(True, "The question named no period.")

    mentioned = dates_in(text)
    if not mentioned:
        # A claim carrying no date cannot contradict a period. A ranking
        # across categories is about whatever window produced it, and the
        # window came from the task, not from the sentence.
        return PeriodVerdict(True, "The claim names no date.")

    if window:
        start, end = window
        inside = [d for d in mentioned if start[:7] <= f"{d[0]:04d}-{d[1]:02d}" <= end[:7]]
        if inside:
            return PeriodVerdict(True, f"Dated within {start} to {end}.")
        shown = ", ".join(f"{y:04d}-{m:02d}" for y, m in mentioned[:3])
        return PeriodVerdict(
            False,
            f"The claim is dated {shown}, while the question asks about {start} to {end}.",
        )

    wanted = quarters_named(time_scope)
    if not wanted:
        return PeriodVerdict(True, "The named period could not be compared to a date.")

    got = {_quarter_of(month) for _, month in mentioned}
    if got & wanted:
        return PeriodVerdict(True, f"Dated in Q{', Q'.join(str(q) for q in sorted(got & wanted))}.")
    shown = ", ".join(f"{y:04d}-{m:02d}" for y, m in mentioned[:3])
    return PeriodVerdict(
        False,
        f"The claim is dated {shown}, which falls in "
        f"Q{', Q'.join(str(q) for q in sorted(got))}, while the question asks about "
        f"Q{', Q'.join(str(q) for q in sorted(wanted))}.",
    )


def resolve_span(time_scope: str | None) -> tuple[str, str] | None:
    """The date range a scope resolves to, if it resolves to one.

    A comparison scope spans from the baseline's start to the current
    period's end, because a claim about either half is on topic. A single
    resolved period gives its own window. A scope naming a quarter with no
    year resolves to nothing, and `check_period` falls back to comparing
    quarter numbers.

    Imported here rather than at module scope: `timescope` is agent-layer
    and this module is imported by the provider layer, so a top-level
    import would point the dependency the wrong way.
    """
    if not time_scope:
        return None
    from agentic_analytics.agents.timescope import comparison_window, parse_time_scope

    window = comparison_window(time_scope)
    if window is not None:
        return (window.baseline[0], window.current[1])
    resolved = parse_time_scope(time_scope)
    if resolved is not None and resolved.focus_period:
        return (resolved.start, resolved.end)
    return None


#: Column types that cannot be a breakdown. Anything else can: a
#: categorical column is a dimension whether or not a metric layer has
#: blessed it.
_NON_DIMENSION_TYPES = ("INT", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL")


def dataset_dimensions(catalog: dict[str, Any], metrics: list[dict[str, Any]]) -> set[str]:
    """Every column of this dataset a finding could be sliced by.

    From the dataset in front of us rather than from a global, which is the
    bug this replaces: the relevance rules read their dimension list from
    the demo warehouse's metric registry, loaded once at import. An upload
    has no metric registry, so its own columns were not in that set and the
    rules that depend on it silently never fired -- the checks passed by
    doing nothing, which is the worst way for a check to pass.

    A governed dataset contributes its declared dimensions. An upload
    contributes its categorical columns, since nothing has declared
    anything and a column of strings is a plausible breakdown.
    """
    names: set[str] = set()
    for metric in metrics or []:
        if isinstance(metric, dict):
            for dimension in metric.get("valid_dimensions") or []:
                names.add(str(dimension).lower())
    if names:
        return names
    tables = catalog.get("tables") if isinstance(catalog, dict) else None
    for table in tables or []:
        if not isinstance(table, dict):
            continue
        for column in table.get("columns") or []:
            if not isinstance(column, dict):
                continue
            declared = str(column.get("type", "")).upper()
            if any(kind in declared for kind in _NON_DIMENSION_TYPES):
                continue
            names.add(str(column.get("name", "")).lower())
    return {n for n in names if n}
