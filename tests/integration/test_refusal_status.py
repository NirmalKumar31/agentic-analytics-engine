"""A question that could not be mapped is refused, and says so once.

Two failures lived here. A run that could not map its question ended as
`completed` with nothing in it, which reads as "we looked and found
nothing" rather than "we could not understand the question". And the one
refusal was described three times in three phrasings, because three
places each added their own sentence about it:

    The question was not executed because ...
    the question could not be mapped safely: ...
    The run stopped early: the question could not be mapped safely: ...

A reader who sees the same point three times learns to skip the section,
which is where a real limitation would appear.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from agentic_analytics.config import Settings
from agentic_analytics.graph.runner import run_analysis
from agentic_analytics.llm.fake import FakeProvider
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.warehouse.session import SessionManager, open_upload_session

#: A table with several numeric columns, a numeric flag to group by and a
#: date column -- enough for a question to be unmappable for each of the
#: distinct reasons below.
HEADER = "Store,Date,Weekly_Sales,Holiday_Flag,Temperature"
ROWS = [
    (store, f"2010-{(index % 12) + 1:02d}-05", value, flag, 40.0 + index % 30)
    for index, (store, value, flag) in enumerate(
        (s, v, f)
        for s in range(1, 41)
        for v, f in ((100.0, 0), (300.0, 0), (500.0, 0), (200.0, 1), (400.0, 1), (600.0, 1))
    )
]


async def _run(question: str) -> Any:
    cfg = Settings(
        provider_mode="fake",
        live_analytics_enabled=True,
        uploads_enabled=True,
        log_json=False,
    )
    body = "\n".join(",".join(str(v) for v in row) for row in ROWS)
    manager = SessionManager()
    provider = FakeProvider()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fixture.csv"
        path.write_text(f"{HEADER}\n{body}\n")
        try:
            session = manager.add(open_upload_session(path, path.name, "csv"))
            server = build_server(manager, cfg)
            return await run_analysis(question, session, server, settings=cfg, provider=provider)
        finally:
            await provider.aclose()
            manager.close_all()


@pytest.mark.parametrize(
    "question",
    [
        # A restriction that names no column of the table.
        "average Weekly_Sales by Holiday_Flag for tenure 3 to 9",
        # A range that selects nothing.
        "average Weekly_Sales by Holiday_Flag for Temperature 90 to 10",
        # A measure the table does not have.
        "What is the average profit by Holiday_Flag?",
    ],
)
async def test_an_unmappable_question_is_refused_not_completed(question: str) -> None:
    """`completed` with nothing in it is a different claim from `refused`,
    and only one of them is true here."""
    result = await _run(question)
    assert result.outcome == "refused", (
        f"outcome={result.outcome} with {len(result.published)} published"
    )
    assert not result.published
    assert result.stopped_reason


@pytest.mark.parametrize(
    "question",
    [
        "average Weekly_Sales by Holiday_Flag for tenure 3 to 9",
        "average Weekly_Sales by Holiday_Flag for Temperature 90 to 10",
        "What is the average profit by Holiday_Flag?",
    ],
)
async def test_a_refusal_states_its_reason_once(question: str) -> None:
    result = await _run(question)
    limitations = result.report.limitations if result.report else []
    assert len(limitations) == 1, limitations
    # And it is the specific sentence, not a generic one.
    assert "not executed" in limitations[0] or "not answered" in limitations[0]
    assert len(limitations[0]) > 40, "a refusal has to say what could not be resolved"


async def test_a_refusal_suggests_what_to_do() -> None:
    """One actionable sentence beats several warnings."""
    result = await _run("average Weekly_Sales by Holiday_Flag for tenure 3 to 9")
    limitations = result.report.limitations if result.report else []
    assert limitations
    assert "name the column" in limitations[0]


async def test_an_answerable_question_still_completes_with_its_answer() -> None:
    """The taxonomy must not turn working questions into refusals."""
    result = await _run("What is the average Weekly_Sales by Holiday_Flag?")
    assert result.outcome == "completed"
    assert result.published
    text = result.published[0].text
    assert "300" in text and "400" in text, text


async def test_a_completed_run_does_not_carry_a_refusal_sentence() -> None:
    result = await _run("What is the average Weekly_Sales by Holiday_Flag?")
    limitations = result.report.limitations if result.report else []
    assert not any("not executed" in x or "not answered" in x for x in limitations), limitations
