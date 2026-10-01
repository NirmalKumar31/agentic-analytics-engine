"""A derived retail fixture: 45 stores, weekly rows, 33 monthly periods.

Synthetic, like the sleep fixture, and for the same reason: the shapes that
broke presentation -- a 45-category complete breakdown and a 33-period
monthly series -- were seen on data that cannot be committed.

The store totals are exact to the cent rather than approximate. Each
store's weekly values are its target total spread evenly with a zero-sum
jitter, and the final week absorbs the rounding residue, so summing the
committed rows reproduces the target exactly. A fixture whose oracle values
only nearly match is a fixture that cannot distinguish a real arithmetic
regression from its own noise.

Distinct from `retail_weekly`, which is a smaller 26-week fixture used by
the shape-refusal tests. Both are kept: they assert different things.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from pathlib import Path

STORES = 45
WEEKS = 143
ROWS = STORES * WEEKS  # 6,435

#: Chosen so that stepping 143 weeks produces exactly 33 distinct calendar
#: months, which is the period count the series tests assert.
START = date(2010, 2, 5)
EXPECTED_PERIODS = 33

#: The two ends of the breakdown, which the oracle tests pin exactly.
HIGHEST_STORE = 20
HIGHEST_TOTAL = 301_397_792.46
LOWEST_STORE = 33
LOWEST_TOTAL = 37_160_221.96


def _store_total(store: int) -> float:
    """One store's total revenue across every week.

    The two named stores take their reported totals. The rest are spread
    deterministically strictly inside that range, so the maximum and the
    minimum are unambiguous and no other store ties either end.
    """
    if store == HIGHEST_STORE:
        return HIGHEST_TOTAL
    if store == LOWEST_STORE:
        return LOWEST_TOTAL
    span = HIGHEST_TOTAL - LOWEST_TOTAL
    # A fixed irrational step walks the interior without repeating.
    position = (store * 0.6180339887) % 1.0
    return round(LOWEST_TOTAL + span * (0.06 + 0.88 * position), 2)


def _weekly(total: float) -> list[float]:
    """`WEEKS` values summing to `total` exactly, with visible spread."""
    share = total / WEEKS
    values: list[float] = []
    for week in range(WEEKS - 1):
        # Seasonal-looking but deterministic: a repeating pattern scaled to
        # the store's own size, so every store has shape without randomness.
        swing = 1.0 + 0.18 * (((week * 7) % 11) - 5) / 5.0
        values.append(round(share * swing, 2))
    # The last week closes the gap, so the committed rows sum to the cent.
    values.append(round(total - sum(values), 2))
    return values


def rows() -> list[list[object]]:
    """The fixture, as rows. No clock and no random source."""
    out: list[list[object]] = []
    for store in range(1, STORES + 1):
        weekly = _weekly(_store_total(store))
        for week in range(WEEKS):
            day = START + timedelta(weeks=week)
            out.append(
                [
                    store,
                    day.strftime("%d-%m-%Y"),
                    weekly[week],
                    1 if (store + week) % 7 == 0 else 0,
                    round(14.0 + 9.0 * (((week * 5) % 13) - 6) / 6.0, 1),
                ]
            )
    return out


HEADER = ["Store", "Trading_Date", "Weekly_Revenue", "Promo_Flag", "Avg_Temp_C"]


def as_csv() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows(rows())
    return buffer.getvalue()


def write_csv(directory: Path, name: str = "retail_monthly.csv") -> Path:
    path = directory / name
    path.write_text(as_csv(), encoding="utf-8")
    return path
