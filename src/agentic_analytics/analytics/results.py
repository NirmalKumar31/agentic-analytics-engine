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

    def cell(self, row: int, column: str) -> Scalar:
        """Value at a row index and column name, for evidence references."""
        if column not in self.columns:
            raise KeyError(f"column {column!r} not in result {self.result_id}")
        if not 0 <= row < len(self.rows):
            raise IndexError(f"row {row} out of range for result {self.result_id}")
        return self.rows[row][self.columns.index(column)]

    def to_records(self) -> list[dict[str, Scalar]]:
        return [dict(zip(self.columns, row, strict=False)) for row in self.rows]

    def compact(self) -> dict[str, Any]:
        """The representation handed to an agent.

        Rows are included because an agent must be able to read the numbers it
        will cite, but the shape is fixed and bounded by the caller.
        """
        payload: dict[str, Any] = {
            "result_id": self.result_id,
            "tool_name": self.tool_name,
            "columns": self.columns,
            "rows": self.rows,
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
