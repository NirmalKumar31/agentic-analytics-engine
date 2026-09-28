"""Whether a question is one this engine can answer about this dataset.

A public demo that dispatches a model on anything it is handed has two
problems. The small one is that the visitor waits through a full run to be
told nothing survived verification. The larger one is that each such run
costs real money and consumes a shared quota, so a stream of questions this
tool cannot answer takes the tool away from people it could have.

This check runs before the first model call, and before a concurrency slot
is taken. It is deliberately a *relevance* test and not a safety filter:
the question is either about the data in front of it or it is not, and
saying so plainly is more honest than a classifier pretending to judge
intent.

It fails open by design. A question that references anything in the
dataset, or that is phrased analytically at all, is admitted -- the
verification pipeline is what decides whether an answer holds up, and this
is only here to catch the case where there is obviously nothing to
analyse. Rejecting a real question would be far worse than admitting a
hopeless one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Words that indicate someone is asking about data rather than asking for
#: prose. Deliberately broad: a question containing any of these is
#: admitted, because the cost of turning away a real analytical question is
#: much higher than the cost of running a doomed one.
ANALYTICAL_TERMS = frozenset(
    [
        "anomaly",
        "average",
        "avg",
        "basket",
        "bottom",
        "breakdown",
        "by",
        "change",
        "changed",
        "churn",
        "cohort",
        "column",
        "columns",
        "compare",
        "comparison",
        "contribution",
        "conversion",
        "correlation",
        "cost",
        "costs",
        "count",
        "customers",
        "data",
        "dataset",
        "decline",
        "decrease",
        "describe",
        "distribution",
        "driver",
        "drivers",
        "drop",
        "fell",
        "best",
        "biggest",
        "earn",
        "earned",
        "fastest",
        "forecast",
        "largest",
        "least",
        "longest",
        "made",
        "most",
        "quickest",
        "slowest",
        "smallest",
        "spent",
        "took",
        "worst",
        "grew",
        "growth",
        "higher",
        "highest",
        "increase",
        "list",
        "lower",
        "lowest",
        "many",
        "margin",
        "maximum",
        "mean",
        "median",
        "metric",
        "minimum",
        "month",
        "much",
        "orders",
        "outlier",
        "overview",
        "per",
        "percent",
        "percentage",
        "period",
        "profile",
        "profit",
        "qoq",
        "quarter",
        "rank",
        "ranked",
        "rate",
        "ratio",
        "retention",
        "returns",
        "revenue",
        "rose",
        "row",
        "rows",
        "sales",
        "schema",
        "seasonality",
        "segment",
        "segments",
        "share",
        "show",
        "significant",
        "spend",
        "spending",
        "sum",
        "summarise",
        "summarize",
        "summary",
        "table",
        "tables",
        "top",
        "total",
        "trend",
        "trends",
        "units",
        "variance",
        "versus",
        "volume",
        "vs",
        "week",
        "year",
        "yoy",
    ]
)

#: Shapes that are asking this tool to be a different tool. Matched only to
#: explain the refusal better; a question containing one is still admitted
#: if it also references the dataset, because "write a summary of revenue by
#: region" is a legitimate request phrased as an instruction.
_PROSE_REQUEST = re.compile(
    r"\b(write|compose|draft|translate|summari[sz]e this text|tell me a|"
    r"generate a (poem|story|essay|song|joke)|act as|pretend to be|"
    r"ignore (the |all |previous )?instructions?|system prompt)\b",
    re.IGNORECASE,
)

_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    """Question words, tokenised the way the vocabulary is built.

    Underscores are separators here as they are there. Keeping them made
    `kwh_consumed` one token that matched neither `kwh` nor `consumed`, so
    for any dataset with underscored columns the vocabulary contributed
    nothing at all and every question fell through to the analytical-term
    fallback -- which is how questions naming a column exactly were still
    judged as though they had named nothing.
    """
    return {w for w in _WORD.findall(text.lower()) if w}


@dataclass(frozen=True)
class ScopeVerdict:
    """Whether to run, and what to tell the visitor if not."""

    in_scope: bool
    reason: str = ""
    message: str = ""


#: Stable identifiers. The UI maps these; a visitor never sees an internal
#: string, and a caller never matches on wording.
OUT_OF_SCOPE = "question_not_about_dataset"
EMPTY_QUESTION = "question_empty"


def dataset_vocabulary(catalog: dict[str, Any], metrics: list[dict[str, Any]]) -> set[str]:
    """Every word a question could use to name part of this dataset.

    Table names, column names and metric names, split on underscores so that
    "gross margin" matches `gross_margin_pct` the way a person would write
    it. Short fragments are dropped: "id" and "at" match everything and
    would make the check meaningless.
    """
    words: set[str] = set()
    for table in catalog.get("tables") or []:
        if not isinstance(table, dict):
            continue
        _add_identifier(words, str(table.get("name", "")))
        for column in table.get("columns") or []:
            if isinstance(column, dict):
                _add_identifier(words, str(column.get("name", "")))
    for metric in metrics or []:
        if isinstance(metric, dict):
            _add_identifier(words, str(metric.get("name", "")))
    return words


def _add_identifier(words: set[str], identifier: str) -> None:
    for part in identifier.lower().split("_"):
        if len(part) > 2:
            words.add(part)


def check_scope(
    question: str,
    catalog: dict[str, Any],
    metrics: list[dict[str, Any]] | None = None,
) -> ScopeVerdict:
    """Decide whether to spend a run on this question.

    Admitted when the question names anything in the dataset, or uses the
    vocabulary of analysis at all. Refused only when it does neither, which
    in practice means it is not about data.
    """
    text = " ".join(question.split())
    if not text:
        return ScopeVerdict(
            False,
            EMPTY_QUESTION,
            "Ask a question about the dataset to start an analysis.",
        )

    words = _words(text)
    vocabulary = dataset_vocabulary(catalog, metrics or [])

    if words & vocabulary:
        return ScopeVerdict(True)
    if words & ANALYTICAL_TERMS:
        return ScopeVerdict(True)

    # Nothing from the dataset and nothing analytical. Say what this tool
    # does rather than guessing what the visitor wanted.
    hint = "asks for text rather than analysis" if _PROSE_REQUEST.search(text) else ""
    return ScopeVerdict(
        False,
        OUT_OF_SCOPE,
        (
            "This tool answers questions about the dataset that is loaded -- "
            "totals, trends, comparisons and what drove a change. "
            + (f"That question {hint}. " if hint else "")
            + "Try naming a metric, a column or a period."
        ),
    )


def suggestion_in_scope(suggestion: str, vocabulary: set[str]) -> bool:
    """Whether a model-suggested follow-up is about this dataset.

    The report's "next questions" are the one place model-written text
    reaches a visitor. They are already stripped of numbers and causal
    claims; this keeps them on the subject as well, so a run cannot put
    arbitrary text on the page by way of a suggestion.

    Same two-part test as `check_scope`, and for the same reason: demanding
    a dataset word alone drops perfectly good follow-ups that happen to be
    phrased naturally -- "Does the move persist, or does it recover?" names
    no column and is exactly the question a reader would ask next.
    """
    words = _words(suggestion)
    return bool(words & vocabulary or words & ANALYTICAL_TERMS)
