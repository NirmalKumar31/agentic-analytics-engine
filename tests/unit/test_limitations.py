"""Rejections described by what actually happened to them.

The report used to carry one sentence for every rejection: "N proposed
finding(s) were withheld because the cited results did not support them."
That is true of some rejections and false of most, and the false cases are
the misleading ones -- a finding withheld as irrelevant had good evidence
and simply did not answer the question, and one withheld because the
verifier was unreachable was never judged at all. Both were reported as
evidence failures, which sends a reader looking for a data problem the
engine never found.
"""

from __future__ import annotations

import pytest

from agentic_analytics.verification.limitations import known_rules, rejection_limitations


def test_no_rejections_produces_no_sentence() -> None:
    assert rejection_limitations([]) == []


def test_one_reason_produces_one_sentence() -> None:
    out = rejection_limitations(["irrelevant_to_question"] * 3)
    assert len(out) == 1
    assert out[0].startswith("3 proposed findings were withheld")


def test_a_single_rejection_reads_as_singular() -> None:
    out = rejection_limitations(["numeric_mismatch"])
    assert out == [
        "1 proposed finding was withheld because the numbers they stated could not "
        "be derived from the results they cited."
    ]


def test_each_reason_gets_its_own_sentence() -> None:
    """Grouped by reason, not listed per finding: a report withholding
    eleven findings for two reasons should say two things."""
    out = rejection_limitations(
        ["irrelevant_to_question"] * 4 + ["numeric_mismatch"] * 2 + ["critic_unavailable"]
    )
    assert len(out) == 3
    assert sum(1 for line in out if "4 proposed findings" in line) == 1
    assert sum(1 for line in out if "2 proposed findings" in line) == 1
    assert sum(1 for line in out if "1 proposed finding " in line) == 1


@pytest.mark.parametrize(
    ("rule", "must_say", "must_not_say"),
    [
        ("irrelevant_to_question", "did not answer the question", "did not support"),
        ("critic_unavailable", "never judged", "did not support"),
        ("verification_budget_exhausted", "reached its budget", "did not support"),
        ("ambiguous_mapping", "without guessing", "did not support"),
        ("duplicate_finding", "repeated a finding", "did not support"),
        ("no_evidence", "cited no result", "did not answer"),
        ("causal_from_observational", "asserted a cause", "did not answer"),
        ("significance_without_test", "without a statistical test", "did not answer"),
        ("numeric_mismatch", "could not be derived", "did not answer"),
    ],
)
def test_each_reason_says_what_happened_and_not_what_did_not(
    rule: str, must_say: str, must_not_say: str
) -> None:
    """The defect was a wrong explanation, so both halves are asserted:
    the right words present and the misleading ones absent."""
    line = rejection_limitations([rule])[0]
    assert must_say in line, line
    assert must_not_say not in line, line


def test_an_unknown_rule_is_not_called_an_evidence_failure() -> None:
    """A rule added without prose must degrade to naming itself.

    Defaulting to "unsupported" is exactly the bug this module replaces, so
    an unrecognised rule says only that it was withheld.
    """
    line = rejection_limitations(["some_new_gate"])[0]
    assert "some_new_gate" in line
    assert "did not support" not in line
    assert "did not answer" not in line


def test_every_rule_the_engine_can_emit_has_prose() -> None:
    """Adding a gate without a sentence fails here rather than shipping a
    report that describes it wrongly."""
    from agentic_analytics.agents.critic import IRRELEVANT
    from agentic_analytics.verification.claims import CAUSAL_RULE, SIGNIFICANCE_RULE

    emitted = {
        IRRELEVANT,
        CAUSAL_RULE,
        SIGNIFICANCE_RULE,
        "no_evidence",
        "missing_result",
        "numeric_mismatch",
        "critic",
        "critic_unavailable",
        "verification_budget_exhausted",
        "duplicate_finding",
    }
    missing = emitted - known_rules()
    assert not missing, f"these rules would be described wrongly: {sorted(missing)}"
