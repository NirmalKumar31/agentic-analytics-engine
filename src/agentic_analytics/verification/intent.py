"""Does a finding answer the question the engine was asked?

For an uploaded table the engine already knows the answer to that, and it
knew it before the model was consulted. `resolve_question` decomposes the
question into an operation, a measure, a dimension and a period, and
refuses when it cannot. That mapping is a statement of intent, and a
finding either speaks to it or does not.

The first paid run is why this exists. Asked "what is the total net
value?", the model proposed three claims and was asked, per claim,
whether it answered the question. It replied `answers_question: true` for
a finding reporting the column's type and null rate, while its own
free-text reason said the claim "does not report the column's total". The
plumbing was fine; the judgement was not. A gate whose only input is the
model's opinion of its own output is not a gate.

So the model keeps its veto and loses its power to grant. A finding
publishes when the model says it answers *and* the resolved intent agrees
it does. Where no mapping is available -- the governed warehouse, an
ambiguous question. This abstains rather than guessing, and the existing
checks decide alone.

Profiling findings are not rejected in general. They are the right answer
to "what is in this file"; they are the wrong answer to "what is the
total", and the mapping is what tells the two apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Words that describe a column rather than measure it. A claim built from
#: these reports the shape of the data, which answers a question about the
#: shape of the data and no other kind.
_PROFILE_VOCABULARY = re.compile(
    r"\b(typed|type|data.type|null|nulls|null.rate|non.null|distinct|"
    r"cardinality|column|columns|schema|varchar|double|bigint|integer|"
    r"boolean|timestamp|missing|populated)\b",
    re.IGNORECASE,
)

#: The words that report each kind of aggregate, keyed by the operation
#: the mapping resolved. Matching the *specific* aggregate matters: a mean
#: is an aggregate, and it is not an answer to a question about a total.
_AGGREGATE_WORDS: dict[str, re.Pattern[str]] = {
    "sum": re.compile(r"\b(total|totals|totalled|sum|sums|summed|combined|overall)\b", re.I),
    "average": re.compile(r"\b(average|averages|averaged|mean|means|median)\b", re.I),
    "count": re.compile(r"\b(count|counts|counted|number of|how many|rows)\b", re.I),
    "ranking": re.compile(
        r"\b(highest|lowest|top|bottom|most|least|ranked|ranking|largest|smallest)\b", re.I
    ),
    "trend": re.compile(r"\b(over time|trend|by month|monthly|rose|fell|grew|declined)\b", re.I),
}

#: Any aggregate at all. Used to tell "reports the wrong aggregate" from
#: "reports no aggregate", which are different failures.
_ANY_AGGREGATE = re.compile(
    r"\b(total|sum|summed|average|mean|median|count|counted|overall|combined|"
    r"aggregate|highest|lowest|top|ranked)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IntentVerdict:
    """Whether a claim speaks to the resolved intent.

    `applicable` is false when there is no mapping to judge against, and
    the caller must then not treat `answers` as a decision.
    """

    applicable: bool
    answers: bool = True
    reason: str = ""


def _words(text: str) -> str:
    """The claim with identifiers flattened into words.

    `total_gross_amount` contains "total", but `\btotal\b` does not match
    it: an underscore is a word character, so there is no boundary after
    "total". Matching the raw text rejected every breakdown finding in the
    corpus, because the engine names its own output columns that way.
    """
    return text.replace("_", " ")


def _mentions(text: str, column: str) -> bool:
    """Whether a claim names a column, in either spelling.

    Columns are written `net_value` and prose says "net value", so both
    are checked.
    """
    lowered = text.lower()
    return column.lower() in lowered or column.replace("_", " ").lower() in lowered


def check_intent(text: str, mapping: Any) -> IntentVerdict:
    """Judge one claim against a resolved question mapping."""
    if mapping is None or not getattr(mapping, "confident", False):
        # Nothing reliable to judge against. Fail open here so that the
        # evidence and relevance checks decide; the mapping's own refusal
        # is what stops an ambiguous question being answered at all.
        return IntentVerdict(applicable=False)

    operation = getattr(mapping, "operation", "")
    if operation == "profile":
        # The question asked what the table contains, so a claim about the
        # table's shape is the answer.
        return IntentVerdict(applicable=False)

    measure = getattr(mapping, "measure", None)
    wants_aggregate = operation in ("sum", "average", "count", "ranking", "trend")
    if not wants_aggregate:
        return IntentVerdict(applicable=False)

    describes_shape = bool(_PROFILE_VOCABULARY.search(_words(text)))
    wanted = _AGGREGATE_WORDS.get(operation)
    reports_wanted = bool(wanted.search(_words(text))) if wanted else False
    reports_any = bool(_ANY_AGGREGATE.search(_words(text)))

    # A claim about the column's type, null rate or cardinality, offered
    # against a question that asked for a figure derived from it. True,
    # checkable, and not an answer.
    if describes_shape and not reports_wanted:
        return IntentVerdict(
            applicable=True,
            answers=False,
            reason=(
                "The question asks for "
                f"{_describe(operation, measure)}, while the claim describes the "
                "column rather than reporting that figure."
            ),
        )

    # Reports an aggregate, but not the one that was asked for. A mean is
    # a perfectly good number and it is not a total.
    if reports_any and not reports_wanted:
        return IntentVerdict(
            applicable=True,
            answers=False,
            reason=(
                f"The question asks for {_describe(operation, measure)}, while the "
                "claim reports a different statistic."
            ),
        )

    # Named the wrong measure. "Total revenue" is not answered by the
    # total of a different column, however exact.
    if measure and reports_wanted and not _mentions(text, measure):
        other = _other_measure_named(text, mapping)
        if other:
            return IntentVerdict(
                applicable=True,
                answers=False,
                reason=(
                    f"The question asks about {measure.replace('_', ' ')}, "
                    f"while the claim reports {other.replace('_', ' ')}."
                ),
            )

    return IntentVerdict(applicable=True, answers=True)


def _describe(operation: str, measure: str | None) -> str:
    what = (measure or "the rows").replace("_", " ")
    return {
        "sum": f"the total of {what}",
        "average": f"the average of {what}",
        "count": "a count of rows",
        "ranking": f"a ranking by {what}",
        "trend": f"{what} over time",
    }.get(operation, f"a figure derived from {what}")


def _other_measure_named(text: str, mapping: Any) -> str | None:
    """A different measure the claim reports instead.

    Only columns the mapping knows about, so a word that merely resembles
    a column name is not treated as one.
    """
    for column in getattr(mapping, "named_columns", None) or []:
        if column != getattr(mapping, "measure", None) and _mentions(text, column):
            return str(column)
    return None
