"""Derived columns keep the semantics of what they derive from.

Each test here corresponds to a defect that was live on the deployed build
and measured on a real run, not to a hypothetical. The scope line under
every headline with a date filter read

    Order date None ['2025-01-01', '2025-12-31']

-- a literal `None` where the operator belongs and a Python list repr of
two stored dates. The period column of every timeseries table read
`2025-01-01T00:00:00` directly beneath a headline that said "Oct 2025",
from the same cell. And `change_abs` on a revenue measure printed
`361,921.95` beside `$1,050,312.91`.

None of them is a bug at the point of render. All three are the layer
below not knowing what the column is.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from agentic_analytics.analytics.labels import (
    DERIVED_COLUMNS,
    PERCENTAGE_POINTS,
    Derivation,
    derivation_of,
    derived_unit,
)
from agentic_analytics.presentation.fields import display_field_for, display_value
from agentic_analytics.presentation.schemas import DisplayField, SemanticKind
from agentic_analytics.presentation.summarize import date_range_label, describe_filter


class _Col:
    """The minimum `display_field_for` reads."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.role = "measure"
        self.data_type = "DOUBLE"
        self.distinct_count = 4
        self.min_value = "0"
        self.max_value = "100"
        self.null_pct = 0.0
        self.reason = ""
        self.additive = "unknown"
        self.ambiguous = False


def field_for(name: str, measure_unit: str | None, grain: str | None = None) -> DisplayField:
    derivation = derivation_of(name)
    assert derivation is not None, f"{name} is not in the derivation map"
    return display_field_for(
        _Col(name),
        derivation=derivation,
        measure_unit=measure_unit,
        time_grain=grain,
    )


# ------------------------------------------------- a date range in words


class TestDateRangeFilterDisplay:
    def test_a_range_filter_names_the_column_and_formats_both_endpoints(self) -> None:
        described = describe_filter({"column": "order_date", "value": ["2025-01-01", "2025-12-31"]})
        assert described == "Order date: Jan 1 - Dec 31, 2025"

    def test_it_publishes_no_none_no_list_and_no_stored_instant(self) -> None:
        described = describe_filter(
            {"column": "order_date", "value": ["2025-01-01T00:00:00", "2025-12-31T00:00:00"]}
        )
        assert "None" not in described
        assert "[" not in described and "]" not in described
        assert "T00:00:00" not in described

    def test_a_window_inside_one_year_states_the_year_once(self) -> None:
        assert date_range_label("2025-01-01", "2025-12-31") == "Jan 1 - Dec 31, 2025"

    def test_a_window_across_two_years_states_both(self) -> None:
        assert date_range_label("2024-06-01", "2025-03-15") == "Jun 1, 2024 - Mar 15, 2025"

    def test_a_tuple_filter_is_read_the_same_way_as_a_dict(self) -> None:
        """Both shapes reach this layer, and the tuple branch was the broken one."""
        assert describe_filter(("order_date", ["2025-01-01", "2025-12-31"])) == describe_filter(
            {"column": "order_date", "value": ["2025-01-01", "2025-12-31"]}
        )

    def test_an_operator_becomes_a_word_rather_than_a_symbol(self) -> None:
        described = describe_filter({"column": "revenue", "operator": ">=", "value": 1000})
        assert described == "Revenue is at least 1000"
        assert ">=" not in described

    def test_a_filter_with_no_column_is_dropped_rather_than_described(self) -> None:
        """The raw predicate is still in the accepted contract in the drawer."""
        assert describe_filter({"operator": "=", "value": "West"}) == ""

    def test_a_filter_with_no_value_names_the_predicate_without_a_none(self) -> None:
        assert "None" not in describe_filter({"column": "region", "operator": "="})


# ------------------------------------------------------ a derived period


class TestTemporalDerivedPeriod:
    def test_period_is_a_time_column_even_though_no_profile_describes_it(self) -> None:
        """It did not exist before the query, so the role lookup found nothing."""
        field = field_for("period", "$", "month")
        assert field.semantic_kind is SemanticKind.TIME

    def test_it_carries_the_grain_so_the_table_does_not_have_to_find_it(self) -> None:
        assert field_for("period", "$", "quarter").time_grain == "quarter"

    def test_a_period_cell_reads_as_a_month_not_as_a_stored_instant(self) -> None:
        field = field_for("period", "$", "month")
        assert display_value("2025-10-01T00:00:00", field) == "Oct 2025"

    def test_the_table_and_the_headline_resolve_one_cell_the_same_way(self) -> None:
        """The parity defect, as one assertion.

        `period_label` is what the headline used; `display_value` is what
        the table now uses. Same cell, same grain, same string.
        """
        from agentic_analytics.presentation.fields import period_label

        field = field_for("period", "$", "month")
        cell = "2025-01-01T00:00:00"
        assert display_value(cell, field) == period_label(cell, "month")

    def test_a_period_takes_no_unit_from_the_measure(self) -> None:
        assert field_for("period", "$", "month").unit is None


# ----------------------------------------------- currency and rate deltas


class TestCurrencyDeltas:
    @pytest.mark.parametrize("column", ["change_abs", "diff_vs_best", "change", "prev_period"])
    def test_a_delta_of_a_currency_measure_is_currency(self, column: str) -> None:
        assert field_for(column, "$").unit == "$"

    def test_and_renders_with_the_symbol_in_front(self) -> None:
        assert display_value(361921.95, field_for("change_abs", "$")) == "$361,921.95"

    def test_a_measure_with_no_declared_unit_yields_deltas_with_none(self) -> None:
        """Never invented. "revenue" does not say which currency."""
        assert field_for("change_abs", None).unit is None
        assert display_value(361921.95, field_for("change_abs", None)) == "361,921.95"


class TestRateDeltasArePercentagePoints:
    """A rate's change is percentage points, and that is not a formatting
    preference.

    A return rate moving from 8.51% to 7.80% fell by 0.71 **percentage
    points**. Writing "0.71%" claims a relative change of a hundredth of
    that. The two readings differ by two orders of magnitude, and a reader
    has no way to tell which was meant.
    """

    @pytest.mark.parametrize(
        "column",
        ["change_abs", "diff_vs_best", "rate_change", "rate_effect", "mix_effect", "interaction"],
    )
    def test_a_delta_of_a_rate_is_percentage_points(self, column: str) -> None:
        assert field_for(column, "%").unit == PERCENTAGE_POINTS

    @pytest.mark.parametrize(
        "column",
        ["change_abs", "diff_vs_best", "rate_change", "rate_effect", "mix_effect", "interaction"],
    )
    def test_and_never_merely_inherits_the_percent_sign(self, column: str) -> None:
        assert field_for(column, "%").unit != "%"

    def test_it_renders_with_a_space_because_0_71pp_reads_as_a_typo(self) -> None:
        assert display_value(0.71, field_for("change_abs", "%")) == "0.71 pp"

    def test_a_proportion_the_tool_computed_is_still_a_percentage(self) -> None:
        """`share_of_total_pct` is a share, not a difference of two rates."""
        for column in ("share_of_total_pct", "change_pct", "contribution_pct"):
            assert field_for(column, "%").unit == "%"
            assert field_for(column, "$").unit == "%", (
                f"{column} is a percentage whatever the measure is"
            )

    def test_the_rule_is_stated_once_rather_than_per_column(self) -> None:
        assert derived_unit(Derivation.DELTA_OF_MEASURE, "%") == PERCENTAGE_POINTS
        assert derived_unit(Derivation.DELTA_OF_MEASURE, "$") == "$"
        assert derived_unit(Derivation.DELTA_OF_MEASURE, None) is None


# ---------------------------------------------------- counts and ordinals


class TestCountsAreWholeNumbers:
    @pytest.mark.parametrize("column", ["row_count", "value_count"])
    def test_a_count_has_no_unit_and_no_decimals(self, column: str) -> None:
        field = field_for(column, "$")
        assert field.unit is None
        assert field.precision == 0

    def test_a_count_stored_as_a_float_is_still_a_whole_number_of_things(self) -> None:
        assert display_value(19471.0, field_for("row_count", "%")) == "19,471"

    def test_a_rank_is_an_ordinal_with_no_unit(self) -> None:
        field = field_for("rank_desc", "$")
        assert field.unit is None
        assert display_value(1, field) == "1"


# ------------------------------------- nothing internal reaches a reader


FORBIDDEN = {
    "a Python None": re.compile(r"\bNone\b"),
    "a list repr": re.compile(r"[\[\]]"),
    "a stored instant": re.compile(r"\d{4}-\d{2}-\d{2}T"),
    "a raw internal key": re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b"),
}


class TestNoInternalFormInAReaderString:
    """Every derived column's label and every rendered cell, swept.

    The four things a reader field may never be, checked over the whole
    derivation map rather than over the columns someone remembered.
    """

    @pytest.mark.parametrize("column", sorted(DERIVED_COLUMNS))
    def test_the_label_is_not_the_engines_own_name(self, column: str) -> None:
        label = field_for(column, "%", "month").display_label
        for description, pattern in FORBIDDEN.items():
            assert not pattern.search(label), f"{column} labelled {label!r} contains {description}"

    @pytest.mark.parametrize("column", sorted(DERIVED_COLUMNS))
    def test_a_rendered_cell_is_not_an_internal_form(self, column: str) -> None:
        field = field_for(column, "%", "month")
        raw = "2025-01-01T00:00:00" if field.semantic_kind is SemanticKind.TIME else 12.345
        rendered = display_value(raw, field)
        for description, pattern in FORBIDDEN.items():
            assert not pattern.search(rendered), (
                f"{column} rendered {rendered!r}, which contains {description}"
            )

    def test_a_missing_cell_is_an_em_dash_and_never_a_word(self) -> None:
        assert display_value(None, field_for("prev_period", "$")) == "—"


# ---------------------------------------------- the wiring, not the rule
#
# Everything above exercises `display_field_for` with a derivation handed
# to it. That leaves the question nothing was asking: does
# `display_fields_for` actually *look the derivation up*?
#
# It did not, and a mutation proved the gap: replacing the lookup in
# `build.py` with `derivation = None` reintroduced the whole defect and
# every test above still passed, because each of them supplies the
# derivation itself. The committed recordings did not catch it either:
# they already hold a correct snapshot, so a change that only affects
# future recordings is invisible to them.


class TestDisplayFieldsForLooksTheDerivationUp:
    """The seam between the map and the result."""

    @staticmethod
    def _snapshot() -> Any:
        from agentic_analytics.analytics.results import ResultSnapshot

        return ResultSnapshot(
            tool_name="analyze_timeseries",
            columns=["period", "revenue", "prev_period", "change_abs", "change_pct"],
            rows=[
                ["2025-01-01T00:00:00", 1050312.91, None, None, None],
                ["2025-04-01T00:00:00", 1412234.86, 1050312.91, 361921.95, 34.46],
            ],
            row_count=2,
        )

    @staticmethod
    def _mapping() -> Any:
        class Mapping:
            measure = "revenue"
            # The contract names the tool's own output column, which is
            # what a timeseries grouped by period actually does.
            dimensions = ("period",)
            filters = ()
            time_grain = "month"
            period = None
            operation = "sum"
            measure_format = "currency"

        return Mapping()

    def _fields(self) -> dict[str, DisplayField]:
        from agentic_analytics.presentation.build import display_fields_for

        return {
            field.source_name: field
            for field in display_fields_for(self._mapping(), self._snapshot())
        }

    def test_the_period_column_comes_back_as_time_with_its_grain(self) -> None:
        field = self._fields()["period"]
        assert field.semantic_kind is SemanticKind.TIME
        assert field.time_grain == "month"

    def test_the_measures_currency_reaches_its_deltas(self) -> None:
        fields = self._fields()
        assert fields["revenue"].unit == "$"
        assert fields["prev_period"].unit == "$"
        assert fields["change_abs"].unit == "$"

    def test_a_proportion_is_a_percentage_beside_a_currency_measure(self) -> None:
        assert self._fields()["change_pct"].unit == "%"

    def test_the_table_reads_as_the_headline_does(self) -> None:
        """End to end over the seam: the cell, the field, the string."""
        fields = self._fields()
        snapshot = self._snapshot()
        assert display_value(snapshot.cell(0, "period"), fields["period"]) == "Jan 2025"
        assert display_value(snapshot.cell(0, "revenue"), fields["revenue"]) == "$1,050,312.91"
        assert display_value(snapshot.cell(1, "change_abs"), fields["change_abs"]) == "$361,921.95"
        assert display_value(snapshot.cell(1, "change_pct"), fields["change_pct"]) == "34.46%"

    def test_a_rate_measures_delta_is_percentage_points_through_the_seam(self) -> None:
        from agentic_analytics.analytics.results import ResultSnapshot
        from agentic_analytics.presentation.build import display_fields_for

        class Mapping:
            measure = "return_rate"
            dimensions = ("customer_segment",)
            filters = ()
            time_grain = None
            period = None
            operation = "average"
            measure_format = "percent"

        snapshot = ResultSnapshot(
            tool_name="compare_segments",
            columns=["customer_segment", "return_rate", "diff_vs_best", "rank_desc"],
            rows=[["new", 9.96, 0.0, 1], ["loyal", 7.66, -2.2976, 4]],
            row_count=2,
        )
        fields = {f.source_name: f for f in display_fields_for(Mapping(), snapshot)}
        assert fields["diff_vs_best"].unit == PERCENTAGE_POINTS
        assert display_value(snapshot.cell(1, "diff_vs_best"), fields["diff_vs_best"]) == "-2.30 pp"
        assert display_value(snapshot.cell(1, "rank_desc"), fields["rank_desc"]) == "4"


class TestAResolvedFilterWritesItsOwnSentence:
    """`row_filters.py` keeps a `describe()` per kind, and it is preferred.

    Filters reach this layer as resolved objects on the path that matters
    most -- a refused or empty run, where the scope line *is* the
    explanation. They fell through the dict and tuple branches, `str(entry)`
    became the column name, and the scope line published

        CategoryFilter(column='region', value='Atlantis', negated=False,
                       source text='where region is Atlantis')

    a dataclass repr, as prose, under a heading that said "No findings to
    publish". Found by a browser test asserting something else.
    """

    def test_a_category_filter_reads_as_a_sentence(self) -> None:
        from agentic_analytics.analytics.row_filters import CategoryFilter

        assert (
            describe_filter(CategoryFilter(column="region", value="Atlantis"))
            == "Region is Atlantis"
        )

    def test_a_negated_filter_says_so(self) -> None:
        from agentic_analytics.analytics.row_filters import CategoryFilter

        described = describe_filter(
            CategoryFilter(column="customer_segment", value="vip", negated=True)
        )
        assert described == "Customer segment is not vip"

    def test_a_null_filter_reads_as_missing(self) -> None:
        from agentic_analytics.analytics.row_filters import NullFilter

        assert describe_filter(NullFilter(column="order_date")) == "Order date is missing"

    def test_a_numeric_filter_names_its_bound_in_words(self) -> None:
        from decimal import Decimal

        from agentic_analytics.analytics.row_filters import RowFilter

        described = describe_filter(
            RowFilter(column="revenue", operator=">=", value=Decimal("1000"))
        )
        assert described == "Revenue at least 1000"
        assert ">=" not in described

    @pytest.mark.parametrize(
        "make",
        [
            lambda: __import__(
                "agentic_analytics.analytics.row_filters", fromlist=["CategoryFilter"]
            ).CategoryFilter(column="region", value="Atlantis"),
            lambda: __import__(
                "agentic_analytics.analytics.row_filters", fromlist=["NullFilter"]
            ).NullFilter(column="order_date", negated=True),
        ],
    )
    def test_no_repr_of_an_object_ever_reaches_a_reader(self, make: Any) -> None:
        described = describe_filter(make())
        assert "(" not in described and ")" not in described
        assert "=" not in described
        for pattern in FORBIDDEN.values():
            assert not pattern.search(described), f"{described!r} contains an internal form"


class TestAnEmptyResultIsNotAnUnsummarisableOne:
    """Two reasons reached one sentence, and it was the wrong one for both.

    An empty result was presented as "The analysis produced a result that
    could not be summarised as an answer", which blames the engine for
    what is almost always a filter that matched nothing, and sends the
    reader looking for a fault in the product rather than at the
    restriction they asked for.

    This was reachable only because `scope_for` raised on a resolved filter
    object and `_presentation_for` swallows every exception by design, so
    every filtered run quietly fell back to the pre-presentation rendering
    path -- where `report.limitations` happened to carry a better sentence.
    Fixing the crash removed the accident, so the explanation had to be
    built properly.
    """

    @staticmethod
    def _presentation(filters: tuple[Any, ...], rows: list[list[Any]]) -> Any:
        from agentic_analytics.analytics.results import ResultSnapshot
        from agentic_analytics.presentation import build_presentation

        class Mapping:
            measure = "revenue"
            dimensions = ("region",)
            time_grain = None
            period = None
            operation = "sum"
            measure_format = "currency"

        Mapping.filters = filters
        return build_presentation(
            mapping=Mapping(),
            snapshot=ResultSnapshot(
                tool_name="aggregate_for_question",
                columns=["region", "revenue"],
                rows=rows,
                row_count=len(rows),
            ),
            findings=[],
            question_coverage=None,
            chart_decision={},
            schema=None,
            planner_fallback=False,
            outcome="completed",
            stopped_reason="",
        )

    def _filtered_empty(self) -> Any:
        from agentic_analytics.analytics.row_filters import CategoryFilter

        return self._presentation((CategoryFilter(column="region", value="Atlantis"),), [])

    def test_building_a_presentation_over_a_resolved_filter_does_not_raise(self) -> None:
        """The crash itself. `scope_for` did `list(entry)` on a dataclass."""
        assert self._filtered_empty() is not None

    def test_the_headline_says_no_rows_matched_rather_than_blaming_the_engine(self) -> None:
        headline = self._filtered_empty().headline
        assert "No rows matched" in headline
        assert "could not be summarised" not in headline

    def test_the_caveat_names_the_restriction_that_emptied_it(self) -> None:
        caveats = self._filtered_empty().caveats
        assert [c.code for c in caveats] == ["no_rows_matched"]
        assert "Region is Atlantis" in caveats[0].message

    def test_the_caveat_and_the_scope_line_agree_about_the_filter(self) -> None:
        """One formatter, so the explanation cannot contradict the scope."""
        presentation = self._filtered_empty()
        assert presentation.scope.filters == ["Region is Atlantis"]
        assert presentation.scope.filters[0] in presentation.caveats[0].message

    def test_an_empty_table_with_no_filter_blames_no_restriction(self) -> None:
        caveats = self._presentation((), []).caveats
        assert "No rows were available" in caveats[0].message

    def test_a_result_with_rows_that_still_cannot_be_summarised_says_so(self) -> None:
        """The other reason keeps its own sentence."""
        presentation = self._presentation((), [["West", None]])
        assert "could not be summarised" in presentation.headline
        assert [c.code for c in presentation.caveats] == ["unsummarisable_result"]

    def test_no_internal_form_reaches_either_sentence(self) -> None:
        presentation = self._filtered_empty()
        for text in [presentation.headline, *(c.message for c in presentation.caveats)]:
            for description, pattern in FORBIDDEN.items():
                assert not pattern.search(text), f"{text!r} contains {description}"
