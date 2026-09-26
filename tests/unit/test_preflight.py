"""Checking a tool call before it crosses MCP.

The server refuses all of these anyway. Checking first matters because the
server's refusal arrives as an exception the worker never sees, so a wrong
assumption about the schema survives every remaining attempt. The point of
this layer is the *message*: a worker told the valid metric names can pick
one, and a worker told "tool returned an error" cannot.

The other half of these tests is the line this layer must not cross. It
refuses; it never repairs. `sales` does not become `revenue` here, however
obvious that looks, because a guess that is wrong would carry the engine's
authority rather than the model's.
"""

from __future__ import annotations

import pytest

from agentic_analytics.agents.execution import (
    ExecutionContract,
    MetricContract,
    TableContract,
    ToolContract,
)
from agentic_analytics.agents.preflight import (
    ENGINE_OWNED_ARGUMENT,
    MALFORMED_FILTER,
    METRIC_TOOL_WITHOUT_METRICS,
    MISSING_REQUIRED_ARGUMENT,
    MISSING_TEST_VARIABLE,
    NON_NUMERIC_COLUMN,
    UNFILTERABLE_COLUMN,
    UNKNOWN_ARGUMENT,
    UNKNOWN_COLUMN,
    UNKNOWN_DIMENSION,
    UNKNOWN_GRAIN,
    UNKNOWN_METRIC,
    UNKNOWN_TABLE,
    UNKNOWN_TEST_TYPE,
    UNKNOWN_TOOL,
    UPLOAD_TOOL_ON_GOVERNED_DATASET,
    preflight,
)

REVENUE = MetricContract(
    name="revenue",
    description="Net revenue.",
    valid_dimensions=["acquisition_channel", "region"],
    time_field="order_date",
    format="currency",
)
MARGIN = MetricContract(
    name="gross_margin_pct",
    description="Gross margin.",
    valid_dimensions=["category"],
    time_field="order_date",
    format="percent",
)
ORDERS = TableContract(
    name="orders",
    row_count=10,
    columns=[("order_id", "VARCHAR"), ("order_date", "DATE")],
)
CUSTOMERS = TableContract(
    name="customers",
    row_count=5,
    columns=[("customer_id", "VARCHAR"), ("acquisition_channel", "VARCHAR")],
)

COMPARE = ToolContract(
    name="compare_segments",
    description="Compare a metric across segments.",
    required={"metric": "string", "dimension": "string"},
    optional={"segments": "list[string]"},
)
COMPUTE = ToolContract(
    name="compute_metric",
    description="Compute a metric.",
    required={"metric": "string"},
    optional={
        "dimensions": "list[string]",
        "grain": "string",
        "filters": "list[object]",
    },
)
PROFILE = ToolContract(
    name="profile_table",
    description="Describe a table.",
    required={"table": "string"},
    optional={"columns": "list[string]"},
)
AGGREGATE = ToolContract(
    name="aggregate_for_question",
    description="Answer a question about a table.",
    required={"table": "string", "question": "string"},
    optional={},
)


def _contract(
    metrics: list[MetricContract] | None = None,
    tools: list[ToolContract] | None = None,
) -> ExecutionContract:
    return ExecutionContract(
        metrics=[REVENUE, MARGIN] if metrics is None else metrics,
        tables=[ORDERS, CUSTOMERS],
        tools=tools or [COMPARE, COMPUTE, PROFILE, AGGREGATE],
        grains=["day", "month", "quarter", "year"],
        has_metrics=bool([REVENUE] if metrics is None else metrics),
    )


# ------------------------------------------------------------ acceptance
def test_a_well_formed_semantic_call_is_allowed_through() -> None:
    """The case the warehouse run never reached."""
    assert (
        preflight(
            "compare_segments",
            {"metric": "revenue", "dimension": "acquisition_channel"},
            _contract(),
        )
        is None
    )


def test_a_well_formed_physical_call_is_allowed_through() -> None:
    assert preflight("profile_table", {"table": "orders"}, _contract()) is None


def test_an_optional_argument_is_optional() -> None:
    assert preflight("compute_metric", {"metric": "revenue"}, _contract()) is None
    assert preflight("compute_metric", {"metric": "revenue", "grain": "month"}, _contract()) is None


# -------------------------------------------------------------- refusals
def test_an_unknown_tool_is_refused_with_the_real_list() -> None:
    rejection = preflight("magic_tool", {}, _contract())
    assert rejection is not None
    assert rejection.category == UNKNOWN_TOOL
    assert "compare_segments" in rejection.message


def test_the_exact_failure_from_the_warehouse_run_is_now_caught_early() -> None:
    """`revenue` is not a column; `acquisition_channel` is not on `orders`.

    The model asked `aggregate_for_question(table="orders", ...)` for both.
    Now the refusal names the tables and their real columns, so the next
    decision can be different rather than the same one again.
    """
    rejection = preflight(
        "profile_table",
        {"table": "orders", "columns": ["revenue", "acquisition_channel"]},
        _contract(),
    )
    assert rejection is not None
    assert rejection.category == UNKNOWN_COLUMN
    assert "order_id" in rejection.message and "order_date" in rejection.message


def test_an_unknown_metric_is_refused_and_the_real_ones_are_named() -> None:
    rejection = preflight(
        "compare_segments", {"metric": "sales", "dimension": "region"}, _contract()
    )
    assert rejection is not None
    assert rejection.category == UNKNOWN_METRIC
    assert "revenue" in rejection.message
    assert "gross_margin_pct" in rejection.message


def test_a_dimension_is_checked_against_its_own_metric() -> None:
    """`category` is a real dimension -- but not of `revenue`.

    Checking against the union of every metric's dimensions would let this
    through and produce a server error the worker cannot read.
    """
    rejection = preflight(
        "compare_segments", {"metric": "revenue", "dimension": "category"}, _contract()
    )
    assert rejection is not None
    assert rejection.category == UNKNOWN_DIMENSION
    assert "acquisition_channel, region" in rejection.message
    # And the same dimension on the metric that does support it is fine.
    assert (
        preflight(
            "compare_segments", {"metric": "gross_margin_pct", "dimension": "category"}, _contract()
        )
        is None
    )


def test_an_unknown_table_is_refused_with_the_real_tables() -> None:
    rejection = preflight("profile_table", {"table": "sales"}, _contract())
    assert rejection is not None
    assert rejection.category == UNKNOWN_TABLE
    assert "orders" in rejection.message and "customers" in rejection.message


def test_a_missing_required_argument_is_refused() -> None:
    rejection = preflight("compare_segments", {"metric": "revenue"}, _contract())
    assert rejection is not None
    assert rejection.category == MISSING_REQUIRED_ARGUMENT
    assert "dimension" in rejection.message


def test_an_unknown_argument_is_refused() -> None:
    rejection = preflight("profile_table", {"table": "orders", "sort_by": "revenue"}, _contract())
    assert rejection is not None
    assert rejection.category == UNKNOWN_ARGUMENT
    assert "sort_by" in rejection.message


def test_an_invalid_grain_is_refused() -> None:
    rejection = preflight(
        "compute_metric", {"metric": "revenue", "grain": "fortnight"}, _contract()
    )
    assert rejection is not None
    assert rejection.category == UNKNOWN_GRAIN
    assert "quarter" in rejection.message


def test_a_metric_tool_on_a_metric_free_dataset_is_refused() -> None:
    rejection = preflight(
        "compare_segments", {"metric": "revenue", "dimension": "x"}, _contract(metrics=[])
    )
    assert rejection is not None
    assert rejection.category == METRIC_TOOL_WITHOUT_METRICS
    assert "aggregate_for_question" in rejection.message


@pytest.mark.parametrize("owned", ["session_id", "session_key", "task_id"])
def test_a_call_setting_an_engine_owned_argument_is_refused(owned: str) -> None:
    """A model must never supply the capability, or address another session."""
    rejection = preflight("profile_table", {"table": "orders", owned: "anything"}, _contract())
    assert rejection is not None
    assert rejection.category == ENGINE_OWNED_ARGUMENT
    assert owned in rejection.message


# ------------------------------------------- refuses, never repairs
@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("metric", "sales"),
        ("metric", "Revenue"),
        ("metric", "revenues"),
        ("metric", "total_revenue"),
    ],
)
def test_a_near_miss_metric_name_is_refused_and_not_corrected(argument: str, value: str) -> None:
    """No fuzzy matching. `Revenue` is not `revenue` here.

    Case-folding looks harmless and is the first step onto a slope whose
    bottom is the engine deciding what the model meant. If the model wants
    `revenue` it can read the name and type it; the refusal shows it.
    """
    rejection = preflight("compare_segments", {argument: value, "dimension": "region"}, _contract())
    assert rejection is not None
    assert rejection.category == UNKNOWN_METRIC


def test_a_near_miss_dimension_is_refused_and_not_corrected() -> None:
    rejection = preflight(
        "compare_segments", {"metric": "revenue", "dimension": "channel"}, _contract()
    )
    assert rejection is not None
    assert rejection.category == UNKNOWN_DIMENSION
    # It names the valid ones, which is help. It does not substitute one.
    assert "acquisition_channel" in rejection.message


def test_preflight_never_mutates_the_arguments_it_is_given() -> None:
    """Whatever it decides, it does not quietly rewrite the call."""
    arguments = {"metric": "sales", "dimension": "region"}
    before = dict(arguments)
    preflight("compare_segments", arguments, _contract())
    assert arguments == before


# ------------------------------------------------------- narrow checking
def test_a_semantic_tool_is_not_checked_against_physical_columns() -> None:
    """`dimensions` on a metric tool are semantic names, not columns.

    Checking them against `orders`' columns would reject the semantic
    interface precisely for using semantic names.
    """
    assert (
        preflight(
            "compute_metric",
            {"metric": "revenue", "dimensions": ["acquisition_channel"]},
            _contract(),
        )
        is None
    )


def test_a_truncated_table_cannot_support_an_unknown_column_verdict() -> None:
    """If the columns shown are incomplete, "no such column" may be wrong."""
    partial = TableContract(
        name="wide",
        row_count=1,
        columns=[("a", "VARCHAR")],
        truncated_columns=30,
    )
    contract = ExecutionContract(
        metrics=[], tables=[partial], tools=[PROFILE], grains=[], has_metrics=False
    )
    assert preflight("profile_table", {"table": "wide", "columns": ["z"]}, contract) is None


def test_the_first_failure_is_the_one_reported() -> None:
    """Two errors at once is one mistake to the worker, not two."""
    rejection = preflight(
        "compare_segments", {"metric": "sales", "dimension": "nonsense"}, _contract()
    )
    assert rejection is not None
    assert rejection.category == UNKNOWN_METRIC


def test_a_rejection_serialises_for_telemetry() -> None:
    rejection = preflight("magic", {}, _contract())
    assert rejection is not None
    assert set(rejection.as_dict()) == {"category", "message"}


def test_a_non_string_argument_does_not_crash_the_check() -> None:
    """Model output is untrusted: a number where a name belongs is normal."""
    for value in (7, None, {"a": 1}, [1, 2]):
        preflight("profile_table", {"table": value}, _contract())  # type: ignore[dict-item]


# ------------------------------------------------------------- filters
def test_a_filter_in_the_wrong_shape_is_refused_with_the_right_one() -> None:
    """Observed: a model wrote `field`/`operator` for `column`/`op`.

    The server answered with a three-line Pydantic validation error. Naming
    the shape is shorter and is something the model can act on.
    """
    rejection = preflight(
        "compute_metric",
        {"metric": "revenue", "filters": [{"field": "order_date", "operator": ">=", "value": "x"}]},
        _contract(),
    )
    assert rejection is not None
    assert rejection.category == MALFORMED_FILTER
    assert '"column"' in rejection.message and '"op"' in rejection.message


def test_filters_that_are_not_a_list_are_refused() -> None:
    rejection = preflight(
        "compute_metric",
        {"metric": "revenue", "filters": "order_date >= '2025-07-01'"},
        _contract(),
    )
    assert rejection is not None
    assert rejection.category == MALFORMED_FILTER
    assert "list" in rejection.message


def test_a_filter_item_that_is_not_an_object_is_refused() -> None:
    rejection = preflight(
        "compute_metric", {"metric": "revenue", "filters": ["order_date"]}, _contract()
    )
    assert rejection is not None
    assert rejection.category == MALFORMED_FILTER


def test_a_filter_on_a_column_the_metric_cannot_use_is_refused() -> None:
    """Silently dropping a filter changes what the number means."""
    rejection = preflight(
        "compute_metric",
        {"metric": "revenue", "filters": [{"column": "quarter", "op": "=", "value": "Q3 2025"}]},
        _contract(),
    )
    assert rejection is not None
    assert rejection.category == UNFILTERABLE_COLUMN
    assert "acquisition_channel" in rejection.message
    assert "order_date" in rejection.message


def test_a_filter_on_a_dimension_or_the_time_field_is_allowed() -> None:
    for column in ("acquisition_channel", "region", "order_date"):
        assert (
            preflight(
                "compute_metric",
                {"metric": "revenue", "filters": [{"column": column, "op": "=", "value": "x"}]},
                _contract(),
            )
            is None
        ), column


def test_an_empty_filter_list_is_fine() -> None:
    assert preflight("compute_metric", {"metric": "revenue", "filters": []}, _contract()) is None


def test_filters_are_not_checked_against_a_metric_that_does_not_exist() -> None:
    """The unknown metric is the mistake to report, not a knock-on effect."""
    rejection = preflight(
        "compute_metric",
        {"metric": "sales", "filters": [{"column": "nope", "op": "=", "value": 1}]},
        _contract(),
    )
    assert rejection is not None
    assert rejection.category == UNKNOWN_METRIC


# ----------------------------------- the metric layer is not optional
def test_an_upload_tool_is_refused_on_a_governed_dataset() -> None:
    """The mirror of the metric-tool rule, and the one a real run needed.

    On the demo warehouse the planner kept choosing
    `aggregate_for_question`, which maps a question onto one table's raw
    columns. Every call was refused by the server -- correctly, since
    `revenue` is a metric-layer definition and not a column -- but only
    after a round trip, and with a message about ambiguous columns rather
    than about the layer being bypassed.
    """
    rejection = preflight(
        "aggregate_for_question",
        {"table": "orders", "question": "revenue by acquisition channel"},
        _contract(),
    )
    assert rejection is not None
    assert rejection.category == UPLOAD_TOOL_ON_GOVERNED_DATASET
    assert "compute_metric" in rejection.message
    assert "revenue" in rejection.message


def test_the_same_tool_is_fine_where_there_is_no_metric_layer() -> None:
    """An upload is exactly what it is for."""
    contract = ExecutionContract(
        metrics=[],
        tables=[ORDERS],
        tools=[AGGREGATE],
        grains=[],
        has_metrics=False,
        available_tools=["aggregate_for_question"],
    )
    assert (
        preflight("aggregate_for_question", {"table": "orders", "question": "total"}, contract)
        is None
    )


def test_a_physical_tool_that_is_not_an_upload_mapper_still_works() -> None:
    """Profiling a table is legitimate on any dataset."""
    assert preflight("profile_table", {"table": "orders"}, _contract()) is None


# ------------------------------------------- statistical_test variables
STATS = ToolContract(
    name="statistical_test",
    description="Run a statistical test.",
    required={"test_type": "string", "variables": "object"},
    optional={"filters": "list[object]"},
)
TRIALS = TableContract(
    name="trials",
    row_count=100,
    columns=[("plan", "VARCHAR"), ("days", "DOUBLE"), ("converted", "INTEGER")],
)


def _stats_contract() -> ExecutionContract:
    return ExecutionContract(
        metrics=[],
        tables=[TRIALS],
        tools=[STATS],
        grains=[],
        has_metrics=False,
        available_tools=["statistical_test"],
    )


def test_a_test_type_that_does_not_exist_is_refused_with_the_real_list() -> None:
    """Stage 1 spent a call discovering `independent_t_test` is not a test."""
    rejection = preflight(
        "statistical_test",
        {"test_type": "independent_t_test", "variables": {"table": "trials"}},
        _stats_contract(),
    )
    assert rejection is not None
    assert rejection.category == UNKNOWN_TEST_TYPE
    assert "welch_t_test" in rejection.message


def test_a_missing_relation_is_refused_with_the_table_names() -> None:
    rejection = preflight(
        "statistical_test",
        {"test_type": "welch_t_test", "variables": {"group_column": "plan"}},
        _stats_contract(),
    )
    assert rejection is not None
    assert rejection.category == MISSING_TEST_VARIABLE
    assert "trials" in rejection.message


def test_a_missing_test_variable_is_refused_with_what_the_test_needs() -> None:
    """`variables` is typed `object`, so the schema alone says nothing."""
    rejection = preflight(
        "statistical_test",
        {"test_type": "welch_t_test", "variables": {"table": "trials", "group_column": "plan"}},
        _stats_contract(),
    )
    assert rejection is not None
    assert rejection.category == MISSING_TEST_VARIABLE
    assert "value_column" in rejection.message


def test_chi_square_asks_for_its_own_variables_not_another_tests() -> None:
    rejection = preflight(
        "statistical_test",
        {"test_type": "chi_square", "variables": {"table": "trials", "group_column": "plan"}},
        _stats_contract(),
    )
    assert rejection is not None
    assert "row_column" in rejection.message
    assert "value_column" not in rejection.message


def test_a_non_numeric_value_column_is_refused() -> None:
    """A t-test on a category label is meaningless, not merely weaker."""
    rejection = preflight(
        "statistical_test",
        {
            "test_type": "welch_t_test",
            "variables": {"table": "trials", "group_column": "days", "value_column": "plan"},
        },
        _stats_contract(),
    )
    assert rejection is not None
    assert rejection.category == NON_NUMERIC_COLUMN
    assert "days" in rejection.message


def test_a_text_group_column_is_fine() -> None:
    """Grouping is what a label is for; only the measured value must be numeric."""
    assert (
        preflight(
            "statistical_test",
            {
                "test_type": "welch_t_test",
                "variables": {"table": "trials", "group_column": "plan", "value_column": "days"},
            },
            _stats_contract(),
        )
        is None
    )


def test_a_column_that_does_not_exist_is_refused() -> None:
    rejection = preflight(
        "statistical_test",
        {
            "test_type": "welch_t_test",
            "variables": {"table": "trials", "group_column": "plan", "value_column": "nope"},
        },
        _stats_contract(),
    )
    assert rejection is not None
    assert rejection.category == UNKNOWN_COLUMN


def test_a_metric_model_relation_is_left_to_the_server() -> None:
    """A semantic model is not in the table catalogue, so no verdict is possible."""
    assert (
        preflight(
            "statistical_test",
            {
                "test_type": "welch_t_test",
                "variables": {
                    "model": "customer_lifecycle",
                    "group_column": "x",
                    "value_column": "y",
                },
            },
            _stats_contract(),
        )
        is None
    )


def test_the_preflight_contract_matches_the_engines_own_table() -> None:
    """Two descriptions of one contract drift; this asserts they are one."""
    from agentic_analytics.analytics.stats import TEST_TYPES, TEST_VARIABLES

    assert set(TEST_VARIABLES) == set(TEST_TYPES)
