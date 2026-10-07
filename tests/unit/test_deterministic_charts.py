"""The deterministic chart builder, which had no test at all.

`chart_for` produces an encoding and the `result_id` of the result it
draws, and the browser joins the two immediately before rendering. That
split is deliberate -- the rows are in the payload once, where the table
and verification read them, so a chart cannot disagree with the table
beside it, but it means a chart is only renderable if every field it
encodes is actually a column of the cited result. When that does not hold
the browser has no rows to bind and the reader gets a message instead of a
chart, which is how "chart data must be inline values" reached production.

So the invariant asserted here is the one the browser depends on: for
every chart kind, every encoded field resolves against the snapshot. Plus
the two properties the split exists to provide -- no data on the wire, and
the same inputs producing the same specification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from agentic_analytics.analytics.charts import chart_for
from agentic_analytics.analytics.results import ResultSnapshot

#: Mirrors `FORBIDDEN_KEYS` in web/src/lib/chartSafety.ts. A specification
#: carrying any of these is rejected in the browser, so emitting one here
#: would publish a chart that cannot render.
FORBIDDEN_KEYS = {
    "signals",
    "expr",
    "datasets",
    "transform",
    "params",
    "selection",
    "url",
    "loader",
    "usermeta",
    "onerror",
}

#: Mirrors `ALLOWED_TYPES` in the same module.
ALLOWED_TYPES = {"quantitative", "nominal", "ordinal", "temporal"}


@dataclass
class Mapping:
    """Only the attributes `chart_for` reads."""

    operation: str
    dimensions: tuple[str, ...] = ()
    measure: str | None = None
    confident: bool = True
    named_columns: list[str] = field(default_factory=list)


def snapshot(
    columns: list[str],
    rows: list[list[Any]],
    measure: str,
    column_types: dict[str, str] | None = None,
) -> ResultSnapshot:
    return ResultSnapshot(
        tool_name="aggregate_for_question",
        columns=columns,
        rows=rows,
        row_count=len(rows),
        column_types=column_types or {},
        column_lineage={measure: {"kind": "aggregate", "aggregate": "SUM", "column": "revenue"}},
    )


def encoded_fields(spec: dict[str, Any]) -> list[tuple[str, str, str | None]]:
    """Every (channel, field, type) the specification encodes."""
    out: list[tuple[str, str, str | None]] = []
    for channel, definition in (spec.get("encoding") or {}).items():
        entries = definition if isinstance(definition, list) else [definition]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("field")
            if isinstance(name, str):
                kind = entry.get("type")
                out.append((channel, name, kind if isinstance(kind, str) else None))
    return out


def walk(node: Any) -> list[str]:
    """Every key anywhere in the specification."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.append(key)
            found.extend(walk(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(walk(item))
    return found


# One case per kind the builder can produce.
CASES: dict[str, tuple[Mapping, ResultSnapshot]] = {
    "bar": (
        Mapping("sum", ("region",), "revenue"),
        snapshot(
            ["region", "total_revenue", "row_count"],
            [["North", 10.5, 3], ["South", 20.25, 4]],
            "total_revenue",
        ),
    ),
    "ranked_bar": (
        Mapping("rank", ("region",), "revenue"),
        snapshot(
            ["region", "total_revenue", "row_count"],
            [["South", 20.25, 4], ["North", 10.5, 3]],
            "total_revenue",
        ),
    ),
    "line": (
        Mapping("trend", ("period",), "revenue"),
        snapshot(
            ["period", "total_revenue", "row_count"],
            [["2025-01", 10.5, 3], ["2025-02", 20.25, 4]],
            "total_revenue",
        ),
    ),
    "grouped_bar": (
        Mapping("sum", ("region", "business_type"), "revenue"),
        snapshot(
            ["region", "business_type", "total_revenue", "row_count"],
            [["North", "Retail", 10.5, 3], ["South", "Services", 20.25, 4]],
            "total_revenue",
        ),
    ),
    "kpi": (
        Mapping("sum", (), "revenue"),
        snapshot(["total_revenue", "row_count"], [[30.75, 7]], "total_revenue"),
    ),
}


@pytest.mark.parametrize("kind", sorted(CASES))
def test_each_kind_is_produced(kind: str) -> None:
    mapping, result = CASES[kind]
    assert chart_for(mapping, result)["kind"] == kind


@pytest.mark.parametrize("kind", sorted(CASES))
def test_every_encoded_field_is_a_column_of_the_cited_result(kind: str) -> None:
    """The invariant the browser's hydration depends on.

    A field that is not a column has no rows to bind, so the chart renders
    as a message rather than a plot, and nothing on the server would
    notice, because the server never binds the data.
    """
    mapping, result = CASES[kind]
    chart = chart_for(mapping, result)
    if chart["kind"] == "kpi":
        assert chart["spec"]["value_column"] in result.columns
        return
    fields = encoded_fields(chart["spec"])
    assert fields, "the chart encodes nothing"
    for channel, name, _ in fields:
        assert name in result.columns, f"{channel} plots {name!r}, not a column of the result"


@pytest.mark.parametrize("kind", sorted(CASES))
def test_no_specification_carries_its_own_data(kind: str) -> None:
    """The rows stay in the result, so the chart cannot contradict the table."""
    mapping, result = CASES[kind]
    chart = chart_for(mapping, result)
    assert "data" not in chart["spec"]


@pytest.mark.parametrize("kind", sorted(CASES))
def test_no_specification_carries_a_key_the_browser_rejects(kind: str) -> None:
    mapping, result = CASES[kind]
    chart = chart_for(mapping, result)
    assert not FORBIDDEN_KEYS.intersection(walk(chart["spec"]))


@pytest.mark.parametrize("kind", sorted(CASES))
def test_every_encoding_type_is_one_the_browser_allows(kind: str) -> None:
    mapping, result = CASES[kind]
    chart = chart_for(mapping, result)
    if chart["kind"] == "kpi":
        return
    for channel, _, declared in encoded_fields(chart["spec"]):
        assert declared in ALLOWED_TYPES, f"{channel} declares {declared!r}"


@pytest.mark.parametrize("kind", sorted(CASES))
def test_the_same_inputs_produce_the_same_specification(kind: str) -> None:
    """What lets Compare Both show one chart rather than two that differ."""
    mapping, result = CASES[kind]
    assert chart_for(mapping, result) == chart_for(mapping, result)


def test_a_fractional_measure_is_formatted_to_two_places() -> None:
    """The axis has to read like the table, which shows two places."""
    mapping, result = CASES["bar"]
    spec = chart_for(mapping, result)["spec"]
    assert spec["encoding"]["y"]["axis"]["format"] == ",.2f"
    tooltip = spec["encoding"]["tooltip"]
    assert tooltip[-1]["format"] == ",.2f"


def test_a_whole_measure_carries_no_decimals() -> None:
    """A count of orders shown as 1,234.00 is not what the table shows."""
    result = snapshot(
        ["region", "total_orders", "row_count"],
        [["North", 12, 3], ["South", 20, 4]],
        "total_orders",
    )
    spec = chart_for(Mapping("count", ("region",), "orders"), result)["spec"]
    assert spec["encoding"]["y"]["axis"]["format"] == ","


def test_the_tooltip_names_every_cut_and_then_the_measure() -> None:
    mapping, result = CASES["grouped_bar"]
    tooltip = chart_for(mapping, result)["spec"]["encoding"]["tooltip"]
    assert [entry["field"] for entry in tooltip] == [
        "region",
        "business_type",
        "total_revenue",
    ]


def test_an_unresolved_question_declines_rather_than_guessing() -> None:
    _, result = CASES["bar"]
    chart = chart_for(Mapping("sum", ("region",), "revenue", confident=False), result)
    assert chart["kind"] == "none"
    assert chart["no_chart_reason"]


def test_an_empty_result_declines_with_a_reason() -> None:
    result = snapshot(["region", "total_revenue", "row_count"], [], "total_revenue")
    chart = chart_for(Mapping("sum", ("region",), "revenue"), result)
    assert chart["kind"] == "none"
    assert "no rows" in chart["no_chart_reason"]


def test_too_many_categories_declines_rather_than_drawing_an_unreadable_chart() -> None:
    rows = [[f"store-{i}", float(i) + 0.5, 1] for i in range(200)]
    result = snapshot(["store", "total_revenue", "row_count"], rows, "total_revenue")
    chart = chart_for(Mapping("sum", ("store",), "revenue"), result)
    assert chart["kind"] == "none"
    assert "readable" in chart["no_chart_reason"]
    # And it points the reader at the complete answer rather than stopping.
    assert "table" in chart["no_chart_reason"]


# ----------------------------------------------- a period is a date, drawn as one


def trend(grain: str | None = "month") -> tuple[Mapping, ResultSnapshot]:
    """A monthly trend, shaped exactly as the engine returns one.

    The period values are ISO strings because that is what `_coerce` in
    `analytics/execute.py` makes of a DuckDB `DATE_TRUNC`, and the whole
    point of this fixture is that they are dates rather than labels.
    """
    mapping = Mapping("trend", (), "Weekly_Sales")
    mapping.time_grain = grain  # type: ignore[attr-defined]
    return mapping, snapshot(
        ["period", "total_weekly_sales", "row_count"],
        [
            ["2010-02-01T00:00:00", 190332983.04, 180],
            ["2010-03-01T00:00:00", 181919802.50, 180],
            ["2010-12-01T00:00:00", 288760532.72, 225],
        ],
        "total_weekly_sales",
    )


class TestThePeriodAxisIsATimeAxis:
    """A published monthly trend had `1264982400000` down its x axis.

    That is the epoch millisecond for February 2010, and the table beside
    it read "Feb 2010" for the same row. The cause was one word: the
    period axis was declared `ordinal`, so neither formatter would write a
    date format onto it -- the presentation layer keys its format on
    `type == "temporal"`, found a category instead, and wrote only the
    title. "Period" arrived; the ticks did not.

    Nothing asserted the axis type before this, which is why a sweep that
    rendered the chart, found its marks and measured its labels stayed
    green across the defect.
    """

    def test_the_axis_is_temporal_and_not_a_category(self) -> None:
        mapping, result = trend()
        x = chart_for(mapping, result)["spec"]["encoding"]["x"]
        assert x["field"] == "period"
        assert x["type"] == "temporal", "a date drawn as an unordered category"

    def test_it_carries_a_date_format_rather_than_leaving_the_ticks_raw(self) -> None:
        mapping, result = trend()
        x = chart_for(mapping, result)["spec"]["encoding"]["x"]
        assert x["axis"]["format"] == "%b %Y"

    def test_it_keeps_as_many_labels_as_fit_rather_than_dropping_them(self) -> None:
        """Vega resolves a collision by dropping labels, and a 33-month
        axis that silently loses most of its own is worse than a tight
        one. The registry path sets this; the uploaded path did not."""
        mapping, result = trend()
        x = chart_for(mapping, result)["spec"]["encoding"]["x"]
        assert x["axis"]["labelOverlap"] == "greedy"

    def test_the_tooltip_says_the_same_thing_as_the_axis(self) -> None:
        """The hover was the worst of the three surfaces: a reader asks for
        it, so whatever it returns reads as the precise answer."""
        mapping, result = trend()
        tooltip = chart_for(mapping, result)["spec"]["encoding"]["tooltip"]
        period = next(entry for entry in tooltip if entry["field"] == "period")
        assert period["type"] == "temporal"
        assert period["format"] == "%b %Y"

    @pytest.mark.parametrize(
        ("grain", "expected"),
        [
            ("day", "%b %-d, %Y"),
            ("week", "%b %-d, %Y"),
            ("month", "%b %Y"),
            ("quarter", "%b %Y"),
            ("year", "%Y"),
        ],
    )
    def test_the_declared_grain_decides_how_much_of_the_date_is_shown(
        self, grain: str, expected: str
    ) -> None:
        mapping, result = trend(grain)
        x = chart_for(mapping, result)["spec"]["encoding"]["x"]
        assert x["axis"]["format"] == expected

    def test_an_undeclared_grain_still_formats_as_a_date(self) -> None:
        """Every period column is a `DATE_TRUNC`, so the value is a date
        whatever the grain. The fallback decides only how much is shown,
        never whether to show a stored instant."""
        mapping, result = trend(None)
        x = chart_for(mapping, result)["spec"]["encoding"]["x"]
        assert x["type"] == "temporal"
        assert x["axis"]["format"] == "%b %Y"

    def test_a_category_axis_is_still_a_category(self) -> None:
        """The fix is about the period column, not about every line axis."""
        mapping, result = CASES["bar"]
        x = chart_for(mapping, result)["spec"]["encoding"]["x"]
        assert x["type"] == "nominal"

    def test_the_presentation_layer_reads_one_map_and_not_a_copy(self) -> None:
        """Two copies of a time format is how an axis and a tooltip come to
        disagree. The presentation layer imports this one."""
        from agentic_analytics.analytics.charts import TIME_AXIS_FORMAT
        from agentic_analytics.presentation import charts as presentation_charts

        assert presentation_charts._TIME_FORMAT is TIME_AXIS_FORMAT


class TestAResultCutMoreWaysThanACanvasCanSeparate:
    """The absence of this branch was not an empty panel. It was a wrong
    chart.

    A result cut three ways fell through to the two-cut branch, where
    `other = next(c for c in resolved if c != category)` takes the first
    remaining cut and the third is never encoded. A 120-row
    segment-by-channel-by-region cross-tab was drawn as twenty bars, each
    holding six different regions stacked invisibly on one another, under
    a title naming two of the three cuts.
    """

    @staticmethod
    def cross_tab() -> tuple[Mapping, ResultSnapshot]:
        rows = [
            [segment, channel, region, 0.5, 2]
            for segment in ("vip", "loyal", "new", "churn")
            for channel in ("affiliate", "social", "search", "email", "direct")
            for region in ("Midwest", "South", "East", "West", "North", "Central")
        ]
        return (
            Mapping(
                "aggregate",
                ("customer_segment", "acquisition_channel", "region"),
                "repeat_purchase_rate",
            ),
            snapshot(
                [
                    "customer_segment",
                    "acquisition_channel",
                    "region",
                    "repeat_purchase_rate",
                    "row_count",
                ],
                rows,
                "repeat_purchase_rate",
            ),
        )

    def test_it_is_declined_rather_than_drawn_without_one_of_its_cuts(self) -> None:
        mapping, result = self.cross_tab()
        chart = chart_for(mapping, result)
        assert chart["kind"] == "none"

    def test_the_reason_names_every_cut_the_result_actually_has(self) -> None:
        """A reader has to be able to tell this from "too many categories"."""
        mapping, result = self.cross_tab()
        reason = chart_for(mapping, result)["no_chart_reason"]
        for cut in ("customer segment", "acquisition channel", "region"):
            assert cut in reason
        assert "table" in reason

    def test_no_specification_is_emitted_at_all(self) -> None:
        """Not an unreadable chart with a warning: no chart."""
        mapping, result = self.cross_tab()
        assert chart_for(mapping, result).get("spec") is None

    def test_two_cuts_are_still_drawn(self) -> None:
        """The boundary, so the branch cannot drift into refusing everything."""
        mapping, result = CASES["grouped_bar"]
        assert chart_for(mapping, result)["kind"] == "grouped_bar"

    def test_a_period_and_two_categories_is_also_three_cuts(self) -> None:
        """Time is a cut like any other here: x is taken, colour is taken,
        and a third has nowhere to go."""
        rows = [["2024-01-01T00:00:00", "North", "Retail", 1.0, 2]]
        result = snapshot(
            ["period", "region", "business_type", "total_revenue", "row_count"],
            rows,
            "total_revenue",
        )
        mapping = Mapping("trend", ("region", "business_type"), "revenue")
        assert chart_for(mapping, result)["kind"] == "none"


class TestAnOrderedNumericCutIsDrawnAsASequence:
    """`age`, and forty-eight bars.

    A published report answered "what is the average bedtime_phone_minutes
    by age" over 8,500 rows and drew 48 nominal bars. Verified against the
    dataset, every value in it was exact; the drawing was the defect. Two
    things were wrong with it and only one of them was visible:

    * forty-eight tick labels collided along the foot of the frame; and
    * a nominal axis sorts its values as strings, so their left-to-right
      order was a coincidence of every age in the file having two digits.

    `presentation/fields.py` already resolves such a column to
    `SemanticKind.ORDERED_NUMERIC`, and its comment says plotting one on a
    categorical axis "is what made a 48-age breakdown unreadable". The
    chart builder had never been told.
    """

    #: Forty-eight ages, as the published result had them.
    AGES = [[18 + index, 60.0 + index * 0.1, 200] for index in range(48)]
    COLUMNS = ["age", "average_phone_minutes", "row_count"]
    TYPES = {"age": "BIGINT", "average_phone_minutes": "DOUBLE", "row_count": "BIGINT"}

    def chart(self, rows: list[list[Any]] | None = None, **types: str) -> dict[str, Any]:
        declared = {**self.TYPES, **types}
        snap = snapshot(self.COLUMNS, rows or self.AGES, "average_phone_minutes", declared)
        return chart_for(
            Mapping(operation="aggregate", dimensions=("age",), measure="phone_minutes"), snap
        )

    def test_it_is_a_line_on_a_number_line(self) -> None:
        out = self.chart()
        assert out["kind"] == "line"
        x = out["spec"]["encoding"]["x"]
        assert x["field"] == "age"
        # Not `ordinal`: Vega gives an ordinal axis one discrete tick per
        # value and crowds forty-eight of them exactly as the bars did.
        assert x["type"] == "quantitative"

    def test_the_y_axis_keeps_its_zero(self) -> None:
        """The one change here that would have been dishonest.

        The published values span 51.70 to 65.49 -- about a tenth of their
        own mean, and some of those groups rest on 29 rows. A suppressed
        baseline draws a decisive pattern over a result whose shape is
        "nearly flat, with noise". The axis *type* was the defect.
        """
        y = self.chart()["spec"]["encoding"]["y"]
        assert "scale" not in y
        assert str(self.chart()["spec"]).find("zero") == -1

    def test_a_few_ordered_values_are_still_bars(self) -> None:
        """Bars compare lengths better than a line does, and that is what
        a reader wants from a handful of groups. The threshold decides
        which of two honest drawings is used, never whether to draw."""
        out = self.chart(rows=[[n, 60.0 + n, 200] for n in range(1, 6)])
        assert out["kind"] == "bar"
        assert out["spec"]["encoding"]["x"]["type"] == "nominal"

    def test_a_cut_that_declares_no_type_is_left_as_it_was(self) -> None:
        """A result written before the engine recorded column types knows
        nothing about the column, and guessing from the values is the move
        this codebase does not make."""
        snap = snapshot(self.COLUMNS, self.AGES, "average_phone_minutes", None)
        out = chart_for(
            Mapping(operation="aggregate", dimensions=("age",), measure="phone_minutes"), snap
        )
        assert out["kind"] == "bar"

    def test_a_text_cut_is_still_a_set_of_labels(self) -> None:
        rows = [[f"segment-{index}", 60.0 + index, 200] for index in range(20)]
        snap = snapshot(
            ["segment", "average_phone_minutes", "row_count"],
            rows,
            "average_phone_minutes",
            {"segment": "VARCHAR", "average_phone_minutes": "DOUBLE"},
        )
        out = chart_for(
            Mapping(operation="aggregate", dimensions=("segment",), measure="phone_minutes"), snap
        )
        assert out["kind"] == "bar"
        assert out["spec"]["encoding"]["x"]["type"] == "nominal"

    def test_a_declared_type_carrying_parameters_is_still_numeric(self) -> None:
        """`DECIMAL(10,2)` names its precision, and an exact-match lookup
        against it finds nothing."""
        out = self.chart(age="DECIMAL(10,2)")
        assert out["kind"] == "line"

    def test_a_rank_question_still_gets_its_ranking(self) -> None:
        """Asked for an ordering by value, the ordering by value is the
        answer -- drawing it in cut order instead would answer a different
        question."""
        snap = snapshot(self.COLUMNS, self.AGES, "average_phone_minutes", self.TYPES)
        out = chart_for(
            Mapping(operation="rank", dimensions=("age",), measure="phone_minutes"), snap
        )
        assert out["kind"] == "ranked_bar"

    def test_too_many_values_is_still_declined(self) -> None:
        """The sequence is a better drawing of a readable number of
        groups, not a licence to draw an unreadable one. Nothing is
        charted here that was declined before."""
        rows = [[index, 60.0, 200] for index in range(200)]
        out = self.chart(rows=rows)
        assert out["kind"] == "none"
        assert "more than a readable bar chart shows" in out["no_chart_reason"]

    def test_the_specification_stays_inside_what_the_browser_allows(self) -> None:
        spec = self.chart()["spec"]
        assert not (FORBIDDEN_KEYS & set(_keys(spec)))
        for _channel, _field_name, type_name in encoded_fields(spec):
            assert type_name is None or type_name in ALLOWED_TYPES


def _keys(node: Any) -> list[str]:
    if isinstance(node, dict):
        out: list[str] = []
        for key, value in node.items():
            out.append(key)
            out.extend(_keys(value))
        return out
    if isinstance(node, list):
        return [key for item in node for key in _keys(item)]
    return []
