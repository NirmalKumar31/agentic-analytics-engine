"""A fixture with the role hazards that defeated the resolver in production.

Derived, not redistributed. The evidence file is the user's own data and is
not in this repository; what is reproduced is the shape that mattered:

* a numeric measure that is ~98% distinct, so schema inference calls it an
  identifier, and an identifier was not allowed to be a measure, which is
  why "total website_visits by age" summed `age` instead;
* a repeating numeric column the question names as the grouping;
* a second repeating numeric column, so a two-cut request is expressible;
* text dimensions of low cardinality;
* a near-unique text column, which must stay ungroupable because its values
  would travel into a remote prompt as group labels.

Values are generated deterministically so an independent oracle over the
same file is stable. Expected results are computed by DuckDB in the tests,
never restated here.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

ROWS = 1_200
#: Distinct ages, matching the evidence file's 41.
AGE_MIN, AGE_MAX = 20, 60
REGIONS = ("north", "south", "east", "west")
BUSINESS_TYPES = ("Ecommerce", "Manufacturing", "Retail", "Services")
PLANS = ("free", "starter", "growth", "enterprise")
TEAM_SIZES = (1, 2, 3, 5, 8, 13, 21, 34)

HEADER = [
    "account_ref",
    "age",
    "team_size",
    "website_visits",
    "annual_revenue",
    "region",
    "business_type",
    "subscription_plan",
]


def rows() -> list[list[object]]:
    """Deterministic rows. No RNG, so the oracle never drifts."""
    out: list[list[object]] = []
    for index in range(ROWS):
        age = AGE_MIN + (index * 17) % (AGE_MAX - AGE_MIN + 1)
        # Near-unique: a different value on almost every row, which is what
        # makes inference read it as an identifier rather than a measure.
        visits = 1_000 + index * 137 + (index % 7)
        out.append(
            [
                f"acct-{index:05d}",
                age,
                TEAM_SIZES[index % len(TEAM_SIZES)],
                visits,
                round(1_000 + (index * 7_919) % 90_000 + index / 100, 2),
                REGIONS[index % len(REGIONS)],
                BUSINESS_TYPES[(index // 3) % len(BUSINESS_TYPES)],
                PLANS[(index // 5) % len(PLANS)],
            ]
        )
    return out


def as_csv() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADER)
    writer.writerows(rows())
    return buffer.getvalue()


def write_csv(directory: Path, name: str = "saas_accounts.csv") -> Path:
    path = directory / name
    path.write_text(as_csv())
    return path
