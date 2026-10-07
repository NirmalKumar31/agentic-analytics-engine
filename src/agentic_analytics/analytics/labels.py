"""Reader-facing names for the columns the analytics tools produce.

A result's column names are an output contract: `decompose_change` returns
`rate_effect`, `mix_effect` and `interaction` because that is what a
shift-share decomposition computes. Those names are right in the evidence
drawer, where a reader has asked how the figure was reached and the
provenance has to match what was queried.

They are wrong as the primary answer. A live run published a chart titled
"Rate effect by segment" with `rate_effect` down its y-axis, and no reader
outside the team can act on either. "Effect from return-rate differences"
says the same thing and says it to the person reading it.

Declared here rather than derived, and declared beside nothing else: these
are the names the tools emit, so the mapping is part of the same contract.
It is not string manipulation over arbitrary text -- `humanize` already
does the conservative version of that, and "rate effect" is exactly what it
produces. A label a reader can use has to be written by someone who knows
what the column means.

Anything absent falls through to `humanize`. A column this does not know
is still the engine's own name, and inventing a phrase for it would be a
claim about what it measures.
"""

from __future__ import annotations

from enum import StrEnum

#: Shift-share decomposition. The three effects a ratio's change splits
#: into, named for what they say rather than for the arithmetic.
_SHIFT_SHARE = {
    # The groups' own rates moved.
    "rate_effect": "Effect from rate differences",
    # The groups' shares of the whole moved.
    "mix_effect": "Effect from mix of groups",
    # Both moved together, which is neither of the above and not their sum.
    "interaction": "Combined effect",
    "baseline_rate": "Rate at the start",
    "current_rate": "Rate at the end",
    "rate_change": "Change in rate",
}

#: Additive decomposition, which splits a total rather than a ratio.
_ADDITIVE = {
    "baseline": "Value at the start",
    "current": "Value at the end",
    "change": "Change",
    "contribution_pct": "Share of the total change",
}

#: `compare_segments` and `analyze_timeseries` output columns.
_COMPARISON = {
    "share_of_total_pct": "Share of the total",
    "diff_vs_best": "Difference from the highest",
    "rank_desc": "Rank",
    "prev_period": "Previous period",
    "change_abs": "Change",
    "change_pct": "Change as a percentage",
    "row_count": "Rows counted",
}

#: `statistical_test` output columns. `group` is the two group names, and
#: `rate` is the share of each group for which the indicator held.
_STATISTICAL = {
    "group": "Group",
    "n": "Observations",
    "successes": "Matching observations",
    "rate": "Share",
}

#: Every analytical output column with a reader-facing name.
OUTPUT_COLUMN_LABELS: dict[str, str] = {
    **_SHIFT_SHARE,
    **_ADDITIVE,
    **_COMPARISON,
    **_STATISTICAL,
}


def output_label(column: str) -> str | None:
    """The reader-facing name for an analytical output column, if there is one."""
    return OUTPUT_COLUMN_LABELS.get(column)


def column_label(column: str) -> str:
    """A reader-facing name for any column, declared or derived.

    The declared label when there is one; otherwise words separated and
    the first capitalised. Conservative beyond that: it does not expand
    abbreviations, because `pct` becoming "Percentage" on a column that
    turns out not to be one compounds the error, and `Branch_No` becoming
    "Branch Number" is a claim about someone else's naming.

    One function so a chart title, an axis, a table header and a headline
    cannot disagree about what a column is called.
    """
    declared = OUTPUT_COLUMN_LABELS.get(column)
    if declared is not None:
        return declared
    cleaned = " ".join(column.replace("_", " ").replace("-", " ").split())
    if not cleaned:
        return column
    return cleaned[0].upper() + cleaned[1:]


# --------------------------------------------------------------- semantics
#
# A name is half of an output contract. The other half is what the column
# *is*, and for a derived column that is a statement about the column it
# derives from, not about itself.
#
# This was being decided by falling back. `period` is produced by
# `analyze_timeseries` and has no entry in the upload profile, so the role
# lookup found nothing and it became a CATEGORY: the headline said
# "Oct 2025" and the table beside it said "2025-01-01T00:00:00", from the
# same value. `change_abs` on a revenue measure found no unit and printed
# "361,921.95" next to "$1,050,312.91". Neither is a formatting bug at the
# point of render; both are the layer below not knowing what the column is.
#
# Declared here, beside the labels, because it is the same contract: these
# columns are ours, so what they mean is known and can be written down.


class Derivation(StrEnum):
    """What a derived output column is, relative to the measure."""

    #: The measure itself, at another point. `prev_period`, `baseline`.
    #: Same unit, same formatting.
    SAME_AS_MEASURE = "same_as_measure"

    #: A *difference* between two values of the measure.
    #:
    #: Not simply the measure's own unit. A difference of two currency
    #: amounts is currency; a difference of two **rates is percentage
    #: points**, and writing it with a `%` says the wrong thing: a return
    #: rate moving from 8.51% to 7.80% fell by 0.71 percentage points, not
    #: by 0.71%, which would be a relative change of a hundredth of that.
    DELTA_OF_MEASURE = "delta_of_measure"

    #: A proportion the tool computed, always on a 0-100 scale and always a
    #: percentage whatever the measure is. `share_of_total_pct`,
    #: `change_pct`, `contribution_pct`.
    PROPORTION = "proportion"

    #: The time bucket a row aggregates. Formatted as a period at the
    #: grain, never as the stored instant.
    PERIOD = "period"

    #: A whole number of things. No unit, no decimals.
    COUNT = "count"

    #: An ordinal position. A whole number, and never a quantity.
    RANK = "rank"

    #: A proportion on a **0-1** scale, written as a percentage.
    #:
    #: The only column that is one is `rate` from a proportion test, where
    #: it is `successes / n`. That is not inferred from seeing 0.5045:
    #: `stats.py:_require_binary` refuses any value column that is not an
    #: indicator, so the tool's own contract guarantees the range. The
    #: hundred is carried on the field as `scale`, where a reader of the
    #: presentation can see it, rather than applied silently somewhere.
    PROPORTION_0_1 = "proportion_0_1"


#: Every column the analytics tools derive, and what it is.
#:
#: A column absent from this map is not derived as far as this layer knows,
#: and is described from its own evidence as before. Absence is the safe
#: direction: it costs a reader a unit, where a wrong entry would print one
#: that is not true.
DERIVED_COLUMNS: dict[str, Derivation] = {
    # analyze_timeseries
    "period": Derivation.PERIOD,
    "prev_period": Derivation.SAME_AS_MEASURE,
    "change_abs": Derivation.DELTA_OF_MEASURE,
    "change_pct": Derivation.PROPORTION,
    # compare_segments
    "share_of_total_pct": Derivation.PROPORTION,
    "diff_vs_best": Derivation.DELTA_OF_MEASURE,
    "rank_desc": Derivation.RANK,
    "row_count": Derivation.COUNT,
    "value_count": Derivation.COUNT,
    # statistical_test, two-proportion z
    "rate": Derivation.PROPORTION_0_1,
    "successes": Derivation.COUNT,
    "n": Derivation.COUNT,
    # decompose_change, additive
    "baseline": Derivation.SAME_AS_MEASURE,
    "current": Derivation.SAME_AS_MEASURE,
    "change": Derivation.DELTA_OF_MEASURE,
    "contribution_pct": Derivation.PROPORTION,
    # decompose_change, shift-share. The measure being decomposed is the
    # rate, so its own unit carries: a rate's start and end are rates, and
    # the three effects are all differences of rates.
    "baseline_rate": Derivation.SAME_AS_MEASURE,
    "current_rate": Derivation.SAME_AS_MEASURE,
    "rate_change": Derivation.DELTA_OF_MEASURE,
    "rate_effect": Derivation.DELTA_OF_MEASURE,
    "mix_effect": Derivation.DELTA_OF_MEASURE,
    "interaction": Derivation.DELTA_OF_MEASURE,
}

#: Percentage points. The unit of a difference between two percentages.
PERCENTAGE_POINTS = "pp"


def derivation_of(column: str) -> Derivation | None:
    """What this output column is, or None if this layer does not know."""
    return DERIVED_COLUMNS.get(column)


def derived_unit(derivation: Derivation, measure_unit: str | None) -> str | None:
    """The unit a derived column carries, given the measure's.

    Never invents one: a measure with no declared unit yields derived
    columns with no unit, which is the honest answer and the same one this
    layer gave before. The exception is `PROPORTION`, whose columns are
    percentages because our own tools computed them as percentages -- that
    is declared knowledge, not a guess from a name.
    """
    if derivation in (Derivation.PROPORTION, Derivation.PROPORTION_0_1):
        return "%"
    if derivation in (Derivation.COUNT, Derivation.RANK, Derivation.PERIOD):
        return None
    if derivation is Derivation.DELTA_OF_MEASURE:
        # The whole point of the distinction.
        return PERCENTAGE_POINTS if measure_unit == "%" else measure_unit
    return measure_unit
