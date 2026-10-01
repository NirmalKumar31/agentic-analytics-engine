"""Automatic routing through the real pipeline, counting what it spends.

The unit tests pin the policy. These run it: a real upload, real schema
inference, the real resolver, the real graph, and a planner factory that
records every construction.

Construction is the thing worth counting rather than requests, because
building the governed cloud provider is where the durable ledger slot is
taken. A run that builds one has already spent quota whether or not it
goes on to ask anything.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from tests.corpus.runner import RemoteFakeProvider
from tests.fixtures.sleep_study import write_csv

from agentic_analytics.config import Settings
from agentic_analytics.graph.runner import RunResult, run_analysis
from agentic_analytics.llm.fake import FakeProvider
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.warehouse.session import SessionManager, open_upload_session


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_csv(tmp_path_factory.mktemp("auto-routing"))


class Planner:
    """Records each construction, so a spent quota slot is visible."""

    def __init__(self) -> None:
        self.builds = 0

    async def __call__(self) -> Any:
        self.builds += 1
        return RemoteFakeProvider()


def _run(dataset: Path, question: str, planner: Planner | None) -> RunResult:
    async def go() -> RunResult:
        manager = SessionManager()
        settings = Settings(
            upload_dir=dataset.parent / "uploads",
            live_analytics_enabled=True,
            uploads_enabled=True,
            provider_mode="fake",
            log_json=False,
        )
        provider = FakeProvider()
        try:
            session = manager.add(open_upload_session(dataset, dataset.name, "csv"))
            return await run_analysis(
                question,
                session,
                build_server(manager, settings),
                settings=settings,
                provider=provider,
                open_planner=planner,
            )
        finally:
            await provider.aclose()
            manager.close_all()

    return asyncio.run(go())


def _route(result: RunResult) -> dict[str, Any]:
    events = [e for e in result.events if e["type"] == "contract_resolved"]
    assert events, "an automatic run must record how it was routed"
    return dict(events[0]["data"])


def test_an_exact_question_answers_without_building_a_planner(
    dataset: Path,
) -> None:
    planner = Planner()
    result = _run(dataset, "What is the average total_sleep_hours by chronotype?", planner)

    assert result.outcome == "completed"
    assert result.published, result.stopped_reason
    assert planner.builds == 0, "an exact question took a ledger slot it did not need"

    route = _route(result)
    assert route["route"] == "rules_exact"
    assert route["resolution_state"] == "exact"
    assert route["issues"] == []
    assert route["model_calls"] == 0
    assert route["contract_hash"]


def test_a_missing_column_refuses_without_building_a_planner(dataset: Path) -> None:
    planner = Planner()
    result = _run(dataset, "What is the total gross_margin by chronotype?", planner)

    assert result.outcome == "refused"
    assert not result.published
    assert planner.builds == 0, "spent a quota slot on a certain refusal"

    route = _route(result)
    assert route["route"] == "refused"
    assert route["resolution_state"] == "unresolved"
    assert "unresolved_measure" in route["issues"]
    assert route["ai_eligible"] is False


def test_ambiguity_builds_one_planner_and_records_the_call(dataset: Path) -> None:
    planner = Planner()
    result = _run(dataset, "What is the average by chronotype?", planner)

    assert planner.builds == 1, "ambiguity must cost one planning request, not more"
    route = _route(result)
    assert route["resolution_state"] == "ambiguous"
    assert route["ai_eligible"] is True
    assert route["model_calls"] >= 1


def test_an_exact_question_still_answers_with_no_planner_available(
    dataset: Path,
) -> None:
    """The property that makes automatic routing safe as a default.

    A deployment with no credential, or a broken one, still answers every
    question the rules can resolve.
    """
    result = _run(dataset, "What is the average deep_sleep_pct by age?", None)
    assert result.outcome == "completed"
    assert result.published


def test_a_refused_route_publishes_a_refusal_rather_than_an_answer(
    dataset: Path,
) -> None:
    """A refusal must not arrive looking like a result.

    The presentation contract is what the reader sees, so the refusal has
    to reach it as a refusal -- not as an empty report beside a
    verification badge, which is what the older path produced.
    """
    result = _run(dataset, "What is the total gross_margin by chronotype?", Planner())
    assert result.presentation is not None
    assert result.presentation.shape.value == "refusal"
    assert result.presentation.highlights == []
    assert result.presentation.table is None
    assert result.presentation.headline


def test_a_dropped_restriction_is_no_longer_answered_for_everyone(
    dataset: Path,
) -> None:
    """The defect automatic routing exposed.

    `where mood is good` named no column of the table. The equality clause
    was skipped rather than recorded, so the restriction vanished and the
    average for every participant was published as the answer to a
    question about a subset. It must now refuse, and it must be classified
    as ambiguous rather than unresolved, because a planner reading the
    sentence may well bind it to a real column.
    """
    planner = Planner()
    result = _run(dataset, "What is the average total_sleep_hours where mood is good?", planner)

    assert result.outcome == "refused"
    assert not result.published
    route = _route(result)
    assert route["resolution_state"] == "ambiguous"
    assert "missing_filter_binding" in route["issues"]
    # It was worth one request, because the column might have been bindable.
    assert planner.builds == 1


def test_a_bindable_restriction_still_answers_exactly(dataset: Path) -> None:
    """The fix above must not refuse questions that already worked.

    Single-word category values only. A multi-word value is captured up to
    its first space -- `chronotype is Night Owl` binds as `Night` -- which
    filters to no rows and is declined by the empty-population guard. That
    is a separate pre-existing limit in the filter grammar, recorded in
    LIMITATIONS, and it is not on the routing path: the question resolves
    exactly, so no planner is consulted either way.
    """
    planner = Planner()
    for question in (
        "What is the average total_sleep_hours where chronotype is Intermediate?",
        "What is the average deep_sleep_pct where age is above 40?",
    ):
        result = _run(dataset, question, planner)
        assert result.outcome == "completed", f"{question}: {result.stopped_reason}"
        assert result.published, question
    assert planner.builds == 0, "a fully specified restriction needs no planner"


def test_the_route_event_discloses_no_prompt_and_no_cells(dataset: Path) -> None:
    """What the planning audit shows must leak nothing."""
    result = _run(dataset, "What is the average by chronotype?", Planner())
    route = _route(result)
    flat = str(route).lower()
    for forbidden in ("prompt", "system:", "api_key", "participant", "bearer"):
        assert forbidden not in flat
    assert set(route) <= {
        "planner",
        "model_calls",
        "route",
        "resolution_state",
        "issues",
        "ai_eligible",
        "contract_hash",
        "duration_ms",
        "fallback",
    }


def test_explicit_modes_are_untouched_by_automatic_routing(dataset: Path) -> None:
    """Passing no factory must behave exactly as before.

    Automatic routing is additive. A deployment, an audit run or a Compare
    Both pane that does not ask for it must not acquire it.
    """
    result = _run(dataset, "What is the average total_sleep_hours by chronotype?", None)
    assert result.outcome == "completed"
    assert result.published
    # No automatic route was chosen, so none is claimed.
    events = [e for e in result.events if e["type"] == "contract_resolved"]
    for event in events:
        assert "route" not in event["data"]
