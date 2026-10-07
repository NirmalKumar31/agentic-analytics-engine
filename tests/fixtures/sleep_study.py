"""A derived sleep fixture, built to reproduce reported aggregates.

This is synthetic. It exists because the shapes that broke presentation --
a two-value flag, three nominal categories and a 48-value ordered numeric
dimension -- were observed on a dataset that cannot be committed. The
numbers below are the aggregates to reproduce, not anyone's data, and
every row here is generated.

The construction is worth explaining because the targets are not
independent. `blue_light_filter_active` and `chronotype` both partition the
same 8,500 rows, so the flag means and the chronotype means constrain the
same values. Those two sets of targets are not exactly simultaneously
satisfiable -- the flag totals imply a grand total of 53,266.12 and the
chronotype totals imply 53,264.45, because each reported figure is itself
a rounded mean. So the fixture targets the rounded values: each group mean
must round to its target at two places, which is what the source figures
actually assert.

Within that, the values are solved rather than fudged: a per-chronotype
base plus a flag offset, with the offsets chosen so the flag means land on
their targets and the chronotype means stay on theirs, and a zero-sum
jitter so the groups have spread without moving any mean.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

ROWS = 8500

#: Flag groups: label, row count, target mean total sleep hours.
FLAG_GROUPS: tuple[tuple[int, int, float], ...] = (
    (0, 4524, 6.21),
    (1, 3976, 6.33),
)

#: Chronotype groups: label, row count, target mean total sleep hours.
CHRONOTYPE_GROUPS: tuple[tuple[str, int, float], ...] = (
    ("Intermediate", 3880, 6.44),
    ("Morning Lark", 2165, 7.38),
    ("Night Owl", 2455, 5.01),
)

#: Ages 18..65 inclusive: 48 ordered groups.
AGE_MIN = 18
AGE_MAX = 65
AGE_GROUPS = AGE_MAX - AGE_MIN + 1

#: Target mean deep sleep percentage at the extremes, and which age holds
#: each. Every other age must fall strictly between them.
DEEP_SLEEP_MAX_AGE = 54
DEEP_SLEEP_MAX = 22.45
DEEP_SLEEP_MIN_AGE = 64
DEEP_SLEEP_MIN = 21.18


def _flag_counts() -> dict[str, int]:
    """Flag=1 rows per chronotype, proportional and summing exactly.

    Proportional allocation keeps each chronotype's mix of flag values the
    same, which is what lets a single flag offset leave the chronotype
    means where they are.
    """
    on_total = next(count for value, count, _ in FLAG_GROUPS if value == 1)
    share = on_total / ROWS
    counts = {name: round(count * share) for name, count, _ in CHRONOTYPE_GROUPS}
    # Settle any rounding residue on the largest group, so the totals match
    # exactly rather than approximately.
    residue = on_total - sum(counts.values())
    largest = max(CHRONOTYPE_GROUPS, key=lambda group: group[1])[0]
    counts[largest] += residue
    return counts


def _solve_offsets() -> tuple[float, float, dict[str, float]]:
    """Flag offsets and per-chronotype bases that satisfy both targets.

    Linear in the unknowns, so a short fixed-point iteration converges and
    stays deterministic. `on` is added to a flagged row and `off`
    subtracted from an unflagged one; each chronotype's base absorbs the
    mix it happens to carry, which is what holds its own mean in place.
    """
    on_count = next(count for value, count, _ in FLAG_GROUPS if value == 1)
    off_count = next(count for value, count, _ in FLAG_GROUPS if value == 0)
    on_target = next(mean for value, _, mean in FLAG_GROUPS if value == 1)
    off_target = next(mean for value, _, mean in FLAG_GROUPS if value == 0)
    per_chronotype_on = _flag_counts()

    on_offset, off_offset = 0.0, 0.0
    bases: dict[str, float] = {}
    for _ in range(64):
        bases = {}
        for name, count, target in CHRONOTYPE_GROUPS:
            flagged = per_chronotype_on[name]
            unflagged = count - flagged
            # base + (flagged*on - unflagged*off)/count == target
            bases[name] = target - (flagged * on_offset - unflagged * off_offset) / count

        flagged_base = sum(
            per_chronotype_on[name] * bases[name] for name, _, _ in CHRONOTYPE_GROUPS
        )
        unflagged_base = sum(
            (count - per_chronotype_on[name]) * bases[name] for name, count, _ in CHRONOTYPE_GROUPS
        )
        on_offset = on_target - flagged_base / on_count
        off_offset = unflagged_base / off_count - off_target
    return on_offset, off_offset, bases


def _spread(total: int, buckets: int) -> list[int]:
    """`total` split across `buckets` as evenly as integers allow."""
    base, extra = divmod(total, buckets)
    return [base + (1 if i < extra else 0) for i in range(buckets)]


def _zero_sum_jitter(count: int, amplitude: float) -> list[float]:
    """Offsets that give a group spread without moving its mean.

    Paired positive and negative, so they cancel exactly. An odd group
    gets one zero rather than an unbalanced remainder.
    """
    out: list[float] = []
    for index in range(count // 2):
        step = amplitude * ((index % 5) + 1) / 5
        out.extend((step, -step))
    if count % 2:
        out.append(0.0)
    return out


def _deep_sleep_target(age: int) -> float:
    """Mean deep sleep percentage for one age.

    The two extremes are fixed by the reported figures; the rest are
    deterministic interior values, kept strictly inside the range so the
    maximum and minimum are unambiguous.
    """
    if age == DEEP_SLEEP_MAX_AGE:
        return DEEP_SLEEP_MAX
    if age == DEEP_SLEEP_MIN_AGE:
        return DEEP_SLEEP_MIN
    span = DEEP_SLEEP_MAX - DEEP_SLEEP_MIN
    # A fixed irrational step spreads the ages through the interior without
    # repeating and without needing a random source.
    position = ((age - AGE_MIN) * 0.6180339887) % 1.0
    return DEEP_SLEEP_MIN + span * (0.08 + 0.84 * position)


def rows() -> list[list[object]]:
    """The fixture, as rows. Deterministic: no clock, no random source."""
    on_offset, off_offset, bases = _solve_offsets()
    per_chronotype_on = _flag_counts()

    # Which chronotype each row belongs to, in blocks.
    chronotype_of: list[str] = []
    for name, count, _ in CHRONOTYPE_GROUPS:
        chronotype_of.extend([name] * count)

    # Flags spread evenly inside each chronotype block, so every group
    # carries the intended proportion rather than a clump.
    flag_of: list[int] = []
    for name, count, _ in CHRONOTYPE_GROUPS:
        flagged = per_chronotype_on[name]
        taken = 0
        for index in range(count):
            want = round((index + 1) * flagged / count)
            if want > taken:
                flag_of.append(1)
                taken += 1
            else:
                flag_of.append(0)

    # Sleep hours: the chronotype base, the flag offset, and a jitter that
    # sums to zero inside each chronotype.
    jitter: list[float] = []
    for _, count, _ in CHRONOTYPE_GROUPS:
        jitter.extend(_zero_sum_jitter(count, 0.45))

    # Ages spread across the 48 groups, and deep sleep built per age with
    # its own zero-sum jitter so each age mean is exact.
    age_counts = _spread(ROWS, AGE_GROUPS)
    age_of: list[int] = []
    deep_of: list[float] = []
    for offset, count in enumerate(age_counts):
        age = AGE_MIN + offset
        age_of.extend([age] * count)
        target = _deep_sleep_target(age)
        deep_of.extend(target + step for step in _zero_sum_jitter(count, 0.30))

    out: list[list[object]] = []
    for index in range(ROWS):
        chronotype = chronotype_of[index]
        flag = flag_of[index]
        hours = bases[chronotype] + (on_offset if flag else -off_offset) + jitter[index]
        out.append(
            [
                f"P{index + 1:05d}",
                age_of[index],
                chronotype,
                flag,
                round(hours, 4),
                round(deep_of[index], 4),
            ]
        )
    return out


HEADER = [
    "participant_id",
    "age",
    "chronotype",
    "blue_light_filter_active",
    "total_sleep_hours",
    "deep_sleep_pct",
]


def as_csv() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows(rows())
    return buffer.getvalue()


def write_csv(directory: Path, name: str = "sleep_study.csv") -> Path:
    path = directory / name
    path.write_text(as_csv(), encoding="utf-8")
    return path
