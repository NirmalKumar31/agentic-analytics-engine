"""The presentation contract, as a pure function.

These build snapshots directly so each shape and each safety rule can be
asserted on its own. The integration tests put the same shapes through the
real upload path; this file is where the rules themselves are pinned.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from agentic_analytics.analytics.results import EvidenceCell, GroupCoverage, ResultSnapshot
from agentic_analytics.presentation import (
    AnalysisPresentation,
    PresentationShape,
    SemanticKind,
    build_presentation,
    display_field_for,
    humanize,
    label_value,
    numbers_resolve,
)
from agentic_analytics.presentation.schemas import (
    DisplayField,
    InterpretationLevel,
    PresentationChart,
    PresentationHighlight,
    PresentationScope,
    PresentationValue,
)
from agentic_analytics.presentation.summarize import scope_for


@dataclass
class Mapping:
    """Only what the presentation layer reads from a contract."""

    operation: str
    dimensions: tuple[str, ...] = ()
    measure: str | None = None
    confident: bool = True
    time_grain: str | None = None
    ascending: bool | None = None
    period: tuple[str, str] | None = None
    filters: tuple[Any, ...] = ()
    named_columns: list[str] = dc_field(default_factory=list)


@dataclass
class Column:
    """An inferred-schema column, as the profiler would report it."""

    name: str
    data_type: str
    role: str
    distinct_count: int = 0
    min_value: str | None = None
    max_value: str | None = None
    null_pct: float = 0.0
    reason: str = ""
    additive: str = "unknown"
    ambiguous: bool = False


@dataclass
class Schema:
    fields: list[Column]


def snapshot(
    columns: list[str],
    rows: list[list[Any]],
    measure: str,
    *,
    source: str | None = None,
    coverage: GroupCoverage | None = None,
    statistical_result: Any = None,
) -> ResultSnapshot:
    """A result shaped like one the engine produces.

    `measure` is the output column; `source` is the dataset column it was
    aggregated from, which is what `column_lineage` actually records.
    """
    return ResultSnapshot(
        tool_name="aggregate_for_question",
        columns=columns,
        rows=rows,
        row_count=len(rows),
        column_lineage={
            measure: {"kind": "aggregate", "aggregate": "SUM", "column": source or measure}
        },
        group_coverage=coverage,
        statistical_result=statistical_result,
    )


def complete(groups: int, rows_total: int) -> GroupCoverage:
    return GroupCoverage(
        complete=True,
        groups_returned=groups,
        groups_total=groups,
        rows_total=rows_total,
        rows_matching=rows_total,
        rows_represented=rows_total,
    )


# ───────────────────────────────────── display field safety


class TestBooleanSafety:
    """A two-valued column is not therefore a flag.

    This is the rule that stops a store table with two stores being
    relabelled "Off" and "On", and it is why the gate is the values present
    rather than how many there are.
    """

    def test_a_zero_one_integer_flag_is_boolean(self) -> None:
        field = display_field_for(
            Column("blue_light_filter_active", "BIGINT", "dimension", 2, "0", "1")
        )
        assert field.semantic_kind is SemanticKind.BOOLEAN
        assert field.boolean_labels == {"0": "Off", "1": "On", "false": "Off", "true": "On"}

    def test_a_declared_boolean_is_boolean(self) -> None:
        field = display_field_for(Column("is_member", "BOOLEAN", "dimension", 2, "false", "true"))
        assert field.semantic_kind is SemanticKind.BOOLEAN

    def test_two_arbitrary_values_are_not_a_boolean(self) -> None:
        # The defect this prevents: a numeric column with two values is a
        # two-group breakdown, not a flag, and must keep its own values.
        field = display_field_for(Column("store", "BIGINT", "dimension", 2, "20", "33"))
        assert field.semantic_kind is not SemanticKind.BOOLEAN
        assert field.boolean_labels is None

    def test_observed_values_override_a_stale_profile(self) -> None:
        field = display_field_for(
            Column("code", "BIGINT", "dimension", 2, "0", "1"),
            observed_values={"4", "9"},
        )
        assert field.boolean_labels is None

    def test_three_valued_zero_one_column_is_not_boolean(self) -> None:
        field = display_field_for(Column("flagish", "BIGINT", "dimension", 3, "0", "2"))
        assert field.boolean_labels is None

    def test_a_flag_value_reads_as_its_state(self) -> None:
        field = display_field_for(
            Column("blue_light_filter_active", "BIGINT", "dimension", 2, "0", "1")
        )
        assert label_value(1, field) == "On"
        assert label_value(0, field) == "Off"
        assert label_value(1.0, field) == "On"

    def test_a_non_boolean_value_is_shown_as_stored(self) -> None:
        field = display_field_for(Column("region", "VARCHAR", "dimension", 4))
        assert label_value("North", field) == "North"

    def test_labels_on_a_non_boolean_field_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not a boolean"):
            DisplayField(
                source_name="store",
                display_label="Store",
                semantic_kind=SemanticKind.CATEGORY,
                boolean_labels={"0": "Off", "1": "On"},
            )


class TestUnitSafety:
    def test_a_percentage_needs_a_name_and_a_compatible_range(self) -> None:
        field = display_field_for(Column("deep_sleep_pct", "DOUBLE", "measure", 900, "4.1", "48.9"))
        assert field.unit == "%"

    def test_a_ratio_named_rate_is_not_a_percentage(self) -> None:
        # 0.03 formatted as 3% would multiply the reader's understanding.
        field = display_field_for(Column("churn_rate", "DOUBLE", "measure", 90, "0.01", "0.09"))
        assert field.unit is None

    def test_a_percent_name_outside_the_range_is_not_a_percentage(self) -> None:
        field = display_field_for(Column("pct_id", "BIGINT", "measure", 900, "0", "90210"))
        assert field.unit is None

    def test_currency_is_never_inferred_from_a_name(self) -> None:
        # "revenue" does not say which currency, and a wrong unit is worse
        # than none.
        field = display_field_for(Column("annual_revenue", "DOUBLE", "measure", 900, "1", "9e6"))
        assert field.unit is None

    def test_a_declared_unit_is_carried_through(self) -> None:
        field = display_field_for(Column("total", "DOUBLE", "measure", 9), unit="USD")
        assert field.unit == "USD"


class TestOrdering:
    def test_a_numeric_grouping_is_ordered(self) -> None:
        # The defect: 48 ages plotted on a categorical axis.
        field = display_field_for(Column("age", "BIGINT", "dimension", 48, "18", "65"))
        assert field.ordered
        assert field.semantic_kind is SemanticKind.ORDERED_NUMERIC

    def test_a_text_category_is_not_ordered(self) -> None:
        field = display_field_for(Column("chronotype", "VARCHAR", "dimension", 3))
        assert not field.ordered

    def test_a_flag_is_not_ordered(self) -> None:
        field = display_field_for(Column("promo_flag", "BIGINT", "dimension", 2, "0", "1"))
        assert not field.ordered


def test_humanize_separates_words_without_inventing_them() -> None:
    assert humanize("total_sleep_hours") == "Total sleep hours"
    assert humanize("Branch_No") == "Branch No"
    # It must not expand an abbreviation it cannot verify.
    assert "Number" not in humanize("Branch_No")


# ───────────────────────────────────── shapes


def test_a_single_figure_is_a_scalar() -> None:
    result = snapshot(["total_revenue", "row_count"], [[1234.5, 10]], "total_revenue")
    presentation = build_presentation(mapping=Mapping("sum", (), "revenue"), snapshot=result)
    assert presentation.shape is PresentationShape.SCALAR
    assert "1,234.50" in presentation.headline
    assert presentation.secondary_summary is not None
    assert "10" in presentation.secondary_summary


def test_a_flag_breakdown_is_a_boolean_comparison() -> None:
    result = snapshot(
        ["blue_light_filter_active", "avg_total_sleep_hours", "row_count"],
        [[0, 6.21, 4524], [1, 6.33, 3976]],
        "avg_total_sleep_hours",
        coverage=complete(2, 8500),
    )
    schema = Schema(
        [
            Column("blue_light_filter_active", "BIGINT", "dimension", 2, "0", "1"),
            Column("total_sleep_hours", "DOUBLE", "measure", 900, "3.1", "9.8"),
        ]
    )
    presentation = build_presentation(
        mapping=Mapping("average", ("blue_light_filter_active",), "total_sleep_hours"),
        snapshot=result,
        schema=schema,
    )
    assert presentation.shape is PresentationShape.BOOLEAN_COMPARISON
    # Both states named, neither printed as a bare 0 or 1.
    assert "On" in presentation.headline and "Off" in presentation.headline
    assert "6.33" in presentation.headline and "6.21" in presentation.headline
    # The descriptive difference, and no claim about significance.
    highlight = presentation.highlights[0]
    assert highlight.delta is not None
    assert highlight.delta.formatted_value == "0.12"
    assert highlight.interpretation_level is InterpretationLevel.DERIVED
    assert "descriptive" in (presentation.secondary_summary or "").lower()


def test_a_boolean_comparison_never_claims_significance() -> None:
    result = snapshot(
        ["flag", "avg_x", "row_count"],
        [[0, 1.0, 5], [1, 9.0, 5]],
        "avg_x",
        coverage=complete(2, 10),
    )
    schema = Schema(
        [Column("flag", "BIGINT", "dimension", 2, "0", "1"), Column("x", "DOUBLE", "measure", 9)]
    )
    presentation = build_presentation(
        mapping=Mapping("average", ("flag",), "x"), snapshot=result, schema=schema
    )
    text = f"{presentation.headline} {presentation.secondary_summary}".lower()
    for forbidden in ("significant", "significantly", "causes", "caused", "because of", "due to"):
        assert forbidden not in text, f"the summary claims {forbidden!r} with no test run"


def test_three_categories_name_the_ends_and_point_at_the_table() -> None:
    result = snapshot(
        ["chronotype", "avg_total_sleep_hours", "row_count"],
        [["Intermediate", 6.44, 3880], ["Morning Lark", 7.38, 2165], ["Night Owl", 5.01, 2455]],
        "avg_total_sleep_hours",
        coverage=complete(3, 8500),
    )
    presentation = build_presentation(
        mapping=Mapping("average", ("chronotype",), "total_sleep_hours"), snapshot=result
    )
    assert presentation.shape is PresentationShape.CATEGORICAL_BREAKDOWN
    assert "Morning Lark" in presentation.headline and "7.38" in presentation.headline
    assert "Night Owl" in presentation.headline and "5.01" in presentation.headline
    assert "table" in (presentation.secondary_summary or "").lower()
    # The failure this replaces.
    assert presentation.headline.count(";") <= 1


def test_an_ordered_numeric_dimension_states_a_range_not_a_trend() -> None:
    rows = [[age, 21.18 + (age % 7) * 0.1, 177] for age in range(18, 66)]
    rows[36][1] = 22.45  # age 54
    rows[46][1] = 21.18  # age 64
    result = snapshot(
        ["age", "avg_deep_sleep_pct", "row_count"],
        rows,
        "avg_deep_sleep_pct",
        coverage=complete(48, 8500),
    )
    schema = Schema(
        [
            Column("age", "BIGINT", "dimension", 48, "18", "65"),
            Column("deep_sleep_pct", "DOUBLE", "measure", 900, "4.1", "48.9"),
        ]
    )
    presentation = build_presentation(
        mapping=Mapping("average", ("age",), "deep_sleep_pct"), snapshot=result, schema=schema
    )
    assert presentation.shape is PresentationShape.ORDERED_NUMERIC_SERIES
    # Same sentence shape as any complete breakdown: the engine cannot tell
    # an ordered quantity from an entity key, so it does not phrase them
    # differently. What the order buys is a numeric chart axis.
    assert "highest" in presentation.headline.lower()
    assert "22.45" in presentation.headline and "21.18" in presentation.headline
    assert "range from" in (presentation.secondary_summary or "").lower()
    assert "descriptive" in (presentation.secondary_summary or "").lower()
    assert "no trend" in (presentation.secondary_summary or "").lower()


def test_a_ranking_claims_only_the_end_it_was_asked_for() -> None:
    # A top-N list says nothing about the global minimum. Claiming the last
    # returned row was "the lowest" was a published falsehood.
    result = snapshot(
        ["store", "total_weekly_revenue"],
        [[20, 301397792.46], [19, 290000000.0], [4, 280000000.0]],
        "total_weekly_revenue",
        coverage=GroupCoverage(
            complete=False,
            groups_returned=3,
            groups_total=45,
            query_limit=3,
            ordering="measure",
            ranked_by_request=True,
        ),
    )
    presentation = build_presentation(
        mapping=Mapping("rank", ("store",), "weekly_revenue", ascending=False), snapshot=result
    )
    assert presentation.shape is PresentationShape.RANKING
    assert "highest" in presentation.headline
    assert "lowest" not in presentation.headline.lower()
    assert len(presentation.highlights) == 1


def test_a_time_series_states_its_peak_without_enumerating_periods() -> None:
    rows = [[f"2010-{m:02d}", 1000.0 + m * 10, 45] for m in range(1, 13)]
    result = snapshot(
        ["period", "total_weekly_revenue", "row_count"],
        rows,
        "total_weekly_revenue",
        coverage=complete(12, 6435),
    )
    presentation = build_presentation(
        mapping=Mapping("trend", ("period",), "weekly_revenue", time_grain="month"),
        snapshot=result,
    )
    assert presentation.shape is PresentationShape.TIME_SERIES
    # The peak's period, as a reader reads it rather than as it is stored.
    # This asserted "2010-12" and passed over headlines that said
    # "2025-12-01T00:00:00", a serialisation format shown to a reader.
    assert "Dec 2010" in presentation.headline
    assert "T00:00:00" not in presentation.headline
    assert presentation.headline.count(";") <= 1
    assert "further period" not in presentation.headline.lower()


def test_two_cuts_are_multi_dimensional() -> None:
    result = snapshot(
        ["region", "business_type", "total_revenue"],
        [["North", "Retail", 10.5], ["South", "Services", 20.25]],
        "total_revenue",
        coverage=complete(2, 100),
    )
    presentation = build_presentation(
        mapping=Mapping("sum", ("region", "business_type"), "revenue"), snapshot=result
    )
    assert presentation.shape is PresentationShape.MULTI_DIMENSIONAL
    assert "North / Retail" in presentation.headline


# ───────────────────────────────────── coverage honesty


def test_an_incomplete_breakdown_does_not_claim_the_highest() -> None:
    result = snapshot(
        ["store", "total_revenue"],
        [[20, 300.0], [33, 37.0]],
        "total_revenue",
        coverage=GroupCoverage(complete=False, groups_returned=2, groups_total=45),
    )
    presentation = build_presentation(
        mapping=Mapping("sum", ("store",), "revenue"), snapshot=result
    )
    assert "highest" not in presentation.headline.lower()
    assert "largest group in this result" in presentation.headline
    codes = {c.code for c in presentation.caveats}
    assert "incomplete_groups" in codes


def test_a_complete_breakdown_may_claim_the_highest() -> None:
    result = snapshot(
        ["store", "total_revenue"],
        [[20, 300.0], [33, 37.0]],
        "total_revenue",
        coverage=complete(2, 100),
    )
    presentation = build_presentation(
        mapping=Mapping("sum", ("store",), "revenue"), snapshot=result
    )
    assert "highest" in presentation.headline.lower()
    assert presentation.scope.complete is True


def test_truncation_is_disclosed() -> None:
    result = snapshot(["store", "total_revenue"], [[1, 2.0]], "total_revenue")
    result = result.model_copy(update={"truncated": True})
    presentation = build_presentation(
        mapping=Mapping("sum", ("store",), "revenue"), snapshot=result
    )
    assert "result_truncated" in {c.code for c in presentation.caveats}


def test_scope_carries_recorded_counts_rather_than_recomputing_them() -> None:
    coverage = GroupCoverage(
        complete=False,
        groups_returned=25,
        groups_total=45,
        rows_total=6435,
        rows_matching=6435,
        rows_represented=3575,
        query_limit=25,
    )
    result = snapshot(["store", "total_revenue"], [[1, 2.0]], "total_revenue", coverage=coverage)
    scope = scope_for(Mapping("sum", ("store",), "revenue"), result)
    assert scope.groups_returned == 25
    assert scope.groups_total == 45
    assert scope.rows_represented == 3575
    assert scope.complete is False


# ───────────────────────────────────── prose discipline


def test_a_headline_may_not_enumerate_with_semicolons() -> None:
    with pytest.raises(ValidationError, match="semicolons"):
        AnalysisPresentation(
            shape=PresentationShape.CATEGORICAL_BREAKDOWN,
            headline="Totals by store: 1: 222,402,808.85; 2: 190,000.00; 3: 1,000.00",
        )


def test_a_headline_may_not_trail_off_in_further_groups() -> None:
    with pytest.raises(ValidationError, match="further group"):
        AnalysisPresentation(
            shape=PresentationShape.CATEGORICAL_BREAKDOWN,
            headline="Total revenue by store: 1: 10.00, and further groups in the cited result.",
        )


def test_a_secondary_summary_is_held_to_the_same_rule() -> None:
    with pytest.raises(ValidationError, match="further period"):
        AnalysisPresentation(
            shape=PresentationShape.TIME_SERIES,
            headline="Revenue peaked in March.",
            secondary_summary="January 1.00, February 2.00, and further periods follow.",
        )


def test_every_number_in_the_prose_resolves_to_something_recorded() -> None:
    result = snapshot(
        ["chronotype", "avg_hours", "row_count"],
        [["Morning Lark", 7.38, 2165], ["Night Owl", 5.01, 2455]],
        "avg_hours",
        coverage=complete(2, 8500),
    )
    presentation = build_presentation(
        mapping=Mapping("average", ("chronotype",), "hours"), snapshot=result
    )
    scope = presentation.scope
    for text in (presentation.headline, presentation.secondary_summary or ""):
        assert numbers_resolve(text, result, scope) == []


def test_a_figure_the_result_does_not_hold_is_refused() -> None:
    result = snapshot(["store", "total"], [[1, 10.0]], "total")
    unresolved = numbers_resolve("The total is 999.99.", result, PresentationScope())
    assert unresolved == [999.99]


def test_a_declared_difference_counts_as_recorded() -> None:
    result = snapshot(["flag", "avg_x"], [[0, 6.21], [1, 6.33]], "avg_x")
    assert (
        numbers_resolve(
            "A descriptive difference of 0.12.",
            result,
            PresentationScope(),
            derived=[Decimal("0.12")],
        )
        == []
    )


# ───────────────────────────────────── highlights and provenance


def test_every_highlight_resolves_to_an_exact_cell() -> None:
    result = snapshot(
        ["chronotype", "avg_hours", "row_count"],
        [["Intermediate", 6.44, 3880], ["Morning Lark", 7.38, 2165], ["Night Owl", 5.01, 2455]],
        "avg_hours",
        coverage=complete(3, 8500),
    )
    presentation = build_presentation(
        mapping=Mapping("average", ("chronotype",), "hours"), snapshot=result
    )
    assert presentation.highlights
    for highlight in presentation.highlights:
        assert highlight.evidence_cells
        for cell in highlight.evidence_cells:
            assert cell.result_id == result.result_id
            assert cell.column in result.columns
            assert result.cell(cell.row, cell.column) == cell.value


def test_a_highlight_without_evidence_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PresentationHighlight(
            highlight_id="x",
            label="Unsupported",
            value=PresentationValue(formatted_value="1.00"),
            evidence_cells=[],
        )


def test_a_delta_requires_both_sides_and_is_derived() -> None:
    value = PresentationValue(formatted_value="2.00", raw_value=2.0)
    cell = EvidenceCell(result_id="r", row=0, column="c", value=2.0)
    with pytest.raises(ValidationError, match="difference from nothing"):
        PresentationHighlight(
            highlight_id="x",
            label="l",
            value=value,
            delta=value,
            evidence_cells=[cell],
        )
    with pytest.raises(ValidationError, match="derived, not measured"):
        PresentationHighlight(
            highlight_id="x",
            label="l",
            value=value,
            comparison_value=value,
            delta=value,
            evidence_cells=[cell],
            interpretation_level=InterpretationLevel.MEASURED,
        )


# ───────────────────────────────────── charts


def test_a_published_chart_specification_carries_no_rows() -> None:
    # The rows live in the result once, where the table and verification
    # read them; a chart carrying its own copy could disagree with the
    # table beside it.
    with pytest.raises(ValidationError, match="must not carry its own rows"):
        PresentationChart(
            chart_id="c",
            result_id="r",
            kind="bar",
            spec={"mark": "bar", "encoding": {}, "data": {"values": [{"x": 1}]}},
        )


def test_declining_a_chart_requires_a_reason() -> None:
    with pytest.raises(ValidationError, match="reason the reader can read"):
        PresentationChart(kind="none")


def test_a_declined_chart_explains_itself_to_the_reader() -> None:
    result = snapshot(["store", "total"], [[i, float(i)] for i in range(200)], "total")
    presentation = build_presentation(
        mapping=Mapping("sum", ("store",), "total"),
        snapshot=result,
        chart_decision={
            "kind": "none",
            "no_chart_reason": "200 categories is more than a readable bar chart shows; the complete result is in the table",
        },
    )
    assert presentation.chart is not None
    assert presentation.chart.kind == "none"
    assert "readable" in (presentation.chart.no_chart_reason or "")

    # And **once**. The reason used to be here *and* as a `no_chart`
    # caveat, so a reader was told the same thing twice, once where the
    # chart would be and again under "What to be careful about", which is
    # for things that qualify the answer. A missing chart does not qualify
    # an answer; it is a fact about the space where a chart is not, and it
    # belongs in that space.
    assert "no_chart" not in {c.code for c in presentation.caveats}

    # The engine's own reason survives. It names the actual cause: 200
    # categories, which is better than anything the presentation layer
    # could derive, so only the "nothing was recorded" placeholder is
    # replaced.
    assert "200 categories" in (presentation.chart.no_chart_reason or "")


def test_chart_axes_are_named_so_the_frontend_need_not_parse_the_spec() -> None:
    result = snapshot(["region", "total_revenue"], [["North", 1.5]], "total_revenue")
    presentation = build_presentation(
        mapping=Mapping("sum", ("region",), "revenue"),
        snapshot=result,
        chart_decision={
            "kind": "bar",
            "title": "total revenue by region",
            "spec": {
                "mark": "bar",
                "encoding": {
                    "x": {"field": "region", "type": "nominal"},
                    "y": {"field": "total_revenue", "type": "quantitative"},
                },
            },
        },
    )
    assert presentation.chart is not None
    assert presentation.chart.x_field == "region"
    assert presentation.chart.y_field == "total_revenue"


# ───────────────────────────────────── terminal states


def test_a_refusal_presents_a_reason_and_no_figures() -> None:
    presentation = build_presentation(
        mapping=Mapping("sum", (), "nope", confident=False),
        snapshot=None,
        outcome="refused",
        stopped_reason="'gross_margin' is not a column of this dataset.",
    )
    assert presentation.shape is PresentationShape.REFUSAL
    assert "gross_margin" in presentation.headline
    assert presentation.highlights == []
    assert presentation.table is None


def test_a_failure_is_distinct_from_a_refusal() -> None:
    presentation = build_presentation(
        mapping=Mapping("sum", (), "x"),
        snapshot=None,
        outcome="failed",
        stopped_reason="the query could not be executed",
    )
    assert presentation.shape is PresentationShape.FAILURE
    assert presentation.highlights == []


def test_a_terminal_shape_may_not_carry_a_result() -> None:
    with pytest.raises(ValidationError, match="presents no result"):
        AnalysisPresentation(
            shape=PresentationShape.REFUSAL,
            headline="refused",
            highlights=[
                PresentationHighlight(
                    highlight_id="x",
                    label="l",
                    value=PresentationValue(formatted_value="1"),
                    evidence_cells=[EvidenceCell(result_id="r", row=0, column="c")],
                )
            ],
        )


def test_a_planner_fallback_is_not_presented_as_agreement() -> None:
    result = snapshot(["store", "total"], [[1, 2.0]], "total")
    presentation = build_presentation(
        mapping=Mapping("sum", ("store",), "total"), snapshot=result, planner_fallback=True
    )
    caveat = next(c for c in presentation.caveats if c.code == "planner_fallback")
    assert "not the model agreeing independently" in caveat.message


def test_a_ranking_sums_missing_observations_across_every_returned_group() -> None:
    result = snapshot(
        ["store", "total_net_value", "row_count", "value_count"],
        [["A", 100.0, 5, 5], ["B", 80.0, 6, 4]],
        "total_net_value",
        source="net_value",
    )

    presentation = build_presentation(
        mapping=Mapping("rank", ("store",), "net_value", ascending=False), snapshot=result
    )

    caveat = next(c for c in presentation.caveats if c.code == "missing_measure_values")
    assert caveat.message == (
        "2 rows represented in this result had no net_value value and were excluded "
        "from the aggregate."
    )


def test_the_observation_count_is_evidence_and_not_a_business_column() -> None:
    """`value_count` belongs to scope, not to the table a reader reads.

    It is `COUNT(measure)`, which exists so the engine can say how many rows
    actually contributed to an aggregate. Shown beside `row_count` on every
    result it reads as a second analytical measure, repeats a number the
    scope line already states, and adds a column to a table that has to fit
    a 360px phone.

    This is asserted directly rather than through the page's width, because
    the layout no longer notices: the report's tables scroll inside their
    own wrapper, so an extra column stopped producing the horizontal
    overflow a browser test could catch. A mutation restoring `value_count`
    to the visible set passed the 360px golden test, the contract tests and
    the ResultPanel tests alike -- the invariant was implemented and
    unenforced.
    """
    result = snapshot(
        ["store", "total_net_value", "row_count", "value_count"],
        [["A", 100.0, 5, 5], ["B", 80.0, 6, 4]],
        "total_net_value",
        source="net_value",
    )

    presentation = build_presentation(
        mapping=Mapping("rank", ("store",), "net_value", ascending=False), snapshot=result
    )

    shown = presentation.table.visible_columns
    assert "value_count" not in shown, (
        "value_count reached the business table; it is scope evidence, and a "
        "reader would read it as a second measure"
    )
    # The columns a reader does need are still there, so this cannot pass by
    # the table having been emptied.
    assert "store" in shown
    assert "total_net_value" in shown
    assert "row_count" in shown

    # And it is still available as evidence: excluded from the table is not
    # the same as discarded.
    assert "value_count" in result.columns
    assert any(c.code == "missing_measure_values" for c in presentation.caveats)


# ───────────────────────────────────── serialization


def test_the_contract_round_trips_through_json() -> None:
    result = snapshot(
        ["chronotype", "avg_hours", "row_count"],
        [["Morning Lark", 7.38, 2165], ["Night Owl", 5.01, 2455]],
        "avg_hours",
        coverage=complete(2, 8500),
    )
    presentation = build_presentation(
        mapping=Mapping("average", ("chronotype",), "hours"), snapshot=result
    )
    payload = presentation.model_dump(mode="json")
    import json

    revived = AnalysisPresentation.model_validate(json.loads(json.dumps(payload)))
    assert revived == presentation
    assert revived.schema_version == presentation.schema_version


def test_unknown_fields_are_rejected_rather_than_ignored() -> None:
    with pytest.raises(ValidationError):
        AnalysisPresentation(
            shape=PresentationShape.SCALAR,
            headline="x",
            nonsense=True,  # type: ignore[call-arg]
        )


def test_the_builder_is_deterministic() -> None:
    result = snapshot(
        ["chronotype", "avg_hours"],
        [["A", 1.5], ["B", 2.5]],
        "avg_hours",
        coverage=complete(2, 10),
    )
    mapping = Mapping("average", ("chronotype",), "hours")
    first = build_presentation(mapping=mapping, snapshot=result)
    second = build_presentation(mapping=mapping, snapshot=result)
    assert first == second


# ───────────────────────────────────── archived payloads


class TestCompatibility:
    """An older recording renders without being rewritten.

    The committed recordings are evidence produced by a build with no
    presentation layer. Deriving a limited presentation lets the archive
    render; fabricating the parts it never recorded would destroy what
    makes it evidence.
    """

    @staticmethod
    def payload(**over: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "outcome": "completed",
            "stopped_reason": "",
            "findings": [{"finding_id": "f1", "text": "Average revenue by region: north: 10."}],
            "charts": [],
            "results": {
                "r1": {
                    "result_id": "r1",
                    "tool_name": "aggregate_for_question",
                    "columns": ["region", "avg_revenue"],
                    "rows": [["north", 10.0]],
                }
            },
            "query_contract": {"operation": "average", "dimensions": ["region"]},
        }
        base.update(over)
        return base

    def test_it_says_it_was_derived(self) -> None:
        from agentic_analytics.presentation.compatibility import presentation_from_payload

        presentation = presentation_from_payload(self.payload())
        assert presentation.compatibility_derived
        assert "compatibility_derived" in {c.code for c in presentation.caveats}

    def test_it_repeats_the_recorded_answer_verbatim(self) -> None:
        # Including prose this contract would not now produce. What the run
        # published is what the archive shows.
        from agentic_analytics.presentation.compatibility import presentation_from_payload

        presentation = presentation_from_payload(self.payload())
        assert presentation.headline == "Average revenue by region: north: 10."

    def test_absent_coverage_stays_absent_rather_than_becoming_complete(self) -> None:
        # "We did not record this" and "every group is present" were
        # confused once already, and a top-25 of 45 was published as a
        # complete breakdown.
        from agentic_analytics.presentation.compatibility import presentation_from_payload

        presentation = presentation_from_payload(self.payload())
        assert presentation.scope.complete is None
        assert presentation.scope.groups_total is None

    def test_recorded_coverage_is_carried_through(self) -> None:
        from agentic_analytics.presentation.compatibility import presentation_from_payload

        payload = self.payload()
        payload["results"]["r1"]["group_coverage"] = {
            "complete": False,
            "groups_returned": 25,
            "groups_total": 45,
        }
        presentation = presentation_from_payload(payload)
        assert presentation.scope.complete is False
        assert presentation.scope.groups_total == 45

    def test_a_refused_archive_is_a_refusal(self) -> None:
        from agentic_analytics.presentation.compatibility import presentation_from_payload

        presentation = presentation_from_payload(
            self.payload(outcome="refused", stopped_reason="no such column", findings=[])
        )
        assert presentation.shape is PresentationShape.REFUSAL
        assert presentation.headline == "no such column"
        assert presentation.table is None

    def test_an_archived_chart_with_inline_rows_is_not_restated(self) -> None:
        from agentic_analytics.presentation.compatibility import presentation_from_payload

        payload = self.payload(
            charts=[
                {
                    "chart_id": "c1",
                    "result_id": "r1",
                    "title": "t",
                    "spec": {"mark": "bar", "data": {"values": [{"region": "north"}]}},
                }
            ]
        )
        presentation = presentation_from_payload(payload)
        assert presentation.chart is not None
        assert presentation.chart.spec is None


def test_a_long_label_is_never_sliced_mid_number() -> None:
    """No arbitrary truncation of the published sentence.

    A hard slice at 400 characters once cut the engine's own complete
    answer mid-number -- `301,397,792.46` became `301,397`, and numeric
    verification then rejected it for stating a value the result did not
    hold. The presentation layer must not truncate at all: it states the
    shape of a breakdown instead of growing with it.
    """
    long_name = "a_very_long_category_label_" * 6
    result = snapshot(
        ["category", "total_revenue"],
        [[long_name + "_high", 301397792.46], [long_name + "_low", 37160221.96]],
        "total_revenue",
        source="revenue",
        coverage=complete(2, 6435),
    )
    presentation = build_presentation(
        mapping=Mapping("sum", ("category",), "revenue"), snapshot=result
    )
    # The full figures survive intact.
    assert "301,397,792.46" in presentation.headline
    assert "37,160,221.96" in presentation.headline
    # Nothing was cut off.
    assert not presentation.headline.rstrip().endswith(("…", "...", ","))
    assert presentation.headline.endswith(".")
    # And the sentence remains verifiable despite its length.
    assert numbers_resolve(presentation.headline, result, presentation.scope) == []


def test_the_headline_does_not_grow_with_the_number_of_groups() -> None:
    """A 45-group breakdown states its shape, not its contents."""
    rows = [[f"store-{i}", float(i) * 1000.0] for i in range(1, 46)]
    result = snapshot(
        ["store", "total_revenue"],
        rows,
        "total_revenue",
        source="revenue",
        coverage=complete(45, 6435),
    )
    presentation = build_presentation(
        mapping=Mapping("sum", ("store",), "revenue"), snapshot=result
    )
    # Two figures named, not forty-five.
    assert len(presentation.headline) < 200
    assert presentation.headline.count(";") <= 1
