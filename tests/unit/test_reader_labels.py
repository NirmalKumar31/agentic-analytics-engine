"""Analytical output columns, named for the reader.

A live run published a chart titled "Rate effect by segment" with
`rate_effect` down its y-axis. Both are the arithmetic naming itself: a
shift-share decomposition computes a rate effect, a mix effect and an
interaction, and none of those three words tells a reader what moved.

The names are right in the evidence drawer, where a reader has asked how
the figure was reached and the provenance must match what was queried.
They are wrong as the answer.

One source of labels, so a chart title, an axis, a table header and a
headline cannot disagree about what a column is called. These tests pin
that it is one source and that it reaches every surface built from it.
"""

from __future__ import annotations

from agentic_analytics.analytics.charts import _label
from agentic_analytics.analytics.drivers import Decomposition
from agentic_analytics.analytics.labels import OUTPUT_COLUMN_LABELS, column_label
from agentic_analytics.presentation.fields import humanize

#: The three the audit named, and what a reader should see instead.
THE_THREE = {
    "rate_effect": "Effect from rate differences",
    "mix_effect": "Effect from mix of groups",
    "interaction": "Combined effect",
}


def test_the_decomposition_effects_read_as_english() -> None:
    for column, expected in THE_THREE.items():
        assert column_label(column) == expected


def test_no_declared_label_is_an_identifier() -> None:
    """A label containing an underscore would be the defect restated."""
    for column, label in OUTPUT_COLUMN_LABELS.items():
        assert "_" not in label, f"{column}'s label is still an identifier: {label}"
        assert label[:1] == label[:1].upper(), f"{column}'s label is not a sentence"


def test_every_column_a_decomposition_emits_has_a_label() -> None:
    """The contract and the labels are declared together or they drift.

    `DecompositionResult.columns` is the output contract. A column added to
    it without a label would reach a reader as its own name, which is how
    this started.
    """
    for kind in ("additive", "shift_share"):
        result = Decomposition(
            kind=kind,  # type: ignore[arg-type]
            metric="return_rate",
            dimension="customer_segment",
            baseline_total=0.0,
            current_total=0.0,
            observed_change=0.0,
            explained_change=0.0,
            residual=0.0,
            reconciled=True,
        )
        for column in result.columns:
            # `segment` is the dimension's own placeholder name and is
            # replaced by the real dimension before a reader sees it.
            if column == "segment":
                continue
            assert column in OUTPUT_COLUMN_LABELS, (
                f"{kind} emits {column!r} with no reader-facing label"
            )


def test_the_axis_and_the_table_header_agree() -> None:
    """Two surfaces, one source.

    The chart axis and the presentation's display label are produced by
    different modules. Before this they were two string transforms, and a
    reader could be shown "rate effect" on one and `rate_effect` on the
    other.
    """
    for column in THE_THREE:
        assert _label(column) == humanize(column)


def test_an_unknown_column_is_not_renamed() -> None:
    """Conservative where it has no business being otherwise.

    An uploaded file's `branch_no` is someone else's naming. Separating the
    words is a presentation choice; expanding `no` to "number" would be a
    claim about what their column means.
    """
    assert column_label("branch_no") == "Branch no"
    assert column_label("customer_segment") == "Customer segment"
    assert column_label("revenue") == "Revenue"
    assert column_label("") == ""
