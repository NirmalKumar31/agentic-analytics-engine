"""Check a proposed tool call before it crosses MCP.

Every rule here could be left to the server, which refuses all of these
anyway. The reason to check first is that the server's refusal costs a round
trip and, more importantly, arrives as an exception rather than as something
the worker can act on. A worker told "metric 'sales' does not exist; valid
metrics are revenue, gross_margin_pct, ..." can choose again. A worker told
"tool returned an error" cannot, and will spend its remaining five attempts
on variations of the same wrong assumption.

The hard rule is that this layer **refuses; it does not repair**. There is a
real difference between the engine supplying what it owns, the session
handle, the capability, the task id, and the engine deciding what a model
meant. Mapping `sales` to `revenue` or `channel` to `acquisition_channel`
would be a guess dressed as a correction, and when it guessed wrong the
resulting number would carry the engine's authority rather than the model's.
So a near-miss is reported with the valid names beside it, and the model
chooses. No fuzzy matching anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentic_analytics.agents.execution import (
    ENGINE_OWNED_ARGUMENTS,
    SEMANTIC_TOOLS,
    ExecutionContract,
)

#: Failure categories, kept as stable identifiers so telemetry can count
#: them without matching on wording written for a model to read.
UNKNOWN_TOOL = "unknown_tool"
ENGINE_OWNED_ARGUMENT = "engine_owned_argument"
UNKNOWN_ARGUMENT = "unknown_argument"
MISSING_REQUIRED_ARGUMENT = "missing_required_argument"
UNKNOWN_METRIC = "unknown_metric"
UNKNOWN_DIMENSION = "unknown_dimension"
UNKNOWN_TABLE = "unknown_table"
UNKNOWN_COLUMN = "unknown_column"
UNKNOWN_GRAIN = "unknown_grain"
METRIC_TOOL_WITHOUT_METRICS = "metric_tool_without_metric_layer"
UPLOAD_TOOL_ON_GOVERNED_DATASET = "upload_tool_on_governed_dataset"
UNKNOWN_TEST_TYPE = "unknown_test_type"
MISSING_TEST_VARIABLE = "missing_test_variable"
NON_NUMERIC_COLUMN = "non_numeric_column"
MALFORMED_FILTER = "malformed_filter"
UNFILTERABLE_COLUMN = "unfilterable_column"

#: The filter shape the engine accepts. A model writing `field`/`operator`
#: instead of `column`/`op` produced a Pydantic validation error three lines
#: long; naming the shape here is both shorter and something it can act on.
FILTER_KEYS = frozenset({"column", "op", "value"})

#: Arguments that name a physical table, by the tools that take one.
_TABLE_ARGUMENTS = frozenset({"table"})
#: Arguments that name a time grain.
_GRAIN_ARGUMENTS = frozenset({"grain", "time_grain"})


@dataclass(frozen=True)
class PreflightRejection:
    """Why a proposed call was not dispatched."""

    category: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"category": self.category, "message": self.message}


def preflight(
    tool: str,
    arguments: dict[str, Any],
    contract: ExecutionContract,
) -> PreflightRejection | None:
    """Return why this call cannot work, or `None` if it may be dispatched.

    The first failure wins: a call naming an unknown metric *and* an unknown
    dimension is one mistake to the worker, and reporting both invites it to
    fix the second while leaving the first.
    """
    if not contract.knows_tool(tool):
        available = contract.available_tools or sorted(t.name for t in contract.tools)
        return PreflightRejection(
            UNKNOWN_TOOL,
            f"tool {tool!r} does not exist. Available tools: {', '.join(available)}",
        )

    for argument in sorted(arguments):
        if argument in ENGINE_OWNED_ARGUMENTS:
            return PreflightRejection(
                ENGINE_OWNED_ARGUMENT,
                f"{argument!r} is supplied by the engine and must not be set by a "
                "tool call. Remove it and call the tool again.",
            )

    # A tool the server exposes but whose arguments were not described in
    # this prompt is still legal to call. Checking it against a contract we
    # do not hold would refuse a valid call, so the argument rules below are
    # skipped and MCP decides. Narrowing what is *described* must never
    # narrow what is *permitted*.
    known = contract.tool(tool)
    if known is not None:
        unknown = sorted(set(arguments) - known.argument_names)
        if unknown:
            return PreflightRejection(
                UNKNOWN_ARGUMENT,
                f"{tool} does not accept {', '.join(repr(a) for a in unknown)}. "
                f"It accepts: {', '.join(sorted(known.argument_names)) or '(no arguments)'}",
            )

        missing = sorted(set(known.required) - set(arguments))
        if missing:
            spelled = ", ".join(f"{k}: {v}" for k, v in sorted(known.required.items()))
            return PreflightRejection(
                MISSING_REQUIRED_ARGUMENT,
                f"{tool} requires {', '.join(repr(a) for a in missing)}. "
                f"Required arguments: {spelled}",
            )

    if tool in SEMANTIC_TOOLS and not contract.has_metrics:
        return PreflightRejection(
            METRIC_TOOL_WITHOUT_METRICS,
            f"{tool} needs a metric layer and this dataset has none. Use "
            "aggregate_for_question or profile_table against a table instead.",
        )

    # The mirror of the rule above, and the one a real run needed. On the
    # demo warehouse the planner kept reaching for `aggregate_for_question`,
    # which maps a question onto one table's raw columns. The server refused
    # every call, correctly, since "revenue" is a metric-layer definition
    # and not a column, but only after a round trip, with a message about
    # ambiguous columns rather than about the layer being bypassed.
    if tool == "aggregate_for_question" and contract.has_metrics:
        return PreflightRejection(
            UPLOAD_TOOL_ON_GOVERNED_DATASET,
            "aggregate_for_question maps a question onto one table's raw "
            "columns and this dataset has a governed metric layer, where the "
            "measures are defined. Use compute_metric, compare_segments or "
            "analyze_timeseries with one of: "
            f"{', '.join(contract.metric_names[:12])}",
        )

    metric_names = _metric_arguments(arguments)
    for value in metric_names:
        if contract.metric(value) is None:
            return PreflightRejection(
                UNKNOWN_METRIC,
                f"metric {value!r} is not defined. Defined metrics: "
                f"{', '.join(contract.metric_names) or '(none)'}",
            )

    rejection = _check_dimensions(arguments, metric_names, contract)
    if rejection is not None:
        return rejection

    rejection = _check_filters(arguments, metric_names, contract)
    if rejection is not None:
        return rejection

    if tool == "statistical_test":
        rejection = _check_statistical_test(arguments, contract)
        if rejection is not None:
            return rejection

    for argument in sorted(set(arguments) & _TABLE_ARGUMENTS):
        value = arguments[argument]
        if isinstance(value, str) and contract.table(value) is None:
            return PreflightRejection(
                UNKNOWN_TABLE,
                f"table {value!r} does not exist. Tables in this dataset: "
                f"{', '.join(contract.table_names) or '(none)'}",
            )

    rejection = _check_columns(tool, arguments, contract)
    if rejection is not None:
        return rejection

    for argument in sorted(set(arguments) & _GRAIN_ARGUMENTS):
        value = arguments[argument]
        if isinstance(value, str) and contract.grains and value not in contract.grains:
            return PreflightRejection(
                UNKNOWN_GRAIN,
                f"{argument} {value!r} is not a valid time grain. Valid grains: "
                f"{', '.join(contract.grains)}",
            )

    return None


def _metric_arguments(arguments: dict[str, Any]) -> list[str]:
    """Metric names this call references, from the arguments that hold them."""
    found: list[str] = []
    for key in ("metric",):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            found.append(value)
    for key in ("metrics", "required_metrics"):
        value = arguments.get(key)
        if isinstance(value, list):
            found.extend(v for v in value if isinstance(v, str) and v)
    return found


def _check_dimensions(
    arguments: dict[str, Any],
    metric_names: list[str],
    contract: ExecutionContract,
) -> PreflightRejection | None:
    """A dimension is only meaningful relative to a metric.

    Checked against the metric this call names rather than against the union
    of every metric's dimensions, because `revenue by carrier` is wrong in a
    way that "carrier is a dimension somewhere" would hide.
    """
    named: list[str] = []
    for key in ("dimension",):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            named.append(value)
    for key in ("dimensions", "group_by"):
        value = arguments.get(key)
        if isinstance(value, list):
            named.extend(v for v in value if isinstance(v, str) and v)
    if not named:
        return None

    for metric_name in metric_names:
        metric = contract.metric(metric_name)
        if metric is None:  # pragma: no cover - already rejected above
            continue
        for dimension in named:
            if dimension not in metric.valid_dimensions:
                return PreflightRejection(
                    UNKNOWN_DIMENSION,
                    f"metric {metric_name!r} does not support dimension "
                    f"{dimension!r}. Valid dimensions for {metric_name}: "
                    f"{', '.join(metric.valid_dimensions) or '(none)'}",
                )
    return None


def _check_filters(
    arguments: dict[str, Any],
    metric_names: list[str],
    contract: ExecutionContract,
) -> PreflightRejection | None:
    """Filter shape, and the columns a metric may be filtered on.

    A filter that does not apply is not a small mistake: silently dropping
    one changes what the returned number means, so the engine refuses. The
    filterable set is the metric's own dimensions plus its time field --
    deliberately narrow, because filters arrive from a model and a wider
    surface is a wider surface for `SQLGuard` to defend.
    """
    raw = arguments.get("filters")
    if raw is None:
        return None
    if not isinstance(raw, list):
        return PreflightRejection(
            MALFORMED_FILTER,
            f"filters must be a list of {{column, op, value}} objects, not {type(raw).__name__}",
        )

    for item in raw:
        if not isinstance(item, dict):
            return PreflightRejection(
                MALFORMED_FILTER,
                f"each filter must be a {{column, op, value}} object; got {item!r}",
            )
        unknown = sorted(set(item) - FILTER_KEYS)
        if unknown or "column" not in item:
            return PreflightRejection(
                MALFORMED_FILTER,
                f"filter {item!r} is not the right shape. Use "
                '{"column": <name>, "op": <operator>, "value": <value>}',
            )

        column = item.get("column")
        if not isinstance(column, str):
            continue
        for metric_name in metric_names:
            metric = contract.metric(metric_name)
            if metric is None:  # pragma: no cover - rejected earlier
                continue
            allowed = [*metric.valid_dimensions, metric.time_field]
            if column not in allowed:
                return PreflightRejection(
                    UNFILTERABLE_COLUMN,
                    f"metric {metric_name!r} cannot be filtered on {column!r}. "
                    f"Filterable: {', '.join(n for n in allowed if n)}",
                )
    return None


def _check_statistical_test(
    arguments: dict[str, Any],
    contract: ExecutionContract,
) -> PreflightRejection | None:
    """The `variables` contract, which the tool schema cannot express.

    `variables` is typed `object` in the MCP schema, so the argument list a
    worker is shown says nothing about what belongs inside it. The engine
    has always known. `stats.TEST_VARIABLES` is the same table the
    handlers enforce, and a real run spent six calls rediscovering it one
    error at a time.

    Column *types* are checked too. A correlation between two text columns
    is not a weaker result, it is a meaningless one, and the contract
    already carries every column's type.
    """
    from agentic_analytics.analytics.stats import TEST_RELATION_KEYS, TEST_VARIABLES

    test_type = arguments.get("test_type")
    if not isinstance(test_type, str) or test_type not in TEST_VARIABLES:
        return PreflightRejection(
            UNKNOWN_TEST_TYPE,
            f"test_type {test_type!r} does not exist. Valid: {', '.join(sorted(TEST_VARIABLES))}",
        )

    variables = arguments.get("variables")
    if not isinstance(variables, dict):
        return PreflightRejection(
            MISSING_TEST_VARIABLE,
            "variables must be an object naming the relation and the columns "
            f"this test needs: {', '.join(TEST_VARIABLES[test_type])}",
        )

    if not any(isinstance(variables.get(k), str) for k in TEST_RELATION_KEYS):
        return PreflightRejection(
            MISSING_TEST_VARIABLE,
            "variables must name the relation to test on, as "
            f"{' or '.join(TEST_RELATION_KEYS)}. Tables: "
            f"{', '.join(contract.table_names) or '(none)'}",
        )

    for key in TEST_VARIABLES[test_type]:
        if not isinstance(variables.get(key), str) or not variables[key]:
            return PreflightRejection(
                MISSING_TEST_VARIABLE,
                f"{test_type} needs variables.{key}. It needs: "
                f"{', '.join(TEST_VARIABLES[test_type])}, plus the relation.",
            )

    table_name = next(
        (variables[k] for k in TEST_RELATION_KEYS if isinstance(variables.get(k), str)), None
    )
    table = contract.table(str(table_name))
    if table is None or table.truncated_columns:
        # A relation this contract does not fully describe (a metric model,
        # or a table whose columns were trimmed) cannot support a "no such
        # column" verdict.
        return None

    known = dict(table.columns)
    for key in TEST_VARIABLES[test_type]:
        column = variables[key]
        if column not in known:
            return PreflightRejection(
                UNKNOWN_COLUMN,
                f"table {table_name!r} has no column {column!r}. Columns: "
                f"{', '.join(sorted(known))}",
            )
        if key in _NUMERIC_TEST_VARIABLES and not _is_numeric(known[column]):
            return PreflightRejection(
                NON_NUMERIC_COLUMN,
                f"{test_type} needs a numeric {key}, and {column!r} is "
                f"{known[column]}. Numeric columns: "
                f"{', '.join(sorted(c for c, k in known.items() if _is_numeric(k))) or '(none)'}",
            )
    return None


#: Variables that must name a numeric column. A group label may be text; the
#: value being tested may not.
_NUMERIC_TEST_VARIABLES = frozenset({"value_column", "x_column", "y_column"})

#: DuckDB type names that a statistical test can use.
_NUMERIC_TYPES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "FLOAT",
    "DOUBLE",
    "REAL",
    "DECIMAL",
    "NUMERIC",
)


def _is_numeric(duck_type: str) -> bool:
    return duck_type.upper().startswith(_NUMERIC_TYPES)


def _check_columns(
    tool: str,
    arguments: dict[str, Any],
    contract: ExecutionContract,
) -> PreflightRejection | None:
    """Column references, but only where a column is what is meant.

    Restricted to calls that already name a real table. A `columns` argument
    on a metric tool is a different thing, and checking it against physical
    columns would reject the semantic interface for using semantic names.
    """
    if tool in SEMANTIC_TOOLS:
        return None
    table_name = arguments.get("table")
    if not isinstance(table_name, str):
        return None
    table = contract.table(table_name)
    if table is None:  # pragma: no cover - already rejected above
        return None

    known = {name for name, _ in table.columns}
    if not known or table.truncated_columns:
        # An incompletely described table cannot support a "does not exist"
        # verdict: the column may be one of the ones not shown.
        return None

    named: list[str] = []
    for key in ("columns", "group_by", "dimensions"):
        value = arguments.get(key)
        if isinstance(value, list):
            named.extend(v for v in value if isinstance(v, str) and v)
    for key in ("column", "value_column", "group_column"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            named.append(value)

    for column in named:
        if column not in known:
            return PreflightRejection(
                UNKNOWN_COLUMN,
                f"table {table_name!r} has no column {column!r}. Columns of "
                f"{table_name}: {', '.join(sorted(known))}",
            )
    return None
