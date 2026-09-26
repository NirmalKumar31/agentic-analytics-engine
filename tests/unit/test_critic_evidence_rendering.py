"""What the critic is shown for a cited cell.

Found by the worker-findings probe. A real local model proposed four correct
claims -- "The South region has a revenue of 38200.5", with a cell reference
that pointed at exactly that number -- and the critic rejected them on the
grounds that the cited cell was empty. It was not empty. `EvidenceCell.value`
is optional, the model had left it unset, and the prompt rendered that unset
field straight into the line the critic reads:

    - res_grouped[1].revenue = None (None)

So the engine, which held the value the whole time, asked a model to check a
claim against a null it had printed itself. Every check downstream was
working; the sentence handed to the last one was wrong.
"""

from __future__ import annotations

from typing import Any

from agentic_analytics.agents.critic import _critic_prompt
from agentic_analytics.agents.schemas import CandidateFinding
from agentic_analytics.analytics.results import EvidenceCell, ResultSnapshot

GROUPED = ResultSnapshot(
    result_id="res_grouped",
    tool_name="aggregate_for_question",
    columns=["region", "revenue"],
    rows=[["North", 52100.0], ["South", 38200.5]],
    row_count=2,
)


def _finding(**cell: Any) -> CandidateFinding:
    base: dict[str, Any] = {"result_id": "res_grouped", "row": 1, "column": "revenue"}
    return CandidateFinding(
        text="The South region has a revenue of 38200.5.",
        kind="calculated_fact",
        result_ids=["res_grouped"],
        evidence_cells=[EvidenceCell(**(base | cell))],
    )


def test_a_cell_the_model_did_not_echo_is_resolved_by_the_engine() -> None:
    prompt = _critic_prompt(_finding(), [GROUPED])
    assert "res_grouped[1].revenue = 38200.5" in prompt
    assert "= None" not in prompt


def test_a_correctly_echoed_value_is_shown_once() -> None:
    prompt = _critic_prompt(_finding(value=38200.5), [GROUPED])
    assert "res_grouped[1].revenue = 38200.5" in prompt
    assert "the finding states" not in prompt


def test_a_miscopied_value_is_shown_next_to_the_real_one() -> None:
    """The critic should be able to see a bad copy, not be fed it."""
    prompt = _critic_prompt(_finding(value=38000.0), [GROUPED])
    assert "res_grouped[1].revenue = 38200.5" in prompt
    assert "the finding states 38000.0" in prompt


def test_an_integer_echo_of_a_float_cell_is_not_flagged() -> None:
    finding = CandidateFinding(
        text="North revenue was 52100.",
        result_ids=["res_grouped"],
        evidence_cells=[
            EvidenceCell(result_id="res_grouped", row=0, column="revenue", value=52100)
        ],
    )
    assert "the finding states" not in _critic_prompt(finding, [GROUPED])


def test_a_cell_pointing_outside_the_result_says_so() -> None:
    prompt = _critic_prompt(_finding(row=9), [GROUPED])
    assert "no such cell" in prompt
    assert "= None" not in prompt


def test_a_cell_naming_an_unknown_column_says_so() -> None:
    prompt = _critic_prompt(_finding(column="turnover"), [GROUPED])
    assert "no such cell" in prompt


def test_a_cell_in_a_result_that_was_not_cited_says_so() -> None:
    prompt = _critic_prompt(_finding(result_id="res_elsewhere"), [GROUPED])
    assert "not cited or is unavailable" in prompt


def test_a_label_survives_resolution() -> None:
    prompt = _critic_prompt(_finding(label="South revenue"), [GROUPED])
    assert "= 38200.5 (South revenue)" in prompt


def test_a_finding_with_no_cells_still_renders() -> None:
    finding = CandidateFinding(text="Revenue was 38200.5.", result_ids=["res_grouped"])
    assert "(none)" in _critic_prompt(finding, [GROUPED])


# ---------------------------------------------------------------- privacy
def test_a_withheld_upload_cell_is_not_revealed_by_the_resolution() -> None:
    """The fix must not become the leak the redaction exists to stop.

    Resolving through `rows` rather than `agent_rows` would print an
    uploaded file's largest value to a remote model in the one line nobody
    was checking.
    """
    from agentic_analytics.analytics.results import RAW_CELL_COLUMNS

    column = sorted(RAW_CELL_COLUMNS)[0]
    profile = ResultSnapshot(
        result_id="res_upload",
        tool_name="profile_table",
        columns=["column_name", column],
        rows=[["salary", 987654.32]],
        row_count=1,
        withhold_cells=True,
    )
    finding = CandidateFinding(
        text="A value is present.",
        result_ids=["res_upload"],
        evidence_cells=[EvidenceCell(result_id="res_upload", row=0, column=column)],
    )
    prompt = _critic_prompt(finding, [profile])
    assert "987654.32" not in prompt


def test_agent_cell_matches_cell_when_nothing_is_withheld() -> None:
    assert GROUPED.agent_cell(1, "revenue") == GROUPED.cell(1, "revenue") == 38200.5


def test_agent_cell_refuses_a_bad_reference_the_same_way_cell_does() -> None:
    import pytest

    with pytest.raises(KeyError):
        GROUPED.agent_cell(0, "nope")
    with pytest.raises(IndexError):
        GROUPED.agent_cell(7, "revenue")
