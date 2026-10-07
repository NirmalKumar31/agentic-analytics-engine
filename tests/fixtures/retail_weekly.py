"""A committed fixture with the shape that broke the engine in production.

Derived, not redistributed: the real evidence file is third-party data and
is not in this repository. What is reproduced here is every property that
mattered, because each one defeated a different assumption in the tests:

* more than 25 groups: 45 of them, past the old `GROUP_LIMIT`;
* a numeric grouping key whose values recur across many rows, which the
  classifier read as a measure and averaged;
* case-preserving underscore headers, which the result aliaser lowercases;
* a `DD-MM-YYYY` date column, so date handling is not assumed to be ISO;
* a binary flag with both values present;
* a fractional measure large enough that formatted values exceed the old
  400-character claim limit when every group is listed.

Values are generated deterministically, and the expected results are
computed by an independent DuckDB query in the test rather than restated
here as constants -- a hand-written expectation is only ever as good as the
hand that wrote it.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

#: Groups. Past the historic 25-group limit on purpose.
BRANCHES = 45
#: Weeks per branch. Enough that each branch value recurs often.
WEEKS = 26
ROWS = BRANCHES * WEEKS


def rows() -> list[list[object]]:
    """Deterministic rows. No RNG, so the oracle is stable across runs."""
    out: list[list[object]] = []
    for branch in range(1, BRANCHES + 1):
        for week in range(WEEKS):
            # A spread wide enough that branch totals differ clearly, and
            # large enough to exercise thousands separators.
            amount = 100_000 + (branch * 7_919 + week * 1_327) % 900_000
            day = 1 + (week * 13) % 28
            month = 1 + (week * 5) % 12
            year = 2010 + (week % 2)
            out.append(
                [
                    branch,
                    f"{day:02d}-{month:02d}-{year}",
                    round(amount + branch / 100, 2),
                    1 if week % 7 == 0 else 0,
                    round(10 + (branch * 3 + week) % 70 + 0.5, 1),
                ]
            )
    return out


HEADER = ["Branch_No", "Trading_Date", "Weekly_Revenue", "Promo_Flag", "Avg_Temp_C"]


def as_csv() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADER)
    writer.writerows(rows())
    return buffer.getvalue()


def write_csv(directory: Path, name: str = "retail_weekly.csv") -> Path:
    path = directory / name
    path.write_text(as_csv())
    return path
