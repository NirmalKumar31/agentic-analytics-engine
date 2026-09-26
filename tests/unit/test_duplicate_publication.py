"""Publishing the same sentence twice is a product bug, not a safety one.

Stage 1 published "The product family 'Home' has the highest total net value
of 40189.25." twice, from two tasks that had found it independently. Both
were true, both passed every gate, and printing both still inflates the
finding count, the report length and the apparent breadth of the analysis.

The collapse is exact-text only. No similarity, no stemming, no synonyms:
anything that could change what a sentence asserts has to keep the two
apart, because a deduplicator that merges "rose by 5%" with "rose by 6%" is
a verification failure wearing a tidiness costume.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentic_analytics.agents.schemas import PublishedFinding
from agentic_analytics.verification.claims import (
    collapse_exact_duplicates,
    normalise_claim_text,
)

HOME = "The product family 'Home' has the highest total net value of 40189.25."


# ------------------------------------------------------- what collapses
@pytest.mark.parametrize(
    "variant",
    [
        HOME,
        HOME.rstrip("."),
        "  The product family 'Home' has the highest total net value of 40189.25.  ",
        "The product family 'Home' has the highest\ttotal net value of 40189.25.",
        "the product family 'home' has the highest total net value of 40189.25",
    ],
)
def test_presentation_differences_collapse(variant: str) -> None:
    assert normalise_claim_text(variant) == normalise_claim_text(HOME)


# --------------------------------------------------- what must not collapse
@pytest.mark.parametrize(
    ("a", "b", "why"),
    [
        (
            "Revenue rose by 5%.",
            "Revenue rose by 6%.",
            "a different number is a different claim",
        ),
        (
            "Revenue rose by 5%.",
            "Revenue rose by 5.0%.",
            "a differently written number is still a number",
        ),
        (
            "Revenue rose in Q3.",
            "Revenue did not rise in Q3.",
            "negation reverses the claim",
        ),
        (
            "Revenue rose in Q3.",
            "Revenue may have risen in Q3.",
            "modality changes what is asserted",
        ),
        (
            "Revenue rose in Q3.",
            "Revenue rose in Q4.",
            "time scope changes what is asserted",
        ),
        (
            "The North region led on revenue.",
            "The South region led on revenue.",
            "entity names must survive",
        ),
        (
            "Spend was 100 USD.",
            "Spend was 100 GBP.",
            "units must survive",
        ),
        (
            "Home has the highest total net value.",
            "The product family 'Home' has the highest total net value.",
            "different wording of one idea is not an exact duplicate",
        ),
    ],
)
def test_meaning_bearing_differences_are_kept_apart(a: str, b: str, why: str) -> None:
    assert normalise_claim_text(a) != normalise_claim_text(b), why


def test_normalisation_is_stable_and_idempotent() -> None:
    once = normalise_claim_text(HOME)
    assert normalise_claim_text(once) == once


# ------------------------------------------- through the publication path
def _finding(text: str, finding_id: str, results: list[str] | None = None) -> PublishedFinding:
    return PublishedFinding(
        finding_id=finding_id,
        text=text,
        kind="calculated_fact",
        task_id="task_1",
        result_ids=results or ["res_1"],
        evidence_cells=[],
        metric_ids=[],
        verification_status="supported",
        verifier_reason="ok",
        verifier_rule="critic",
    )


def _collapse(findings: list[PublishedFinding]) -> tuple[list[PublishedFinding], list[Any]]:
    return collapse_exact_duplicates(
        findings,
        text_of=lambda f: f.text,
        id_of=lambda f: f.finding_id,
        task_of=lambda f: f.task_id or "",
        results_of=lambda f: list(f.result_ids),
    )


def test_two_identical_supported_claims_publish_once() -> None:
    """The exact Stage-1 case: same sentence, different evidence."""
    kept, duplicates = _collapse(
        [
            _finding(HOME, "fin_a", ["res_75d3fae393be"]),
            _finding(HOME, "fin_b", ["res_62948398859e", "res_2e3581ea004c"]),
        ]
    )
    assert [f.finding_id for f in kept] == ["fin_a"], "the first verified one survives"
    assert len(duplicates) == 1
    assert duplicates[0].finding_id == "fin_b"
    assert duplicates[0].duplicate_of == "fin_a"
    # Kept in the audit trail, with its own provenance intact.
    assert duplicates[0].result_ids == ["res_62948398859e", "res_2e3581ea004c"]


def test_the_survivor_is_left_exactly_as_it_was_verified() -> None:
    """Merging the duplicate's evidence would cite what was never checked."""
    first = _finding(HOME, "fin_a", ["res_1"])
    kept, _ = _collapse([first, _finding(HOME, "fin_b", ["res_2", "res_3"])])
    assert kept[0] is first
    assert kept[0].result_ids == ["res_1"], "provenance was not unioned"


def test_a_different_number_keeps_both() -> None:
    kept, duplicates = _collapse(
        [
            _finding("Revenue rose by 5%.", "fin_a"),
            _finding("Revenue rose by 6%.", "fin_b"),
        ]
    )
    assert len(kept) == 2
    assert duplicates == []


def test_a_negated_claim_keeps_both() -> None:
    kept, _ = _collapse(
        [
            _finding("Revenue rose in Q3.", "fin_a"),
            _finding("Revenue did not rise in Q3.", "fin_b"),
        ]
    )
    assert len(kept) == 2


def test_similar_but_differently_worded_claims_keep_both() -> None:
    """No semantic similarity: only identical sentences collapse."""
    kept, _ = _collapse(
        [
            _finding("Home has the highest total net value.", "fin_a"),
            _finding("The product family 'Home' has the highest total net value.", "fin_b"),
        ]
    )
    assert len(kept) == 2


def test_three_identical_claims_leave_one() -> None:
    kept, duplicates = _collapse(
        [_finding(HOME, "fin_a"), _finding(HOME, "fin_b"), _finding(HOME, "fin_c")]
    )
    assert [f.finding_id for f in kept] == ["fin_a"]
    assert [d.finding_id for d in duplicates] == ["fin_b", "fin_c"]
    assert all(d.duplicate_of == "fin_a" for d in duplicates)


def test_an_unsupported_duplicate_cannot_influence_the_survivor() -> None:
    """Only supported findings reach the collapse at all.

    The graph builds its list from verdicts that came back `supported`, so
    an unsupported claim with the same text is never a candidate to survive
    and cannot displace or suppress one that was verified.
    """
    import inspect

    from agentic_analytics.graph.build import build_graph

    source = inspect.getsource(build_graph)
    assert 'if verdict.status == "supported":' in source
    assert "supported.append" in source
    assert source.index("supported.append") < source.index("collapse_exact_duplicates")


def test_the_counts_stay_separable() -> None:
    """Candidate, supported and published-unique are three different numbers."""
    findings = [_finding(HOME, "fin_a"), _finding(HOME, "fin_b"), _finding("Other.", "fin_c")]
    kept, duplicates = _collapse(findings)
    assert len(findings) == 3, "supported verdicts"
    assert len(kept) == 2, "unique published"
    assert len(duplicates) == 1
    assert len(kept) + len(duplicates) == len(findings)


def test_a_suppressed_duplicate_serialises_for_telemetry() -> None:
    _, duplicates = _collapse([_finding(HOME, "fin_a"), _finding(HOME, "fin_b")])
    payload = duplicates[0].as_dict()
    assert set(payload) == {"finding_id", "duplicate_of", "task_id", "result_ids"}


def test_a_suppressed_duplicate_is_not_counted_as_a_rejection() -> None:
    """It passed every gate. Calling it "withheld" would misreport safety."""
    import inspect

    from agentic_analytics.graph.build import build_graph

    source = inspect.getsource(build_graph)
    block = source[
        source.index("collapse_exact_duplicates") : source.index("limitations: list[str]")
    ]
    assert "rejected.append" not in block


def test_the_survivors_provenance_is_never_merged_anywhere() -> None:
    import inspect

    from agentic_analytics.graph.build import build_graph

    source = inspect.getsource(build_graph)
    assert "result_ids.extend" not in source
    assert "evidence_cells.extend" not in source
