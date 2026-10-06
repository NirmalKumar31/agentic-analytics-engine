"""The display contract, enforced on the Python side.

`display_value` and `web/src/lib/displayValue.ts` are two implementations
of one rule, and this file and `src/test/displayValue.test.ts` read the
same hand-written cases. Neither side is the authority: when they disagree
about how a cell reads, one of the two suites fails and names the case.

Hand-written rather than generated. A fixture produced by either
implementation would make that implementation's test a tautology, and the
defect this guards against is exactly the one that shipped -- a backend
headline saying "Oct 2025" over a table saying "2025-01-01T00:00:00", from
the same cell, with each surface internally consistent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentic_analytics.presentation.fields import display_value
from agentic_analytics.presentation.schemas import DisplayField

CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "web"
    / "src"
    / "test"
    / "fixtures"
    / "displayValueContract.json"
)


def _contract() -> dict[str, Any]:
    return json.loads(CONTRACT.read_text())


def _field(case: dict[str, Any], defaults: dict[str, Any]) -> DisplayField | None:
    if case["field"] is None:
        return None
    return DisplayField(**{**defaults, **case["field"]})


def _cases() -> list[tuple[str, dict[str, Any]]]:
    contract = _contract()
    return [(case["name"], case) for case in contract["cases"]]


def test_the_contract_is_committed_and_not_empty() -> None:
    contract = _contract()
    assert CONTRACT.is_file()
    assert len(contract["cases"]) >= 20, (
        "the display contract is the only thing holding the two formatters "
        "together; a thin one is a weak join"
    )


@pytest.mark.parametrize(("name", "case"), _cases(), ids=[n for n, _ in _cases()])
def test_display_value_matches_the_contract(name: str, case: dict[str, Any]) -> None:
    defaults = _contract()["field_defaults"]
    field = _field(case, defaults)
    assert display_value(case["raw"], field) == case["expected"], name


def test_no_reader_string_in_the_contract_is_an_identifier_or_a_serialisation() -> None:
    """The four things a reader field may never be.

    Asserted over the contract itself, so a case added later cannot declare
    an expectation that publishes one of them.
    """
    import re

    forbidden = {
        "a Python None": re.compile(r"\bNone\b"),
        "a Python or JSON list": re.compile(r"[\[\]]"),
        "a stored instant": re.compile(r"\d{4}-\d{2}-\d{2}T"),
        "a bare null": re.compile(r"\bnull\b"),
    }
    for case in _contract()["cases"]:
        expected = case["expected"]
        # The one legitimate exception: a value the engine stored that this
        # layer declines to reformat is shown unchanged, and the case says so.
        if "as the engine stored it" in case["name"]:
            continue
        for description, pattern in forbidden.items():
            assert not pattern.search(expected), (
                f"{case['name']!r} expects {expected!r}, which contains {description}"
            )
