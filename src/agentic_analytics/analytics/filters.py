"""Structured filters.

Agents never write a WHERE clause as text. They supply column/operator/value
triples, the column is checked against the model's declared dimensions, the
operator against a closed set, and the value is bound as a query parameter.
A filter therefore cannot carry SQL even if a model is talked into trying.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

FilterOp = Literal[
    "=",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
    "in",
    "not_in",
    "between",
    "is_null",
    "is_not_null",
]

# Operators that take no value, one value, a list, or exactly two values.
_NO_VALUE = {"is_null", "is_not_null"}
_LIST_VALUE = {"in", "not_in"}
_PAIR_VALUE = {"between"}

MAX_IN_VALUES = 100


class FilterError(ValueError):
    """A filter was malformed or referenced a column that is not filterable."""


class Filter(BaseModel):
    """One predicate."""

    column: str
    op: FilterOp = "="
    value: Any = None

    model_config = {"extra": "forbid"}


def build_where(
    filters: list[Filter] | None,
    allowed_columns: set[str],
    column_map: dict[str, str] | None = None,
) -> tuple[str, list[Any]]:
    """Render filters to a parameterised WHERE clause.

    ``column_map`` translates an agent-facing dimension name to the column in
    the underlying relation. Anything not in ``allowed_columns`` is rejected.
    """
    if not filters:
        return "", []

    clauses: list[str] = []
    params: list[Any] = []
    for f in filters:
        if f.column not in allowed_columns:
            raise FilterError(
                f"cannot filter on {f.column!r}; filterable columns: {sorted(allowed_columns)}"
            )
        column = (column_map or {}).get(f.column, f.column)
        quoted = f'"{column}"'

        if f.op in _NO_VALUE:
            clauses.append(f"{quoted} IS NULL" if f.op == "is_null" else f"{quoted} IS NOT NULL")
            continue

        if f.op in _LIST_VALUE:
            values = f.value if isinstance(f.value, list | tuple) else [f.value]
            values = [v for v in values if v is not None]
            if not values:
                raise FilterError(f"filter on {f.column!r} with op {f.op!r} needs values")
            if len(values) > MAX_IN_VALUES:
                raise FilterError(
                    f"filter on {f.column!r} has {len(values)} values; the limit is {MAX_IN_VALUES}"
                )
            placeholders = ", ".join("?" for _ in values)
            negate = "NOT " if f.op == "not_in" else ""
            clauses.append(f"{quoted} {negate}IN ({placeholders})")
            params.extend(values)
            continue

        if f.op in _PAIR_VALUE:
            if not isinstance(f.value, list | tuple) or len(f.value) != 2:
                raise FilterError(
                    f"filter on {f.column!r} with op 'between' needs exactly two values"
                )
            clauses.append(f"{quoted} BETWEEN ? AND ?")
            params.extend(list(f.value))
            continue

        if f.value is None:
            raise FilterError(f"filter on {f.column!r} with op {f.op!r} needs a value")
        clauses.append(f"{quoted} {f.op} ?")
        params.append(f.value)

    return " WHERE " + " AND ".join(clauses), params


def coerce_filters(raw: Any) -> list[Filter]:
    """Accept the several shapes a model tends to produce for filters.

    Models emit ``[{"column": ..., "op": ..., "value": ...}]`` when prompted
    well and ``{"region": "West"}`` when not. Both are accepted; anything
    else raises rather than being silently dropped, because a silently
    dropped filter changes what a number means.
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [Filter(column=k, op="=", value=v) for k, v in raw.items()]
    if isinstance(raw, list):
        out: list[Filter] = []
        for item in raw:
            if isinstance(item, Filter):
                out.append(item)
            elif isinstance(item, dict):
                try:
                    out.append(Filter(**item))
                except Exception as exc:
                    raise FilterError(f"malformed filter {item!r}: {exc}") from None
            else:
                raise FilterError(f"malformed filter {item!r}")
        return out
    raise FilterError(f"filters must be a list or an object, got {type(raw).__name__}")
