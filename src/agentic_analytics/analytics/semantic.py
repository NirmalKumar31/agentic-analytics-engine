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

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal, cast

from agentic_analytics.analytics.execute import QueryError, fetch_rows
from agentic_analytics.analytics.row_filters import VALUE_LOOKUP_LIMIT
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
# A numeric column is a grouping only if its values genuinely repeat: few
# distinct values *and* a small share of the rows. An absolute threshold
# alone turns every numeric column in a ten-row file into a dimension.
#
# The absolute ceiling was 12, and that was the blind spot. A sales table
# with 45 stores over 6,435 rows has a `Store` column that repeats 143
# times per value -- 0.7% distinct, about as categorical as data gets --
# and it was classified as a *measure* because 45 > 12. The engine then
# summed store numbers: "which store had the highest total sales" became
# the total of store ids grouped by holiday flag, and "average profit by
# store" answered with the average store id (23) instead of refusing for
# the missing column.
#
# The share test is what actually distinguishes a key from a quantity, and
# `MIN_ROWS_FOR_CARDINALITY_RULES` is what stops it firing on tiny files.
# The ceiling only has to agree with the one used for text groupings, so a
# 45-value integer key and a 45-value text key classify alike.
MAX_NUMERIC_DIMENSION_DISTINCT = 12
MAX_NUMERIC_DIMENSION_SHARE = 0.2

# The second tier, for a key with more values than a small enumeration.
# Cardinality alone cannot separate a key from a quantity when both are
# low-cardinality integers, so the discriminator is how often a value
# repeats rather than how many values there are:
#
#   Store      45 distinct / 6,435 rows = 0.7%   -> 143 repeats each
#   rating      5 distinct /   500 rows = 1.0%   -> 100 repeats each
#   qty        40 distinct /   500 rows = 8.0%   ->  12 repeats each
#
# A column whose every value recurs across dozens of rows is identifying
# something those rows share. A quantity recorded per row does not behave
# that way. `qty` stays a measure under this rule and `Store` becomes a
# dimension, which is the distinction that matters.
#
# This is a heuristic and it has a known failure: a genuinely
# low-cardinality quantity in a very large table -- `qty` between 1 and 40
# across 100,000 rows -- reads as a dimension here. That direction is the
# safe one. A quantity misread as a dimension is still aggregatable when
# the question names it, so "total qty by region" is unaffected; a key
# misread as a measure is not recoverable, and produced "the average
# profit by Store is 23" -- the mean of the store numbers -- for a table
# with no profit column at all.
KEY_DIMENSION_MAX_DISTINCT = 200
KEY_DIMENSION_MAX_SHARE = 0.02
MIN_ROWS_FOR_CARDINALITY_RULES = 40
#: How few distinct values a key-named text column may hold before it is
#: read as a category instead. Deliberately small: a genuine key has
#: hundreds, and this is only reached when the name suggested otherwise.
NAMED_KEY_DIMENSION_DISTINCT = 25
#: Weak evidence that a numeric column labels a thing rather than measuring
#: one. Consulted *only* inside the uncertain band, never on its own.
#:
#: Cardinality cannot settle this band and pretending otherwise would be
#: dishonest: a branch number recurring across 20 rows and a basket size
#: recurring across 12 are indistinguishable from the data. So the name is
#: allowed to tip a decision it is not allowed to make -- which is why
#: `basket_size` and `units` stay measures while `outlet_no` and `postcode`
#: become groupings, and why a column called `qty` is unaffected however
#: its values happen to be distributed.
CODE_NAME_HINTS = (
    "account",
    "article",
    "branch",
    "cat",
    "class",
    "dept",
    "district",
    "employee",
    "grade",
    "group",
    "outlet",
    "post",
    "ref",
    "region",
    "sku",
    "staff",
    "store",
    "team",
    "tier",
    "warehouse",
    "zip",
    "zone",
)


def _looks_like_a_label(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in CODE_NAME_HINTS)


#: Names that read as a quantity worth adding up. Summing one of these is
#: a normal business question, so it may be suggested unprompted.
STRONGLY_ADDITIVE_HINTS = (
    "amount",
    "balance",
    "clicks",
    "cost",
    "count",
    "gmv",
    "impressions",
    "orders",
    "paid",
    "payment",
    "price",
    "profit",
    "qty",
    "quantity",
    "revenue",
    "sales",
    "sessions",
    "spend",
    "tickets",
    "total",
    "units",
    "value",
    "views",
    "visits",
    "volume",
)

#: Names that read as an attribute of a row rather than a quantity of it.
#: Adding these up is arithmetically possible and analytically meaningless:
#: the sum of ages, of ratings, of percentages. An explicit request to sum
#: one is honoured; nothing suggests it.
WEAKLY_ADDITIVE_HINTS = (
    "age",
    "average",
    "avg",
    "grade",
    "index",
    "latitude",
    "level",
    "longitude",
    "margin_pct",
    "median",
    "percent",
    "pct",
    "position",
    "rank",
    "rate",
    "ratio",
    "rating",
    "score",
    "share",
    "temperature",
    "tenure",
    "year",
)

#: Generated columns a suggestion must never name.
NOISE_PREFIXES = ("noise_", "random_", "dummy_", "unused_")

AdditiveConfidence = Literal["strong", "weak", "unknown"]

#: Where a field's effective role came from. `inferred` is this module's own
#: classification; `user_confirmed` means the person who uploaded the file
#: said which of two readings applies, for their session only.
RoleSource = Literal["inferred", "user_confirmed"]

#: The only transitions this version offers.
#:
#: Deliberately narrow. The close call this module reports is a numeric
#: column that could be a quantity or a code list, so those are the two
#: readings a person can choose between. Offering `time`, `identifier` or
#: `ignored` would be offering conversions no inference class has been
#: tested against, and an untested transition is a way to produce a wrong
#: number with a confident label on it.
CONFIRMABLE_ROLES: tuple[FieldRole, ...] = ("measure", "dimension")


def _additive_confidence(name: str, role: FieldRole, dtype: str) -> AdditiveConfidence:
    """How safe it is to *suggest* summing this column.

    Separate from whether it can be summed at all, which is the role's job.
    The engine will total any numeric column a visitor names; what it must
    not do is propose the total of a column whose sum means nothing. The
    site suggested "Which team_size contributes most to age?", which is
    reproducible arithmetic and not a question anyone wants answered.
    """
    if role != "measure" or dtype not in NUMERIC_TYPES:
        return "unknown"
    lowered = name.lower()
    if any(lowered.startswith(prefix) for prefix in NOISE_PREFIXES):
        return "weak"
    # Matched on name *tokens*, not substrings. `page_views` contains
    # "age", and so do `average`, `usage`, `package` and `coverage`, so
    # substring matching called a page-view count an attribute.
    tokens = {token for token in re.split(r"[^a-z0-9]+", lowered) if token}
    if tokens & set(WEAKLY_ADDITIVE_HINTS):
        return "weak"
    if tokens & set(STRONGLY_ADDITIVE_HINTS):
        return "strong"
    return "unknown"


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
    #: How safe it is to *suggest* summing this column, as distinct from
    #: whether it may be summed when asked. See `_additive_confidence`.
    additive: AdditiveConfidence = "unknown"
    #: Whether the role was a close call. A numeric column can sit in a
    #: band where a code list and a genuine count are indistinguishable
    #: from the data; resolving that silently is how a store number became
    #: a measure and was averaged. Surfaced so the schema can say so.
    ambiguous: bool = False
    min_value: str | None = None
    max_value: str | None = None
    #: What inference decided, kept even after a confirmation replaces the
    #: effective role. `role` is the effective one and stays the
    #: compatibility field; this is the historical fact beside it. Losing it
    #: would make a confirmed column indistinguishable from one the engine
    #: got right on its own, which is the difference a reader is entitled to.
    #: `None` means the two are the same and nothing was confirmed.
    inferred_role: FieldRole | None = None
    #: Whether `role` is this module's classification or the session owner's.
    role_source: RoleSource = "inferred"

    @property
    def effective_inferred_role(self) -> FieldRole:
        """What inference decided, whether or not it was later confirmed."""
        return self.inferred_role if self.inferred_role is not None else self.role

    @property
    def allowed_confirmed_roles(self) -> list[FieldRole]:
        """The readings a session owner may choose between for this column.

        Empty unless inference reported a close call: a confident
        classification is not a menu, and offering one would invite someone
        to "correct" a column the data already settles.
        """
        if not self.ambiguous:
            return []
        return list(CONFIRMABLE_ROLES)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "role": self.role,
            "null_pct": self.null_pct,
            "distinct_count": self.distinct_count,
            "additive": self.additive,
            # Unchanged by a confirmation. The values are still
            # indistinguishable; someone has supplied the missing fact, which
            # is a different thing from the data having settled it.
            "ambiguous": self.ambiguous,
            "reason": self.reason,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "inferred_role": self.effective_inferred_role,
            "role_source": self.role_source,
            "allowed_confirmed_roles": self.allowed_confirmed_roles,
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
    #: Which generation of session role confirmations produced this schema.
    #: Zero is raw inference. A run records the revision it used so its
    #: evidence stays readable after the session's schema moves on.
    schema_revision: int = 0

    @property
    def confirmed_role_count(self) -> int:
        return sum(1 for f in self.fields if f.role_source == "user_confirmed")

    @property
    def unresolved_ambiguity_count(self) -> int:
        """Close calls nobody has settled yet.

        A confirmed field is still ambiguous -- the values did not change --
        so counting `ambiguous` alone would keep reporting work that is
        done.
        """
        return sum(1 for f in self.fields if f.ambiguous and f.role_source == "inferred")

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

    @property
    def aggregatable_if_named(self) -> list[str]:
        """Numeric columns that were classified as something else.

        A low-cardinality integer is read as a dimension, because a column
        holding 1 to 9 across four hundred rows is more often a band or a
        rating than a quantity. That is the right default for a column
        nobody mentioned.

        It is the wrong answer when the question names it: "total
        units_sold by store_code" has said exactly which column to total,
        and refusing on the grounds that the column might have been a
        rating substitutes the engine's guess for the user's instruction.
        Aggregating a named column is not a guess; these are offered only
        when the question names one.
        """
        return [
            f.name
            for f in self.fields
            if f.role in ("dimension", "identifier") and f.data_type in NUMERIC_TYPES
        ]

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
            "aggregatable_if_named": self.aggregatable_if_named,
            "ambiguities": self.ambiguities,
            # Still "inferred" at the top level even when fields carry
            # confirmations: the classification is this module's, and a
            # session owner settling two close calls does not make the
            # schema a governed definition.
            "schema_revision": self.schema_revision,
            "confirmed_role_count": self.confirmed_role_count,
            "unresolved_ambiguity_count": self.unresolved_ambiguity_count,
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


def category_value_lookup(session: AnalysisSession, table: str) -> Any:
    """A reader of a column's own values, for binding a named category.

    Built here because this is where the session and the profiled schema
    already meet. The values are read on the server and handed to the
    resolver; they are never put in a schema payload. `infer_schema` emits
    NULL bounds for non-numeric columns on purpose, so no cell value of a
    text column reaches the browser, and binding a filter must not be the
    thing that changes that.

    Returns `None` for a column that is not a bounded category -- too many
    distinct values to be something a reader names in a sentence -- so the
    caller falls back to its previous behaviour instead of scanning.
    """
    cache: dict[str, list[str] | None] = {}

    def lookup(column: str) -> list[str] | None:
        if column in cache:
            return cache[column]
        info = session.tables.get(table)
        names = {str(c["name"]) for c in (getattr(info, "columns", None) or [])}
        if column not in names:
            cache[column] = None
            return None
        quoted = f'"{column.replace(chr(34), chr(34) * 2)}"'
        quoted_table = f'"{table.replace(chr(34), chr(34) * 2)}"'
        try:
            _, rows = fetch_rows(
                session,
                f"SELECT DISTINCT {quoted}::VARCHAR FROM {quoted_table} "
                f"WHERE {quoted} IS NOT NULL LIMIT {VALUE_LOOKUP_LIMIT + 1}",
            )
        except QueryError:
            cache[column] = None
            return None
        if len(rows) > VALUE_LOOKUP_LIMIT:
            # Not a category. Declining to answer is the caller's job; this
            # only declines to pretend it knows the value set.
            cache[column] = None
            return None
        cache[column] = [str(row[0]) for row in rows]
        return cache[column]

    return lookup


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
        role, reason, ambiguous = _classify(
            str(name),
            str(dtype),
            distinct_count,
            total,
            non_null=int(non_null),
            low=low,
            high=high,
        )
        fields.append(
            InferredField(
                name=str(name),
                data_type=str(dtype),
                role=role,
                null_pct=null_pct,
                distinct_count=distinct_count,
                reason=reason,
                ambiguous=ambiguous,
                additive=_additive_confidence(str(name), role, str(dtype)),
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


#: A contiguous integer run covering its whole range, where the values are
#: also mostly distinct, is a row-numbering sequence.
#:
#: Coverage alone is not enough and assuming it was cost a working case: a
#: `qty` column of random integers from 1 to 40 fills its range exactly, so
#: it read as a sequence and was classified as an identifier. Small-range
#: integers nearly always fill their range. The uniqueness floor is what
#: separates `order_id` 1..500 over 500 rows from a quantity.
SEQUENCE_COVERAGE = 0.98
SEQUENCE_MIN_UNIQUENESS = 0.5
#: How often a key-named numeric column's values must recur before the data
#: overrules the name. Below this it is treated as an identifier.
KEY_NAME_MIN_REPEATS = 10
#: Years read as a dimension. A four-digit integer inside this window,
#: spanning at most a couple of centuries, is a calendar year -- summing it
#: is meaningless and grouping by it is the common request.
YEAR_MIN, YEAR_MAX, YEAR_MAX_SPAN = 1900, 2100, 200


def _as_int(text: str | None) -> int | None:
    try:
        return int(float(str(text)))
    except (TypeError, ValueError):
        return None


def _numeric_signals(
    dtype: str, distinct_count: int, non_null: int, row_count: int, low: Any, high: Any
) -> dict[str, Any]:
    """What the data says about a numeric column, before any naming.

    Every signal is derived from the profile already gathered, so this adds
    no queries. They are read together: no single one is decisive, which is
    the lesson of a store number classified as a measure because 45 > 12.
    """
    uniqueness = distinct_count / max(row_count, 1)
    bottom, top = _as_int(low), _as_int(high)
    span = (top - bottom + 1) if bottom is not None and top is not None else None
    return {
        "integral": dtype in INTEGER_TYPES,
        "uniqueness": uniqueness,
        "repeats": row_count // max(distinct_count, 1),
        "binary": distinct_count == 2 and bottom in (0, 1) and top in (0, 1),
        # Fills its own range: a label list rather than a measurement.
        "sequential": bool(
            span and span > 1 and distinct_count / span >= SEQUENCE_COVERAGE and span >= 10
        ),
        "year_like": bool(
            bottom is not None
            and top is not None
            and YEAR_MIN <= bottom <= YEAR_MAX
            and YEAR_MIN <= top <= YEAR_MAX
            and (span or 0) <= YEAR_MAX_SPAN
        ),
        "near_unique": uniqueness >= IDENTIFIER_UNIQUENESS and row_count > 50,
        "distinct": distinct_count,
        "non_null": non_null,
    }


def _classify_numeric(
    name: str, signals: dict[str, Any], row_count: int
) -> tuple[FieldRole, str, bool]:
    """Role for a numeric column, from the signals together.

    Ambiguity is returned rather than resolved silently. A column in the
    band where a code and a quantity look alike stays aggregatable, but is
    flagged so the schema can say so instead of presenting it as a settled
    measure.
    """
    distinct = int(signals["distinct"])
    uniqueness = float(signals["uniqueness"])

    if _looks_like_identifier(name):
        # A key-shaped name does not make a repeating column ungroupable.
        # `postcode`, `store_code` and `account_no` are ordinary groupings,
        # and reading the name over the data is what once made "total units
        # by store_code" unanswerable. The data decides; the name only
        # decides when the data is silent.
        if signals["near_unique"] or int(signals["repeats"]) < KEY_NAME_MIN_REPEATS:
            return "identifier", "numeric, and the name reads as a key", False
        return (
            "dimension",
            f"a code whose values each recur across about {signals['repeats']} rows",
            False,
        )

    if distinct <= 1:
        return "ignored", "constant", False

    if signals["binary"]:
        return "dimension", "a binary flag", False

    if not signals["integral"]:
        # A fractional value is a measurement. Nobody groups by a price.
        return "measure", "a fractional quantity", False

    if signals["near_unique"]:
        return "identifier", f"integer and {uniqueness:.0%} distinct, so probably a key", False

    if signals["sequential"] and uniqueness >= SEQUENCE_MIN_UNIQUENESS:
        return "identifier", "integers filling their own range, so a sequence", False

    if signals["year_like"] and row_count >= MIN_ROWS_FOR_CARDINALITY_RULES:
        return "dimension", "four-digit values in a calendar-year range", False

    if row_count >= MIN_ROWS_FOR_CARDINALITY_RULES:
        if distinct <= MAX_NUMERIC_DIMENSION_DISTINCT and uniqueness <= MAX_NUMERIC_DIMENSION_SHARE:
            return "dimension", f"integer repeating across only {distinct} values", False
        if distinct <= KEY_DIMENSION_MAX_DISTINCT and uniqueness <= KEY_DIMENSION_MAX_SHARE:
            return (
                "dimension",
                f"integer whose {distinct} values each recur across about "
                f"{signals['repeats']} rows, so it identifies rather than measures",
                False,
            )
        if distinct <= KEY_DIMENSION_MAX_DISTINCT and uniqueness <= MAX_NUMERIC_DIMENSION_SHARE:
            # The band where a small code list and a genuine count look the
            # same from the data alone. The name is weak evidence and is
            # allowed to tip it here, having had no say above.
            if _looks_like_a_label(name):
                return (
                    "dimension",
                    f"integer with {distinct} values recurring across about "
                    f"{signals['repeats']} rows, and a name that reads as a label",
                    False,
                )
            return (
                "measure",
                f"integer with {distinct} values recurring across about "
                f"{signals['repeats']} rows; could be a count or a code",
                True,
            )
    return "measure", "numeric and aggregatable", False


def _classify(
    name: str,
    dtype: str,
    distinct_count: int,
    row_count: int,
    *,
    non_null: int | None = None,
    low: Any = None,
    high: Any = None,
) -> tuple[FieldRole, str, bool]:
    """Assign a role, say why, and say whether it was a close call.

    The third value is the honest part. A numeric column can sit in a band
    where a code list and a genuine count are indistinguishable from the
    data, and the old classifier resolved that silently -- which is how a
    store number became a measure and the engine averaged it.
    """
    if dtype in TEMPORAL_TYPES:
        return "time", "temporal type", False

    if dtype in NUMERIC_TYPES:
        signals = _numeric_signals(
            dtype, distinct_count, int(non_null or row_count), row_count, low, high
        )
        return _classify_numeric(name, signals, row_count)

    if dtype == "BOOLEAN":
        return "dimension", "boolean", False

    # Text and everything else.
    if distinct_count <= 1:
        return "ignored", "constant", False
    if _looks_like_identifier(name):
        # Unless the data says otherwise. A key takes a different value on
        # almost every row; a column of four values repeated across four
        # hundred rows is a category whatever it is called, and `store_code`,
        # `region_code` and `account_no` are all ordinary groupings. Reading
        # the name over the cardinality made them ungroupable, so "total
        # units_sold by store_code" could not be answered at all.
        repeats = distinct_count <= NAMED_KEY_DIMENSION_DISTINCT
        if not (row_count >= MIN_ROWS_FOR_CARDINALITY_RULES and repeats):
            return "identifier", "the name reads as a key", False
        return (
            "dimension",
            f"the name reads as a key, but only {distinct_count} values repeat "
            f"across {row_count} rows",
            False,
        )
    uniqueness = distinct_count / max(row_count, 1)
    if uniqueness >= IDENTIFIER_UNIQUENESS and row_count >= MIN_ROWS_FOR_CARDINALITY_RULES:
        # One row per value. Grouping by it returns the rows, so it is a
        # label rather than a category -- and on a remote run those labels
        # would be uploaded cells travelling as group keys.
        return (
            "identifier",
            f"{uniqueness:.0%} distinct, so one row per value rather than a grouping",
            False,
        )
    if distinct_count <= MAX_DIMENSION_CARDINALITY:
        return "dimension", f"{distinct_count} distinct values", False
    # Nearly-unique text with no key-like name is free text, not an
    # identifier. Either way it cannot be grouped by.
    return "ignored", f"{distinct_count} distinct values is too many to group by", False


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


class RoleConfirmationError(ValueError):
    """A confirmation that must be refused before anything is applied.

    Carries a stable `reason` beside the readable message so the API can
    report which rule refused without parsing prose.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def validate_role_confirmations(
    inferred: InferredSchema,
    confirmations: Mapping[str, str],
) -> None:
    """Refuse the whole batch before any of it is applied.

    Validation is separate from application so a request that is wrong in
    its third change does not leave the first two in place. Every rule here
    is about the *class* of column, never about its name: a name-based
    exception is a rule that is wrong on the next dataset.
    """
    by_name = {f.name: f for f in inferred.fields}
    for column, role in confirmations.items():
        field_ = by_name.get(column)
        if field_ is None:
            raise RoleConfirmationError(
                "unknown_column",
                f"{column!r} is not a column of this table",
            )
        if not field_.ambiguous:
            raise RoleConfirmationError(
                "not_ambiguous",
                f"{column!r} was not a close call, so there is nothing to settle; "
                f"the data classifies it as {field_.role}",
            )
        if role not in CONFIRMABLE_ROLES:
            raise RoleConfirmationError(
                "unsupported_role",
                f"{role!r} is not offered for {column!r}; this close call is "
                f"between {' and '.join(CONFIRMABLE_ROLES)}",
            )
        if field_.data_type not in NUMERIC_TYPES:
            # The only close call this module reports is numeric. A
            # non-numeric field reaching here means a new inference class
            # arrived without its own tested transitions.
            raise RoleConfirmationError(
                "unsupported_type",
                f"{column!r} holds {field_.data_type}, which has no confirmable readings",
            )


def apply_role_confirmations(
    inferred: InferredSchema,
    confirmations: Mapping[str, str],
    *,
    revision: int = 0,
) -> InferredSchema:
    """The inferred schema as the session owner has settled it.

    Pure: `inferred` is not mutated, and the result is a new schema whose
    derived lists are rebuilt from the effective roles. Serialization stays
    out of this -- `as_dict()` never reaches into session state, so a schema
    can be serialized without a session and this layer can be tested without
    one.

    What a confirmation does and does not change:

      - `role` becomes the confirmed reading, and the derived lists follow.
      - `inferred_role` keeps what classification decided.
      - `ambiguous` stays true. The values are still indistinguishable;
        someone has supplied the missing fact rather than the data having
        settled it.
      - `additive` is cleared to `unknown` when a column becomes a measure
        by confirmation. Confirming that a column is a quantity is not
        evidence that summing it is meaningful, and carrying a `strong`
        from a different classification would let a suggestion claim more
        than anyone established.

    Confirming the role inference already chose is meaningful and is kept:
    the reading is unchanged but its warrant is now the session owner's.
    """
    validate_role_confirmations(inferred, confirmations)

    fields: list[InferredField] = []
    for original in inferred.fields:
        confirmed = confirmations.get(original.name)
        if confirmed is None:
            fields.append(replace(original))
            continue
        became_measure = confirmed == "measure" and original.role != "measure"
        fields.append(
            replace(
                original,
                role=cast(FieldRole, confirmed),
                inferred_role=original.effective_inferred_role,
                role_source="user_confirmed",
                additive="unknown" if became_measure else original.additive,
            )
        )

    return InferredSchema(
        table=inferred.table,
        row_count=inferred.row_count,
        fields=fields,
        ambiguities=list(inferred.ambiguities),
        schema_revision=revision,
    )


def effective_schema(
    session: Any,
    table: str,
    *,
    confirmation_snapshot: Any | None = None,
) -> InferredSchema:
    """The schema every semantic consumer should read.

    `infer_schema` stays the raw deterministic classification and is what
    the inference tests exercise. This is that, plus whatever the session
    owner has settled -- and it is the one place the two are combined.

    That matters because three layers derive the schema independently: the
    API for the profile a reader sees, the graph for the resolver and the
    planner, and the MCP tools for execution. If each applied confirmations
    for itself they would drift, and the failure would be the worst kind:
    the reader shown one role while the arithmetic used another.

    Pass `confirmation_snapshot` to pin a run to the generation it was
    admitted with. Without it the session's current state is read, which is
    right for a one-shot profile and wrong inside a run.
    """
    inferred = infer_schema(session, table)
    snapshot = confirmation_snapshot
    if snapshot is None:
        getter = getattr(session, "role_confirmation_snapshot", None)
        if getter is None:
            return inferred
        snapshot = getter()
    confirmations = dict(getattr(snapshot, "confirmations", {}) or {})
    if not confirmations:
        # Still carry the revision: a run admitted at revision 2 that has no
        # confirmations left should report 2, not 0.
        return replace(inferred, schema_revision=int(getattr(snapshot, "revision", 0)))
    return apply_role_confirmations(
        inferred,
        confirmations,
        revision=int(getattr(snapshot, "revision", 0)),
    )
