"""The worker's execution contract: what it may name, and what it may not.

A real run on the demo warehouse made 36 tool calls and failed all 36. The
plan was good -- six sensible segmentation tasks -- and the model then asked
for `acquisition_channel` on `orders`, which is a column of `customers`, and
for `revenue` as a column when it is a metric-layer definition.

The fix is not to tell a model where `acquisition_channel` lives. It is to
stop requiring it to know: for a metric tool the worker names a metric and a
dimension, and which relations those resolve to is the metric layer's
business. These tests pin that boundary, and the separation between the
semantic interface and the physical schema that makes it legible.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentic_analytics.agents.execution import (
    ENGINE_OWNED_ARGUMENTS,
    SCHEMA_BANNER,
    MetricContract,
    TableContract,
    ToolContract,
    build_execution_contract,
    build_tool_contracts,
    contract_size,
    render_contract,
    select_tools,
    signature,
)
from agentic_analytics.agents.schemas import AnalysisTask


class _Tool:
    def __init__(self, name: str, schema: dict[str, Any], description: str = "") -> None:
        self.name = name
        self.input_schema = schema
        self.description = description


class _Listing:
    def __init__(self, tools: list[_Tool]) -> None:
        self.tools = tools


CATALOG: dict[str, Any] = {
    "tables": [
        {
            "name": "orders",
            "row_count": 12000,
            "columns": [
                {"name": "order_id", "type": "VARCHAR"},
                {"name": "customer_id", "type": "VARCHAR"},
                {"name": "order_date", "type": "DATE"},
            ],
        },
        {
            "name": "customers",
            "row_count": 3000,
            "columns": [
                {"name": "customer_id", "type": "VARCHAR"},
                {"name": "acquisition_channel", "type": "VARCHAR"},
            ],
        },
    ]
}


class _Metric:
    def __init__(self, decomposable: bool = False) -> None:
        self.is_decomposable = decomposable


class _Registry:
    """The shape `MetricRegistry` presents, with two metrics."""

    def describe_all(self) -> list[dict[str, object]]:
        return [
            {
                "name": "revenue",
                "description": "Net revenue after discounts.",
                "valid_dimensions": ["acquisition_channel", "region"],
                "time_field": "order_date",
                "format": "currency",
            },
            {
                "name": "gross_margin_pct",
                "description": "Gross margin as a percentage.",
                "valid_dimensions": ["category", "region"],
                "time_field": "order_date",
                "format": "percent",
            },
        ]

    def metric(self, name: str) -> _Metric:
        return _Metric(decomposable=name == "revenue")


def _contract(registry: Any = None, tools: list[ToolContract] | None = None) -> Any:
    return build_execution_contract(
        catalog=CATALOG,
        registry=registry,
        tool_contracts=tools or [],
        grains=["day", "month", "quarter", "year"],
    )


# ------------------------------------------------------- tool contracts
def test_engine_owned_arguments_never_reach_the_model() -> None:
    """The capability is a bearer secret; the model must not see the field.

    Showing `session_key` in an argument list is an invitation to supply
    one, and a model that supplies a wrong one has learned that the field
    exists. The engine injects all three on every call.
    """
    listing = _Listing(
        [
            _Tool(
                "compare_segments",
                {
                    "properties": {
                        "session_id": {"type": "string"},
                        "session_key": {"type": "string"},
                        "task_id": {"type": "string"},
                        "metric": {"type": "string"},
                        "dimension": {"type": "string"},
                        "segments": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["session_id", "session_key", "metric", "dimension"],
                },
            )
        ]
    )
    contracts = build_tool_contracts(listing)
    assert len(contracts) == 1
    tool = contracts[0]

    assert set(tool.required) == {"metric", "dimension"}
    assert set(tool.optional) == {"segments"}
    for owned in ENGINE_OWNED_ARGUMENTS:
        assert owned not in tool.argument_names

    # The only place these names may appear is the instruction telling the
    # model not to set them. They must never appear as an argument it is
    # invited to fill, and no capability *value* appears anywhere.
    rendered = render_contract(_contract(tools=contracts), AnalysisTask(objective="o"))
    argument_lines = [
        line
        for line in rendered.splitlines()
        if line.strip().startswith(("required:", "optional:"))
    ]
    assert argument_lines
    for line in argument_lines:
        for owned in ENGINE_OWNED_ARGUMENTS:
            assert owned not in line, line
    assert "supplied by the engine" in rendered


def test_argument_types_are_rendered_readably() -> None:
    listing = _Listing(
        [
            _Tool(
                "compute_metric",
                {
                    "properties": {
                        "metric": {"type": "string"},
                        "limit": {"type": "integer"},
                        "dimensions": {"type": "array", "items": {"type": "string"}},
                        "filters": {"type": "array", "items": {"type": "object"}},
                        "grain": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    },
                    "required": ["metric"],
                },
            )
        ]
    )
    tool = build_tool_contracts(listing)[0]
    assert tool.required["metric"] == "string"
    assert tool.optional["limit"] == "integer"
    assert tool.optional["dimensions"] == "list[string]"
    assert tool.optional["filters"] == "list[object]"
    # A nullable string is a string, not "string | null": the null is what
    # omitting the argument already means.
    assert tool.optional["grain"] == "string"


def test_the_listing_is_the_source_of_truth_for_arguments() -> None:
    """A prompt with a hand-written signature drifts from the server.

    So this asserts the contract is *derived*: a tool the server does not
    list cannot appear, however plausible its name.
    """
    contracts = build_tool_contracts(_Listing([_Tool("profile_table", {"properties": {}})]))
    assert [c.name for c in contracts] == ["profile_table"]


def test_only_the_selected_tools_are_described() -> None:
    listing = _Listing([_Tool(n, {"properties": {}}) for n in ("a", "b", "c")])
    assert [c.name for c in build_tool_contracts(listing, only={"b"})] == ["b"]


# ------------------------------------------------ semantic vs physical
def test_a_metric_is_presented_as_a_metric_and_not_as_a_column() -> None:
    """The distinction the warehouse failure turned on.

    `revenue` is not a column of anything. A worker that reads one
    undifferentiated list of names has no way to tell, which is how it ends
    up asking for `revenue` from `orders`.
    """
    rendered = render_contract(_contract(registry=_Registry()), AnalysisTask(objective="o"))

    semantic = rendered.index("SEMANTIC ANALYTICS INTERFACE")
    physical = rendered.index(SCHEMA_BANNER)
    assert semantic < physical, "the semantic interface must come first"

    section = rendered[semantic:physical]
    assert "revenue" in section
    assert "valid_dimensions: acquisition_channel, region" in section
    assert "time_field: order_date" in section
    assert "not columns" in section


def test_the_dimension_is_listed_against_its_metric_not_its_table() -> None:
    """A worker should never learn that `acquisition_channel` is on customers.

    That mapping is the metric layer's, and a worker reasoning about the
    join would be doing the engine's job with less information.
    """
    rendered = render_contract(_contract(registry=_Registry()), AnalysisTask(objective="o"))
    semantic = rendered[
        rendered.index("SEMANTIC ANALYTICS INTERFACE") : rendered.index(SCHEMA_BANNER)
    ]
    assert "customers" not in semantic
    assert "join" not in semantic.lower()
    # But the instruction not to reason about joins is stated somewhere.
    assert "the metric layer owns them" in rendered


def test_the_physical_schema_carries_columns_and_types() -> None:
    rendered = render_contract(_contract(registry=_Registry()), AnalysisTask(objective="o"))
    physical = rendered[rendered.index(SCHEMA_BANNER) :]
    assert "orders" in physical
    assert "order_date: DATE" in physical
    assert "acquisition_channel: VARCHAR" in physical
    assert "12000 rows" in physical


def test_a_dataset_with_no_metric_layer_says_so_plainly() -> None:
    rendered = render_contract(_contract(registry=None), AnalysisTask(objective="o"))
    assert "no metric layer" in rendered
    assert SCHEMA_BANNER in rendered


def test_a_decomposable_metric_is_marked() -> None:
    rendered = render_contract(_contract(registry=_Registry()), AnalysisTask(objective="o"))
    assert "supports decompose_change" in rendered


# ------------------------------------------------------------- bounding
def test_the_schema_is_bounded_in_tables_and_columns() -> None:
    """An unbounded catalog moves the failure, it does not fix it."""
    from agentic_analytics.agents.execution import MAX_COLUMNS_PER_TABLE, MAX_TABLES

    catalog = {
        "tables": [
            {
                "name": f"t{i}",
                "row_count": 1,
                "columns": [{"name": f"c{j}", "type": "VARCHAR"} for j in range(80)],
            }
            for i in range(40)
        ]
    }
    contract = build_execution_contract(catalog=catalog, registry=None, tool_contracts=[])
    assert len(contract.tables) == MAX_TABLES
    assert len(contract.tables[0].columns) == MAX_COLUMNS_PER_TABLE
    assert contract.tables[0].truncated_columns == 80 - MAX_COLUMNS_PER_TABLE

    rendered = render_contract(contract, AnalysisTask(objective="o"))
    assert f"and {80 - MAX_COLUMNS_PER_TABLE} further columns" in rendered


def test_only_a_handful_of_tool_schemas_are_shown() -> None:
    """Twelve argument lists on each of six decisions is the other failure."""
    available = [
        "compute_metric",
        "compare_segments",
        "analyze_timeseries",
        "decompose_change",
        "rank_contributors",
        "compare_periods",
        "statistical_test",
        "profile_table",
        "list_metrics",
        "run_readonly_sql",
        "sample_rows",
        "list_tables",
    ]
    chosen = select_tools(
        available, AnalysisTask(objective="o", preferred_tool="compute_metric"), has_metrics=True
    )
    assert len(chosen) <= 6
    assert "compute_metric" in chosen


def test_the_preferred_tool_is_always_described() -> None:
    chosen = select_tools(
        ["profile_table", "compute_metric", "run_readonly_sql"],
        AnalysisTask(objective="o", preferred_tool="run_readonly_sql"),
        has_metrics=True,
    )
    assert "run_readonly_sql" in chosen


def test_a_metric_free_dataset_is_not_offered_metric_tools() -> None:
    chosen = select_tools(
        ["compute_metric", "aggregate_for_question", "profile_table"],
        AnalysisTask(objective="o", preferred_tool="aggregate_for_question"),
        has_metrics=False,
    )
    assert "compute_metric" not in chosen
    assert "aggregate_for_question" in chosen


def test_the_rendered_contract_stays_small_enough_to_measure() -> None:
    """Bounding the context is pointless if nobody checks it stayed bounded."""
    rendered = render_contract(_contract(registry=_Registry()), AnalysisTask(objective="o"))
    size = contract_size(rendered)
    assert size["characters"] == len(rendered)
    assert size["lines"] > 1
    assert size["characters"] < 6000, f"contract grew to {size['characters']} characters"


# ------------------------------------------------------------ signature
def test_argument_order_cannot_disguise_a_repeated_call() -> None:
    a = signature("compute_metric", {"metric": "revenue", "grain": "month"})
    b = signature("compute_metric", {"grain": "month", "metric": "revenue"})
    assert a == b


def test_a_different_argument_is_a_different_call() -> None:
    a = signature("compute_metric", {"metric": "revenue"})
    b = signature("compute_metric", {"metric": "orders"})
    assert a != b


def test_an_unserialisable_argument_still_produces_a_signature() -> None:
    assert signature("t", {"x": object()})


# -------------------------------------------------------------- lookups
def test_contract_lookups_return_none_for_unknown_names() -> None:
    contract = _contract(registry=_Registry(), tools=[ToolContract(name="t", description="")])
    assert contract.tool("t") is not None
    assert contract.tool("nope") is None
    assert contract.metric("revenue") is not None
    assert contract.metric("sales") is None
    assert contract.table("orders") is not None
    assert contract.table("sales") is None
    assert contract.table_names == ["orders", "customers"]
    assert contract.metric_names == ["revenue", "gross_margin_pct"]


@pytest.mark.parametrize(
    ("contract_type", "kwargs"),
    [
        (
            MetricContract,
            {
                "name": "m",
                "description": "",
                "valid_dimensions": [],
                "time_field": "",
                "format": "",
            },
        ),
        (TableContract, {"name": "t", "row_count": 0, "columns": []}),
        (ToolContract, {"name": "x", "description": ""}),
    ],
)
def test_contract_pieces_are_immutable(contract_type: Any, kwargs: dict[str, Any]) -> None:
    """Frozen: nothing downstream should be able to widen what was granted."""
    instance = contract_type(**kwargs)
    with pytest.raises(Exception):
        instance.name = "changed"  # type: ignore[misc]
