"""A claim may be shortened. It may not be made to say something else.

`301,397,792.46` became `301,397` when a 45-group breakdown hit a hard
400-character slice. Numeric verification then rejected the engine's own
complete answer for stating a value not in its results, and a two-group
summary was published in its place. The truncation manufactured the false
claim it was then blamed for.
"""

from __future__ import annotations

import re

import pytest

from agentic_analytics.agents.schemas import (
    MAX_CLAIM_CHARS,
    CandidateFinding,
    _shorten_without_splitting_a_number,
)


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d[\d,]*(?:\.\d+)?", text)


def test_the_production_value_can_never_become_a_different_one() -> None:
    """The exact regression the work order asks for."""
    entries = [f"{index}: 301,397,792.46" for index in range(1, 40)]
    text = "Total Weekly Sales by Store: " + "; ".join(entries) + "."
    assert len(text) > MAX_CLAIM_CHARS

    shortened = _shorten_without_splitting_a_number(text)

    assert len(shortened) <= MAX_CLAIM_CHARS
    assert "301,397," not in shortened.replace("301,397,792.46", "")
    for token in _numbers(shortened):
        assert token in {"301,397,792.46"} | {str(i) for i in range(1, 40)}, token


@pytest.mark.parametrize(
    "value",
    ["301,397,792.46", "1,234.5", "6,231,919,435.55", "0.07", "45"],
)
def test_no_shortening_leaves_a_fragment_of_a_number(value: str) -> None:
    """Every cut position is tried, so none can split a value."""
    for filler in range(MAX_CLAIM_CHARS + 40):
        text = "x" * filler + " " + value + " tail"
        shortened = _shorten_without_splitting_a_number(text)
        for token in _numbers(shortened):
            assert token == value or token in value.split(","), (
                f"{token!r} is a fragment of {value!r} at filler {filler}"
            )


def test_a_claim_within_the_limit_is_untouched() -> None:
    text = "The total annual revenue is 77,781.76 across 150 rows."
    assert _shorten_without_splitting_a_number(text) == text


def test_a_shortened_claim_does_not_end_mid_separator() -> None:
    text = "a: 1.5; " * 120
    shortened = _shorten_without_splitting_a_number(text)
    assert not shortened.rstrip().endswith((";", ",", "-"))
    assert shortened.endswith(".")


def test_the_finding_model_applies_it() -> None:
    """The validator is the only place this can be enforced."""
    long_claim = "Total by Store: " + "; ".join(f"{i}: 301,397,792.46" for i in range(1, 40))
    finding = CandidateFinding(text=long_claim, kind="calculated_fact")

    assert len(finding.text) <= MAX_CLAIM_CHARS
    for token in _numbers(finding.text):
        assert token == "301,397,792.46" or token.isdigit()
