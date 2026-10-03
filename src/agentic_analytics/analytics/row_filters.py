"""Row filters a question asked for, resolved against the uploaded schema.

A question can restrict the population it is about -- "for people aged 30
to 40", "where price is under 20", "ratings of at least 4". The planner
understood the measure, the grouping and the period and understood none of
this, so the restriction was dropped and the answer was computed over every
row. It was not reported as dropped either: the mapping said it was
confident, and the number it returned looked like an answer to the question
that was asked.

Two things follow, and the second matters more than the first.

The first is that resolvable filters are resolved: a real column of the
inferred schema, an operator its type permits, and a value parsed as a
finite number. Nothing from the question is ever interpolated into SQL --
the column is quoted through the identifier guard and the value is bound as
a literal only after it has survived parsing as a number.

The second is that unresolvable ones are *refused*. A question that clearly
restricts its population and cannot be mapped to a column must not fall
through and be answered over everything. `constraint_detected` is that
signal: it is set whenever the wording restricts, whether or not a filter
came out, and the planner refuses when it is set and nothing was resolved.
Silently widening the population is the failure this module exists to stop.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

#: Operators, and the SQL they become. Closed set: the question never
#: chooses an operator by name, it chooses one of these by phrasing.
Operator = str

#: Words that restrict a population. Their presence is what makes a
#: question "constrained"; resolving them to a column is separate, and a
#: question that restricts without resolving is refused rather than
#: widened.
_RESTRICTIVE = re.compile(
    r"\b(between|from|aged|age[sd]?|over|under|above|below|at\s+least|at\s+most|"
    r"greater\s+than|less\s+than|more\s+than|fewer\s+than|no\s+more\s+than|"
    r"no\s+less\s+than|exactly|equal\s+to|up\s+to|starting\s+at|minimum|maximum|"
    r"only|where|with|for)\b",
    re.IGNORECASE,
)

#: A number the wording could be restricting by. Deliberately excludes
#: anything that reads as a year or a quarter -- those are the period
#: layer's business, and treating "in 1998" as a numeric filter would
#: refuse questions the engine already answers correctly.
_NUMBER = re.compile(r"(?<![\w.])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)(?![\w.])")
_YEARLIKE = re.compile(r"\b(19|20)\d{2}\b")
_QUARTER = re.compile(r"\bq[1-4]\b", re.IGNORECASE)

#: Phrasings that bound a range. `to` and `and` both appear; "30 to 40" and
#: "between 30 and 40" are the same request.
_RANGE = re.compile(
    r"\b(?:between\s+)?(?P<lo>-?\d+(?:\.\d+)?)\s*(?:to|-|\u2013|and|through)\s*(?P<hi>-?\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)

#: A comparison written out, with the column named before it.
_COMPARISON = re.compile(
    # At most three words. An unbounded run of words and spaces swallowed
    # whole clauses: in "where status is active and quantity at least 5"
    # the column capture ran from "average" to "quantity", consuming the
    # categorical filter's text so it could never be claimed.
    r"(?P<col>[A-Za-z_]\w*(?:\s+\w+){0,2}?)\s*"
    r"(?P<op>>=|<=|>|<|==|=|\bis\s+at\s+least\b|\bis\s+at\s+most\b|"
    r"\bat\s+least\b|\bat\s+most\b|\bover\b|\bunder\b|\babove\b|\bbelow\b|"
    r"\bgreater\s+than\b|\bless\s+than\b|\bmore\s+than\b|\bfewer\s+than\b|"
    # Word equality, after the comparisons so "is at least" still wins.
    r"\bis\s+equal\s+to\b|\bequals\b|\bis\b)\s*"
    r"(?P<val>-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

_WORD_OPS: dict[str, Operator] = {
    ">=": ">=",
    "<=": "<=",
    ">": ">",
    "<": "<",
    "==": "=",
    "=": "=",
    "at least": ">=",
    "is at least": ">=",
    "no less than": ">=",
    "minimum": ">=",
    "at most": "<=",
    "is at most": "<=",
    "no more than": "<=",
    "maximum": "<=",
    "over": ">",
    "above": ">",
    "greater than": ">",
    "more than": ">",
    "under": "<",
    "below": "<",
    "less than": "<",
    "fewer than": "<",
    # Equality in words. "where Promo_Flag is 1" is one of the commonest
    # ways a restriction gets written, and without these the parser saw a
    # restriction it could not map and refused the question -- the safe
    # direction, but a refusal for a phrasing the engine understands
    # everywhere else.
    "is": "=",
    "equals": "=",
    "is equal to": "=",
}

#: The most filters one question may carry. A bound, because each one is
#: another predicate composed into the same statement and a question
#: needing more than this is not one the deterministic parser should be
#: guessing at.
MAX_FILTERS = 6

#: Text a categorical value may consist of. Deliberately narrow: the value
#: is rendered into the statement, so anything outside letters, digits,
#: spaces, underscores, hyphens and dots is refused rather than escaped.
#: An escaping bug is a vulnerability; a refusal is an inconvenience.
_SAFE_VALUE = re.compile(r"^[\w][\w .-]{0,62}$", re.UNICODE)

#: Where an unquoted category value has to stop.
#:
#: `where chronotype is Night Owl by age` states a filter *and* a grouping,
#: and a value class that simply allowed spaces would swallow `by age` into
#: the category. These are the words that begin another clause, so a value
#: runs up to the first one of them and no further. Sentence punctuation
#: ends it too.
#:
#: This is a closed list rather than "stop at anything suspicious": a
#: boundary the grammar does not know about ends the value early, which
#: produces a refusal naming a value the column does not have -- visible and
#: correctable. The opposite error, swallowing a clause, produces a filter
#: nobody asked for.
#: A lookahead, so the boundary word is not consumed. Consuming it hid an
#: `or` from the disjunction guard, and a question that asked for a union
#: was read as a single filter -- silently widening nothing, but answering a
#: narrower question than the one asked.
_VALUE_BOUNDARY = (
    r"(?=\s+(?:by|per|grouped\s+by|group\s+by|for|with|where|and|or|only|"
    r"top|bottom|highest|lowest|most|least|ordered|order|sorted|sort|"
    r"ranked|rank|between|over|during|in)\b|\s*[,;.?!]|\s*$)"
)

#: `where status is active`, `where chronotype is "Night Owl"`.
#:
#: Two ways to write a value. A quoted one is taken literally and may hold
#: spaces without further argument. An unquoted one may hold spaces too, but
#: only as far as `_VALUE_BOUNDARY` allows, because an unquoted value has no
#: closing mark of its own.
#:
#: The captured text is a *candidate*. Which value it names is settled
#: against the column's own values, server-side, by `parse_filters`.
_EQUALITY = re.compile(
    r"\b(?:where|for|with|only)\s+(?P<col>[A-Za-z_][\w ]{0,40}?)\s+"
    r"(?:is|=|equals|equal\s+to)\s+"
    r"(?:"
    r"(?P<q>[\"\'])(?P<qval>[^\"\']{1,62})(?P=q)"
    r"|"
    r"(?P<val>[\w][\w .-]{0,62}?)"
    r")" + _VALUE_BOUNDARY,
    re.IGNORECASE,
)

#: A categorical restriction that was stated and not parsed.
#:
#: `_restricts` looks for a leftover *number* beside a restrictive word, so
#: it cannot see `where weather is <something unreadable>`: there is no
#: number in it. Such a clause used to disappear, and the question was
#: answered over every row -- the same failure the unknown-column branch
#: refuses, arriving by a different route.
_STATED_CATEGORY = re.compile(
    r"\b(?:where|with|only)\s+(?P<col>[A-Za-z_][\w ]{0,40}?)\s+"
    r"(?:is|=|equals|equal\s+to)\b",
    re.IGNORECASE,
)

#: `where value is missing` / `is not missing`.
_NULLITY = re.compile(
    r"\b(?:where|with)\s+(?P<col>[A-Za-z_][\w ]{0,40}?)\s+is\s+"
    r"(?P<neg>not\s+)?(?:missing|null|empty|blank)\b",
    re.IGNORECASE,
)

#: Disjunction is not supported. Detected so it refuses rather than being
#: silently read as conjunction, which would widen the population.
_DISJUNCTION = re.compile(r"\b(?:or|either)\b", re.IGNORECASE)

#: Numeric column types a comparison is meaningful on.
_NUMERIC_TYPES = frozenset(
    {
        "BIGINT",
        "DOUBLE",
        "DECIMAL",
        "FLOAT",
        "REAL",
        "HUGEINT",
        "INTEGER",
        "SMALLINT",
        "TINYINT",
        "UBIGINT",
        "UINTEGER",
        "USMALLINT",
        "UTINYINT",
        "NUMERIC",
    }
)


@dataclass(frozen=True)
class CategoryFilter:
    """An exact match on a non-numeric column.

    The value is not a number, so it is rendered as a quoted literal --
    which is why `_SAFE_VALUE` is narrow and a value outside it refuses.
    """

    column: str
    value: str
    negated: bool = False
    source_text: str = ""

    @property
    def operator(self) -> str:
        return "!=" if self.negated else "="

    def as_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "operator": self.operator,
            "value": self.value,
            "value_type": "text",
            "source_text": self.source_text,
        }

    def describe(self) -> str:
        word = "is not" if self.negated else "is"
        return f"{self.column.replace('_', ' ')} {word} {self.value}"


@dataclass(frozen=True)
class NullFilter:
    """`is missing` / `is not missing` on any column."""

    column: str
    negated: bool = False
    source_text: str = ""

    @property
    def operator(self) -> str:
        return "IS NOT NULL" if self.negated else "IS NULL"

    def as_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "operator": self.operator,
            "value": None,
            "value_type": "null",
            "source_text": self.source_text,
        }

    def describe(self) -> str:
        return f"{self.column.replace('_', ' ')} {'is present' if self.negated else 'is missing'}"


@dataclass(frozen=True)
class RowFilter:
    """One resolved restriction: a real column, a permitted operator, a
    finite number."""

    column: str
    operator: Operator
    value: Decimal
    #: The words this came from, kept for provenance so a reader can see
    #: which part of their question produced which predicate.
    source_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "operator": self.operator,
            "value": float(self.value),
            "source_text": self.source_text,
        }

    def describe(self) -> str:
        pretty = self.column.replace("_", " ")
        word = {">=": "at least", "<=": "at most", ">": "over", "<": "under", "=": "exactly"}
        return f"{pretty} {word.get(self.operator, self.operator)} {_plain(self.value)}"


#: Any restriction a question can state. Three kinds, one compiler.
Filter = RowFilter | CategoryFilter | NullFilter


@dataclass(frozen=True)
class FilterResolution:
    """What the wording asked for, and whether it could be honoured."""

    filters: tuple[Filter, ...] = ()
    #: The wording restricts the population, whether or not a filter came
    #: out of it. The planner refuses when this is set and `filters` is
    #: empty, because the alternative is answering a different question.
    constraint_detected: bool = False
    #: Why a detected restriction could not be resolved. Shown to the
    #: visitor, so it names the problem rather than the internals.
    refusal: str | None = None

    @property
    def usable(self) -> bool:
        return self.refusal is None

    def as_dicts(self) -> list[dict[str, Any]]:
        return [f.as_dict() for f in self.filters]


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _number(raw: str) -> Decimal | None:
    """A finite decimal, or nothing.

    Thousands separators are accepted because people write them; anything
    else -- infinities, NaN, exponent soup, stray text -- is refused rather
    than coerced, because this value is about to bound a query.
    """
    text = raw.strip().replace(",", "")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite() or math.isinf(float(value)) or math.isnan(float(value)):
        return None
    return value


def _numeric_columns(schema: dict[str, Any]) -> dict[str, str]:
    """Column name to declared type, for the columns a comparison fits."""
    out: dict[str, str] = {}
    for field in schema.get("fields") or []:
        name = str(field.get("name", ""))
        declared = str(field.get("data_type") or field.get("type") or "")
        if name and _base_type(declared) in _NUMERIC_TYPES:
            out[name] = declared
    return out


#: How many words before a number a column reference may sit. A filter is
#: spoken beside its value -- "people aged 30 to 40", "team size under 10"
#: -- so a column named further back is describing something else.
_LOCALITY = 2


def _base_type(declared: str | None) -> str:
    """`DECIMAL(18,2)` is a DECIMAL."""
    return (declared or "").split("(")[0].strip().upper()


def _all_columns(schema: dict[str, Any]) -> dict[str, str]:
    """Every column and its declared type."""
    out: dict[str, str] = {}
    for field_ in schema.get("fields") or []:
        name = str(field_.get("name", ""))
        if name:
            out[name] = str(field_.get("data_type") or field_.get("type") or "")
    return out


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _resolve_column(phrase: str, columns: dict[str, str]) -> tuple[str | None, bool]:
    """Match a spoken column reference to a real column.

    Returns the column and whether the reference was *ambiguous* -- two
    columns answering equally well is refused, not guessed between.
    Underscore and spoken spellings both resolve, so "annual revenue" and
    "annual_revenue" are the same request.
    """
    want = _normalise(phrase)
    if not want:
        return None, False
    exact = [c for c in columns if _normalise(c) == want]
    if len(exact) == 1:
        return exact[0], False
    if len(exact) > 1:
        return None, True

    # Otherwise the phrase is a run of words before a number, and more
    # than one column may appear in it: "average annual revenue by region
    # for people aged 30 to 40" mentions both `annual_revenue` and `age`.
    # They are not equally good candidates. The column a range belongs to
    # is the one spoken next to it -- "aged" here -- and `annual_revenue`
    # is the measure, mentioned earlier and about to be aggregated.
    #
    # So candidates are ranked by how close their mention sits to the
    # number, and only a genuine tie is ambiguous. Taking any match in the
    # window refused this question outright; taking the first would have
    # filtered on revenue.
    tokens = want.split()
    positions: dict[str, int] = {}
    for column in columns:
        parts = _normalise(column).split()
        if not parts:
            continue
        best = -1
        # Where this column's words last appear as a run. A column can be
        # several words -- `monthly_ad_spend` is three -- so matching one
        # token at a time can never find it.
        for index in range(len(tokens)):
            width = len(parts)
            if index + 1 >= width and tokens[index + 1 - width : index + 1] == parts:
                best = max(best, index)
        if best < 0 and len(parts) == 1:
            # A grammatical variant of a single-word column: `aged` names
            # `age`. Restricted to one word so a stray token cannot claim
            # a compound column.
            for index, token in enumerate(tokens):
                if _stem_match(parts[0], [token]):
                    best = max(best, index)
        if best >= 0:
            positions[column] = best
    if not positions:
        return None, False

    # And the mention has to sit next to the number, not merely somewhere
    # before it. Without this, "average annual revenue for tenure 30 to 40"
    # binds the range to `annual_revenue` -- the measure, named five words
    # earlier -- because no `tenure` column exists to outrank it. Filtering
    # revenue by 30 to 40 is a confidently wrong answer; refusing is right.
    positions = {c: i for c, i in positions.items() if i >= len(tokens) - _LOCALITY}
    if not positions:
        return None, False

    nearest = max(positions.values())
    closest = sorted(c for c, i in positions.items() if i == nearest)
    if len(closest) == 1:
        return closest[0], False
    return None, True


def _stem_match(column: str, tokens: list[str]) -> bool:
    """`aged` names `age`; `ratings` names `customer_rating`.

    Every word of the column must be accounted for, so `rating` does not
    claim `customer_rating` when a plain `rating` column also exists -- that
    case returns two hits and is refused as ambiguous instead.
    """
    parts = column.split()
    if not parts:
        return False
    return all(any(t.startswith(p) or p.startswith(t) for t in tokens) for p in parts)


#: How many distinct values of a column the binder will consider. A
#: category a reader names in a sentence is one of a handful; a column with
#: thousands of distinct values is not a category and the binder declines to
#: guess rather than scanning them.
VALUE_LOOKUP_LIMIT = 200

#: Supplied by a caller that holds the session. Returns the column's own
#: distinct values, or None when it cannot say.
ValueLookup = Callable[[str], "list[str] | None"]


def bind_category_value(
    column: str, candidate: str, lookup: ValueLookup | None
) -> tuple[str | None, str | None]:
    """Settle a captured value against the column's own values.

    Returns ``(value, refusal)``. The value returned is the one the data
    holds, not the one the question typed: matching is case-insensitive so
    `night owl` finds `Night Owl`, and execution then uses the stored
    spelling.

    Without a lookup this returns the candidate unchanged, which is the
    behaviour every caller had before: the value reaches SQL, matches
    nothing if it is wrong, and the empty-population guard declines the run.
    Safe, but it never says the value was the problem.

    The values are read server-side, from the session, and never travel to
    the browser. `infer_schema` deliberately emits NULL bounds for
    non-numeric columns, so a schema payload carries no cell values; adding
    them there to make this easier would have widened that boundary.
    """
    if lookup is None:
        return candidate, None
    try:
        values = lookup(column)
    except Exception:  # a profiling failure must not become a wrong answer
        values = None
    if values is None:
        return candidate, None

    folded = candidate.casefold()
    exact = [value for value in values if value.casefold() == folded]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        # Two stored spellings differing only in case. Picking one would be
        # a coin toss that changes which rows are counted.
        return None, (
            f"{column.replace('_', ' ')} has more than one value spelled like "
            f"{candidate!r}; the question cannot say which was meant"
        )

    prefixed = [value for value in values if value.casefold().startswith(folded)]
    if len(prefixed) > 1:
        shown = ", ".join(repr(value) for value in sorted(prefixed)[:3])
        return None, (
            f"{candidate!r} begins more than one value of "
            f"{column.replace('_', ' ')} ({shown}); name the whole value"
        )
    if len(prefixed) == 1:
        # A unique prefix is still not what the reader wrote. Completing it
        # would answer a question they did not ask, and the whole value is
        # one word away.
        return None, (
            f"{column.replace('_', ' ')} has no value {candidate!r}; did you mean {prefixed[0]!r}?"
        )
    return None, (
        f"{column.replace('_', ' ')} has no value {candidate!r}, so the filter was not applied"
    )


def parse_filters(
    question: str, schema: dict[str, Any], *, value_lookup: ValueLookup | None = None
) -> FilterResolution:
    """Every row restriction the question states, or a refusal.

    `value_lookup` lets a caller holding the session settle a category
    value against the column's own values. Without it the parser behaves
    exactly as before.
    """
    columns = _numeric_columns(schema)
    text = question.strip()

    # Periods belong to the date layer. Blanking them first stops "in
    # 1998" and "Q2 2025" being read as numeric bounds, which would refuse
    # questions the engine answers correctly today.
    scannable = _QUARTER.sub(" ", _YEARLIKE.sub(" ", text))

    filters: list[Filter] = []
    consumed: list[tuple[int, int]] = []

    # Ranges first: "aged 30 to 40" is one restriction, not two loose
    # numbers, and reading it as two comparisons would lose the pairing
    # that makes a reversed range detectable.
    for match in _RANGE.finditer(scannable):
        lo, hi = _number(match.group("lo")), _number(match.group("hi"))
        if lo is None or hi is None:
            return FilterResolution(
                constraint_detected=True,
                refusal=(f"the range {match.group(0).strip()!r} could not be read as two numbers"),
            )
        before = scannable[max(0, match.start() - 60) : match.start()]
        column, ambiguous = _resolve_column(before, columns)
        if ambiguous:
            return FilterResolution(
                constraint_detected=True,
                refusal=(
                    f"the range {match.group(0).strip()!r} could refer to more than one "
                    "column of this table, so it was not applied"
                ),
            )
        if column is None:
            return FilterResolution(
                constraint_detected=True,
                refusal=(
                    f"the question restricts to {match.group(0).strip()!r} but does not say "
                    "which column that applies to; name the column, for example "
                    f"'{next(iter(columns), 'a numeric column')} between "
                    f"{_plain(lo)} and {_plain(hi)}'"
                ),
            )
        if lo > hi:
            return FilterResolution(
                constraint_detected=True,
                refusal=(
                    f"the range for {column.replace('_', ' ')} runs from {_plain(lo)} "
                    f"down to {_plain(hi)}, which selects nothing"
                ),
            )
        # Inclusive unless the wording says otherwise. "30 to 40" includes
        # both 30 and 40 in every ordinary reading.
        exclusive = re.search(r"\b(exclusive|excluding|strictly)\b", scannable, re.IGNORECASE)
        lo_op, hi_op = (">", "<") if exclusive else (">=", "<=")
        filters.append(RowFilter(column, lo_op, lo, match.group(0).strip()))
        filters.append(RowFilter(column, hi_op, hi, match.group(0).strip()))
        consumed.append((match.start(), match.end()))

    # Then written comparisons, skipping any span a range already claimed.
    for match in _COMPARISON.finditer(scannable):
        if any(s <= match.start() < e for s, e in consumed):
            continue
        value = _number(match.group("val"))
        if value is None:
            continue
        # Symbols first, then words. `_normalise` strips punctuation, so
        # looking up only the normalised form turned ">=" into "" and
        # dropped every symbolic comparison silently.
        raw = " ".join(match.group("op").lower().split())
        operator = _WORD_OPS.get(raw) or _WORD_OPS.get(_normalise(match.group("op")))
        if operator is None:
            continue
        column, ambiguous = _resolve_column(match.group("col"), columns)
        if ambiguous:
            return FilterResolution(
                constraint_detected=True,
                refusal=(
                    f"{match.group('col').strip()!r} could refer to more than one column "
                    "of this table, so the filter was not applied"
                ),
            )
        if column is None:
            continue
        filters.append(RowFilter(column, operator, value, match.group(0).strip()))
        consumed.append((match.start(), match.end()))

    every = _all_columns(schema)

    # Exact matches on a non-numeric column: "where status is active".
    for match in _EQUALITY.finditer(scannable):
        if any(s <= match.start() < e for s, e in consumed):
            continue
        column, ambiguous = _resolve_column(match.group("col"), every)
        if ambiguous:
            return FilterResolution(
                constraint_detected=True,
                refusal=(
                    f"{match.group('col').strip()!r} could refer to more than one column "
                    "of this table, so the filter was not applied"
                ),
            )
        if column is None:
            # A restriction was stated and could not be bound to a column.
            #
            # This used to `continue`, so the clause vanished and the
            # question was answered over every row -- "average sleep where
            # mood is good" returned the average for everyone, confidently,
            # on a table with no mood column. That is not a slightly worse
            # answer, it is an answer to a different question, and it is
            # the exact failure the sibling `ambiguous` branch above
            # refuses. This module's own policy is to err toward detecting:
            # a false detection costs a refusal with a reason, a missed one
            # costs a wrong answer presented as the right one.
            return FilterResolution(
                constraint_detected=True,
                refusal=(
                    f"the question restricts rows by {match.group('col').strip()!r}, "
                    "which is not a column of this table; name a column that is"
                ),
            )
        if _base_type(every.get(column)) in _NUMERIC_TYPES:
            # A number written as a word against a numeric column is a
            # comparison, not a category, and `_COMPARISON` owns it.
            continue
        text_value = (match.group("qval") or match.group("val") or "").strip()
        if not _SAFE_VALUE.match(text_value):
            return FilterResolution(
                constraint_detected=True,
                refusal=(
                    f"the value {text_value!r} contains characters this engine will not put "
                    "into a query; quote a plain value"
                ),
            )
        bound, value_refusal = bind_category_value(column, text_value, value_lookup)
        if value_refusal:
            return FilterResolution(constraint_detected=True, refusal=value_refusal)
        assert bound is not None
        filters.append(CategoryFilter(column, bound, source_text=match.group(0).strip()))
        consumed.append((match.start(), match.end()))

    # Presence and absence.
    for match in _NULLITY.finditer(scannable):
        if any(s <= match.start() < e for s, e in consumed):
            continue
        column, ambiguous = _resolve_column(match.group("col"), every)
        if ambiguous or column is None:
            continue
        filters.append(
            NullFilter(column, negated=bool(match.group("neg")), source_text=match.group(0).strip())
        )
        consumed.append((match.start(), match.end()))

    # Disjunction is not supported, and must not be read as conjunction:
    # "region Cairo or Delta" restricted to Cairo alone would answer a
    # narrower question than the one asked.
    if filters and _DISJUNCTION.search(scannable):
        spans = [scannable[s:e] for s, e in consumed]
        if not any(_DISJUNCTION.search(span) for span in spans):
            return FilterResolution(
                constraint_detected=True,
                refusal=(
                    "this engine combines filters with 'and' only; rewrite the question "
                    "without 'or', or ask for each case separately"
                ),
            )

    if len(filters) > MAX_FILTERS:
        return FilterResolution(
            constraint_detected=True,
            refusal=(
                f"the question states more than {MAX_FILTERS} restrictions, which is "
                "more than this engine composes in one query"
            ),
        )

    contradiction = _contradiction(filters)
    if contradiction is not None:
        return FilterResolution(constraint_detected=True, refusal=contradiction)

    # A categorical clause that nothing above consumed. Erring toward
    # detecting is this module's policy, and the cost of the two mistakes is
    # not symmetric: a false refusal is an inconvenience with a reason
    # attached, a missed restriction is a confident answer to a narrower
    # question than the one that was asked.
    for match in _STATED_CATEGORY.finditer(scannable):
        # Overlap across the whole clause, not just its first character.
        # A range records only the span of its numbers, so `where age is
        # between 30 and 40` leaves `where age is` unconsumed and would
        # otherwise be reported as unreadable after being read correctly.
        window_end = min(len(scannable), match.end() + 60)
        if any(start < window_end and end > match.start() for start, end in consumed):
            continue
        clause = scannable[match.start() : min(len(scannable), match.end() + 40)].strip()
        return FilterResolution(
            constraint_detected=True,
            refusal=(
                f"the question restricts rows with {clause!r}, and the value could "
                "not be read; quote it, or name a value the column has"
            ),
        )

    detected = bool(filters) or _restricts(scannable, consumed)
    return FilterResolution(tuple(_deduplicate(filters)), constraint_detected=detected)


def _deduplicate(filters: list[Filter]) -> list[Filter]:
    """The same restriction said twice is one restriction."""
    seen: set[tuple[Any, ...]] = set()
    out: list[Any] = []
    for f in filters:
        key = (f.column, f.operator, getattr(f, "value", None))
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _contradiction(filters: list[Filter]) -> str | None:
    """A pair that can never both hold selects nothing, and is far more
    likely a misreading than a request for an empty answer."""
    numeric = [f for f in filters if isinstance(f, RowFilter)]
    for column in {f.column for f in numeric}:
        lower = [f.value for f in numeric if f.column == column and f.operator in (">=", ">")]
        upper = [f.value for f in numeric if f.column == column and f.operator in ("<=", "<")]
        if lower and upper and max(lower) > min(upper):
            pretty = column.replace("_", " ")
            return (
                f"the restrictions on {pretty} cannot both hold "
                f"(at least {_plain(max(lower))} and at most {_plain(min(upper))})"
            )
    return None


def _restricts(text: str, consumed: list[tuple[int, int]]) -> bool:
    """Whether wording restricts the population beyond what was resolved.

    A number left over next to a restrictive word, after ranges and
    comparisons have taken theirs. This is what makes an unparsed
    restriction refuse rather than widen, so it errs toward detecting: a
    false detection costs a refusal with a reason, a missed one costs a
    wrong answer presented as the right one.
    """
    for match in _NUMBER.finditer(text):
        if any(s <= match.start() < e for s, e in consumed):
            continue
        window = text[max(0, match.start() - 40) : match.end() + 20]
        if _RESTRICTIVE.search(window):
            return True
    return False


def filters_from_plan(specs: list[dict[str, Any]], schema: dict[str, Any]) -> FilterResolution:
    """Validate a model's filter plan against the local schema.

    A cloud planner is useful for language, not authority.  It returns exact
    column identifiers and string values; this function independently checks
    both before constructing the same immutable filter objects the rule parser
    produces.  No provider-supplied SQL or operator outside the closed set can
    reach the compiler.
    """
    if len(specs) > MAX_FILTERS:
        return FilterResolution(
            constraint_detected=True,
            refusal=f"the plan contains more than {MAX_FILTERS} row restrictions",
        )
    columns = _all_columns(schema)
    out: list[Filter] = []
    for spec in specs:
        column = str(spec.get("column", ""))
        operator = str(spec.get("operator", ""))
        source_text = str(spec.get("source_text", "")).strip()
        raw = str(spec.get("value", "")).strip()
        if column not in columns:
            return FilterResolution(
                constraint_detected=True,
                refusal=f"the planned filter names a column this table does not have: {column!r}",
            )
        if operator in {"IS NULL", "IS NOT NULL"}:
            if raw:
                return FilterResolution(
                    constraint_detected=True,
                    refusal=f"{operator} on {column!r} must not carry a value",
                )
            out.append(
                NullFilter(column, negated=operator == "IS NOT NULL", source_text=source_text)
            )
            continue

        numeric = _base_type(columns[column]) in _NUMERIC_TYPES
        if numeric:
            if operator not in {"=", ">", ">=", "<", "<="}:
                return FilterResolution(
                    constraint_detected=True,
                    refusal=f"operator {operator!r} is not supported for numeric column {column!r}",
                )
            value = _number(raw)
            if value is None:
                return FilterResolution(
                    constraint_detected=True,
                    refusal=f"the planned value for {column!r} is not a finite number",
                )
            out.append(RowFilter(column, operator, value, source_text=source_text))
            continue

        if operator not in {"=", "!="}:
            return FilterResolution(
                constraint_detected=True,
                refusal=f"operator {operator!r} is not supported for text column {column!r}",
            )
        if not _SAFE_VALUE.fullmatch(raw):
            return FilterResolution(
                constraint_detected=True,
                refusal=f"the planned category for {column!r} contains unsafe characters",
            )
        out.append(CategoryFilter(column, raw, negated=operator == "!=", source_text=source_text))

    contradiction = _contradiction(out)
    if contradiction is not None:
        return FilterResolution(constraint_detected=True, refusal=contradiction)
    return FilterResolution(tuple(_deduplicate(out)), constraint_detected=bool(out))


def where_clause(filters: tuple[Filter, ...], quote: Any) -> str:
    """The SQL these filters become.

    The column goes through the caller's identifier guard and the value is
    rendered from a `Decimal` that has already been parsed as finite, so no
    text from the question reaches the statement.
    """
    if not filters:
        return ""
    parts: list[str] = []
    for f in filters:
        if isinstance(f, NullFilter):
            parts.append(f"{quote(f.column)} {f.operator}")
        elif isinstance(f, CategoryFilter):
            # Already restricted to `_SAFE_VALUE`; the doubling is belt and
            # braces for the one character that could still matter.
            literal = f.value.replace("'", "''")
            parts.append(f"{quote(f.column)} {f.operator} '{literal}'")
        else:
            parts.append(f"{quote(f.column)} {f.operator} {_plain(f.value)}")
    # Conjunction only. `_DISJUNCTION` refuses anything that asked for OR,
    # so joining with AND here cannot silently narrow a question.
    return " AND ".join(parts)
