"""Map a question onto an uploaded table's inferred schema, deterministically.

An uploaded file has no semantic metric layer, so the question cannot be
resolved by looking a metric up. What is available instead is the inferred
schema -- which columns look like measures, dimensions and time fields -- and
the words the visitor actually typed.

This module turns those two things into an explicit :class:`QuestionMapping`,
and then into SQL. It is deliberately a small set of rules rather than any
kind of language understanding:

* a column counts as *named* only when its name appears in the question;
* an operation is recognised from a fixed keyword list;
* a column the operation needs may be filled in only when the schema offers
  exactly one candidate of the right role, so the choice is forced rather
  than guessed;
* anything else is **refused**, with a reason, rather than answered.

The refusal path matters more than the success path. A demo that quietly
returns the sum of whatever numeric column happened to be first would look
like it understood the question. Saying it could not map the question is the
honest outcome, and the profile is offered instead.

The SQL is composed here, from column names that came out of the database
catalogue. No model writes it, and it is still guarded before it executes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

Operation = Literal["count", "sum", "average", "trend", "rank", "profile"]

#: Rows returned by a grouped aggregate. Small enough to read, large enough
#: that a real breakdown is not silently cut off.
GROUP_LIMIT = 25
#: Rows returned when the question asked for a top or bottom list.
RANK_LIMIT = 10
#: Points in a trend. A daily series over a few years stays under this.
TREND_LIMIT = 500

#: Operation keywords, most specific first. Order is what makes "average
#: order value by month" a trend rather than an average.
_OPERATION_PATTERNS: list[tuple[str, Operation]] = [
    (
        r"\b(trend|over time|time series|by month|per month|monthly|by week|weekly"
        r"|by day|daily|by quarter|quarterly|by year|yearly|year over year)\b",
        "trend",
    ),
    (
        r"\b(top|highest|largest|biggest|best|most|bottom|lowest|smallest|worst|least"
        r"|rank|ranked|ranking)\b",
        "rank",
    ),
    (r"\b(average|avg|mean|typical)\b", "average"),
    (r"\b(total|sum|sum of|overall|combined)\b", "sum"),
    (r"\b(how many|count|number of|how much|volume)\b", "count"),
    (
        r"\b(compare|comparison|versus|vs\.?|difference between|split by|broken down"
        r"|breakdown|break down|by segment|across|per|group by|grouped by)\b",
        "sum",
    ),
    (
        r"\b(profile|describe|summar(y|ise|ize)|what is in|what's in|overview"
        r"|columns|shape)\b",
        "profile",
    ),
]

#: Words that mean "smallest first" when the question asked for a ranking.
_ASCENDING = re.compile(r"\b(bottom|lowest|smallest|worst|least)\b", re.IGNORECASE)

#: "by category", "per region", "grouped by store". The captured phrase is
#: matched against column names; anything else is ignored.
_GROUPING_PHRASE = re.compile(
    r"\b(?:by|per|across|for each|grouped by|group by|split by)\s+"
    r"(?:the\s+|each\s+|every\s+)?([a-z0-9_ ]{2,40})",
    re.IGNORECASE,
)


@dataclass
class QuestionMapping:
    """What the engine decided the question asked of this table.

    ``confident`` is the whole point: it is false whenever a column had to be
    guessed, and a false value means the caller must not present a number as
    an answer to the question.
    """

    operation: Operation
    table: str
    measure: str | None = None
    dimension: str | None = None
    time_field: str | None = None
    ascending: bool = False
    confident: bool = True
    explanation: str = ""
    named_columns: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "table": self.table,
            "measure": self.measure,
            "dimension": self.dimension,
            "time_field": self.time_field,
            "ascending": self.ascending,
            "confident": self.confident,
            "explanation": self.explanation,
            "named_columns": list(self.named_columns),
            "interpretation": "rule-based",
        }


def _normalise(text: str) -> str:
    """Lowercase, and turn punctuation and underscores into spaces.

    Column names are written `order_total` and questions say "order total",
    so both sides are flattened the same way before they are compared.
    """
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _variants(column: str) -> list[str]:
    """The spellings of a column name a question might plausibly use.

    Only pluralisation: people write "top categories" for a column called
    `category`. Anything cleverer than this would be guessing at synonyms,
    which is exactly what this module refuses to do.
    """
    base = _normalise(column)
    if not base:
        return []
    forms = [base]
    if base.endswith("y"):
        forms.append(base[:-1] + "ies")
    elif base.endswith(("s", "x", "z", "ch", "sh")):
        forms.append(base + "es")
    else:
        forms.append(base + "s")
    return forms


def _mentions(haystack: str, column: str) -> int:
    """Where `column` appears in the normalised question, or -1.

    Whole-token matching, so `id` does not match `paid` and `order` does not
    match `reorder`.
    """
    for needle in _variants(column):
        match = re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack)
        if match:
            return match.start()
    return -1


def _roles(schema: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    return (
        [str(c) for c in schema.get("measures", [])],
        [str(c) for c in schema.get("dimensions", [])],
        [str(c) for c in schema.get("time_fields", [])],
    )


def _pick(named: list[str], candidates: list[str], what: str) -> tuple[str | None, str | None]:
    """Resolve one column, or say why it could not be resolved.

    Two acceptable outcomes: the question named a column of this role, or the
    table offers exactly one, in which case there is nothing to choose. A
    third candidate to pick from is a guess, and a guess is refused.
    """
    overlap = [c for c in named if c in candidates]
    if len(overlap) >= 1:
        return overlap[0], None
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, f"the table has no column that looks like a {what}"
    return None, (
        f"the question does not name which {what} to use, and the table has "
        f"{len(candidates)} to choose from"
    )


def resolve_question(question: str, schema: dict[str, Any]) -> QuestionMapping:
    """Decide, from rules alone, what to compute for this question.

    Returns a mapping whose ``confident`` flag is false -- with ``operation``
    set to ``profile`` -- whenever the question cannot be resolved without
    guessing.
    """
    table = str(schema.get("table", ""))
    measures, dimensions, time_fields = _roles(schema)
    text = _normalise(question)

    def refuse(reason: str) -> QuestionMapping:
        return QuestionMapping(
            operation="profile",
            table=table,
            confident=False,
            explanation=reason,
            named_columns=named,
        )

    # Columns the question actually named, in the order they were written.
    # Order matters: "revenue by region" names the measure first.
    all_columns = [str(f["name"]) for f in schema.get("fields", [])]
    positions = {c: _mentions(text, c) for c in all_columns}
    named = sorted((c for c, pos in positions.items() if pos >= 0), key=lambda c: positions[c])

    operation: Operation | None = next(
        (
            candidate
            for pattern, candidate in _OPERATION_PATTERNS
            if re.search(pattern, text, re.IGNORECASE)
        ),
        None,
    )
    # "revenue by region" states no verb, but the grouping phrase and a
    # named numeric column together say what it wants. Only reachable when
    # both resolve to real columns, so it cannot invent an intent.
    if operation is None and _GROUPING_PHRASE.search(text):
        grouped = any(
            _mentions(_normalise(phrase), c) >= 0
            for phrase in _GROUPING_PHRASE.findall(text)
            for c in dimensions
        )
        if grouped:
            operation = "sum" if any(c in measures for c in named) else "count"

    if operation is None:
        return refuse("the question does not ask for a count, total, average, ranking or trend")
    if operation == "profile":
        return QuestionMapping(
            operation="profile",
            table=table,
            confident=True,
            explanation="the question asked what the table contains",
            named_columns=named,
        )

    # A grouping the question spelled out: "by region", "per store".
    dimension: str | None = None
    for phrase in _GROUPING_PHRASE.findall(text):
        for candidate in dimensions:
            if _mentions(_normalise(phrase), candidate) >= 0:
                dimension = candidate
                break
        if dimension:
            break
    if dimension is None:
        # Otherwise a dimension the question merely named is still a
        # grouping -- "returns by category" and "category returns" ask the
        # same thing.
        mentioned_dims = [c for c in named if c in dimensions]
        dimension = mentioned_dims[0] if mentioned_dims else None

    if operation == "trend":
        time_field, why = _pick(named, time_fields, "date column")
        if time_field is None:
            return refuse(str(why))
        measure = next((c for c in named if c in measures), None)
        if measure is None and len(measures) == 1:
            measure = measures[0]
        return QuestionMapping(
            operation="trend",
            table=table,
            measure=measure,
            time_field=time_field,
            confident=True,
            explanation=(
                f"monthly {'total of ' + measure if measure else 'row count'} over {time_field}"
            ),
            named_columns=named,
        )

    if operation == "count":
        return QuestionMapping(
            operation="count",
            table=table,
            dimension=dimension,
            confident=True,
            explanation=(f"row count by {dimension}" if dimension else "total row count"),
            named_columns=named,
        )

    # sum, average and rank all need a measure.
    measure, why = _pick(named, measures, "numeric column")
    if measure is None:
        if operation == "rank" and dimension:
            # "top regions" with no measure named is a frequency ranking,
            # which needs no numeric column at all.
            return QuestionMapping(
                operation="rank",
                table=table,
                dimension=dimension,
                ascending=bool(_ASCENDING.search(question)),
                confident=True,
                explanation=f"{dimension} values ranked by how often they occur",
                named_columns=named,
            )
        return refuse(str(why))

    if operation == "rank":
        rank_dimension, why = _pick(named, dimensions, "grouping column")
        if dimension:
            rank_dimension = dimension
        if rank_dimension is None:
            return refuse(str(why))
        return QuestionMapping(
            operation="rank",
            table=table,
            measure=measure,
            dimension=rank_dimension,
            ascending=bool(_ASCENDING.search(question)),
            confident=True,
            explanation=(
                f"{'lowest' if _ASCENDING.search(question) else 'highest'} total "
                f"{measure} by {rank_dimension}"
            ),
            named_columns=named,
        )

    verb = "average" if operation == "average" else "total"
    return QuestionMapping(
        operation=operation,
        table=table,
        measure=measure,
        dimension=dimension,
        confident=True,
        explanation=(f"{verb} {measure} by {dimension}" if dimension else f"{verb} {measure}"),
        named_columns=named,
    )


def _alias(*parts: str) -> str:
    """A safe output-column name built from the dataset's own vocabulary.

    Findings quote the result's column names, so `total_revenue by region`
    reads far better than `total_value by segment` -- and a reader can see
    which column of their file the number came from.
    """
    slug = "_".join(re.sub(r"[^a-z0-9]+", "_", part.lower()).strip("_") for part in parts if part)
    slug = re.sub(r"_+", "_", slug).strip("_")
    # A purely non-alphanumeric column name would slug to nothing, and a
    # leading digit is not an identifier.
    if not slug or slug[0].isdigit():
        slug = f"value_{slug}" if slug else "value"
    return slug[:60]


def _quote(identifier: str) -> str:
    """Quote an identifier for DuckDB.

    The name came from the database catalogue, not from a question, but it is
    still quoted: an uploaded CSV can have a column called `select`.
    """
    return '"' + identifier.replace('"', '""') + '"'


def build_sql(mapping: QuestionMapping) -> str | None:
    """Compose the statement for a resolved mapping.

    Returns ``None`` for a mapping that is not answerable as a single
    aggregate -- a profile, or anything not confident.
    """
    if not mapping.confident or mapping.operation == "profile":
        return None
    table = _quote(mapping.table)

    if mapping.operation == "trend":
        if mapping.time_field is None:
            return None
        stamp = _quote(mapping.time_field)
        # Formatted as `2025-03` rather than left as a timestamp: the label
        # is what a chart axis and a finding both show, and `%Y-%m` sorts
        # lexically in the same order it sorts chronologically.
        period = f"strftime(date_trunc('month', CAST({stamp} AS TIMESTAMP)), '%Y-%m')"
        if mapping.measure is None:
            value = "COUNT(*) AS row_total"
        else:
            label = _alias("total", mapping.measure)
            value = f"ROUND(SUM(CAST({_quote(mapping.measure)} AS DOUBLE)), 4) AS {label}"
        return (
            f"SELECT {period} AS period, {value}, COUNT(*) AS row_count "
            f"FROM {table} WHERE {stamp} IS NOT NULL "
            f"GROUP BY 1 ORDER BY 1 LIMIT {TREND_LIMIT}"
        )

    if mapping.operation == "count":
        if not mapping.dimension:
            return f"SELECT COUNT(*) AS row_count FROM {table}"
        dim = _quote(mapping.dimension)
        return (
            f"SELECT {dim} AS {_alias(mapping.dimension)}, COUNT(*) AS row_count "
            f"FROM {table} GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT {GROUP_LIMIT}"
        )

    if mapping.operation == "rank" and mapping.measure is None:
        if mapping.dimension is None:
            return None
        dim = _quote(mapping.dimension)
        direction = "ASC" if mapping.ascending else "DESC"
        return (
            f"SELECT {dim} AS {_alias(mapping.dimension)}, COUNT(*) AS row_count "
            f"FROM {table} GROUP BY 1 ORDER BY 2 {direction} NULLS LAST LIMIT {RANK_LIMIT}"
        )

    if mapping.measure is None:
        return None
    aggregate = "AVG" if mapping.operation == "average" else "SUM"
    label = _alias("average" if mapping.operation == "average" else "total", mapping.measure)
    value = f"ROUND({aggregate}(CAST({_quote(mapping.measure)} AS DOUBLE)), 4) AS {label}"

    if not mapping.dimension:
        return f"SELECT {value}, COUNT(*) AS row_count FROM {table}"

    dim = _quote(mapping.dimension)
    direction = "ASC" if mapping.ascending else "DESC"
    limit = RANK_LIMIT if mapping.operation == "rank" else GROUP_LIMIT
    return (
        f"SELECT {dim} AS {_alias(mapping.dimension)}, {value}, COUNT(*) AS row_count "
        f"FROM {table} GROUP BY 1 ORDER BY 2 {direction} NULLS LAST LIMIT {limit}"
    )
