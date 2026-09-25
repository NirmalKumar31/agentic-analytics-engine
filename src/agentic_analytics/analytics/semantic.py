"""Session-scoped semantic inference for arbitrary datasets.

The built-in warehouse has a governed metric layer: definitions written by
hand, reviewed, and identical for every run. An uploaded file has nothing of
the kind, so one is inferred here from types and cardinality.

Everything this module produces is marked ``status: inferred``. That
distinction is load-bearing and is carried through to the UI: a governed
metric means "someone defined revenue"; an inferred measure means "this
column is numeric and looks additive". Presenting the second as the first
would be inventing business semantics.

The inference is deterministic -- it reads column types and distinct counts,
not a model -- so the same file always yields the same schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agentic_analytics.analytics.execute import QueryError, fetch_rows
from agentic_analytics.warehouse.session import AnalysisSession

FieldRole = Literal["time", "dimension", "measure", "identifier", "ignored"]

INTEGER_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
    }
)
REAL_TYPES = frozenset({"FLOAT", "DOUBLE", "REAL", "DECIMAL"})
NUMERIC_TYPES = INTEGER_TYPES | REAL_TYPES
TEMPORAL_TYPES = frozenset(
    {"DATE", "TIMESTAMP", "TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS", "TIME"}
)

# A numeric column whose values are nearly all distinct is far more likely to
# be a key than a quantity worth summing.
IDENTIFIER_UNIQUENESS = 0.92
# Above this many distinct values a text column is a label, not a grouping.
MAX_DIMENSION_CARDINALITY = 200
# Column names that are keys regardless of how they are typed.
IDENTIFIER_HINTS = ("_id", "id_", "uuid", "guid", "key", "code", "number", "no.")


@dataclass
class InferredField:
    """One column and what it appears to be."""

    name: str
    data_type: str
    role: FieldRole
    null_pct: float
    distinct_count: int
    reason: str
    min_value: str | None = None
    max_value: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "role": self.role,
            "null_pct": self.null_pct,
            "distinct_count": self.distinct_count,
            "reason": self.reason,
            "min_value": self.min_value,
            "max_value": self.max_value,
        }


@dataclass
class InferredSchema:
    """The analytical shape of one table, inferred rather than governed."""

    table: str
    row_count: int
    fields: list[InferredField] = field(default_factory=list)
    #: Present when two or more columns could plausibly be the same concept
    #: and the choice changes the answer.
    ambiguities: list[dict[str, Any]] = field(default_factory=list)

    @property
    def time_fields(self) -> list[str]:
        return [f.name for f in self.fields if f.role == "time"]

    @property
    def dimensions(self) -> list[str]:
        return [f.name for f in self.fields if f.role == "dimension"]

    @property
    def measures(self) -> list[str]:
        return [f.name for f in self.fields if f.role == "measure"]

    @property
    def identifiers(self) -> list[str]:
        return [f.name for f in self.fields if f.role == "identifier"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "row_count": self.row_count,
            "status": "inferred",
            "fields": [f.as_dict() for f in self.fields],
            "time_fields": self.time_fields,
            "dimensions": self.dimensions,
            "measures": self.measures,
            "identifiers": self.identifiers,
            "ambiguities": self.ambiguities,
        }

    def summary_line(self) -> str:
        return (
            f"{self.row_count:,} rows, {len(self.fields)} columns "
            f"({len(self.measures)} measures, {len(self.dimensions)} dimensions, "
            f"{len(self.time_fields)} time fields)"
        )


def _base_type(duck_type: str) -> str:
    return duck_type.split("(")[0].strip().upper()


def _looks_like_identifier(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith("id") or any(hint in lowered for hint in IDENTIFIER_HINTS)


def infer_schema(session: AnalysisSession, table: str) -> InferredSchema:
    """Classify every column of a table by type and cardinality."""
    info = session.tables[table]
    total = max(info.row_count, 1)

    selects: list[str] = []
    for column in info.columns:
        name, dtype = column["name"], _base_type(column["type"])
        quoted = f'"{name.replace(chr(34), chr(34) * 2)}"'
        literal = "'" + name.replace("'", "''") + "'"
        type_literal = "'" + dtype.replace("'", "''") + "'"
        if dtype in NUMERIC_TYPES or dtype in TEMPORAL_TYPES:
            bounds = f"MIN({quoted})::VARCHAR, MAX({quoted})::VARCHAR"
        else:
            bounds = "NULL::VARCHAR, NULL::VARCHAR"
        selects.append(
            f"SELECT {literal} AS name, {type_literal} AS data_type, "
            f"COUNT({quoted}) AS non_null, COUNT(DISTINCT {quoted}) AS distinct_count, "
            f'{bounds} FROM "{table}"'
        )

    try:
        _, rows = fetch_rows(session, "\nUNION ALL\n".join(selects))
    except QueryError as exc:
        raise QueryError(f"the dataset could not be profiled: {exc}") from None

    fields: list[InferredField] = []
    for name, dtype, non_null, distinct, low, high in rows:
        null_pct = round(100.0 * (1 - float(non_null) / total), 2)
        distinct_count = int(distinct)
        role, reason = _classify(str(name), str(dtype), distinct_count, total)
        fields.append(
            InferredField(
                name=str(name),
                data_type=str(dtype),
                role=role,
                null_pct=null_pct,
                distinct_count=distinct_count,
                reason=reason,
                min_value=str(low) if low is not None else None,
                max_value=str(high) if high is not None else None,
            )
        )

    # Preserve the table's own column order rather than the query's.
    order = {c["name"]: i for i, c in enumerate(info.columns)}
    fields.sort(key=lambda f: order.get(f.name, 999))

    schema = InferredSchema(table=table, row_count=info.row_count, fields=fields)
    schema.ambiguities = _find_ambiguities(schema)
    return schema


def _classify(name: str, dtype: str, distinct_count: int, row_count: int) -> tuple[FieldRole, str]:
    """Assign a role, and say why. The reason is shown to the user."""
    if dtype in TEMPORAL_TYPES:
        return "time", "temporal type"

    if dtype in NUMERIC_TYPES:
        if _looks_like_identifier(name):
            return "identifier", "numeric, but the name reads as a key"
        if distinct_count <= 1:
            return "ignored", "constant"
        # Uniqueness only implies a key for integers. A monetary column is
        # often almost entirely distinct and is still a quantity to sum, so
        # applying the heuristic to reals would turn revenue into an id.
        if dtype in INTEGER_TYPES:
            uniqueness = distinct_count / max(row_count, 1)
            if uniqueness >= IDENTIFIER_UNIQUENESS and row_count > 50:
                return "identifier", f"integer and {uniqueness:.0%} distinct, so probably a key"
        if distinct_count <= 12:
            return "dimension", f"numeric with only {distinct_count} distinct values"
        return "measure", "numeric and aggregatable"

    if dtype == "BOOLEAN":
        return "dimension", "boolean"

    # Text and everything else.
    if distinct_count <= 1:
        return "ignored", "constant"
    if _looks_like_identifier(name):
        return "identifier", "the name reads as a key"
    if distinct_count <= MAX_DIMENSION_CARDINALITY:
        return "dimension", f"{distinct_count} distinct values"
    # Nearly-unique text with no key-like name is free text, not an
    # identifier. Either way it cannot be grouped by.
    return "ignored", f"{distinct_count} distinct values is too many to group by"


# Concepts where picking the wrong column silently changes the answer.
_AMBIGUOUS_CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "sales", "amount", "net", "gross", "total", "value"),
    "cost": ("cost", "cogs", "expense", "spend"),
    "quantity": ("quantity", "qty", "units", "count"),
    "date": ("date", "time", "timestamp", "day", "month"),
}


def _find_ambiguities(schema: InferredSchema) -> list[dict[str, Any]]:
    """Cases where the user should be asked rather than guessed at.

    Only raised when two or more columns plausibly mean the same thing. One
    candidate is not ambiguous, and zero candidates is not a question worth
    asking.
    """
    out: list[dict[str, Any]] = []
    for concept, hints in _AMBIGUOUS_CONCEPTS.items():
        pool = schema.time_fields if concept == "date" else schema.measures
        candidates = [name for name in pool if any(hint in name.lower() for hint in hints)]
        if len(candidates) >= 2:
            out.append(
                {
                    "concept": concept,
                    "candidates": candidates,
                    "question": (
                        f"Which column should be treated as {concept}: "
                        + " or ".join(f"`{c}`" for c in candidates)
                        + "?"
                    ),
                }
            )
    return out
