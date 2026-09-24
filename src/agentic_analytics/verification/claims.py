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
            rule="significance_without_test",
            reason=(
                "The claim uses the language of statistical significance but no "
                "statistical test was run on the cited result."
            ),
        )

    if _CAUSAL.search(text) and not _HEDGED.search(text):
        return ClaimVerdict(
            ok=False,
            rule="causal_from_observational",
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
