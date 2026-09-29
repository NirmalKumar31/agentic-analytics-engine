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
    r"(?P<col>[A-Za-z_][\w ]{0,40}?)\s*"
    r"(?P<op>>=|<=|>|<|==|=|\bis\s+at\s+least\b|\bis\s+at\s+most\b|"
    r"\bat\s+least\b|\bat\s+most\b|\bover\b|\bunder\b|\babove\b|\bbelow\b|"
    r"\bgreater\s+than\b|\bless\s+than\b|\bmore\s+than\b|\bfewer\s+than\b)\s*"
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
}

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


@dataclass(frozen=True)
class FilterResolution:
    """What the wording asked for, and whether it could be honoured."""

    filters: tuple[RowFilter, ...] = ()
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
        base = declared.split("(")[0].strip().upper()
        if name and base in _NUMERIC_TYPES:
            out[name] = declared
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
        normalised = _normalise(column)
        best = -1
        for index, token in enumerate(tokens):
            if (
                token == normalised
                or (index > 0 and f"{tokens[index - 1]} {token}" == normalised)
                or _stem_match(normalised, [token])
            ):
                best = max(best, index)
        if best >= 0:
            positions[column] = best
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


def parse_filters(question: str, schema: dict[str, Any]) -> FilterResolution:
    """Every row restriction the question states, or a refusal."""
    columns = _numeric_columns(schema)
    text = question.strip()

    # Periods belong to the date layer. Blanking them first stops "in
    # 1998" and "Q2 2025" being read as numeric bounds, which would refuse
    # questions the engine answers correctly today.
    scannable = _QUARTER.sub(" ", _YEARLIKE.sub(" ", text))

    filters: list[RowFilter] = []
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
        operator = _WORD_OPS.get(_normalise(match.group("op")))
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

    detected = bool(filters) or _restricts(scannable, consumed)
    return FilterResolution(tuple(filters), constraint_detected=detected)


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


def where_clause(filters: tuple[RowFilter, ...], quote: Any) -> str:
    """The SQL these filters become.

    The column goes through the caller's identifier guard and the value is
    rendered from a `Decimal` that has already been parsed as finite, so no
    text from the question reaches the statement.
    """
    if not filters:
        return ""
    parts = [f"{quote(f.column)} {f.operator} {_plain(f.value)}" for f in filters]
    return " AND ".join(parts)
