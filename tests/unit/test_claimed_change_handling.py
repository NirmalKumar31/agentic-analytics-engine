"""An unreadable change declaration is no claim, not a false one.

Found by running a real model. qwen3:4b produced findings whose every stated
number matched a cited cell exactly -- `stated=43985.05` against
`computed=43985.04999999993` -- and every one of them was withheld, because
the optional `claimed_change` field came back without its `from`/`to`. Across
three questions that was 17 of 17 findings rejected and nothing published:
the run looked like a model hallucinating numbers when the numbers were
right.

A malformed change asserts nothing, so it is discarded and the finding is
judged on its stated numbers, which are verified against the cited cells
either way. A change that is *readable and wrong* still fails, and that
distinction is what these tests pin down.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.verification.numeric import verify_numbers

CELLS = [(43985.05, "res_1[0].total_net_value")]


def _snapshot() -> ResultSnapshot:
    return ResultSnapshot(
        result_id="res_1",
        tool_name="run_readonly_sql",
        columns=["total_net_value", "territory"],
        rows=[[43985.04999999993, "North"]],
        row_count=1,
    )


TEXT = "Total net value for North is 43,985.05."


@pytest.mark.parametrize(
    "claimed",
    [
        {"type": "difference"},
        {"type": "difference", "from": None, "to": None, "stated": None},
        {"type": "difference", "from": "a lot", "to": "less", "stated": "some"},
        {"from": 1.0},
        {"type": "percent_change", "stated": 5.0},
    ],
)
def test_a_malformed_change_does_not_fail_a_correct_finding(claimed: dict[str, Any]) -> None:
    verdict = verify_numbers(TEXT, claimed, CELLS, [_snapshot()])
    assert verdict.ok, verdict.reason
    assert verdict.claimed_change_discarded is True
    assert "discarded" in verdict.reason


def test_the_discard_is_visible_rather_than_silent() -> None:
    """A reader must be able to tell something was dropped."""
    verdict = verify_numbers(TEXT, {"type": "difference"}, CELLS, [_snapshot()])
    payload = verdict.as_dict()
    assert payload["claimed_change_discarded"] is True
    assert "discarded" in payload["reason"]


def test_a_readable_but_wrong_change_still_fails() -> None:
    """The distinction the fix turns on. This one is a false claim."""
    verdict = verify_numbers(
        "Revenue rose from 10 to 20, a change of 999.",
        {"type": "difference", "from": 10.0, "to": 20.0, "stated": 999.0},
        [(10.0, "res_1[0].a"), (20.0, "res_1[1].a")],
        [
            ResultSnapshot(
                result_id="res_1",
                tool_name="analyze_timeseries",
                columns=["a"],
                rows=[[10.0], [20.0]],
                row_count=2,
            )
        ],
    )
    assert not verdict.ok
    assert verdict.claimed_change_discarded is False


def test_a_correct_change_still_passes() -> None:
    verdict = verify_numbers(
        "Revenue rose from 10 to 20, a change of 10.",
        {"type": "difference", "from": 10.0, "to": 20.0, "stated": 10.0},
        [(10.0, "res_1[0].a"), (20.0, "res_1[1].a")],
        [
            ResultSnapshot(
                result_id="res_1",
                tool_name="analyze_timeseries",
                columns=["a"],
                rows=[[10.0], [20.0]],
                row_count=2,
            )
        ],
    )
    assert verdict.ok
    assert verdict.claimed_change_discarded is False


def test_a_wrong_number_still_fails_even_with_a_malformed_change() -> None:
    """Discarding the change must not weaken the check on the text."""
    verdict = verify_numbers(
        "Total net value for North is 99,999.99.",
        {"type": "difference"},
        CELLS,
        [_snapshot()],
    )
    assert not verdict.ok
    assert "do not appear" in verdict.reason


def test_the_published_finding_does_not_show_a_discarded_change() -> None:
    """Nothing unverified may be displayed as a calculation."""
    from agentic_analytics.agents.critic import publish
    from agentic_analytics.agents.schemas import CandidateFinding, EvidenceCell, Verdict

    finding = CandidateFinding(
        text=TEXT,
        kind="calculated_fact",
        task_id="t",
        result_ids=["res_1"],
        evidence_cells=[
            EvidenceCell(
                result_id="res_1",
                row=0,
                column="total_net_value",
                value=43985.05,
                label="total",
            )
        ],
        metric_ids=[],
        claimed_change={"type": "difference"},
    )
    verdict = verify_numbers(TEXT, finding.claimed_change, CELLS, [_snapshot()])
    published = publish(
        finding,
        Verdict(
            finding_id=finding.finding_id,
            status="supported",
            reason="ok",
            rule="critic",
            numeric_check=verdict.as_dict(),
        ),
    )
    assert published.claimed_change is None
    # A verified change is still carried through.
    keep = verify_numbers(
        "Revenue rose from 10 to 20, a change of 10.",
        {"type": "difference", "from": 10.0, "to": 20.0, "stated": 10.0},
        [(10.0, "r[0].a"), (20.0, "r[1].a")],
        [
            ResultSnapshot(
                result_id="r",
                tool_name="analyze_timeseries",
                columns=["a"],
                rows=[[10.0], [20.0]],
                row_count=2,
            )
        ],
    )
    assert keep.ok and not keep.claimed_change_discarded
