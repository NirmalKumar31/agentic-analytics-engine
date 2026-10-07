"""The result contract.

Every analytical tool execution produces a :class:`ResultSnapshot`. The
snapshot is the only thing an agent is allowed to cite, and it is produced by
deterministic code, never by a model. A finding that references
``result_id`` can therefore always be re-checked against the rows that
actually came back.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

Scalar = str | int | float | bool | None

CoverageComponent = Literal[
    "operation",
    "measure",
    "dimensions",
    "time_grain",
    "period",
    "filters",
    "ranking_direction",
]
CoverageRejectionCode = Literal[
    "unresolved_question",
    "missing_requested_measure",
    "missing_requested_grouping",
    "missing_requested_time_grain",
    "missing_requested_filter",
    "changed_requested_operation",
    "changed_ranking_direction",
    "result_shape_too_large",
]


def new_result_id() -> str:
    return f"res_{uuid.uuid4().hex[:12]}"


class StatisticalResult(BaseModel):
    """Output of a statistical test.

    Every field here is computed by SciPy or by explicit arithmetic in
    :mod:`agentic_analytics.analytics.stats`. No model writes these numbers.
    """

    test_name: str
    statistic: float
    p_value: float
    sample_sizes: dict[str, int] = Field(default_factory=dict)
    effect_size: float | None = None
    effect_size_name: str | None = None
    confidence_interval: tuple[float, float] | None = None
    confidence_level: float = 0.95
    # Assumptions the caller should know were made, and any that look shaky
    # for this particular sample.
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    # Multiple-comparison accounting. When one analytical task runs several
    # related tests, the family is corrected with Holm's step-down method and
    # `p_value_adjusted` is what the publication gate reads. A single test
    # leaves these unset, and `p_value_adjusted` then equals `p_value`.
    p_value_adjusted: float | None = None
    correction_method: str | None = None
    family_size: int = 1

    # Sampling. Populated only when a test could not read every row, so a
    # reader can tell an exact result from an estimated one.
    rows_available: int | None = None
    rows_used: int | None = None
    sampling_applied: bool = False
    sampling_method: str | None = None
    sampling_seed: int | None = None

    @property
    def effective_p_value(self) -> float:
        """The p-value a significance claim must be judged against."""
        return self.p_value if self.p_value_adjusted is None else self.p_value_adjusted

    @property
    def is_significant(self) -> bool:
        """Significant at the 5% level after any correction."""
        return self.effective_p_value < 0.05


class GroupCoverage(BaseModel):
    """How much of a grouped answer a result actually carries.

    This exists because completeness was being *inferred*, and every
    available signal inferred it wrongly. A grouped aggregate was capped at
    25 groups by the SQL itself, so `truncated`, which means "the result
    exceeded the transport limit" -- stayed false; the population line was
    derived from "the question stated no filters"; and the row count was
    summed over the rows that came back. On a 45-store table all three
    agreed that a top-25 answer covering 3,575 of 6,435 rows was the
    complete breakdown of every row.

    So the four different things that "limit" can mean are recorded
    separately and a reader-facing claim is computed from them, never
    guessed:

    * ``query_limit``: the LIMIT the engine put in the SQL.
    * transport truncation: ``ResultSnapshot.truncated``, unchanged.
    * a UI preview cap: the frontend's business, and never allowed to
      change any number here.
    * ``complete``: whether every group the question asked for is present.
    """

    #: Whether every requested group is in this result.
    complete: bool
    #: Groups in this result.
    groups_returned: int
    #: Groups the question would have produced. `None` when not counted.
    groups_total: int | None = None
    #: Rows in the table, before any filter.
    rows_total: int | None = None
    #: Rows passing the contract's filters and period.
    rows_matching: int | None = None
    #: Rows behind the groups actually returned.
    rows_represented: int | None = None
    #: Non-null measure values in the full filtered population. Aggregate
    #: functions ignore null measures, so this is the effective sample size.
    observations_matching: int | None = None
    #: Non-null measure values behind the groups actually returned.
    observations_represented: int | None = None
    #: The LIMIT the SQL applied, if any.
    query_limit: int | None = None
    #: What the result is ordered by. A breakdown orders by its dimension;
    #: only an explicit ranking orders by the measure.
    ordering: Literal["dimension", "measure", "period"] = "dimension"
    #: Whether the question asked for a ranking. A breakdown that happens to
    #: be cut short is not a top-N list, and must not be described as one.
    ranked_by_request: bool = False

    @property
    def groups_omitted(self) -> int | None:
        if self.groups_total is None:
            return None
        return max(self.groups_total - self.groups_returned, 0)


class QuestionCoverage(BaseModel):
    """Whether the accepted executable contract covers the question.

    This is independent of planner equality and claim verification. Two
    planners may agree on the same incomplete contract; a supported claim
    may faithfully describe a result that answered a different question.
    """

    complete: bool
    required_components: list[CoverageComponent] = Field(default_factory=list)
    applied_components: list[CoverageComponent] = Field(default_factory=list)
    missing_components: list[CoverageComponent] = Field(default_factory=list)
    rejection_codes: list[CoverageRejectionCode] = Field(default_factory=list)
    details: list[str] = Field(default_factory=list)


#: Profile columns whose values are individual cells rather than summaries.
RAW_CELL_COLUMNS = frozenset({"min_value", "max_value"})


class ResultSnapshot(BaseModel):
    """A single tool execution and everything needed to audit it."""

    result_id: str = Field(default_factory=new_result_id)
    tool_name: str
    task_id: str | None = None
    # The exact statement that ran. `None` only for tools that do not query
    # (for example `get_result`).
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Scalar]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    dataset_fingerprint: str = ""
    started_at: float = Field(default_factory=time.time)
    duration_ms: float = 0.0
    parameters: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    statistical_result: StatisticalResult | None = None
    # Present when this result is a driver decomposition. A first-class field
    # rather than a parameter, because an agent has to read the rate/mix split
    # and the reconciliation flag to say anything about it.
    decomposition: dict[str, Any] | None = None
    #: Set for an uploaded dataset when model inference is remote. Redaction
    #: happens in `compact()`, the one representation handed to an agent,
    #: rather than at each call site, so a new agent role cannot forget it.
    #: The stored snapshot keeps every value: the visitor sees their own
    #: file in full, and numeric verification still checks against the truth.
    withhold_cells: bool = False
    #: Declared type per column, as the engine produced it. Present so a
    #: cell holding numeric text can be read as a number when, and only
    #: when the column it came from is numeric. Absent on artifacts
    #: written before this field existed, which read as "nothing declared"
    #: and therefore as "no coercion", the safe direction.
    column_types: dict[str, str] = Field(default_factory=dict)
    #: Where an output column came from, for aggregates:
    #: ``{"total_net_value": {"aggregate": "SUM", "table": "uploaded_data",
    #: "column": "net_value"}}``. Without it an alias is indistinguishable
    #: from a physical column, and a verifier asked to check "the sum of
    #: total_net_value" cannot tell whether the claim invents a column or
    #: names the engine's own output.
    column_lineage: dict[str, dict[str, str]] = Field(default_factory=dict)
    #: Present for a grouped aggregate. Absent means "this result is not a
    #: grouped answer", never "it is complete". A caller must not read a
    #: missing value as a completeness guarantee.
    group_coverage: GroupCoverage | None = None

    def declared_type(self, column: str) -> str | None:
        """The declared type of one output column, if recorded."""
        return self.column_types.get(column)

    def lineage_of(self, column: str) -> dict[str, str] | None:
        """What an output column is derived from, if recorded."""
        return self.column_lineage.get(column)

    def cell(self, row: int, column: str) -> Scalar:
        """Value at a row index and column name, for evidence references."""
        if column not in self.columns:
            raise KeyError(f"column {column!r} not in result {self.result_id}")
        if not 0 <= row < len(self.rows):
            raise IndexError(f"row {row} out of range for result {self.result_id}")
        return self.rows[row][self.columns.index(column)]

    def to_records(self) -> list[dict[str, Scalar]]:
        return [dict(zip(self.columns, row, strict=False)) for row in self.rows]

    def agent_rows(self) -> list[list[Scalar]]:
        """The rows an agent may read. Use this wherever a prompt is built.

        When `withhold_cells` is set, a profile's per-column minimum and
        maximum are blanked. Those two are not summaries of the data, they
        are cells of it -- the largest amount in the file, the earliest date
        in it, and an uploaded file's cells do not go to a third party.

        One method rather than a check at each call site: the leak this
        closes was a prompt that rendered `snapshot.rows` directly while the
        structured payload beside it was already redacted.
        """
        if not self.withhold_cells:
            return self.rows
        hidden = {i for i, name in enumerate(self.columns) if name in RAW_CELL_COLUMNS}
        if not hidden:
            return self.rows
        return [
            [None if i in hidden else value for i, value in enumerate(row)] for row in self.rows
        ]

    def agent_cell(self, row: int, column: str) -> Scalar:
        """The value at a cell *as an agent may be shown it*.

        `cell` returns the truth and is what numeric verification checks
        against. This one goes through `agent_rows`, so a withheld cell
        stays withheld. Any prompt that renders a single value wants this
        method; nothing else should reach into `rows`.
        """
        if column not in self.columns:
            raise KeyError(f"column {column!r} not in result {self.result_id}")
        rows = self.agent_rows()
        if not 0 <= row < len(rows):
            raise IndexError(f"row {row} out of range for result {self.result_id}")
        return rows[row][self.columns.index(column)]

    def compact(self) -> dict[str, Any]:
        """The representation handed to an agent.

        Rows are included because an agent must be able to read the numbers it
        will cite, but the shape is fixed and bounded by the caller.
        """
        payload: dict[str, Any] = {
            "result_id": self.result_id,
            "tool_name": self.tool_name,
            "columns": self.columns,
            "rows": self.agent_rows(),
            "row_count": self.row_count,
            "truncated": self.truncated,
            "dataset_fingerprint": self.dataset_fingerprint,
        }
        if self.warnings:
            payload["warnings"] = self.warnings
        if self.statistical_result is not None:
            payload["statistical_result"] = self.statistical_result.model_dump(exclude_none=True)
        if self.decomposition is not None:
            payload["decomposition"] = self.decomposition
        return payload


EvidenceKind = Literal["calculated_fact", "statistical_result", "interpretation"]


class EvidenceCell(BaseModel):
    """A pointer to one cell of one result."""

    result_id: str
    row: int
    column: str
    value: Scalar = None
    label: str | None = None


class ResultStore:
    """In-memory, per-session store of result snapshots.

    Bounded so that a long run cannot grow without limit; the oldest results
    are dropped first and a dropped id fails loudly on lookup rather than
    returning something else.
    """

    def __init__(self, max_results: int = 512) -> None:
        self._results: dict[str, ResultSnapshot] = {}
        self._order: list[str] = []
        self._max = max_results

    def put(self, snapshot: ResultSnapshot) -> ResultSnapshot:
        self._results[snapshot.result_id] = snapshot
        self._order.append(snapshot.result_id)
        while len(self._order) > self._max:
            self._results.pop(self._order.pop(0), None)
        return snapshot

    def get(self, result_id: str) -> ResultSnapshot:
        try:
            return self._results[result_id]
        except KeyError:
            raise KeyError(f"unknown result_id {result_id!r}") from None

    def has(self, result_id: str) -> bool:
        return result_id in self._results

    def all(self) -> list[ResultSnapshot]:
        return [self._results[r] for r in self._order if r in self._results]

    def __len__(self) -> int:
        return len(self._results)
