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

#: Every analytical output column with a reader-facing name.
OUTPUT_COLUMN_LABELS: dict[str, str] = {**_SHIFT_SHARE, **_ADDITIVE, **_COMPARISON}


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
