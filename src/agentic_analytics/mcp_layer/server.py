"""The Analytics MCP server.

Built on the official MCP Python SDK v2 (``mcp.server.MCPServer``, formerly
``FastMCP``). Tools are ordinary typed Python functions; the SDK derives the
input and output schemas from the annotations and speaks the protocol.

Every tool resolves a session by id, performs a deterministic computation, and
returns a bounded structured payload. Tools do not call a model, and the
server holds no model credentials.

There are two transports, and which one carries production traffic matters:

* **In-process, and this is what the deployed website uses.** The agent holds
  a real :class:`mcp.Client` connected to this server object. It is a genuine
  client/server pair speaking the protocol -- not a function call dressed up
  as one -- and it is what makes the deployment a single container. It is not
  a network hop.
* **Streamable HTTP at** ``/mcp``, for callers outside the process. Exercised
  by ``tests/integration/test_mcp.py`` against a real server and again
  against the built container. On the public deployment it is deliberately
  withdrawn: ``AAE_MCP_ALLOWED_HOSTS`` is empty, so the transport answers 503
  rather than serving without Host validation, and an anonymous demo gains
  nothing from an internet-facing MCP endpoint.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from pydantic import BaseModel, Field

from agentic_analytics.analytics import catalog as catalog_tools
from agentic_analytics.analytics import compute as compute_tools
from agentic_analytics.analytics import stats as stats_tools
from agentic_analytics.analytics import upload_plan
from agentic_analytics.analytics.execute import QueryError, run_query
from agentic_analytics.analytics.filters import FilterError, coerce_filters
from agentic_analytics.analytics.results import GroupCoverage, ResultSnapshot
from agentic_analytics.analytics.semantic import infer_schema
from agentic_analytics.config import Budgets, Settings, get_settings
from agentic_analytics.logging import get_logger
from agentic_analytics.warehouse.metrics import load_registry
from agentic_analytics.warehouse.session import AnalysisSession, DisclosurePolicy, SessionManager

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
    decomposition: dict[str, Any] | None = None

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
            decomposition=payload.get("decomposition"),
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


class ProfileField(BaseModel):
    name: str
    data_type: str
    role: str
    null_pct: float
    distinct_count: int
    reason: str
    min_value: str | None = None
    max_value: str | None = None


class DatasetProfile(BaseModel):
    """Inferred analytical shape of a table.

    `status` is always "inferred": these roles come from column types and
    cardinality, not from a governed definition anyone wrote down.
    """

    table: str
    row_count: int
    status: str = "inferred"
    fields: list[ProfileField] = Field(default_factory=list)
    time_fields: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    measures: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    ambiguities: list[dict[str, Any]] = Field(default_factory=list)


class MetricList(BaseModel):
    metrics: list[MetricInfo]
    note: str = ""


def _attach_group_coverage(
    session: AnalysisSession,
    snapshot: ResultSnapshot,
    mapping: Any,
    budgets: Budgets,
) -> None:
    """Record what a grouped result covers, and trim any overflow.

    The breakdown SQL asks for one group more than the engine accepts, so a
    result arriving at the ceiling plus one is known to be short rather than
    assumed complete. The extra group is dropped before anything cites the
    result -- a reader must not see a group the coverage block says was not
    returned.

    Coverage counts come from their own aggregate over the same filtered
    population. Deriving them from the returned rows is what produced
    "every row in the dataset" for a result holding 55% of them.

    A failure here leaves `group_coverage` unset, which callers read as "not
    a grouped answer". That is the safe direction: no claim of completeness
    is made from a missing block.
    """
    if not upload_plan.is_breakdown(mapping):
        return

    ceiling = upload_plan.GROUP_RESULT_MAX
    overflowed = len(snapshot.rows) > ceiling
    if overflowed:
        del snapshot.rows[ceiling:]
        snapshot.row_count = len(snapshot.rows)

    groups_total: int | None = None
    rows_matching: int | None = None
    rows_total: int | None = None
    try:
        coverage_sql = upload_plan.build_coverage_sql(mapping)
        if coverage_sql:
            counted = run_query(
                session,
                coverage_sql,
                tool_name="aggregate_for_question:coverage",
                max_rows=1,
                timeout_seconds=budgets.query_timeout_seconds,
                max_sql_length=budgets.max_sql_length,
            )
            if counted.rows:
                record = counted.to_records()[0]
                groups_total = int(record.get("groups_total") or 0)
                rows_matching = int(record.get("rows_matching") or 0)
        totals = run_query(
            session,
            upload_plan.build_row_total_sql(mapping),
            tool_name="aggregate_for_question:rows",
            max_rows=1,
            timeout_seconds=budgets.query_timeout_seconds,
            max_sql_length=budgets.max_sql_length,
        )
        if totals.rows:
            rows_total = int(totals.to_records()[0].get("rows_total") or 0)
    except (QueryError, ValueError, TypeError) as exc:  # pragma: no cover
        log.warning("group_coverage_unavailable", error=str(exc)[:120])

    rows_represented: int | None = None
    if "row_count" in snapshot.columns:
        index = snapshot.columns.index("row_count")
        counts = [
            float(value)
            for row in snapshot.rows
            if isinstance((value := row[index]), int | float) and not isinstance(value, bool)
        ]
        if len(counts) == len(snapshot.rows):
            rows_represented = int(sum(counts))

    returned = len(snapshot.rows)
    complete = not overflowed and (groups_total is None or returned >= groups_total)
    snapshot.group_coverage = GroupCoverage(
        complete=complete,
        groups_returned=returned,
        groups_total=groups_total,
        rows_total=rows_total,
        rows_matching=rows_matching,
        rows_represented=rows_represented,
        query_limit=ceiling if overflowed else None,
        ordering="dimension",
        ranked_by_request=False,
    )


def build_server(manager: SessionManager, settings: Settings | None = None) -> MCPServer:
    """Create the MCP server bound to a session manager.

    Taking the manager as an argument rather than reaching for a global keeps
    the server constructible in a test with its own isolated sessions.
    """
    cfg = settings or get_settings()
    budgets: Budgets = cfg.budgets
    #: A deployment-wide opt-in, not the policy itself. Whether raw uploaded
    #: cells may be disclosed is decided per run: one process now serves a
    #: deterministic run and an AI run against the same session, so a policy
    #: read from `AAE_PROVIDER_MODE` would hand the AI side the local answer.
    _row_disclosure_opt_in = cfg.allow_upload_row_disclosure
    #: The floor under that per-run decision. A process configured to infer
    #: in the cloud has no local run to protect, so every call is remote
    #: whatever the caller injected. Without this the flag is fail-open: a
    #: caller that simply omits it gets the disclosing answer.
    _remote_inference_floor = cfg.provider_mode == "cloud"
    mcp: MCPServer = MCPServer(
        name=SERVER_NAME,
        version="0.1.0",
        instructions=SERVER_INSTRUCTIONS,
        description="Read-only analytical tools over a DuckDB dataset.",
    )

    def _session(session_id: str, session_key: str | None) -> AnalysisSession:
        """Resolve a session from its handle and capability.

        Both are injected by the MCP client, never chosen by a model. A tool
        call carrying a handle but no matching capability is refused, so the
        handle alone -- which appears in resource URIs -- grants nothing.
        """
        try:
            session = manager.get(session_id, session_key)
        except KeyError as exc:
            raise ToolError(str(exc)) from None
        return session

    def _policy(session: AnalysisSession, remote_inference: bool) -> DisclosurePolicy:
        """The disclosure policy for one call, from that call's own run.

        `remote_inference` is injected by the MCP client alongside the
        capability and is not something a model can set. It can only widen
        the remote set, never narrow it below what the deployment already
        is, so omitting it withholds rather than discloses.
        """
        return DisclosurePolicy(
            remote_inference=bool(remote_inference) or _remote_inference_floor,
            dataset_kind=session.kind,
            allow_upload_row_disclosure=_row_disclosure_opt_in,
        )

    def _public_session(session_id: str) -> AnalysisSession:
        """Resolve a session for a resource read, demo datasets only.

        The built-in warehouse is identical for every visitor and holds no
        user data, so its schema and results are safe to expose by handle. An
        uploaded session is refused: its data is reachable only through the
        tools, which carry the capability.
        """
        try:
            session = manager.get_unchecked(session_id)
        except KeyError as exc:
            # ResourceError, not ToolError: the SDK wraps an unexpected
            # exception from a resource handler and the message is lost.
            raise ResourceError(str(exc)) from None
        if session.kind != "demo":
            raise ResourceError(
                "this resource serves the built-in demo dataset only; read an "
                "uploaded session through the tools, which carry its capability"
            )
        return session

    def _guarded(fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Translate internal errors into recoverable tool errors.

        A `ToolError` is returned to the model as an error result it can act
        on -- a misspelled metric, an invalid dimension -- rather than
        crashing the run. Nothing here exposes a stack trace or a host path.
        """
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
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
        except Exception as exc:
            # Anything else is a defect in the engine rather than a bad call,
            # but the model still has to be told something it can act on. Left
            # to escape, the SDK replaces it with "Error executing tool X",
            # which is what a real run saw eight times in one question: the
            # worker could not tell a broken argument from a broken tool, and
            # neither could the operator reading the artifact afterwards.
            log.exception("tool_failed_unexpectedly", tool=getattr(fn, "__name__", "?"))
            raise ToolError(f"the tool failed internally ({type(exc).__name__})") from None

    def _filters(raw: Any) -> Any:
        """Coerce filters *inside* the guard.

        Every tool called `_filters(filters)` in its argument list, so
        the `FilterError` it raises happened before `_guarded` was entered
        and escaped it -- in all eight tools that take filters. A model
        writing `{"field": ..., "operator": ...}` instead of
        `{"column": ..., "op": ...}` therefore got the SDK's generic message
        rather than the one naming the shape it should have used.
        """
        return _guarded(coerce_filters, raw)

    # ---------------------------------------------------------------- tools

    @mcp.tool(description="List the tables available in this dataset with their row counts.")
    def list_tables(session_id: str, session_key: str) -> TableList:
        session = _session(session_id, session_key)
        return TableList(
            tables=[TableSummary(**t) for t in catalog_tools.list_tables(session)],
            dataset_kind=session.kind,
            dataset_fingerprint=session.dataset_fingerprint,
        )

    @mcp.tool(description="Describe one table: its columns, types and row count.")
    def describe_table(
        session_id: str, session_key: str, table: str, remote_inference: bool = False
    ) -> TableDescription:
        # Column names and types are schema, not cells, so this needs no
        # policy: it discloses the shape of the data and never its values.
        session = _session(session_id, session_key)
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
    def profile_table(
        session_id: str, session_key: str, table: str, remote_inference: bool = False
    ) -> ToolResult:
        session = _session(session_id, session_key)
        policy = _policy(session, remote_inference)
        snapshot = _guarded(
            catalog_tools.profile_table,
            session,
            table,
            timeout_seconds=budgets.query_timeout_seconds,
        )
        # On the snapshot, not on the session. Two runs share a session, so
        # a flag stored there is one the other run can change underneath
        # this one.
        snapshot.withhold_cells = policy.withhold_raw_cells
        return ToolResult.of(snapshot)

    @mcp.tool(
        description=(
            "Return a few raw rows from a table. Capped at "
            f"{budgets.max_sample_rows} rows; use aggregates for anything larger."
        )
    )
    def sample_rows(
        session_id: str,
        session_key: str,
        table: str,
        limit: int = 10,
        remote_inference: bool = False,
    ) -> ToolResult:
        session = _session(session_id, session_key)
        policy = _policy(session, remote_inference)
        if policy.withhold_raw_cells:
            # Raw cells are the only path by which unaggregated user data
            # reaches a prompt, and a prompt in this configuration goes to a
            # third party. Somebody trying a spreadsheet in a demo has not
            # agreed to that. Schema, profile and aggregates are enough to
            # plan an analysis, and they are all still available.
            raise ToolError(
                "raw rows from an uploaded dataset are not disclosed while model "
                "inference is remote; use profile_table or an aggregate instead"
            )
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
    def run_readonly_sql(
        session_id: str, session_key: str, sql: str, remote_inference: bool = False
    ) -> ToolResult:
        session = _session(session_id, session_key)
        policy = _policy(session, remote_inference)
        if not policy.allow_row_returning_sql:
            # `SELECT name, city FROM uploaded_data LIMIT 20` is ordinary
            # read-only SQL and the guard permits it, so for an uploaded
            # file under remote inference this is a disclosure path. It is
            # refused rather than filtered: proving a query is
            # aggregation-only is a larger thing than this needs, and a
            # filter that is nearly right leaks.
            raise ToolError(
                "arbitrary SQL over an uploaded dataset is not available while "
                "model inference is remote; use profile_table, "
                "aggregate_for_question or a governed metric tool instead"
            )
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
    def list_metrics(session_id: str, session_key: str) -> MetricList:
        session = _session(session_id, session_key)
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
        session_key: str,
        metric: str,
        dimensions: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        time_grain: Literal["day", "week", "month", "quarter", "year"] | None = None,
        task_id: str | None = None,
    ) -> ToolResult:
        session = _session(session_id, session_key)
        snapshot = _guarded(
            compute_tools.compute_metric,
            session,
            metric,
            dimensions or [],
            _filters(filters),
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
        session_key: str,
        metric: str,
        dimension: str,
        segments: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        task_id: str | None = None,
    ) -> ToolResult:
        session = _session(session_id, session_key)
        snapshot = _guarded(
            compute_tools.compare_segments,
            session,
            metric,
            dimension,
            segments,
            _filters(filters),
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
        session_key: str,
        metric: str,
        grain: Literal["day", "week", "month", "quarter", "year"] = "month",
        time_column: str | None = None,
        filters: list[dict[str, Any]] | None = None,
        task_id: str | None = None,
    ) -> ToolResult:
        session = _session(session_id, session_key)
        snapshot = _guarded(
            compute_tools.analyze_timeseries,
            session,
            metric,
            grain,
            time_column,
            _filters(filters),
            task_id=task_id,
            max_rows=budgets.max_result_rows,
            timeout_seconds=budgets.query_timeout_seconds,
        )
        return ToolResult.of(snapshot)

    @mcp.tool(
        description=(
            "Compare one metric between two explicit date windows. Returns "
            "both values with the absolute and percentage change already "
            "computed. Use this rather than subtracting two separate calls."
        )
    )
    def compare_periods(
        session_id: str,
        session_key: str,
        metric: str,
        baseline_start: str,
        baseline_end: str,
        current_start: str,
        current_end: str,
        filters: list[dict[str, Any]] | None = None,
        task_id: str | None = None,
    ) -> ToolResult:
        session = _session(session_id, session_key)
        snapshot = _guarded(
            compute_tools.compare_periods,
            session,
            metric,
            (baseline_start, baseline_end),
            (current_start, current_end),
            _filters(filters),
            task_id=task_id,
            timeout_seconds=budgets.query_timeout_seconds,
        )
        return ToolResult.of(snapshot)

    @mcp.tool(
        description=(
            "Attribute a metric's change between two periods to the values of "
            "a dimension. An additive metric (revenue, units) decomposes into "
            "per-segment contributions summing to the total change. A rate "
            "metric (gross_margin_pct, return_rate) decomposes into a rate "
            "effect, a mix effect and an interaction term, separating 'every "
            "segment got worse' from 'volume moved to the worse segments'. "
            "This is the tool for a 'what caused it' question; do not attempt "
            "the attribution yourself."
        )
    )
    def decompose_change(
        session_id: str,
        session_key: str,
        metric: str,
        dimension: str,
        baseline_start: str,
        baseline_end: str,
        current_start: str,
        current_end: str,
        filters: list[dict[str, Any]] | None = None,
        task_id: str | None = None,
    ) -> ToolResult:
        session = _session(session_id, session_key)
        snapshot = _guarded(
            compute_tools.decompose_change,
            session,
            metric,
            dimension,
            (baseline_start, baseline_end),
            (current_start, current_end),
            _filters(filters),
            task_id=task_id,
            max_rows=budgets.max_result_rows,
            timeout_seconds=budgets.query_timeout_seconds,
        )
        return ToolResult.of(snapshot)

    @mcp.tool(
        description=(
            "The segments that moved a metric the most between two periods, "
            "largest influence first."
        )
    )
    def rank_contributors(
        session_id: str,
        session_key: str,
        metric: str,
        dimension: str,
        baseline_start: str,
        baseline_end: str,
        current_start: str,
        current_end: str,
        top_n: int = 5,
        filters: list[dict[str, Any]] | None = None,
        task_id: str | None = None,
    ) -> ToolResult:
        session = _session(session_id, session_key)
        snapshot = _guarded(
            compute_tools.rank_contributors,
            session,
            metric,
            dimension,
            (baseline_start, baseline_end),
            (current_start, current_end),
            top_n,
            _filters(filters),
            task_id=task_id,
            timeout_seconds=budgets.query_timeout_seconds,
        )
        return ToolResult.of(snapshot)

    @mcp.tool(
        description=(
            "Infer the analytical shape of a table: which columns are time "
            "fields, dimensions, measures or identifiers, with null rates and "
            "cardinality. Use this first on an uploaded dataset, which has no "
            "predefined metric layer. The roles are inferred from types and "
            "cardinality, not governed definitions."
        )
    )
    def profile_dataset(
        session_id: str, session_key: str, table: str, remote_inference: bool = False
    ) -> DatasetProfile:
        session = _session(session_id, session_key)
        policy = _policy(session, remote_inference)
        name = _guarded(catalog_tools.resolve_table, session, table)
        schema = _guarded(infer_schema, session, name)
        payload = schema.as_dict()
        if policy.withhold_raw_cells:
            # A column's range is two cells of the visitor's file, not a
            # summary of it, so it does not travel to a remote model.
            payload["fields"] = [
                f | {"min_value": None, "max_value": None} for f in payload["fields"]
            ]
        return DatasetProfile(**payload)

    @mcp.tool(
        description=(
            "Answer a question about an arbitrary table by mapping it onto "
            "the inferred schema and computing a bounded aggregate. The "
            "mapping is validated and the SQL is composed by the engine. "
            "Fails with an explanation when the question cannot be mapped "
            "without guessing which column was meant -- use profile_dataset "
            "then."
        )
    )
    def aggregate_for_question(
        session_id: str,
        session_key: str,
        table: str,
        question: str,
        contract: dict[str, Any] | None = None,
    ) -> ToolResult:
        session = _session(session_id, session_key)
        name = _guarded(catalog_tools.resolve_table, session, table)
        schema = _guarded(infer_schema, session, name)
        if contract is None:
            mapping = upload_plan.resolve_question(question, schema.as_dict())
        else:
            if len(json.dumps(contract, sort_keys=True).encode("utf-8")) > 16_384:
                raise ToolError("the query contract is too large")
            mapping = upload_plan.mapping_from_contract(question, schema.as_dict(), contract)
        sql = upload_plan.build_sql(mapping)
        if sql is None:
            # Refusing is the feature. Returning the sum of whichever numeric
            # column happened to be first would look like an answer.
            raise ToolError(
                "this question could not be mapped to the table without guessing "
                f"({mapping.explanation}); call profile_dataset to see the columns"
            )
        snapshot = _guarded(
            run_query,
            session,
            sql,
            tool_name="aggregate_for_question",
            parameters={"table": name, "question": question} | mapping.as_dict(),
            max_rows=budgets.max_result_rows,
            timeout_seconds=budgets.query_timeout_seconds,
            max_sql_length=budgets.max_sql_length,
            extra_warnings=[
                f"question interpreted by {mapping.interpretation}: {mapping.explanation}"
            ],
        )
        # Where each output column came from, so an alias cannot be read
        # as a column of the uploaded file.
        snapshot.column_lineage = upload_plan.sql_lineage(mapping)
        _attach_group_coverage(session, snapshot, mapping, budgets)
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
        session_key: str,
        table: str,
        columns: list[str],
        filters: list[dict[str, Any]] | None = None,
        task_id: str | None = None,
    ) -> ToolResult:
        session = _session(session_id, session_key)
        snapshot = _guarded(
            stats_tools.correlation_matrix,
            session,
            table,
            columns,
            _filters(filters),
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
        session_key: str,
        test_type: str,
        variables: dict[str, Any],
        filters: list[dict[str, Any]] | None = None,
        task_id: str | None = None,
    ) -> ToolResult:
        session = _session(session_id, session_key)
        snapshot = _guarded(
            stats_tools.statistical_test,
            session,
            test_type,
            variables,
            _filters(filters),
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
    def get_result(
        session_id: str, session_key: str, result_id: str, remote_inference: bool = False
    ) -> ToolResult:
        session = _session(session_id, session_key)
        snapshot = _guarded(session.results.get, result_id)
        # Re-decided for *this* call rather than read off the snapshot. The
        # stored flag is whatever the run that computed it was allowed, and
        # a session is shared: without this, re-reading a local run's
        # profile would hand a cloud run the cells it may not see.
        policy = _policy(session, remote_inference)
        snapshot.withhold_cells = policy.withhold_raw_cells
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
        session = _public_session(session_id)
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
        session = _public_session(session_id)
        snapshot: ResultSnapshot = _guarded(session.results.get, result_id)
        return json.dumps(snapshot.model_dump(mode="json"), indent=2, default=str)

    return mcp


TOOL_NAMES: tuple[str, ...] = (
    "list_tables",
    "profile_dataset",
    "compare_periods",
    "decompose_change",
    "rank_contributors",
    "describe_table",
    "profile_table",
    "aggregate_for_question",
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
