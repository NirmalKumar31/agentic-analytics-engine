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

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from agentic_analytics.analytics.row_filters import filters_from_plan, parse_filters

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
        # Temporal *intent*, not temporal vocabulary.
        #
        # This pattern used to include the bare adjectives -- `weekly`,
        # `monthly`, `daily`, `quarterly`, `yearly` -- and those are the
        # words measures are named after. `Weekly_Sales`, `monthly_ad_spend`
        # and `daily_active_users` all made every question about them a
        # trend, so "the average weekly sales by holiday flag" was answered
        # with a monthly series and the grouping was dropped.
        #
        # A grain now has to be asked for as a grouping (`by month`, `per
        # week`) or named as an analysis (`trend`, `over time`, `year over
        # year`). Column names are also stripped before this is matched, so
        # a measure called `trend_score` cannot trigger it either.
        r"\b(trend|trends|over time|time series|timeseries"
        r"|by month|per month|by week|per week|by day|per day"
        r"|by quarter|per quarter|by year|per year"
        r"|year over year|month over month|week over week)\b",
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


#: Grouping words that name a period rather than a column. "by month" is a
#: trend, handled elsewhere, and must not be mistaken for a missing column.
_PERIOD_WORDS = frozenset(
    {
        "month",
        "months",
        "week",
        "weeks",
        "day",
        "days",
        "quarter",
        "quarters",
        "year",
        "years",
        "date",
        "time",
        "period",
        "monthly",
        "weekly",
        "daily",
        "quarterly",
        "yearly",
    }
)


def _unresolved_grouping(text: str, schema: dict[str, Any]) -> str | None:
    """A grouping the question named that matches nothing in the table.

    Returns the phrase so the refusal can quote it back. `None` when the
    question named no grouping, or named a period, or named something that
    does resolve -- in which case the caller has already used it.
    """
    columns = {str(f.get("name", "")).lower() for f in schema.get("fields", [])}
    for phrase in _GROUPING_PHRASE.findall(text):
        tokens = [w for w in re.split(r"[^a-z0-9_]+", phrase.lower().strip()) if w]
        if not tokens:
            continue
        if any(w in _PERIOD_WORDS for w in tokens):
            return None
        # The phrase runs to the end of the clause, so any token matching a
        # column means the grouping did resolve.
        if any(t in columns for t in tokens):
            return None
        if any(_mentions(t, c) >= 0 for t in tokens for c in columns):
            return None
        return " ".join(tokens[:3])
    return None


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
    #: A period the question named, as inclusive ISO bounds, with the column
    #: it applies to. Carried rather than ignored: answering "total revenue
    #: in 1998" over every row in the table is a guessed answer, and it was
    #: the silent guess the upload corpus caught most often.
    period: tuple[str, str] | None = None
    period_field: str | None = None
    #: Row restrictions the question stated, already resolved to real
    #: columns and finite values. Empty is "no restriction asked for",
    #: never "one was asked for and dropped" -- that case refuses.
    filters: tuple[Any, ...] = ()
    ascending: bool = False
    confident: bool = True
    explanation: str = ""
    named_columns: list[str] = field(default_factory=list)
    #: Who interpreted the wording.  This changes provenance, not arithmetic;
    #: the canonical contract deliberately excludes it.
    interpretation: str = "rule-based"

    def canonical_dict(self) -> dict[str, Any]:
        """The semantic contract, stable across planner implementations."""
        filters = [f.as_dict() for f in self.filters]
        filters.sort(
            key=lambda f: json.dumps(
                {k: v for k, v in f.items() if k != "source_text"},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return {
            "operation": self.operation,
            "table": self.table,
            "measure": self.measure,
            "dimension": self.dimension,
            "time_field": self.time_field,
            "period": list(self.period) if self.period else None,
            "period_field": self.period_field,
            "filters": [{k: v for k, v in item.items() if k != "source_text"} for item in filters],
            "ascending": self.ascending,
        }

    @property
    def contract_hash(self) -> str:
        blob = json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "table": self.table,
            "measure": self.measure,
            "period": list(self.period) if self.period else None,
            "period_field": self.period_field,
            "filters": [f.as_dict() for f in self.filters],
            "dimension": self.dimension,
            "time_field": self.time_field,
            "ascending": self.ascending,
            "confident": self.confident,
            "explanation": self.explanation,
            "named_columns": list(self.named_columns),
            "interpretation": self.interpretation,
            "contract_hash": self.contract_hash,
            "canonical_contract": self.canonical_dict(),
        }


def _without_column_names(question: str, schema: dict[str, Any]) -> str:
    """The question with its column references blanked out.

    Columns are named after the things they hold, and those names collide
    with analytical vocabulary: `Weekly_Sales` contains "weekly",
    `monthly_ad_spend` contains "monthly", a `trend_score` column would
    contain "trend". Matching intent against the raw question let a
    measure's name choose the analysis -- "the average weekly sales by
    holiday flag" became a monthly trend with no grouping at all.

    Both spellings are removed, longest first, because a question says
    "weekly sales" where the schema says `Weekly_Sales` and either may
    appear.
    """
    names: list[str] = []
    for field_ in schema.get("fields") or []:
        name = str(field_.get("name", ""))
        if name:
            names.append(name)
            spoken = name.replace("_", " ")
            if spoken != name:
                names.append(spoken)
    out = question
    for name in sorted(set(names), key=len, reverse=True):
        out = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", " ", out, flags=re.IGNORECASE)
    return out


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


def _pick(
    named: list[str],
    candidates: list[str],
    what: str,
    also_if_named: list[str] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve one column, or say why it could not be resolved.

    Two acceptable outcomes: the question named a column of this role, or the
    table offers exactly one, in which case there is nothing to choose. A
    third candidate to pick from is a guess, and a guess is refused.

    `also_if_named` widens the first outcome only. A numeric column read as
    a dimension -- an integer repeating across a handful of values -- is not
    offered as a candidate, because choosing it unprompted would be a guess.
    If the question names it, there is nothing to guess: the user said which
    column to use, and refusing would substitute the engine's inference for
    their instruction.
    """
    overlap = [c for c in named if c in candidates]
    if len(overlap) >= 1:
        return overlap[0], None
    explicit = [c for c in named if c in (also_if_named or [])]
    if explicit:
        return explicit[0], None
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, f"the table has no column that looks like a {what}"
    return None, (
        f"the question does not name which {what} to use, and the table has "
        f"{len(candidates)} to choose from"
    )


def _source_is_in_question(source: str, question: str) -> bool:
    """A model may map an excerpt, but it may not invent one."""
    wanted = _normalise(source)
    return bool(wanted) and wanted in _normalise(question)


def mapping_from_plan(question: str, schema: dict[str, Any], plan: Any) -> QuestionMapping:
    """Validate an AI upload plan and turn it into the governed mapping.

    The provider supplies language interpretation only.  Identifiers, types,
    values, source excerpts and every rule-detectable constraint are checked
    here before the ordinary SQL compiler sees the result.
    """
    payload = plan.model_dump() if hasattr(plan, "model_dump") else dict(plan)
    table = str(schema.get("table", ""))

    def refuse(reason: str) -> QuestionMapping:
        return QuestionMapping(
            operation="profile",
            table=table,
            confident=False,
            explanation=reason,
            interpretation="ai-grounded",
        )

    if not payload.get("confident", True):
        return refuse(
            str(payload.get("ambiguity") or "the AI planner could not resolve the question")
        )
    if str(payload.get("table", "")) != table:
        return refuse("the AI plan named a table that is not the uploaded table")

    operation = str(payload.get("operation", ""))
    if operation not in {"count", "sum", "average", "trend", "rank", "profile"}:
        return refuse(f"the AI plan requested unsupported operation {operation!r}")
    if not _source_is_in_question(str(payload.get("operation_source", "")), question):
        return refuse("the AI plan did not ground its operation in the question")

    fields = {
        str(f.get("name", "")): str(f.get("data_type") or f.get("type") or "")
        for f in schema.get("fields", [])
    }
    measures, _dimensions, time_fields = _roles(schema)
    aggregatable = {str(c) for c in schema.get("aggregatable_if_named", [])}
    measure = payload.get("measure")
    dimension = payload.get("dimension")
    time_field = payload.get("time_field")

    if measure is not None:
        measure = str(measure)
        if measure not in fields or measure not in set(measures) | aggregatable:
            return refuse(f"the AI plan named {measure!r} as a measure, but it is not aggregatable")
        if not _source_is_in_question(str(payload.get("measure_source", "")), question):
            return refuse("the AI plan did not ground its measure in the question")
    if operation in {"sum", "average", "rank"} and measure is None:
        return refuse(f"the {operation} operation needs a measure")

    if dimension is not None:
        dimension = str(dimension)
        # A named numeric column can be a grouping even when the inference
        # layer did not classify it as a dimension.  The source excerpt is
        # the authority that the visitor actually asked for it.
        if dimension not in fields:
            return refuse(
                f"the AI plan named a grouping column this table does not have: {dimension!r}"
            )
        if not _source_is_in_question(str(payload.get("dimension_source", "")), question):
            return refuse("the AI plan did not ground its grouping in the question")
    if measure is not None and dimension == measure:
        return refuse("the AI plan cannot aggregate a column by that same column")

    if time_field is not None:
        time_field = str(time_field)
        if time_field not in time_fields:
            return refuse(f"the AI plan named {time_field!r} as a time field, but it is not one")

    filter_specs = [
        item.model_dump() if hasattr(item, "model_dump") else dict(item)
        for item in payload.get("filters", [])
    ]
    for item in filter_specs:
        if not _source_is_in_question(str(item.get("source_text", "")), question):
            return refuse(
                "the AI plan proposed a filter on "
                f"{item.get('column')!r} without an exact source excerpt"
            )
    resolution = filters_from_plan(filter_specs, schema)
    if resolution.refusal:
        return refuse(resolution.refusal)

    # Anything the rule parser can see is a lower bound on what the model
    # must preserve.  This catches a cloud plan that simply omits "aged 30
    # to 40" while still allowing it to ground a safe synonym the rules do
    # not understand.
    deterministic_filters = parse_filters(question, schema)
    if deterministic_filters.constraint_detected:
        if deterministic_filters.refusal and not resolution.filters:
            return refuse(deterministic_filters.refusal)
        wanted = {
            json.dumps(
                {k: v for k, v in f.as_dict().items() if k != "source_text"},
                sort_keys=True,
            )
            for f in deterministic_filters.filters
        }
        have = {
            json.dumps(
                {k: v for k, v in f.as_dict().items() if k != "source_text"},
                sort_keys=True,
            )
            for f in resolution.filters
        }
        if not wanted <= have:
            return refuse("the AI plan omitted a row restriction stated in the question")

    start = payload.get("period_start")
    end = payload.get("period_end")
    period: tuple[str, str] | None = None
    if start is not None or end is not None:
        if not (start and end and time_field):
            return refuse("the AI plan supplied only part of a time restriction")
        try:
            start_date = date.fromisoformat(str(start))
            end_date = date.fromisoformat(str(end))
        except ValueError:
            return refuse("the AI plan supplied an invalid ISO date")
        if start_date > end_date:
            return refuse("the AI plan supplied a reversed time period")
        period = (str(start), str(end))

    # Protect explicit rule-resolved components from being reinterpreted.
    rules = resolve_question(question, schema)
    if rules.confident:
        if operation != rules.operation:
            return refuse("the AI plan changed the operation explicitly requested in the question")
        if rules.measure in rules.named_columns and measure != rules.measure:
            return refuse("the AI plan changed the measure explicitly named in the question")
        if rules.dimension in rules.named_columns and dimension != rules.dimension:
            return refuse("the AI plan changed the grouping explicitly named in the question")

    return QuestionMapping(
        operation=operation,  # type: ignore[arg-type]
        table=table,
        measure=measure,
        dimension=dimension,
        time_field=time_field,
        period=period,
        period_field=time_field if period else None,
        filters=resolution.filters,
        ascending=bool(payload.get("ascending", False)),
        confident=True,
        # A confident plan's free-text note is not evidence and must not
        # become user-facing explanation.  The structured contract below is
        # the explanation a visitor can inspect.
        explanation="validated schema-grounded AI plan",
        named_columns=[c for c in fields if _mentions(_normalise(question), c) >= 0],
        interpretation="ai-grounded",
    )


def mapping_from_contract(
    question: str, schema: dict[str, Any], contract: dict[str, Any]
) -> QuestionMapping:
    """Revalidate an engine-accepted contract at the MCP boundary."""
    period_value = contract.get("period")
    if period_value is None:
        period = [None, None]
    elif isinstance(period_value, list) and len(period_value) == 2:
        period = period_value
    else:
        return QuestionMapping(
            operation="profile",
            table=str(schema.get("table", "")),
            confident=False,
            explanation="the accepted query contract has an invalid period",
            interpretation="ai-grounded",
        )
    plan = {
        "table": contract.get("table"),
        "operation": contract.get("operation"),
        "operation_source": question,
        "measure": contract.get("measure"),
        "measure_source": question if contract.get("measure") else "",
        "dimension": contract.get("dimension"),
        "dimension_source": question if contract.get("dimension") else "",
        "filters": [
            {
                "column": item.get("column"),
                "operator": item.get("operator"),
                "value": "" if item.get("value") is None else str(item.get("value")),
                "source_text": item.get("source_text") or question,
            }
            for item in (contract.get("filters") or [])
        ],
        "time_field": contract.get("time_field") or contract.get("period_field"),
        "period_start": period[0],
        "period_end": period[1],
        "ascending": bool(contract.get("ascending", False)),
        "confident": bool(contract.get("confident", True)),
        "ambiguity": str(contract.get("explanation") or ""),
    }
    mapping = mapping_from_plan(question, schema, plan)
    wanted_hash = str(contract.get("contract_hash") or "")
    if mapping.confident and (not wanted_hash or mapping.contract_hash != wanted_hash):
        return QuestionMapping(
            operation="profile",
            table=str(schema.get("table", "")),
            confident=False,
            explanation="the accepted query contract changed before execution",
            interpretation="ai-grounded",
        )
    mapping.interpretation = str(contract.get("interpretation") or mapping.interpretation)
    return mapping


def resolve_question(question: str, schema: dict[str, Any]) -> QuestionMapping:
    """Decide, from rules alone, what to compute for this question.

    Returns a mapping whose ``confident`` flag is false -- with ``operation``
    set to ``profile`` -- whenever the question cannot be resolved without
    guessing.
    """
    table = str(schema.get("table", ""))
    measures, dimensions, time_fields = _roles(schema)
    # Numeric columns read as something else. Usable only when named; see
    # `_pick`.
    aggregatable = [str(c) for c in schema.get("aggregatable_if_named", [])]

    # A period the question named. Needs a time column to apply to; without
    # one there is nothing to filter and the question is refused below
    # rather than answered over the whole table.
    from agentic_analytics.agents.timescope import parse_time_scope

    # Read the period from the question with any column name the question
    # mentions removed first. A column called `2024 sales ($)` otherwise
    # made every question about it a question about the year 2024, and the
    # table had no date column to apply that to, so it was refused.
    period_text = _without_column_names(question, schema)
    window = parse_time_scope(period_text)
    named_period: tuple[str, str] | None = None
    period_field: str | None = None
    if window is not None:
        if not time_fields:
            return QuestionMapping(
                operation="profile",
                table=table,
                confident=False,
                explanation=(
                    "the question names a period, and this table has no date column to apply it to"
                ),
                named_columns=[],
            )
        named_period = (window.start, window.end)
        period_field = time_fields[0]

    # Row restrictions, resolved before anything else is decided.
    #
    # A question that restricts its population and is answered over every
    # row is not a slightly worse answer, it is an answer to a different
    # question -- and the old planner returned one confidently, because it
    # had no way to represent the restriction and therefore no way to
    # notice it had dropped one. An unresolvable restriction refuses here
    # rather than falling through.
    resolution = parse_filters(question, schema)
    if resolution.refusal is not None:
        return QuestionMapping(
            operation="profile",
            table=table,
            confident=False,
            explanation=resolution.refusal,
            named_columns=[],
            period=named_period,
            period_field=period_field,
        )
    row_filters = resolution.filters
    if resolution.constraint_detected and not row_filters:
        return QuestionMapping(
            operation="profile",
            table=table,
            confident=False,
            explanation=(
                "the question restricts which rows to include, and that restriction "
                "could not be mapped to a column of this table; name the column and "
                "the bound, for example 'age between 30 and 40'"
            ),
            named_columns=[],
            period=named_period,
            period_field=period_field,
        )

    text = _normalise(question)

    def refuse(reason: str) -> QuestionMapping:
        return QuestionMapping(
            operation="profile",
            table=table,
            confident=False,
            explanation=reason,
            named_columns=named,
            period=named_period,
            period_field=period_field,
        )

    # Columns the question actually named, in the order they were written.
    # Order matters: "revenue by region" names the measure first.
    all_columns = [str(f["name"]) for f in schema.get("fields", [])]
    positions = {c: _mentions(text, c) for c in all_columns}
    named = sorted((c for c, pos in positions.items() if pos >= 0), key=lambda c: positions[c])

    # Matched against the question with its column references removed, so
    # a measure's name cannot choose the analysis. `text` is still used for
    # everything that legitimately reads column references -- the measure,
    # the grouping, the filters.
    intent_text = _normalise(_without_column_names(question, schema))
    operation: Operation | None = next(
        (
            candidate
            for pattern, candidate in _OPERATION_PATTERNS
            if re.search(pattern, intent_text, re.IGNORECASE)
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
            filters=row_filters,
            explanation="the question asked what the table contains",
            named_columns=named,
            period=named_period,
            period_field=period_field,
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

    if dimension is None:
        # A grouping was asked for and nothing in the table answers to it.
        # Dropping it and returning an ungrouped total was the wrong
        # outcome twice over: the visitor asked for a breakdown and got a
        # single number, and the number was presented confidently as the
        # answer. "by loyalty_tier" on a table with no such column is a
        # question about data that is not here.
        unresolved = _unresolved_grouping(text, schema)
        if unresolved:
            return refuse(
                f"the question groups by {unresolved!r}, which is not a column of this table"
            )

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
            filters=row_filters,
            explanation=(
                f"monthly {'total of ' + measure if measure else 'row count'} over {time_field}"
            ),
            named_columns=named,
            period=named_period,
            period_field=period_field,
        )

    if operation == "count":
        return QuestionMapping(
            operation="count",
            table=table,
            dimension=dimension,
            confident=True,
            filters=row_filters,
            explanation=(f"row count by {dimension}" if dimension else "total row count"),
            named_columns=named,
            period=named_period,
            period_field=period_field,
        )

    # sum, average and rank all need a measure.
    measure, why = _pick(named, measures, "numeric column", aggregatable)

    # A column cannot be both the thing being totalled and the thing being
    # grouped by. This happens when a numeric column was read as a
    # dimension -- "total units_sold by store_code" matched `units_sold` on
    # the grouping search first, and then totalled it as well, so the
    # answer was grouped by the very column it was summing.
    if measure is not None and dimension == measure:
        others = [c for c in named if c in dimensions and c != measure]
        dimension = others[0] if others else None

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
                filters=row_filters,
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
            filters=row_filters,
            explanation=(
                f"{'lowest' if _ASCENDING.search(question) else 'highest'} total "
                f"{measure} by {rank_dimension}"
            ),
            named_columns=named,
            period=named_period,
            period_field=period_field,
        )

    verb = "average" if operation == "average" else "total"
    return QuestionMapping(
        operation=operation,
        table=table,
        measure=measure,
        dimension=dimension,
        confident=True,
        filters=row_filters,
        explanation=(f"{verb} {measure} by {dimension}" if dimension else f"{verb} {measure}"),
        named_columns=named,
        period=named_period,
        period_field=period_field,
    )


def alias_for(column: str) -> str:
    """The output-column name a grouping by `column` is returned as.

    Public so the verification layer can compare a requested dimension
    against a result's columns through the same transformation the
    compiler applied, rather than guessing at it.
    """
    return _alias(column)


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


def sql_lineage(mapping: QuestionMapping) -> dict[str, dict[str, str]]:
    """What each aggregate output column is derived from.

    `SUM(net_value)` is emitted as `total_net_value`, and without this the
    alias is indistinguishable from a column of the uploaded file. A
    verifier reading "the sum of total_net_value" could not tell whether
    the claim named the engine's own output or invented a source column,
    and withheld a correct, fully evidenced total on that doubt.

    Keyed by output column, so a reader of the result can go from the
    number back to the file it came from.
    """
    if not mapping.confident or mapping.operation == "profile":
        return {}
    out: dict[str, dict[str, str]] = {}
    if mapping.dimension:
        out[_alias(mapping.dimension)] = {
            "kind": "grouping",
            "table": mapping.table,
            "column": mapping.dimension,
        }
    if mapping.time_field and mapping.operation == "trend":
        out["period"] = {
            "kind": "derived",
            "aggregate": "MONTH",
            "table": mapping.table,
            "column": mapping.time_field,
        }
    if mapping.measure:
        aggregate = "AVG" if mapping.operation == "average" else "SUM"
        label = _alias("average" if mapping.operation == "average" else "total", mapping.measure)
        if mapping.operation == "trend":
            label = _alias("total", mapping.measure)
            aggregate = "SUM"
        out[label] = {
            "kind": "aggregate",
            "aggregate": aggregate,
            "table": mapping.table,
            "column": mapping.measure,
            "expression": f"{aggregate}({mapping.table}.{mapping.measure})",
        }
    elif mapping.operation in ("count", "ranking", "trend"):
        out["row_total" if mapping.operation == "trend" else "row_count"] = {
            "kind": "aggregate",
            "aggregate": "COUNT",
            "table": mapping.table,
            "column": "*",
            "expression": f"COUNT(*) over {mapping.table}",
        }
    out.setdefault(
        "row_count",
        {
            "kind": "aggregate",
            "aggregate": "COUNT",
            "table": mapping.table,
            "column": "*",
            "expression": f"COUNT(*) over {mapping.table}",
        },
    )
    return out


def _quote(identifier: str) -> str:
    """Quote an identifier for DuckDB.

    The name came from the database catalogue, not from a question, but it is
    still quoted: an uploaded CSV can have a column called `select`.
    """
    return '"' + identifier.replace('"', '""') + '"'


def _where(mapping: QuestionMapping) -> str:
    """Everything restricting the rows: the named period and the row
    filters, joined.

    One place, so a new query shape cannot pick up the period and forget
    the filters -- which is the shape of the defect this replaces.
    """
    from agentic_analytics.analytics.row_filters import where_clause

    parts: list[str] = []
    period = _period_filter(mapping).strip()
    if period:
        parts.append(period.removeprefix("WHERE ").strip())
    rows = where_clause(tuple(mapping.filters), _quote)
    if rows:
        parts.append(rows)
    return f" WHERE {' AND '.join(parts)} " if parts else ""


def _period_filter(mapping: QuestionMapping) -> str:
    """A `WHERE` fragment for a named period, or an empty string."""
    if not mapping.period or not mapping.period_field:
        return ""
    column = _quote(mapping.period_field)
    start, end = mapping.period
    return (
        f" WHERE CAST({column} AS DATE) >= DATE '{start}' "
        f"AND CAST({column} AS DATE) <= DATE '{end}'"
    )


def build_sql(mapping: QuestionMapping) -> str | None:
    """Compose the statement for a resolved mapping.

    Returns ``None`` for a mapping that is not answerable as a single
    aggregate -- a profile, or anything not confident.
    """
    if not mapping.confident or mapping.operation == "profile":
        return None
    table = _quote(mapping.table)
    where = _where(mapping)

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
            f"FROM {table} WHERE {stamp} IS NOT NULL{where.replace(' WHERE ', ' AND ', 1)} "
            f"GROUP BY 1 ORDER BY 1 LIMIT {TREND_LIMIT}"
        )

    if mapping.operation == "count":
        if not mapping.dimension:
            return f"SELECT COUNT(*) AS row_count FROM {table}{where}"
        dim = _quote(mapping.dimension)
        return (
            f"SELECT {dim} AS {_alias(mapping.dimension)}, COUNT(*) AS row_count "
            f"FROM {table}{where} GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT {GROUP_LIMIT}"
        )

    if mapping.operation == "rank" and mapping.measure is None:
        if mapping.dimension is None:
            return None
        dim = _quote(mapping.dimension)
        direction = "ASC" if mapping.ascending else "DESC"
        return (
            f"SELECT {dim} AS {_alias(mapping.dimension)}, COUNT(*) AS row_count "
            f"FROM {table}{where} GROUP BY 1 ORDER BY 2 {direction} NULLS LAST LIMIT {RANK_LIMIT}"
        )

    if mapping.measure is None:
        return None
    aggregate = "AVG" if mapping.operation == "average" else "SUM"
    label = _alias("average" if mapping.operation == "average" else "total", mapping.measure)
    value = f"ROUND({aggregate}(CAST({_quote(mapping.measure)} AS DOUBLE)), 4) AS {label}"

    if not mapping.dimension:
        return f"SELECT {value}, COUNT(*) AS row_count FROM {table}{where}"

    dim = _quote(mapping.dimension)
    direction = "ASC" if mapping.ascending else "DESC"
    limit = RANK_LIMIT if mapping.operation == "rank" else GROUP_LIMIT
    return (
        f"SELECT {dim} AS {_alias(mapping.dimension)}, {value}, COUNT(*) AS row_count "
        f"FROM {table}{where} GROUP BY 1 ORDER BY 2 {direction} NULLS LAST LIMIT {limit}"
    )
