"""The Analytics MCP server.

Built on the official MCP Python SDK v2 (``mcp.server.MCPServer``, formerly
``FastMCP``). Tools are ordinary typed Python functions; the SDK derives the
input and output schemas from the annotations and speaks the protocol.

Every tool resolves a session by id, performs a deterministic computation, and
returns a bounded structured payload. Tools do not call a model, and the
server holds no model credentials.

The server is served over Streamable HTTP in production (mounted into the
FastAPI app at ``/mcp``) and connected to in-process in tests, which is the
SDK-supported way to exercise a real client/server pair without a socket.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from agentic_analytics.analytics import catalog as catalog_tools
from agentic_analytics.analytics import compute as compute_tools
from agentic_analytics.analytics import stats as stats_tools
from agentic_analytics.analytics.execute import QueryError, run_query
from agentic_analytics.analytics.filters import FilterError, coerce_filters
from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.config import Budgets, Settings, get_settings
from agentic_analytics.logging import get_logger
from agentic_analytics.warehouse.metrics import load_registry
from agentic_analytics.warehouse.session import AnalysisSession, SessionManager

log = get_logger(__name__)

SERVER_NAME = "agentic-analytics"
SERVER_INSTRUCTIONS = """\
Analytical tools over a read-only DuckDB dataset.

Prefer `compute_metric`, `compare_segments` and `analyze_timeseries` over
hand-written SQL: metrics are defined once in the semantic layer, so two tasks
that both report revenue cannot disagree about what revenue means. Use
`run_readonly_sql` only for shapes the metric layer cannot express.

Never state that a difference is significant without calling
`statistical_test`. Every tool returns a `result_id`; cite it for any number
you report.
"""


class ToolResult(BaseModel):
    """Common envelope. Every analytical tool returns one of these."""

    result_id: str
    tool_name: str
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    dataset_fingerprint: str = ""
    warnings: list[str] = Field(default_factory=list)
    statistical_result: dict[str, Any] | None = None

    @classmethod
    def of(cls, snapshot: ResultSnapshot) -> ToolResult:
        payload = snapshot.compact()
        return cls(
            result_id=payload["result_id"],
            tool_name=payload["tool_name"],
            columns=payload["columns"],
            rows=payload["rows"],
            row_count=payload["row_count"],
            truncated=payload["truncated"],
            dataset_fingerprint=payload["dataset_fingerprint"],
            warnings=payload.get("warnings", []),
            statistical_result=payload.get("statistical_result"),
        )


class TableSummary(BaseModel):
    name: str
    row_count: int
    column_count: int


class TableList(BaseModel):
    tables: list[TableSummary]
    dataset_kind: str
    dataset_fingerprint: str


class ColumnInfo(BaseModel):
    name: str
    type: str


class TableDescription(BaseModel):
    table: str
    row_count: int
    columns: list[ColumnInfo]
    dataset_fingerprint: str


class MetricInfo(BaseModel):
    name: str
    description: str
    expression: str
    valid_dimensions: list[str]
    time_field: str
    format: str


class MetricList(BaseModel):
    metrics: list[MetricInfo]
    note: str = ""


def build_server(manager: SessionManager, settings: Settings | None = None) -> MCPServer:
    """Create the MCP server bound to a session manager.

    Taking the manager as an argument rather than reaching for a global keeps
    the server constructible in a test with its own isolated sessions.
    """
    cfg = settings or get_settings()
    budgets: Budgets = cfg.budgets
    mcp: MCPServer = MCPServer(
        name=SERVER_NAME,
        version="0.1.0",
        instructions=SERVER_INSTRUCTIONS,
        description="Read-only analytical tools over a DuckDB dataset.",
    )

    def _session(session_id: str) -> AnalysisSession:
        try:
            return manager.get(session_id)
        except KeyError as exc:
            raise ToolError(str(exc)) from None

    def _guarded(fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Translate internal errors into recoverable tool errors.

        A `ToolError` is returned to the model as an error result it can act
        on -- a misspelled metric, an invalid dimension -- rather than
        crashing the run. Nothing here exposes a stack trace or a host path.
        """
        try:
            return fn(*args, **kwargs)
        except (
            QueryError,
            FilterError,
            compute_tools.MetricError,
            stats_tools.StatsError,
            catalog_tools.TableNotFound,
            KeyError,
            ValueError,
        ) as exc:
            message = exc.args[0] if exc.args else str(exc)
            raise ToolError(str(message)) from None

    # ---------------------------------------------------------------- tools

    @mcp.tool(description="List the tables available in this dataset with their row counts.")
    def list_tables(session_id: str) -> TableList:
        session = _session(session_id)
        return TableList(
            tables=[TableSummary(**t) for t in catalog_tools.list_tables(session)],
            dataset_kind=session.kind,
            dataset_fingerprint=session.dataset_fingerprint,
        )

    @mcp.tool(description="Describe one table: its columns, types and row count.")
    def describe_table(session_id: str, table: str) -> TableDescription:
        session = _session(session_id)
        described = _guarded(catalog_tools.describe_table, session, table)
        return TableDescription(
            table=described["table"],
            row_count=described["row_count"],
            columns=[ColumnInfo(**c) for c in described["columns"]],
            dataset_fingerprint=described["dataset_fingerprint"],
        )

    @mcp.tool(
        description=(
            "Per-column statistics for a table: null rate, distinct count, "
            "and min/max/mean where the type allows. Use this to orient "
            "yourself before writing SQL."
        )
    )
    def profile_table(session_id: str, table: str) -> ToolResult:
        session = _session(session_id)
        snapshot = _guarded(
            catalog_tools.profile_table,
            session,
            table,
            timeout_seconds=budgets.query_timeout_seconds,
        )
        return ToolResult.of(snapshot)

    @mcp.tool(
        description=(
            "Return a few raw rows from a table. Capped at "
            f"{budgets.max_sample_rows} rows; use aggregates for anything larger."
        )
    )
    def sample_rows(session_id: str, table: str, limit: int = 10) -> ToolResult:
        session = _session(session_id)
        snapshot = _guarded(
            catalog_tools.sample_rows,
            session,
            table,
            limit,
            max_sample_rows=budgets.max_sample_rows,
        )
        return ToolResult.of(snapshot)

    @mcp.tool(
        description=(
            "Run a read-only analytical SQL query. Only SELECT and "
            "WITH...SELECT are accepted, against this dataset's tables only. "
            "Prefer compute_metric when the metric layer can express the "
            "question."
        )
    )
    def run_readonly_sql(session_id: str, sql: str) -> ToolResult:
        session = _session(session_id)
        snapshot = _guarded(
            run_query,
            session,
            sql,
            tool_name="run_readonly_sql",
            max_rows=budgets.max_result_rows,
            timeout_seconds=budgets.query_timeout_seconds,
            max_sql_length=budgets.max_sql_length,
        )
        return ToolResult.of(snapshot)

    @mcp.tool(
        description=(
            "List the business metrics defined for this dataset, with their "
            "SQL expression and the dimensions each one supports."
        )
    )
    def list_metrics(session_id: str) -> MetricList:
        session = _session(session_id)
        if session.registry is None:
            return MetricList(
                metrics=[],
                note=(
                    "This dataset is a single uploaded table and has no metric "
                    "layer. Use profile_table and run_readonly_sql."
                ),
            )
        return MetricList(
            metrics=[MetricInfo(**m) for m in session.registry.describe_all()]  # type: ignore[arg-type]
        )

    @mcp.tool(
        description=(
            "Compute a defined metric, optionally grouped by dimensions and a "
            "time grain (day, week, month, quarter, year). Filters are a list "
            "of {column, op, value} objects."
        )
    )
    def compute_metric(
        session_id: str,
        metric: str,
        dimensions: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        time_grain: Literal["day", "week", "month", "quarter", "year"] | None = None,
        task_id: str | None = None,
    ) -> ToolResult:
        session = _session(session_id)
        snapshot = _guarded(
            compute_tools.compute_metric,
            session,
            metric,
            dimensions or [],
            coerce_filters(filters),
            time_grain,
            task_id=task_id,
            max_rows=budgets.max_result_rows,
            timeout_seconds=budgets.query_timeout_seconds,
        )
        return ToolResult.of(snapshot)

    @mcp.tool(
        description=(
            "Compare one metric across the values of one dimension. Returns "
            "each segment's value, rank, share of total and difference from "
            "the best segment."
        )
    )
    def compare_segments(
        session_id: str,
        metric: str,
        dimension: str,
        segments: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        task_id: str | None = None,
    ) -> ToolResult:
        session = _session(session_id)
        snapshot = _guarded(
            compute_tools.compare_segments,
            session,
            metric,
            dimension,
            segments,
            coerce_filters(filters),
            task_id=task_id,
            max_rows=budgets.max_result_rows,
            timeout_seconds=budgets.query_timeout_seconds,
        )
        return ToolResult.of(snapshot)

    @mcp.tool(
        description=(
            "Compute a metric over time at a given grain, with the "
            "period-over-period absolute and percentage change already "
            "calculated. Do not compute period changes yourself."
        )
    )
    def analyze_timeseries(
        session_id: str,
        metric: str,
        grain: Literal["day", "week", "month", "quarter", "year"] = "month",
        time_column: str | None = None,
        filters: list[dict[str, Any]] | None = None,
        task_id: str | None = None,
    ) -> ToolResult:
        session = _session(session_id)
        snapshot = _guarded(
            compute_tools.analyze_timeseries,
            session,
            metric,
            grain,
            time_column,
            coerce_filters(filters),
            task_id=task_id,
            max_rows=budgets.max_result_rows,
            timeout_seconds=budgets.query_timeout_seconds,
        )
        return ToolResult.of(snapshot)

    @mcp.tool(
        description=(
            "Pairwise Pearson correlation between numeric columns of a table "
            "or semantic model. Correlation is not causation and this does not "
            "adjust for confounders."
        )
    )
    def correlation_matrix(
        session_id: str,
        table: str,
        columns: list[str],
        filters: list[dict[str, Any]] | None = None,
        task_id: str | None = None,
    ) -> ToolResult:
        session = _session(session_id)
        snapshot = _guarded(
            stats_tools.correlation_matrix,
            session,
            table,
            columns,
            coerce_filters(filters),
            task_id=task_id,
            timeout_seconds=budgets.query_timeout_seconds,
        )
        return ToolResult.of(snapshot)

    @mcp.tool(
        description=(
            "Run a statistical test. test_type is one of: two_proportion_z, "
            "welch_t_test, one_way_anova, chi_square, pearson_correlation, "
            "spearman_correlation. `variables` names the model/table and the "
            "columns the test needs, for example "
            '{"model": "customer_lifecycle", "group_column": '
            '"first_delivery_status", "value_column": "is_repeat", '
            '"groups": ["late", "on_time"]}. The statistic, p-value, effect '
            "size and confidence interval are computed here; never estimate "
            "them yourself."
        )
    )
    def statistical_test(
        session_id: str,
        test_type: str,
        variables: dict[str, Any],
        filters: list[dict[str, Any]] | None = None,
        task_id: str | None = None,
    ) -> ToolResult:
        session = _session(session_id)
        snapshot = _guarded(
            stats_tools.statistical_test,
            session,
            test_type,
            variables,
            coerce_filters(filters),
            task_id=task_id,
            timeout_seconds=budgets.query_timeout_seconds,
        )
        return ToolResult.of(snapshot)

    @mcp.tool(
        description=(
            "Retrieve a previously computed result by its result_id, so a "
            "finding can be re-checked against the exact rows it cites."
        )
    )
    def get_result(session_id: str, result_id: str) -> ToolResult:
        session = _session(session_id)
        snapshot = _guarded(session.results.get, result_id)
        return ToolResult.of(snapshot)

    # ------------------------------------------------------------ resources

    @mcp.resource(
        "dataset://catalog",
        name="Dataset catalog",
        description="Tables, row counts and columns for the active session.",
        mime_type="application/json",
    )
    def dataset_catalog() -> str:
        sessions = manager.describe_all()
        return json.dumps({"sessions": sessions}, indent=2)

    @mcp.resource(
        "dataset://schema/{session_id}/{table}",
        name="Table schema",
        description="Column names and types for one table.",
        mime_type="application/json",
    )
    def dataset_schema(session_id: str, table: str) -> str:
        session = _session(session_id)
        return json.dumps(_guarded(catalog_tools.describe_table, session, table), indent=2)

    @mcp.resource(
        "metrics://definitions",
        name="Metric definitions",
        description="The semantic metric layer: every metric and its SQL expression.",
        mime_type="application/json",
    )
    def metric_definitions() -> str:
        return json.dumps({"metrics": load_registry().describe_all()}, indent=2)

    @mcp.resource(
        "result://{session_id}/{result_id}",
        name="Result snapshot",
        description="A stored result, including the SQL that produced it.",
        mime_type="application/json",
    )
    def result_resource(session_id: str, result_id: str) -> str:
        session = _session(session_id)
        snapshot: ResultSnapshot = _guarded(session.results.get, result_id)
        return json.dumps(snapshot.model_dump(mode="json"), indent=2, default=str)

    return mcp


TOOL_NAMES: tuple[str, ...] = (
    "list_tables",
    "describe_table",
    "profile_table",
    "sample_rows",
    "run_readonly_sql",
    "list_metrics",
    "compute_metric",
    "compare_segments",
    "analyze_timeseries",
    "correlation_matrix",
    "statistical_test",
    "get_result",
)
