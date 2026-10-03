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

from agentic_analytics.analytics.resolution import ResolutionIssue
from agentic_analytics.analytics.row_filters import filters_from_plan, parse_filters

Operation = Literal["count", "sum", "average", "trend", "rank", "profile"]

#: Groups a breakdown may return. The old value was 25, with a comment
#: claiming it was "large enough that a real breakdown is not silently cut
#: off". A 45-store sales table falsified that: `total Weekly_Sales by
#: Store` returned the top 25, covering 3,575 of 6,435 rows and omitting
#: $1.58bn of $6.74bn, and the report called it the complete breakdown of
#: every row.
#:
#: Two things changed. This is now aligned with the result transport budget
#: rather than with what fits on a screen -- how many rows a reader wants to
#: scroll is the UI's problem, and the UI may preview fewer without
#: touching the analytical result. And the engine asks for one more group
#: than it will accept, so it can *tell* whether a breakdown was cut short
#: instead of assuming it was not.
GROUP_RESULT_MAX = 500
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
        r"|by month|per month|for each month|by week|per week|for each week"
        r"|by day|per day|for each day|by quarter|per quarter|for each quarter"
        r"|by year|per year|for each year"
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
#:
#: The interrogative forms are here because "which store had the highest
#: total sales" names its grouping without a `by`, and it is one of the
#: commonest shapes a business question takes. Without them a ranking over
#: a numeric grouping column resolved to `profile` and the question was
#: refused -- the engine could rank by a declared dimension but not by a
#: store or product id.
_GROUPING_PHRASE = re.compile(
    r"\b(?:by|per|across|for each|grouped by|group by|split by)\s+"
    r"(?:the\s+|each\s+|every\s+)?([a-z0-9_ ]{2,40})",
    re.IGNORECASE,
)


#: "which store", "whose account". The interrogative subject of a ranking
#: question, which names its grouping without a `by`.
#:
#: Deliberately narrow, and deliberately not folded into
#: `_GROUPING_PHRASE`. Adding these words there matched every question
#: opening with "what", captured the rest of the clause, and broke two
#: working cases: "the average Weekly_Sales by Holiday_Flag" lost its
#: grouping to the hijacked phrase, and "the average profit by Store"
#: stopped refusing and answered with a substituted measure. So: only
#: `which`/`whose`, at most two words, and only consulted when a `by`
#: phrase resolved nothing.
_SUBJECT_PHRASE = re.compile(
    r"\b(?:which|whose)\s+(?:the\s+)?([a-z0-9_]+(?:\s+[a-z0-9_]+)?)",
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

_TIME_GRAIN_PATTERNS: tuple[tuple[str, Literal["day", "week", "month", "quarter", "year"]], ...] = (
    (r"\b(?:by|per|for each|grouped by|group by)\s+days?\b|\bdaily\b", "day"),
    (r"\b(?:by|per|for each|grouped by|group by)\s+weeks?\b|\bweekly\b", "week"),
    (r"\b(?:by|per|for each|grouped by|group by)\s+months?\b|\bmonthly\b", "month"),
    (r"\b(?:by|per|for each|grouped by|group by)\s+quarters?\b|\bquarterly\b", "quarter"),
    (r"\b(?:by|per|for each|grouped by|group by)\s+years?\b|\byearly\b", "year"),
)

#: Date fields whose name is a defensible default event clock for an uploaded
#: fact table.  Deliberately a short allow-list rather than "anything ending
#: in _date": `signup_date`, `birth_date` and `created_at` describe an entity,
#: and filtering a stock or annual measure by one silently changes the
#: business question.  Any real time field remains usable when the question
#: names it explicitly.
_DEFAULT_EVENT_TIME_FIELDS = frozenset(
    {
        "date",
        "event_date",
        "invoice_date",
        "observation_date",
        "order_date",
        "record_date",
        "sale_date",
        "sales_date",
        "trade_date",
        "trading_date",
        "transaction_date",
    }
)


def _period_field_for_question(
    question: str, time_fields: list[str]
) -> tuple[str | None, str | None]:
    """Choose the period clock only when its meaning is stated or generic.

    Returns ``(field, refusal)``.  A model is not allowed to settle the
    refusal: the missing fact is business semantics, not language parsing.
    """
    text = _normalise(question)
    explicit = [field for field in time_fields if _mentions(text, field) >= 0]
    if len(explicit) == 1:
        return explicit[0], None
    if len(explicit) > 1:
        return None, (
            "the question names more than one date column; name the one that "
            "defines the requested time axis or period"
        )
    if (
        len(time_fields) == 1
        and _normalise(time_fields[0]).replace(" ", "_") in _DEFAULT_EVENT_TIME_FIELDS
    ):
        return time_fields[0], None
    choices = ", ".join(repr(field) for field in time_fields[:4])
    return None, (
        "the table does not establish which date defines the requested time axis "
        f"or period; name the date column explicitly ({choices})"
    )


def _time_grain(
    question: str, schema: dict[str, Any]
) -> Literal["day", "week", "month", "quarter", "year"] | None:
    """Return a grain the visitor explicitly requested, never one from a column name."""
    text = _without_column_names(question, schema)
    for pattern, grain in _TIME_GRAIN_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return grain
    return None


#: Words that begin a row restriction. A grouping phrase's capture runs to
#: the end of the clause, so it spills across these into the filter's own
#: text -- and everything after one of them belongs to the restriction, not
#: to the grouping.
_FILTER_LEAD = re.compile(r"\b(?:where|for|with|having|whose|filtered)\b", re.IGNORECASE)


def _grouping_head(phrase: str) -> str:
    """The part of a grouping phrase that actually names groupings.

    "by territory for headcount 41 to 50" is one phrase mentioning two
    groupable columns, and harvesting both turned a filtered breakdown by
    territory into a two-cut breakdown by territory and headcount.
    Excluding every filtered column instead was too blunt: "by store where
    store at most 10" names a column as both a grouping and a restriction,
    which is ordinary and must keep its grouping.

    The boundary is textual. What comes before the first restriction word
    is the grouping; what follows belongs to the filter.
    """
    match = _FILTER_LEAD.search(phrase)
    return phrase[: match.start()] if match else phrase


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


#: Words that sit in measure position without naming one: articles, the
#: operation itself, and the filler a question is built from.
_MEASURE_NOISE = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "is",
        "what",
        "whats",
        "total",
        "sum",
        "average",
        "avg",
        "mean",
        "median",
        "count",
        "number",
        "how",
        "much",
        "many",
        "value",
        "values",
        "amount",
        "figure",
        "figures",
        "overall",
        "combined",
        "per",
        "for",
        "all",
        "each",
        "top",
        "bottom",
        "highest",
        "lowest",
        "rank",
        "ranked",
        "show",
        "me",
        "give",
        "list",
        "and",
        "in",
        "by",
    }
)


#: Declared types a grouping may be widened to. Text is excluded: see
#: the note at the widening itself.
_GROUPABLE_NUMERIC = frozenset(
    {
        "BIGINT",
        "INTEGER",
        "SMALLINT",
        "TINYINT",
        "HUGEINT",
        "UBIGINT",
        "UINTEGER",
        "USMALLINT",
        "UTINYINT",
        "DOUBLE",
        "DECIMAL",
        "FLOAT",
        "REAL",
        "NUMERIC",
    }
)


def _base_numeric(declared: str) -> bool:
    return declared.split("(")[0].strip().upper() in _GROUPABLE_NUMERIC


def _groupable(schema: dict[str, Any]) -> list[str]:
    """Columns this engine will group by, in preference order.

    A near-unique text column is classified as an identifier and kept out
    of `dimensions` precisely because grouping by it puts its raw values
    into the result as group labels -- and from there into a remote
    prompt. Widening to every column reopened that: three privacy tests
    caught an uploaded city name reaching a prompt. A numeric column
    named in a `by` phrase carries no such disclosure.

    Shared with the AI plan validator on purpose. When only the rule path
    consulted this list, a cloud plan could group by an identifier that
    the rules would not offer, and the boundary held in one mode only.
    """
    dimensions = [str(c) for c in schema.get("dimensions", [])]
    return dimensions + [
        name
        for f in schema.get("fields") or []
        if (name := str(f.get("name", "")))
        and name not in dimensions
        and _base_numeric(str(f.get("data_type") or f.get("type") or ""))
    ]


def _named_period(question: str, schema: dict[str, Any]) -> tuple[str, str] | None:
    """The period this question states, as inclusive ISO bounds.

    Read from the question with any column name removed first. A column
    called `2024 sales ($)` otherwise made every question about it a
    question about the year 2024, and the table had no date column to
    apply that to, so it was refused.

    Date arithmetic is rule-owned in both modes. There is nothing for a
    language model to add here -- "Q2 2025" has one correct pair of bounds
    -- and a model that supplies its own can only agree or narrow the
    population without saying so.
    """
    from agentic_analytics.agents.timescope import parse_time_scope

    window = parse_time_scope(_without_column_names(question, schema))
    return None if window is None else (window.start, window.end)


def _unresolved_measure(text: str, schema: dict[str, Any]) -> str | None:
    """A measure the question named that matches nothing in the table.

    Naming a column that does not exist is not the same as naming none.
    Without this, "the average profit by holiday flag" on a table with no
    `profit` fell through to "the table offers exactly one numeric column,
    so there is nothing to choose" and published that column's average as
    the answer -- the measure the question asked for, silently replaced.

    Column references are removed first, in both spellings, because a
    question says "weekly sales" where the schema says `Weekly_Sales`.
    Checking word by word instead rejected that question: neither
    "weekly" nor "sales" alone names the column.

    Only the words before the grouping phrase are read, so the grouping's
    own noun is never mistaken for an unresolved measure.
    """
    head = _GROUPING_PHRASE.split(text)[0] if _GROUPING_PHRASE.search(text) else text
    remaining = _without_column_names(head, schema)
    content = [
        w
        for w in re.split(r"[^a-z0-9_]+", remaining.lower())
        if w and w not in _MEASURE_NOISE and not w.isdigit() and len(w) > 2
    ]
    # A word that resolves to a column by plural is not unresolved.
    # `_without_column_names` strips exact spellings, so "categories" for a
    # column called `category` survived it and read as a measure the table
    # does not have -- which turned "top categories", a legitimate
    # frequency ranking with no measure at all, into a refusal.
    columns = [str(f.get("name", "")) for f in schema.get("fields") or []]
    content = [w for w in content if not any(_mentions(w, c) >= 0 for c in columns)]
    # Nothing left means every content word in measure position named a
    # real column. Nothing there to begin with means no measure was named,
    # which is a different situation the single-candidate rule may settle.
    return " ".join(content[:3]) if content else None


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
    #: Authoritative ordered grouping list. ``dimension`` below is retained
    #: for one release as a compatibility projection only; all decisions,
    #: hashing and SQL compilation use this tuple.
    dimensions: tuple[str, ...] = ()
    dimension: str | None = None
    time_field: str | None = None
    time_grain: Literal["day", "week", "month", "quarter", "year"] | None = None
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
    #: Why the planner named in `interpretation` is the one that decided,
    #: when that was not the obvious answer. Set when a cloud plan could
    #: not honour an unambiguous contract and the engine's own contract
    #: executed instead. Provenance only: excluded from the canonical
    #: contract, because the arithmetic is identical either way.
    planner_note: str = ""
    #: Why this question could not be resolved, when it could not. Typed so
    #: a router can tell "this dataset has no such column" from "the
    #: wording did not say which column": those were the same
    #: `confident=False` and therefore the same non-decision, which left
    #: only two possible policies -- never ask a model, or always ask one.
    #: Diagnostics, not semantics, and so excluded from the canonical
    #: contract: two planners reaching the same contract must hash the same
    #: however much they struggled to get there.
    issues: tuple[ResolutionIssue, ...] = ()

    def __post_init__(self) -> None:
        """Keep the legacy singular grouping honest during migration.

        A two-cut question must never masquerade as a one-cut question.  The
        singular projection therefore exists only when there is exactly one
        authoritative grouping.
        """
        ordered = tuple(dict.fromkeys(str(item) for item in self.dimensions if item))
        if not ordered and self.dimension:
            ordered = (self.dimension,)
        if len(ordered) > 2:
            raise ValueError("at most two grouping dimensions are supported")
        self.dimensions = ordered
        self.dimension = ordered[0] if len(ordered) == 1 else None

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
            "dimensions": list(self.dimensions),
            "time_field": self.time_field,
            "time_grain": self.time_grain,
            "period": list(self.period) if self.period else None,
            "period_field": self.period_field,
            "filters": [{k: v for k, v in item.items() if k != "source_text"} for item in filters],
            # Sort direction only reaches the SQL for a ranking; every other
            # operation orders by its dimension or its period. Carrying the
            # raw flag made two identical interpretations hash differently:
            # a paid Compare Both on "the average X by Y" had the cloud plan
            # return `ascending: true` and the rules `false`, the engine
            # executed both correctly -- same values, same coverage -- and
            # the page reported "different governed interpretations" over a
            # field that changes nothing. Normalised, so contract identity
            # means what it says.
            "ascending": self.ascending if self.operation == "rank" else False,
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
            # Compatibility only. New consumers must read ``dimensions``.
            "dimension": self.dimension,
            "dimensions": list(self.dimensions),
            "time_field": self.time_field,
            "time_grain": self.time_grain,
            "ascending": self.ascending,
            "confident": self.confident,
            "explanation": self.explanation,
            "named_columns": list(self.named_columns),
            "interpretation": self.interpretation,
            "planner_note": self.planner_note,
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
    *,
    unresolved: ResolutionIssue = ResolutionIssue.UNRESOLVED_MEASURE,
    competing: ResolutionIssue = ResolutionIssue.COMPETING_MEASURE_CANDIDATES,
) -> tuple[str | None, str | None, ResolutionIssue | None]:
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
        return overlap[0], None, None
    explicit = [c for c in named if c in (also_if_named or [])]
    if explicit:
        return explicit[0], None, None
    if len(candidates) == 1:
        return candidates[0], None, None
    if not candidates:
        # Nothing to choose from. No planner resolves this, because the
        # column is not in the file -- which is why this is a different
        # issue from having several and not being told which.
        return None, f"the table has no column that looks like a {what}", unresolved
    return (
        None,
        (
            f"the question does not name which {what} to use, and the table has "
            f"{len(candidates)} to choose from"
        ),
        competing,
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
    raw_dimensions = list(payload.get("dimensions") or [])
    raw_sources = list(payload.get("dimension_sources") or [])
    if not raw_dimensions and payload.get("dimension") is not None:
        raw_dimensions = [payload.get("dimension")]
        raw_sources = [payload.get("dimension_source", "")]
    if len(raw_dimensions) > 2 or len(raw_sources) not in {0, len(raw_dimensions)}:
        return refuse("the AI plan supplied an invalid grouping list")
    if not raw_sources:
        raw_sources = [""] * len(raw_dimensions)
    dimensions = tuple(str(item) for item in raw_dimensions)
    time_field = payload.get("time_field")
    time_grain = payload.get("time_grain")

    if measure is not None:
        measure = str(measure)
        if measure not in fields or measure not in set(measures) | aggregatable:
            return refuse(f"the AI plan named {measure!r} as a measure, but it is not aggregatable")
        if not _source_is_in_question(str(payload.get("measure_source", "")), question):
            return refuse("the AI plan did not ground its measure in the question")
    if operation in {"sum", "average", "rank"} and measure is None:
        return refuse(f"the {operation} operation needs a measure")

    for dimension, dimension_source in zip(dimensions, raw_sources, strict=True):
        if dimension not in fields:
            return refuse(
                f"the AI plan named a grouping column this table does not have: {dimension!r}"
            )
        # A named numeric column can be a grouping even when the inference
        # layer did not classify it as a dimension, and the source excerpt
        # is the authority that the visitor asked for it.  An identifier or
        # a free-text column cannot: grouping by one puts its raw values
        # into the result as group labels, and from there into a remote
        # prompt.  Checking only `fields` here left that boundary holding
        # in deterministic mode alone -- an AI plan could group by
        # `customer_name` on a question the rules refuse outright.
        if dimension not in _groupable(schema):
            return refuse(
                f"the AI plan asked to group by {dimension!r}, which this engine does not "
                "offer as a grouping"
            )
        if not _source_is_in_question(str(dimension_source), question):
            return refuse("the AI plan did not ground its grouping in the question")
    if len(set(dimensions)) != len(dimensions):
        return refuse("the AI plan repeated the same grouping column")
    if measure is not None and measure in dimensions:
        return refuse("the AI plan cannot aggregate a column by that same column")

    if time_field is not None:
        time_field = str(time_field)
        if time_field not in time_fields:
            return refuse(f"the AI plan named {time_field!r} as a time field, but it is not one")
    if time_grain is not None and time_grain not in {"day", "week", "month", "quarter", "year"}:
        return refuse("the AI plan supplied an unsupported time grain")
    if time_grain is not None and time_field is None:
        return refuse("the AI plan supplied a time grain without a time field")
    if operation == "trend":
        expected_time_field, time_refusal = _period_field_for_question(question, time_fields)
        if time_refusal:
            return refuse(time_refusal)
        if time_field != expected_time_field:
            return refuse("the AI plan used a trend date column the question did not establish")

    # The column a period filters is not the same thing as the axis a trend
    # is drawn along, and a model is only ever asked for the latter. A
    # contract being revalidated carries both, so it is read here rather
    # than folded into one field; see the construction at the end.
    period_field = payload.get("period_field") or time_field
    if period_field is not None:
        period_field = str(period_field)
        if period_field not in time_fields:
            return refuse(f"the AI plan named {period_field!r} as a time field, but it is not one")

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
        if not (start and end and period_field):
            return refuse("the AI plan supplied only part of a time restriction")
        try:
            start_date = date.fromisoformat(str(start))
            end_date = date.fromisoformat(str(end))
        except ValueError:
            return refuse("the AI plan supplied an invalid ISO date")
        if start_date > end_date:
            return refuse("the AI plan supplied a reversed time period")
        period = (str(start), str(end))

    # The period must be the one the question states, not one the plan
    # chose.  Both directions matter and only one was covered: a plan that
    # drops "in Q2" is caught downstream as a missing restriction, but a
    # plan that *adds* a period narrows the population with nothing in the
    # question behind it, and the answer published is to a different
    # question.  Asked "what is the average annual revenue", a plan
    # restricting to 2024 was executed.
    stated = _named_period(question, schema)
    if period != stated:
        if stated is None:
            return refuse("the AI plan applied a time period the question did not state")
        if period is None:
            if not time_fields:
                return refuse(
                    "the question names a period, and this table has no date column to apply it to"
                )
            return refuse("the AI plan dropped the time period stated in the question")
        return refuse("the AI plan changed the time period stated in the question")

    if period is not None:
        expected_period_field, period_refusal = _period_field_for_question(question, time_fields)
        if period_refusal:
            return refuse(period_refusal)
        if period_field != expected_period_field:
            return refuse(
                "the AI plan applied the period to a date column the question did not establish"
            )

    # Protect explicit rule-resolved components from being reinterpreted.
    rules = resolve_question(question, schema)
    if rules.confident:
        if operation != rules.operation:
            return refuse("the AI plan changed the operation explicitly requested in the question")
        if rules.measure in rules.named_columns and measure != rules.measure:
            return refuse("the AI plan changed the measure explicitly named in the question")
        grouping_is_explicit = any(item in rules.named_columns for item in rules.dimensions)
        if grouping_is_explicit and dimensions != rules.dimensions:
            return refuse("the AI plan changed the grouping explicitly named in the question")
        if rules.time_grain is not None and time_grain != rules.time_grain:
            return refuse("the AI plan changed the time grain explicitly named in the question")
        # Direction is the whole of a ranking question.  "Which region has
        # the highest revenue" answered ascending is the bottom of the
        # table presented as the top, and every other field agrees, so
        # nothing downstream can notice.
        if rules.operation == "rank" and bool(payload.get("ascending", False)) != rules.ascending:
            return refuse("the AI plan reversed the ranking direction the question asked for")

    return QuestionMapping(
        operation=operation,  # type: ignore[arg-type]
        table=table,
        measure=measure,
        dimensions=dimensions,
        # `time_field` is the axis `build_sql` groups a trend along, and
        # the rule path leaves it unset for every other operation. Setting
        # it here from the plan's date column put a trend axis on a rank,
        # which changed the canonical contract without changing the SQL --
        # so the engine's own accepted contract failed its own
        # revalidation, and every non-trend question naming a period
        # ("what was the total revenue in 2024") published nothing.
        time_field=time_field if operation == "trend" else None,
        time_grain=time_grain if operation == "trend" else None,
        period=period,
        period_field=period_field if period else None,
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
        "dimensions": contract.get("dimensions")
        or ([contract.get("dimension")] if contract.get("dimension") else []),
        "dimension_sources": [
            question
            for _ in (
                contract.get("dimensions")
                or ([contract.get("dimension")] if contract.get("dimension") else [])
            )
        ],
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
        "time_grain": contract.get("time_grain"),
        "period_field": contract.get("period_field") or contract.get("time_field"),
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


@dataclass(frozen=True)
class QuestionRequirements:
    """What the question itself fixes, read without consulting any contract.

    This exists because the first attempt at question coverage derived its
    requirements *from the accepted contract*: a grouping the planner had
    dropped was never "required", so nothing was ever missing and
    `complete` was a rename of `confident`. A gate that asks the thing
    under test what it should have done cannot fail.

    So every field here comes from the question text and the schema, using
    the same parsing primitives the resolver uses but none of its
    decisions. A component appearing here and absent from the executed
    contract is a coverage failure whatever the planner thought.
    """

    operation: Operation | None = None
    measure: str | None = None
    dimensions: tuple[str, ...] = ()
    time_grain: Literal["day", "week", "month", "quarter", "year"] | None = None
    period: tuple[str, str] | None = None
    filters: tuple[tuple[str, str, str], ...] = ()
    ascending: bool | None = None


def question_requirements(question: str, schema: dict[str, Any]) -> QuestionRequirements:
    """Extract the components the question states outright.

    Deliberately conservative: it reports only what the wording fixes
    beyond doubt. A component it cannot see is not treated as required, so
    the gate never invents an obligation -- but anything it does see must
    survive into the executed contract.
    """
    text = _normalise(question)
    intent_text = _normalise(_without_column_names(question, schema))

    operation: Operation | None = None
    for pattern, matched_operation in _OPERATION_PATTERNS:
        if re.search(pattern, intent_text, re.IGNORECASE):
            operation = matched_operation
            break

    groupable = _groupable(schema)
    all_columns = [str(f.get("name", "")) for f in schema.get("fields") or []]
    named = [c for c in all_columns if _mentions(text, c) >= 0]

    resolution = parse_filters(question, schema)
    # A column a filter claimed is not a grouping the question asked for.
    # The phrase capture runs to the end of the clause, so "by region for
    # age 30 to 40" mentions two groupable columns -- and counting `age` as
    # a requested grouping made coverage demand a cut the question never
    # asked for, which would have failed a correct contract. The resolver
    # excludes these for the same reason; both must agree or the gate
    # fights the planner.
    dimensions: list[str] = []
    for phrase in _GROUPING_PHRASE.findall(_normalise(question)):
        head = _normalise(_grouping_head(phrase))
        for candidate in groupable:
            if candidate not in dimensions and _mentions(head, candidate) >= 0:
                dimensions.append(candidate)

    # A measure is required only where the question names a column that
    # could be one and did not claim it as the grouping.
    measures, _dims, _time = _roles(schema)
    aggregatable = [str(c) for c in schema.get("aggregatable_if_named", [])]
    measure_candidates = [
        c for c in named if c not in dimensions and (c in measures or c in aggregatable)
    ]
    # An explicitly named numeric column stays a measure candidate even
    # where inference called it an identifier: 98% distinct makes a column
    # a poor grouping, not a thing that cannot be summed.
    if not measure_candidates:
        numeric = {
            str(f.get("name", ""))
            for f in schema.get("fields") or []
            if _base_numeric(str(f.get("data_type") or f.get("type") or ""))
        }
        measure_candidates = [c for c in named if c not in dimensions and c in numeric]
    measure = measure_candidates[0] if len(measure_candidates) == 1 else None

    filters = tuple(
        (
            str(item.get("column", "")),
            str(item.get("operator", "")),
            "" if item.get("value") is None else str(item.get("value")),
        )
        for item in (f.as_dict() for f in resolution.filters)
    )

    ascending: bool | None = None
    if operation == "rank":
        ascending = bool(_ASCENDING.search(question))

    return QuestionRequirements(
        operation=operation,
        measure=measure,
        dimensions=tuple(dimensions),
        time_grain=_time_grain(question, schema),
        period=_named_period(question, schema),
        filters=filters,
        ascending=ascending,
    )


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
    named_period = _named_period(question, schema)
    period_field: str | None = None
    if named_period is not None:
        if not time_fields:
            return QuestionMapping(
                operation="profile",
                table=table,
                confident=False,
                explanation=(
                    "the question names a period, and this table has no date column to apply it to"
                ),
                named_columns=[],
                issues=(ResolutionIssue.MISSING_PERIOD_FIELD,),
            )
        period_field, period_refusal = _period_field_for_question(question, time_fields)
        if period_refusal:
            return QuestionMapping(
                operation="profile",
                table=table,
                confident=False,
                explanation=period_refusal,
                named_columns=[],
                period=named_period,
                issues=(ResolutionIssue.AMBIGUOUS_PERIOD_SEMANTICS,),
            )

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
            issues=(ResolutionIssue.MISSING_FILTER_BINDING,),
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
            issues=(ResolutionIssue.MISSING_FILTER_BINDING,),
        )

    text = _normalise(question)

    def refuse(reason: str, *issues: ResolutionIssue) -> QuestionMapping:
        """Decline, and say which kind of problem it was.

        The issue codes are what let a router tell a question this dataset
        cannot answer from one whose wording was merely underspecified.
        A site that names none is still a refusal; it is just one no
        planner will be asked to retry.
        """
        return QuestionMapping(
            operation="profile",
            table=table,
            confident=False,
            explanation=reason,
            named_columns=named,
            period=named_period,
            period_field=period_field,
            issues=tuple(issues),
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
    # Column references are removed, and so is the text a row filter has
    # already claimed. "total revenue with temperature at least 50"
    # contains "least", which the ranking pattern matched -- so a filtered
    # total became a ranking, needed a grouping it was never given, and was
    # refused. A clause another component owns must not also decide the
    # intent.
    intent_source = _without_column_names(question, schema)
    for row_filter in resolution.filters:
        claimed = str(getattr(row_filter, "source_text", "") or "")
        if claimed:
            intent_source = intent_source.replace(claimed, " ")
            # The excerpt is taken from the original question, so it may not
            # survive column removal verbatim. Fall back to the operator and
            # value, which is the part that misleads the intent matcher.
            tail = claimed.split()[-3:]
            if len(tail) >= 2:
                intent_source = intent_source.replace(" ".join(tail), " ")
    intent_text = _normalise(intent_source)
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
        return refuse(
            "the question does not ask for a count, total, average, ranking or trend",
            ResolutionIssue.UNSUPPORTED_OPERATION,
        )
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
    #
    # Declared dimensions are tried first, then any other column the
    # phrase names. A numeric column is not *offered* as a grouping --
    # choosing one unprompted would be a guess -- but "by store" names it
    # explicitly, and reading that as anything other than a grouping
    # produced "average Store by Holiday_Flag" for a question that said
    # "by Store": both roles inverted.
    requested_dimensions: list[str] = []
    # Widened to *numeric* columns only, never to text.
    #
    # See `_groupable` for why text columns are not widened into.
    groupable = _groupable(schema)
    # A column a filter already claimed is not a grouping. The phrase
    # capture runs to the end of the clause, so "by territory for headcount
    # 41 to 50" is one phrase mentioning two groupable columns -- and
    # harvesting both turned a filtered breakdown by territory into a
    # two-cut breakdown by territory and headcount, which silently changed
    # the answer.
    filter_claimed = {str(getattr(f, "column", "")) for f in resolution.filters}
    for phrase in _GROUPING_PHRASE.findall(text):
        head = _normalise(_grouping_head(phrase))
        for candidate in groupable:
            if candidate not in requested_dimensions and _mentions(head, candidate) >= 0:
                requested_dimensions.append(candidate)
    if not requested_dimensions and operation == "rank":
        # "Which store had the highest total sales" names its grouping as
        # the subject of the question. Restricted to a ranking: elsewhere
        # "which" is usually asking about the answer, not the grouping.
        for phrase in _SUBJECT_PHRASE.findall(text):
            for candidate in groupable:
                if _mentions(_normalise(phrase), candidate) >= 0:
                    requested_dimensions.append(candidate)
                    break
            if requested_dimensions:
                break
    if not requested_dimensions:
        # Otherwise a dimension the question merely named is still a
        # grouping -- "returns by category" and "category returns" ask the
        # same thing.
        # A column a filter claimed is not a grouping the question asked
        # for. "total revenue where promo_flag is 1" restricts to one value
        # of that column; grouping by it then returns a single group and
        # presents a restriction as a breakdown.
        mentioned_dims = [c for c in named if c in dimensions and c not in filter_claimed]
        requested_dimensions = mentioned_dims[:2]

    if len(requested_dimensions) > 2:
        return refuse(
            "the question requests more than two grouping columns; narrow it to one or two",
            ResolutionIssue.UNSUPPORTED_OPERATION,
        )
    grouping = tuple(requested_dimensions)
    grain = _time_grain(question, schema)

    if not grouping and grain is None:
        # A grouping was asked for and nothing in the table answers to it.
        # Dropping it and returning an ungrouped total was the wrong
        # outcome twice over: the visitor asked for a breakdown and got a
        # single number, and the number was presented confidently as the
        # answer. "by loyalty_tier" on a table with no such column is a
        # question about data that is not here.
        unresolved = _unresolved_grouping(text, schema)
        if unresolved:
            return refuse(
                f"the question groups by {unresolved!r}, which is not a column of this table",
                ResolutionIssue.UNRESOLVED_DIMENSION,
            )

    if operation == "trend":
        time_field, why, issue = _pick(
            named,
            time_fields,
            "date column",
            unresolved=ResolutionIssue.MISSING_PERIOD_FIELD,
            competing=ResolutionIssue.COMPETING_DIMENSION_CANDIDATES,
        )
        if time_field is None:
            return refuse(str(why), *([issue] if issue else []))
        expected_time_field, time_refusal = _period_field_for_question(question, time_fields)
        if time_refusal:
            return refuse(time_refusal, ResolutionIssue.AMBIGUOUS_PERIOD_SEMANTICS)
        if time_field != expected_time_field:  # defensive: `_pick` and the policy must agree
            return refuse(
                "the question does not establish that date column as its time axis",
                ResolutionIssue.AMBIGUOUS_PERIOD_SEMANTICS,
            )
        measure = next((c for c in named if c in measures), None)
        if measure is None and len(measures) == 1:
            measure = measures[0]
        return QuestionMapping(
            operation="trend",
            table=table,
            measure=measure,
            dimensions=grouping,
            time_field=time_field,
            time_grain=grain or "month",
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
            dimensions=grouping,
            confident=True,
            filters=row_filters,
            explanation=(f"row count by {', '.join(grouping)}" if grouping else "total row count"),
            named_columns=named,
            period=named_period,
            period_field=period_field,
        )

    # sum, average and rank all need a measure.
    #
    # A column the question named as the *grouping* is withheld from the
    # widening that lets a named numeric dimension be aggregated. The
    # widening is right in itself -- if a question names a numeric column
    # as the thing to measure, the user has said which to use -- but it
    # could not tell a measure reference from a grouping reference. So
    # "the average profit by holiday flag", on a table with no `profit`,
    # took the flag from the `by` phrase, averaged it, and published 0.07
    # as the answer: a measure silently replaced by the column the
    # question asked to group by.
    widening = [c for c in aggregatable if c not in grouping]
    # A column a filter already claimed is not also a candidate measure.
    # "total Weekly_Revenue with Avg_Temp_C at least 50" names two numeric
    # columns, and both being measures made the measure ambiguous -- so a
    # question that says plainly what to total was refused because it also
    # said what to restrict.
    filtered_columns = {str(getattr(f, "column", "")) for f in resolution.filters}
    candidates = [c for c in named if c not in filtered_columns and c not in grouping] or [
        c for c in named if c not in grouping
    ]
    measure, why, issue = _pick(
        candidates,
        measures,
        "numeric column",
        widening,
        unresolved=ResolutionIssue.UNRESOLVED_MEASURE,
        competing=ResolutionIssue.COMPETING_MEASURE_CANDIDATES,
    )

    # The guard applies only where a substitution is possible: the measure
    # was not named by the question and came from "the table offers
    # exactly one numeric column, so there is nothing to choose". A
    # question that named a real measure column needs no checking, and
    # checking it anyway read interrogatives like "which region has" as
    # unresolved measures.
    # Also when nothing could be resolved at all. "the table has 2 numeric
    # columns to choose from" is true but unhelpful for a question that
    # named a column plainly -- it just is not in this table. Naming the
    # word back is the difference between a reader rephrasing and a reader
    # guessing.
    if measure is None or measure not in named:
        unresolved = _unresolved_measure(question, schema)
        if unresolved is not None:
            return refuse(
                f"the question asks about {unresolved!r}, which is not a column of "
                "this table; name a numeric column that is",
                ResolutionIssue.UNRESOLVED_MEASURE,
            )

    # A column cannot be both the thing being totalled and the thing being
    # grouped by. This happens when a numeric column was read as a
    # dimension -- "total units_sold by store_code" matched `units_sold` on
    # the grouping search first, and then totalled it as well, so the
    # answer was grouped by the very column it was summing.
    if measure is not None and measure in grouping:
        # Refuse rather than repair. This branch used to replace the
        # grouping with whatever other dimension the question mentioned,
        # or delete it outright -- and that deletion is exactly how "total
        # website_visits by age" became SUM(age) with no grouping and was
        # published as an answer about website visits.
        #
        # Reaching here means the resolver picked one column for both
        # roles, which is an ambiguity in the question or a gap in the
        # role rules. Either way the visitor asked for something this
        # engine cannot represent, and saying so is the only honest
        # outcome.
        return refuse(
            f"{measure!r} was read as both the value to aggregate and the column to "
            "group by; name a different column for one of them",
            ResolutionIssue.ROLE_COLLISION,
        )

    if measure is None:
        if operation == "rank" and grouping:
            # "top regions" with no measure named is a frequency ranking,
            # which needs no numeric column at all.
            return QuestionMapping(
                operation="rank",
                table=table,
                dimensions=grouping,
                ascending=bool(_ASCENDING.search(question)),
                confident=True,
                filters=row_filters,
                explanation=f"{', '.join(grouping)} values ranked by how often they occur",
                named_columns=named,
            )
        return refuse(str(why), *([issue] if issue else []))

    if operation == "rank":
        # The same groupable set a breakdown uses. Restricting a ranking to
        # declared dimensions refused "which branch had the highest total"
        # on any table whose grouping key is numeric -- the breakdown of
        # the same column worked, so the two paths disagreed about what
        # could be grouped.
        rankable = [c for c in _groupable(schema) if c != measure]
        rank_dimension, why, issue = _pick(
            named,
            rankable,
            "grouping column",
            unresolved=ResolutionIssue.UNRESOLVED_DIMENSION,
            competing=ResolutionIssue.COMPETING_DIMENSION_CANDIDATES,
        )
        if grouping:
            rank_dimension = grouping[0]
        if rank_dimension is None:
            return refuse(str(why), *([issue] if issue else []))
        return QuestionMapping(
            operation="rank",
            table=table,
            measure=measure,
            dimensions=(rank_dimension,),
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
        dimensions=grouping,
        confident=True,
        filters=row_filters,
        explanation=(
            f"{verb} {measure} by {', '.join(grouping)}" if grouping else f"{verb} {measure}"
        ),
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
    for dimension in mapping.dimensions:
        out[_alias(dimension)] = {
            "kind": "grouping",
            "table": mapping.table,
            "column": dimension,
        }
    if mapping.time_field and mapping.operation == "trend":
        out["period"] = {
            "kind": "derived",
            "aggregate": (mapping.time_grain or "month").upper(),
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
        out["value_count"] = {
            "kind": "aggregate",
            "aggregate": "COUNT",
            "table": mapping.table,
            "column": mapping.measure,
            "expression": f"COUNT({mapping.table}.{mapping.measure})",
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


def _execution_where(mapping: QuestionMapping) -> str:
    """The exact row predicate shared by result and coverage queries."""
    where = _where(mapping)
    if mapping.operation != "trend" or not mapping.time_field:
        return where
    stamp = _quote(mapping.time_field)
    return f" WHERE {stamp} IS NOT NULL{where.replace(' WHERE ', ' AND ', 1)}"


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


def is_breakdown(mapping: QuestionMapping) -> bool:
    """Whether this contract asks for a grouped breakdown.

    A ranking is grouped too, but it is a short list the question asked
    for, so its limit is the answer rather than a shortfall.
    """
    return (bool(mapping.dimensions) and mapping.operation in {"count", "sum", "average"}) or (
        mapping.operation == "trend" and mapping.time_field is not None
    )


def _trend_period_expression(mapping: QuestionMapping) -> str:
    """Deterministic display/sort key for the accepted temporal grain."""
    stamp = _quote(mapping.time_field or "")
    grain = mapping.time_grain or "month"
    truncated = f"date_trunc('{grain}', CAST({stamp} AS TIMESTAMP))"
    if grain == "year":
        return f"strftime({truncated}, '%Y')"
    if grain == "month":
        return f"strftime({truncated}, '%Y-%m')"
    if grain in {"day", "week"}:
        return f"strftime({truncated}, '%Y-%m-%d')"
    return f"concat(strftime({truncated}, '%Y'), '-Q', CAST(quarter({truncated}) AS VARCHAR))"


def build_coverage_sql(mapping: QuestionMapping) -> str | None:
    """How many groups and rows the contract's population actually has.

    One extra aggregate over the same filtered population. The alternative
    -- deriving coverage from the rows that came back -- is what produced
    "every row in the dataset" for a result holding 55% of them.
    """
    if not is_breakdown(mapping):
        return None
    table = _quote(mapping.table)
    where = _execution_where(mapping)
    grouping = [_quote(item) for item in mapping.dimensions]
    if mapping.operation == "trend" and mapping.time_field:
        grouping.insert(0, _trend_period_expression(mapping))
    select = ", ".join(f"{expr} AS g{index}" for index, expr in enumerate(grouping, 1))
    ordinals = ", ".join(str(index) for index in range(1, len(grouping) + 1))
    value_inner = ""
    value_outer = ""
    if mapping.measure:
        value_inner = f", COUNT({_quote(mapping.measure)}) AS value_rows"
        value_outer = ", COALESCE(SUM(value_rows), 0) AS observations_matching"
    return (
        "SELECT COUNT(*) AS groups_total, "
        f"COALESCE(SUM(group_rows), 0) AS rows_matching{value_outer} FROM ("
        f"SELECT {select}, COUNT(*) AS group_rows{value_inner} "
        f"FROM {table}{where} GROUP BY {ordinals}) AS grouped"
    )


def build_row_total_sql(mapping: QuestionMapping) -> str:
    """Rows in the table before any filter, so a filtered population can be
    reported as a share of the whole rather than as the whole."""
    return f"SELECT COUNT(*) AS rows_total FROM {_quote(mapping.table)}"


def build_sql(mapping: QuestionMapping) -> str | None:
    """Compose the statement for a resolved mapping.

    Returns ``None`` for a mapping that is not answerable as a single
    aggregate -- a profile, or anything not confident.
    """
    if not mapping.confident or mapping.operation == "profile":
        return None
    table = _quote(mapping.table)
    where = _execution_where(mapping)

    if mapping.operation == "trend":
        if mapping.time_field is None:
            return None
        period = _trend_period_expression(mapping)
        grouping_select = [f"{period} AS period"] + [
            f"{_quote(item)} AS {_alias(item)}" for item in mapping.dimensions
        ]
        group_count = len(grouping_select)
        group_by = ", ".join(str(index) for index in range(1, group_count + 1))
        if mapping.measure is None:
            value = "COUNT(*) AS row_total"
            observation_count = ""
        else:
            label = _alias("total", mapping.measure)
            value = f"ROUND(SUM(CAST({_quote(mapping.measure)} AS DOUBLE)), 4) AS {label}"
            observation_count = f", COUNT({_quote(mapping.measure)}) AS value_count"
        return (
            f"SELECT {', '.join(grouping_select)}, {value}, COUNT(*) AS row_count"
            f"{observation_count} "
            f"FROM {table}{where} "
            f"GROUP BY {group_by} ORDER BY {group_by} LIMIT {TREND_LIMIT + 1}"
        )

    if mapping.operation == "count":
        if not mapping.dimensions:
            return f"SELECT COUNT(*) AS row_count FROM {table}{where}"
        selected = [f"{_quote(item)} AS {_alias(item)}" for item in mapping.dimensions]
        ordinals = ", ".join(str(index) for index in range(1, len(selected) + 1))
        return (
            f"SELECT {', '.join(selected)}, COUNT(*) AS row_count "
            f"FROM {table}{where} GROUP BY {ordinals} ORDER BY {ordinals} NULLS LAST "
            f"LIMIT {GROUP_RESULT_MAX + 1}"
        )

    if mapping.operation == "rank" and mapping.measure is None:
        if not mapping.dimensions:
            return None
        dimension = mapping.dimensions[0]
        dim = _quote(dimension)
        direction = "ASC" if mapping.ascending else "DESC"
        return (
            f"SELECT {dim} AS {_alias(dimension)}, COUNT(*) AS row_count "
            f"FROM {table}{where} GROUP BY 1 ORDER BY 2 {direction} NULLS LAST LIMIT {RANK_LIMIT}"
        )

    if mapping.measure is None:
        return None
    aggregate = "AVG" if mapping.operation == "average" else "SUM"
    label = _alias("average" if mapping.operation == "average" else "total", mapping.measure)
    value = f"ROUND({aggregate}(CAST({_quote(mapping.measure)} AS DOUBLE)), 4) AS {label}"

    if not mapping.dimensions:
        return (
            f"SELECT {value}, COUNT(*) AS row_count, "
            f"COUNT({_quote(mapping.measure)}) AS value_count FROM {table}{where}"
        )

    selected = [f"{_quote(item)} AS {_alias(item)}" for item in mapping.dimensions]
    ordinals = ", ".join(str(index) for index in range(1, len(selected) + 1))
    if mapping.operation == "rank":
        # The question asked for a top or bottom list, so ordering by the
        # measure is the answer rather than an artefact of the limit.
        direction = "ASC" if mapping.ascending else "DESC"
        return (
            f"SELECT {', '.join(selected)}, {value}, COUNT(*) AS row_count, "
            f"COUNT({_quote(mapping.measure)}) AS value_count "
            f"FROM {table}{where} GROUP BY {ordinals} "
            f"ORDER BY {len(selected) + 1} {direction} NULLS LAST "
            f"LIMIT {RANK_LIMIT}"
        )
    # A breakdown. Ordered by the dimension, because ordering by the measure
    # and then cutting at a limit turns "total sales by store" into an
    # undeclared top-list -- which is exactly how a 25-of-45 result came to
    # be published as the complete breakdown.
    return (
        f"SELECT {', '.join(selected)}, {value}, COUNT(*) AS row_count, "
        f"COUNT({_quote(mapping.measure)}) AS value_count "
        f"FROM {table}{where} GROUP BY {ordinals} ORDER BY {ordinals} NULLS LAST "
        f"LIMIT {GROUP_RESULT_MAX + 1}"
    )
