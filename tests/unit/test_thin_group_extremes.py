"""An extreme whose own group holds almost nothing.

    "Repeat purchase rate is highest for vip / affiliate / Midwest, at 100%."

That was published. Every part of it was arithmetically true: the cell
held `1.0`, the verifier checked it against the result and passed it, and
the group behind it held two customers.

The cause is structural rather than accidental. A 120-cell cross-tab
divides a population until some cells hold almost nothing, and a
superlative then *selects* for those cells -- a small denominator is what
makes 100% reachable at all. So the extremes of a fine breakdown are
precisely where the thinnest populations collect, and quoting the figure
alone is the one reading of it that is wrong.

What these pin is the shape of the fix as much as the fix:

* the figure is still the maximum, and the group is still named. A
  presentation layer that answered with the second-highest group instead
  would be substituting a result the engine did not compute, which is the
  rule this product does not break even to avoid an awkward number;
* the population is stated beside it, from the result's own `row_count`;
* and a result whose groups are all amply populated says none of this,
  because a warning on every report is a warning nobody reads.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from agentic_analytics.analytics.results import GroupCoverage, ResultSnapshot
from agentic_analytics.presentation import build_presentation
from agentic_analytics.presentation.schemas import PresentationShape
from agentic_analytics.presentation.summarize import (
    THIN_GROUP_ROWS,
    group_population,
    named_extreme_rows,
    named_rows,
    thin_elsewhere,
    thin_extreme_note,
    thin_named,
    thin_note,
)


class Mapping:
    """Only what the presentation builder reads."""

    def __init__(self, **kwargs: Any) -> None:
        self.measure = "repeat_purchase_rate"
        self.dimensions: tuple[str, ...] = ("customer_segment",)
        self.time_grain = None
        self.period = None
        self.operation = "aggregate"
        self.measure_format = "proportion"
        self.filters: tuple[Any, ...] = ()
        self.confident = True
        for key, value in kwargs.items():
            setattr(self, key, value)


def breakdown(populations: list[int], values: list[float] | None = None) -> ResultSnapshot:
    """One cut, one measure, and a population per group."""
    measured = values or [0.5 + 0.01 * index for index in range(len(populations))]
    rows = [
        [f"group-{index}", measured[index], populations[index]] for index in range(len(populations))
    ]
    return ResultSnapshot(
        tool_name="compute_metric",
        columns=["customer_segment", "repeat_purchase_rate", "row_count"],
        rows=rows,
        row_count=len(rows),
        column_lineage={
            "repeat_purchase_rate": {"kind": "aggregate", "aggregate": "AVG", "column": "repeat"}
        },
        group_coverage=GroupCoverage(
            complete=True,
            groups_returned=len(rows),
            groups_total=len(rows),
            rows_matching=sum(populations),
            rows_represented=sum(populations),
        ),
    )


def presentation(snapshot: ResultSnapshot) -> Any:
    return build_presentation(mapping=Mapping(), snapshot=snapshot, chart_decision={})


def thin(
    snapshot: ResultSnapshot,
    shape: PresentationShape | None = None,
    *,
    ascending: bool = False,
) -> Any:
    """The thin groups a breakdown's answer names."""
    return thin_named(
        Mapping(ascending=ascending),
        snapshot,
        "repeat_purchase_rate",
        shape or PresentationShape.CATEGORICAL_BREAKDOWN,
    )


class TestTheFigureIsKeptAndTheGroundIsStated:
    #: The published cross-tab, reduced to the part that matters: a maximum
    #: of 1.0 over two rows, against amply populated rivals.
    THIN = [2, 140, 150, 160]
    VALUES = [1.0, 0.70, 0.62, 0.55]

    def test_the_maximum_is_still_the_maximum(self) -> None:
        """The number is not the defect and is not changed.

        Written as `100%` and now `100.00%`: a declared precision became a
        pin rather than a hint, because a column of means printed `61.08`,
        `62.94`, `59.48` and then `59`. The figure is what this test is
        about and the figure has not moved -- it is still the maximum, and
        still the group that holds it, which is the whole claim.
        """
        result = presentation(breakdown(self.THIN, self.VALUES))
        assert "100.00%" in result.headline
        assert "group-0" in result.headline
        # And it is still *the maximum*, not the runner-up quietly
        # substituted: 70% is the next value down and is not the subject.
        assert "is highest for group-0" in result.headline

    def test_the_population_is_stated_beside_it(self) -> None:
        result = presentation(breakdown(self.THIN, self.VALUES))
        assert "2 of 452 matching rows" in (result.secondary_summary or "")
        assert "reliable estimate" in (result.secondary_summary or "")

    def test_the_reader_is_warned_where_warnings_belong(self) -> None:
        result = presentation(breakdown(self.THIN, self.VALUES))
        codes = [caveat.code for caveat in result.caveats]
        assert "thin_group_extreme" in codes

    def test_the_caveat_says_what_the_floor_is(self) -> None:
        """A threshold a reader cannot see is a threshold they cannot argue
        with."""
        result = presentation(breakdown(self.THIN, self.VALUES))
        caveat = next(c for c in result.caveats if c.code == "thin_group_extreme")
        assert str(THIN_GROUP_ROWS) in caveat.message
        assert "2 rows" in caveat.message

    def test_the_whole_prose_still_passes_the_numeric_guard(self) -> None:
        """Every figure in a published sentence has to be traceable to the
        cited result, and `build_presentation` raises when one is not. The
        population comes from the result's own `row_count` cell and the
        total from recorded coverage, so both resolve -- but this is the
        assertion that catches a future sentence that states a number the
        guard cannot find, because the runner swallows that exception and
        the presentation would silently disappear."""
        result = presentation(breakdown(self.THIN, self.VALUES))
        assert result.headline and result.secondary_summary


class TestAnAmplyPopulatedResultSaysNothing:
    """A warning on every report is a warning nobody reads.

    This is also the Walmart case, which is why it is here: 45 stores at
    143 rows each is a complete, evenly populated breakdown, and its
    extremes need no qualification at all.
    """

    def test_no_note_and_no_caveat(self) -> None:
        result = presentation(breakdown([143] * 6))
        assert "reliable estimate" not in (result.secondary_summary or "")
        assert "thin_group_extreme" not in [caveat.code for caveat in result.caveats]

    def test_the_boundary_is_the_floor_itself(self) -> None:
        """At the floor, nothing is said. One below it, something is."""
        assert thin(breakdown([THIN_GROUP_ROWS, 200])) == []
        assert thin(breakdown([THIN_GROUP_ROWS - 1, 200]))


class TestWhatTheHelpersWillNotClaim:
    def test_a_result_with_no_row_count_is_not_called_thin(self) -> None:
        """Never having counted is not the same as having counted few."""
        snapshot = ResultSnapshot(
            tool_name="compute_metric",
            columns=["customer_segment", "repeat_purchase_rate"],
            rows=[["a", 1.0], ["b", 0.5]],
            row_count=2,
        )
        assert group_population(snapshot, 0) is None
        assert thin(snapshot) == []
        assert thin_extreme_note(snapshot, thin(snapshot)) == ""

    def test_both_extremes_are_named_when_both_are_thin(self) -> None:
        snapshot = breakdown([2, 3])
        note = thin_extreme_note(snapshot, thin(snapshot))
        assert "the highest group" in note.lower()
        assert "the lowest group" in note.lower()
        assert "either figure" in note

    def test_a_single_group_is_not_described_as_an_extreme(self) -> None:
        """With one group there is no highest and no lowest, and calling it
        either would be a claim about a comparison that was not made."""
        assert [role for role, _row, _pop in thin(breakdown([2]))] == ["this group"]

    def test_the_note_never_adds_a_second_semicolon(self) -> None:
        """The presentation contract rejects one, because a semicolon run
        repeating a grouped table is a prose failure it exists to stop --
        and this sentence is appended to a summary that may already have
        one."""
        for populations in ([2, 140], [2, 3], [2]):
            snapshot = breakdown(populations)
            assert ";" not in thin_extreme_note(snapshot, thin(snapshot))

    @pytest.mark.parametrize("shape", ["ordered", "incomplete"])
    def test_it_survives_the_other_secondary_summaries(self, shape: str) -> None:
        """Three different sentences can occupy `secondary_summary` before
        this one is appended, and the longest of them already carries a
        semicolon."""
        snapshot = breakdown([2, 140, 150])
        if shape == "incomplete":
            assert snapshot.group_coverage is not None
            snapshot.group_coverage.complete = False
            snapshot.group_coverage.groups_total = 90
        result = presentation(snapshot)
        assert "reliable estimate" in (result.secondary_summary or "")


class TestItOnlyEverDescribesAGroupTheAnswerNamed:
    """The constraint that made this shape-aware, and it is not cosmetic.

    `_ranking` reports one end of a ranking *because* claiming the other
    was a published falsehood built from a real cell -- "the tenth row is
    the lowest" on a top-ten list. A note that took the maximum and the
    minimum for every shape would have stated the population of a group
    the headline does not mention, reintroducing that by the back door.
    """

    def test_a_ranking_names_only_the_end_it_asked_for(self) -> None:
        # Ordered descending, so row 0 is the top and the thin group is at
        # the far end, which this answer never mentions.
        snapshot = breakdown([200, 180, 2], [0.9, 0.8, 0.1])
        named = named_extreme_rows(
            snapshot, "repeat_purchase_rate", PresentationShape.RANKING, ascending=False
        )
        assert named == [("the highest group", 0)]
        assert thin(snapshot, PresentationShape.RANKING) == []

    def test_an_ascending_ranking_names_its_own_end(self) -> None:
        snapshot = breakdown([2, 180, 200], [0.1, 0.8, 0.9])
        named = named_extreme_rows(
            snapshot, "repeat_purchase_rate", PresentationShape.RANKING, ascending=True
        )
        assert named == [("the lowest group", 0)]
        roles = [
            role for role, _row, _pop in thin(snapshot, PresentationShape.RANKING, ascending=True)
        ]
        assert roles == ["the lowest group"]

    def test_a_time_series_speaks_of_periods_and_not_of_groups(self) -> None:
        """A month with three rows in it is a thin period, and calling it a
        group would describe the result as something it is not."""
        snapshot = breakdown([2, 180, 200], [0.9, 0.5, 0.4])
        roles = [role for role, _row, _pop in thin(snapshot, PresentationShape.TIME_SERIES)]
        assert roles == ["the peak period"]

    @pytest.mark.parametrize(
        "shape", [PresentationShape.STATISTICAL_TEST, PresentationShape.SCALAR]
    )
    def test_the_shapes_that_name_no_extreme_say_nothing(self, shape: PresentationShape) -> None:
        """A test states its own sample sizes and a scalar has one
        population, which the scope line already gives."""
        assert named_extreme_rows(breakdown([2, 3]), "repeat_purchase_rate", shape) == []
        assert thin(breakdown([2, 3]), shape) == []

    def test_the_summary_and_the_caveat_cannot_name_different_groups(self) -> None:
        """Both read `thin_named`. Each working it out for itself is what
        would diverge, and an ascending ranking is where it would."""
        snapshot = breakdown([2, 140, 150, 160], [1.0, 0.7, 0.6, 0.5])
        result = presentation(snapshot)
        stated = thin_named(
            Mapping(),
            snapshot,
            "repeat_purchase_rate",
            PresentationShape.CATEGORICAL_BREAKDOWN,
        )
        smallest = min(population for _role, _row, population in stated)
        caveat = next(c for c in result.caveats if c.code == "thin_group_extreme")
        assert f"{smallest:,} rows" in caveat.message
        assert f"{smallest:,} of" in (result.secondary_summary or "")


class TestTheGroupsTheAnswerDoesNotName:
    """The gap the named-group check left.

    Verified against a published report and its own dataset. The question
    was "the average bedtime phone minutes by age", the arithmetic was
    exact to the cent in all 48 groups, and the answer named age 63 as
    highest on 47 rows and age 55 as lowest on 66 -- both comfortably over
    the floor, so every surface was silent. Age 64 held 29 rows and sat
    0.42 below the named maximum, second in the ranking, and nothing in
    the report mentioned it.

    `thin_of` was asking the right question about the wrong set. The
    figure a reader is handed is not only the one in the headline: on a
    fine breakdown they are shown a *range*, and a range spanning groups
    of 29 rows and 363 is not the like-for-like comparison it looks like.
    """

    #: 48 groups, the named extremes amply populated, one group under the
    #: floor and nowhere near either end of the naming.
    POPULATIONS = [229, 257, 294, 47, 66, 29, 40, 363]
    #: Highest is index 3 (47 rows), lowest is index 4 (66 rows). The thin
    #: group at index 5 is neither.
    VALUES = [0.61, 0.62, 0.59, 0.90, 0.40, 0.88, 0.59, 0.58]

    def snapshot(self) -> ResultSnapshot:
        return breakdown(self.POPULATIONS, self.VALUES)

    def test_the_named_groups_are_not_thin_so_the_old_note_is_silent(self) -> None:
        """Which is why the defect survived: nothing was wrong with it."""
        assert thin(self.snapshot()) == []

    def test_the_unnamed_thin_group_is_disclosed_anyway(self) -> None:
        result = presentation(self.snapshot())
        secondary = result.secondary_summary or ""
        assert "One group in this result covers 29 of" in secondary
        assert "reliable estimate" in secondary

    def test_it_says_how_many_and_how_small_when_there_are_several(self) -> None:
        snapshot = breakdown([229, 29, 47, 66, 12, 18, 257], [0.61, 0.62, 0.9, 0.4, 0.6, 0.6, 0.6])
        secondary = presentation(snapshot).secondary_summary or ""
        assert "3 groups of 7 rest on too few matching rows" in secondary
        assert "the smallest on 12" in secondary

    def test_a_named_thin_group_makes_the_others_other(self) -> None:
        """Two sentences, and the second must not read as the first again."""
        snapshot = breakdown([2, 140, 29, 150], [1.0, 0.70, 0.62, 0.55])
        secondary = presentation(snapshot).secondary_summary or ""
        assert "The highest group covers 2 of" in secondary
        assert "One other group in this result covers 29 of" in secondary

    def test_a_breakdown_with_no_thin_group_says_none_of_it(self) -> None:
        """A warning on every report is a warning nobody reads."""
        snapshot = breakdown([229, 257, 47, 66, 40], [0.61, 0.62, 0.9, 0.4, 0.6])
        secondary = presentation(snapshot).secondary_summary or ""
        assert "fewer than" not in secondary
        assert "reliable estimate" not in secondary

    def test_the_note_still_adds_no_second_semicolon(self) -> None:
        """The presentation contract rejects one, and this appends two
        sentences now rather than one."""
        snapshot = breakdown([2, 140, 29, 150], [1.0, 0.70, 0.62, 0.55])
        result = presentation(snapshot)
        assert (result.secondary_summary or "").count(";") <= 1

    def test_the_caveat_is_its_own_fact_under_its_own_code(self) -> None:
        """A reader who sees both has to be able to tell they are two."""
        codes = [caveat.code for caveat in presentation(self.snapshot()).caveats]
        assert "thin_groups_elsewhere" in codes
        assert "thin_group_extreme" not in codes

        both = [
            caveat.code
            for caveat in presentation(
                breakdown([2, 140, 29, 150], [1.0, 0.70, 0.62, 0.55])
            ).caveats
        ]
        assert "thin_group_extreme" in both
        assert "thin_groups_elsewhere" in both

    def test_a_shape_that_names_no_group_says_nothing(self) -> None:
        """A scalar has one population, already in the scope line, and a
        statistical test states its own sample sizes. A sentence about
        groups nobody named has no referent."""
        snapshot = self.snapshot()
        assert thin_note(snapshot, []) == ("", [])
        for shape in (PresentationShape.STATISTICAL_TEST, PresentationShape.SCALAR):
            named = named_rows(Mapping(), snapshot, "repeat_purchase_rate", shape)
            assert named == []
            assert thin_note(snapshot, named) == ("", [])

    def test_it_never_claims_the_thin_group_changes_the_ranking(self) -> None:
        """The disclosure is a population, not a significance test.

        Age 64 sat 0.42 under the named maximum on 29 rows, and "so the
        highest may not really be the highest" is a claim this engine does
        not make. It says what the group rests on and stops.
        """
        text = presentation(self.snapshot()).secondary_summary or ""
        for claim in ("not significant", "may not", "within the margin", "indistinguishable"):
            assert claim not in text.lower()

    def test_the_thin_set_excludes_exactly_the_named_rows(self) -> None:
        """Row 0 is the named maximum and thin; row 2 is thin and unnamed.

        The two sets partition the thin rows, so no group is both
        disclosed as a named extreme and counted again as an "other".
        """
        snapshot = breakdown([2, 140, 29, 150], [1.0, 0.70, 0.62, 0.55])
        named = named_rows(
            Mapping(), snapshot, "repeat_purchase_rate", PresentationShape.CATEGORICAL_BREAKDOWN
        )
        assert {row for _role, row in named} == {0, 3}
        assert [
            row
            for _role, row, _pop in thin_named(
                Mapping(),
                snapshot,
                "repeat_purchase_rate",
                PresentationShape.CATEGORICAL_BREAKDOWN,
            )
        ] == [0]
        assert thin_elsewhere(snapshot, named) == [(2, 29)]

    def test_the_prose_never_quotes_the_floor_itself(self) -> None:
        """Thirty is a convention of this module, not a measurement.

        `numbers_resolve` refuses a published sentence carrying a number
        the cited result does not hold, and it refused the first version
        of the plural note for saying "fewer than 30 matching rows each".
        That is the guard working: the floor is arguable by design, and a
        figure a reader cannot check against the result has no business in
        the answer. It belongs in the caveat, which is prose about the
        method rather than a claim about the data.
        """
        snapshot = breakdown([229, 29, 47, 66, 12, 18, 257], [0.61, 0.62, 0.9, 0.4, 0.6, 0.6, 0.6])
        result = presentation(snapshot)
        for text in (result.headline, result.secondary_summary or ""):
            assert str(THIN_GROUP_ROWS) not in text
        # And it is stated where it can be: under what to be careful about.
        message = next(
            caveat.message for caveat in result.caveats if caveat.code == "thin_groups_elsewhere"
        )
        assert str(THIN_GROUP_ROWS) in message

    def test_the_count_it_states_is_reported_as_derived(self) -> None:
        """So the sentence passes the guard honestly rather than by
        wording around it. Spelling "three" would have slipped past
        `numbers_resolve`, which reads numerals -- and a figure that
        evades the check is exactly what the check is for."""
        snapshot = breakdown([229, 29, 47, 66, 12, 18, 257], [0.61, 0.62, 0.9, 0.4, 0.6, 0.6, 0.6])
        named = named_rows(
            Mapping(), snapshot, "repeat_purchase_rate", PresentationShape.CATEGORICAL_BREAKDOWN
        )
        _text, figures = thin_note(snapshot, named)
        assert figures == [Decimal(3)]
