"""Deterministic claim-shape checks.

These catch the three failure modes that do not need a model to spot:

* a causal verb attached to an observational result,
* the word "significant" with no test behind it,
* a claim that cites nothing at all.

Running them before the critic means the critic is only asked the questions a
model is actually good at, and means these rejections are reproducible.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from agentic_analytics.analytics.results import ResultSnapshot

# Verbs and phrases that assert one thing produced another.
_CAUSAL = re.compile(
    r"\b("
    r"caus(?:e|ed|es|ing)"
    r"|because of"
    r"|due to"
    r"|led to|leads to|leading to"
    r"|result(?:ed|s|ing) in"
    r"|responsible for"
    r"|attributable to"
    r"|drove|driven by|drives"
    r"|the reason (?:is|was|for)"
    r"|explains? why"
    r"|as a result of"
    r"|triggered"
    r")\b",
    re.IGNORECASE,
)

# Hedged phrasing that describes an association rather than asserting cause.
_HEDGED = re.compile(
    r"\b("
    r"associated with|correlat|consistent with|suggests?|may |might |appears? to"
    r"|is linked to|co-?occurs?|without adjusting|not establish"
    r")\b",
    re.IGNORECASE,
)

_SIGNIFICANCE = re.compile(
    r"\b(statistically significant|significant(?:ly)?|p\s*[<=>]|p-value)\b",
    re.IGNORECASE,
)


#: Stable identifiers for the deterministic gates, so callers key off these
#: rather than matching on wording written for a person.
CAUSAL_RULE = "causal_from_observational"
SIGNIFICANCE_RULE = "significance_without_test"


@dataclass
class ClaimVerdict:
    """Outcome of the deterministic claim checks."""

    ok: bool
    rule: str = ""
    reason: str = ""


def check_claim(
    text: str, kind: str, results: list[ResultSnapshot], has_result_ids: bool
) -> ClaimVerdict:
    """Apply every deterministic rule. The first failure wins."""
    if not has_result_ids:
        return ClaimVerdict(
            ok=False,
            rule="no_evidence",
            reason="The claim cites no result, so there is nothing to check it against.",
        )
    if not results:
        return ClaimVerdict(
            ok=False,
            rule="missing_result",
            reason="The results this claim cites are not available.",
        )

    has_test = any(r.statistical_result is not None for r in results)
    if _SIGNIFICANCE.search(text) and not has_test:
        return ClaimVerdict(
            ok=False,
            rule=SIGNIFICANCE_RULE,
            reason=(
                "The claim uses the language of statistical significance but no "
                "statistical test was run on the cited result."
            ),
        )

    if _CAUSAL.search(text) and not _HEDGED.search(text):
        return ClaimVerdict(
            ok=False,
            rule=CAUSAL_RULE,
            reason=(
                "The claim asserts causation from observational data. The groups "
                "were not randomly assigned, so the result supports an association "
                "only."
            ),
        )

    return ClaimVerdict(ok=True, rule="passed", reason="No claim-shape rule was violated.")


def is_causal(text: str) -> bool:
    """True when the text asserts cause without hedging."""
    return bool(_CAUSAL.search(text)) and not bool(_HEDGED.search(text))


def normalise_claim_text(text: str) -> str:
    """The key two claims must share to count as the same sentence.

    Deliberately conservative. Whitespace, case and a trailing full stop are
    presentation; everything that could change what the sentence *asserts*
    is left alone -- numbers, units, negation, modality, entity names and
    time scope all survive verbatim, so "rose by 5%" and "rose by 6%",
    "did" and "did not", "may be" and "is", "Q3" and "Q4" are all different
    claims and all stay.

    No similarity, no stemming, no synonyms. Two claims collapse only when
    they are the same sentence written the same way.
    """
    collapsed = " ".join(text.split())
    return collapsed.rstrip(".").casefold()


@dataclass
class SuppressedDuplicate:
    """A verified claim that repeats one already published."""

    finding_id: str
    duplicate_of: str
    task_id: str
    result_ids: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "duplicate_of": self.duplicate_of,
            "task_id": self.task_id,
            "result_ids": list(self.result_ids),
        }


def collapse_exact_duplicates[T](
    items: list[T],
    *,
    text_of: Callable[[T], str],
    id_of: Callable[[T], str],
    task_of: Callable[[T], str],
    results_of: Callable[[T], list[str]],
) -> tuple[list[T], list[SuppressedDuplicate]]:
    """Keep the first of each identical sentence; report the rest.

    The first *verified* occurrence survives, exactly as it was verified.
    Nothing is merged onto it: attaching a later duplicate's result ids to
    the survivor would put evidence behind a sentence that was not checked
    against that evidence, which is a worse citation than the one it
    replaces.
    """
    seen: dict[str, str] = {}
    kept: list[T] = []
    duplicates: list[SuppressedDuplicate] = []
    for item in items:
        key = normalise_claim_text(text_of(item))
        first = seen.get(key)
        if first is not None:
            duplicates.append(
                SuppressedDuplicate(
                    finding_id=id_of(item),
                    duplicate_of=first,
                    task_id=task_of(item),
                    result_ids=results_of(item),
                )
            )
            continue
        seen[key] = id_of(item)
        kept.append(item)
    return kept, duplicates
