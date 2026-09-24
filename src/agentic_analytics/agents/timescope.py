"""Turning a stated time scope into a concrete query window.

A question that names "Q3 2025" is not asking for Q3 in isolation: it is
asking what changed relative to the period before it. So a named quarter
widens to its whole year at quarter grain, and the period the question named
is recorded separately as the one to report the change *into*.

Without this the engine answers a different question than the one asked --
it reports the largest movement anywhere in the dataset, which for seasonal
data is almost never the movement the user meant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

Grain = Literal["day", "week", "month", "quarter", "year"]

_QUARTER_YEAR = re.compile(r"\b(?:(20\d{2})\s*[- ]?\s*)?[Qq]([1-4])(?:\s*,?\s*(20\d{2}))?\b")
_MONTH_YEAR = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(20\d{2})\b",
    re.IGNORECASE,
)
_YEAR_ONLY = re.compile(r"\b(20\d{2})\b")

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


@dataclass(frozen=True)
class TimeWindow:
    """A query window plus the period the question actually named."""

    start: str
    end: str
    grain: Grain
    #: First day of the named period, as ``date_trunc(grain, ...)`` returns it.
    focus_period: str | None = None

    def filter_for(self, time_field: str) -> dict[str, Any]:
        return {"column": time_field, "op": "between", "value": [self.start, self.end]}


def parse_time_scope(scope: str | None) -> TimeWindow | None:
    """Parse a scope string into a window, or ``None`` if it names no period.

    Returning ``None`` is the right answer for "Q3" with no year: filtering to
    a guessed year would silently analyse data the user did not ask about.
    """
    if not scope or not scope.strip():
        return None
    text = scope.strip()

    quarter_match = _QUARTER_YEAR.search(text)
    if quarter_match:
        year = quarter_match.group(1) or quarter_match.group(3)
        if year:
            quarter = int(quarter_match.group(2))
            month = 3 * (quarter - 1) + 1
            return TimeWindow(
                start=f"{year}-01-01",
                end=f"{year}-12-31",
                grain="quarter",
                focus_period=f"{year}-{month:02d}-01",
            )
        return None

    month_match = _MONTH_YEAR.search(text)
    if month_match:
        month = _MONTHS[month_match.group(1).lower()[:3]]
        year = month_match.group(2)
        return TimeWindow(
            start=f"{year}-01-01",
            end=f"{year}-12-31",
            grain="month",
            focus_period=f"{year}-{month:02d}-01",
        )

    year_match = _YEAR_ONLY.search(text)
    if year_match:
        year = year_match.group(1)
        return TimeWindow(start=f"{year}-01-01", end=f"{year}-12-31", grain="quarter")

    return None
