"""The deterministic chart builder, which had no test at all.

`chart_for` produces an encoding and the `result_id` of the result it
draws, and the browser joins the two immediately before rendering. That
split is deliberate -- the rows are in the payload once, where the table
and verification read them, so a chart cannot disagree with the table
beside it -- but it means a chart is only renderable if every field it
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


def snapshot(columns: list[str], rows: list[list[Any]], measure: str) -> ResultSnapshot:
    return ResultSnapshot(
        tool_name="aggregate_for_question",
        columns=columns,
        rows=rows,
        row_count=len(rows),
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
    as a message rather than a plot -- and nothing on the server would
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
