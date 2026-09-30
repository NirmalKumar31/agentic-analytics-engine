"""Why findings were withheld, said accurately.

The report used to carry one sentence for every rejection: *"N proposed
finding(s) were withheld because the cited results did not support them."*
That is true of some rejections and false of most. A finding withheld as
irrelevant had perfectly good evidence and simply did not answer the
question. One withheld because the verifier was unavailable was never
judged at all. Describing both as unsupported tells a reader the engine
found a data problem when it found a relevance problem, or no problem.

So rejections are grouped by the rule that decided them, and each group
gets a sentence that says what actually happened. The rule identifiers are
the same stable strings the UI and the benchmark key off; this module owns
only the prose.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

#: Rule identifier to the sentence a reader should see, in the order the
#: groups are reported. Ordered by how much it matters that the reader
#: notices: a data problem first, an unverifiable claim last.
_REASONS: tuple[tuple[str, str], ...] = (
    (
        "numeric_mismatch",
        "the numbers they stated could not be derived from the results they cited",
    ),
    (
        "no_evidence",
        "they cited no result to check",
    ),
    (
        "missing_result",
        "the results they cited were not available",
    ),
    (
        "causal_from_observational",
        "they asserted a cause from observational data, which this engine "
        "does not accept as established",
    ),
    (
        "significance_without_test",
        "they called a difference significant without a statistical test behind it",
    ),
    (
        "irrelevant_to_question",
        "their evidence held but they did not answer the question that was asked",
    ),
    (
        "ambiguous_mapping",
        "the question could not be mapped to this dataset's columns without guessing",
    ),
    (
        "missing_required_filter",
        "the executed result did not apply every row restriction in the question",
    ),
    (
        "missing_required_dimension",
        "the executed result omitted a requested breakdown",
    ),
    ("wrong_measure", "the executed result measured a different field from the one requested"),
    ("wrong_operation", "the executed result used a different calculation from the one requested"),
    ("missing_required_period", "the executed result did not apply the requested time period"),
    ("wrong_period", "the executed result covered a different time period"),
    ("wrong_output_shape", "the executed result did not have the requested answer shape"),
    (
        "wrong_sort_order",
        "the executed result ranked the rows in the opposite direction from the one asked for",
    ),
    (
        "critic",
        "the verifier judged the cited results not to support them",
    ),
    (
        "critic_unavailable",
        "the verifier could not be reached, so they were never judged",
    ),
    (
        "verification_budget_exhausted",
        "the run reached its budget before they could be verified",
    ),
    (
        "partial_metric_answer",
        "the engine published the complete breakdown instead, so a summary "
        "naming only some of the groups was not needed",
    ),
    (
        "restated_engine_answer",
        "the engine published its own answer, computed from the executed "
        "result, so a restatement of the same figure was not needed",
    ),
    (
        "duplicate_finding",
        "they repeated a finding already published",
    ),
)

_ORDER = {rule: index for index, (rule, _) in enumerate(_REASONS)}
_TEXT = dict(_REASONS)

#: What to say about a rule this module has not been taught. Deliberately
#: not "unsupported": a new rule being described as an evidence failure is
#: exactly the bug this replaces, so an unknown one says only that it was
#: withheld and names itself.
_UNKNOWN = "they were withheld by the {rule} check"


def _plural(count: int) -> tuple[str, str]:
    return ("1 proposed finding", "was") if count == 1 else (f"{count} proposed findings", "were")


def rejection_limitations(rules: Iterable[str]) -> list[str]:
    """One sentence per reason, for the rules that actually fired.

    Grouped rather than listed per finding: a report withholding eleven
    findings for two reasons should say two things, not eleven.
    """
    counted = Counter(rule or "unknown" for rule in rules)
    if not counted:
        return []

    out: list[str] = []
    for rule, count in sorted(counted.items(), key=lambda kv: (_ORDER.get(kv[0], 99), kv[0])):
        subject, verb = _plural(count)
        because = _TEXT.get(rule) or _UNKNOWN.format(rule=rule)
        out.append(f"{subject} {verb} withheld because {because}.")
    return out


def known_rules() -> frozenset[str]:
    """Every rule this module can describe. Asserted against the engine's
    own rules in a test, so adding one without prose fails the build."""
    return frozenset(_TEXT)
