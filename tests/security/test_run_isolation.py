"""What one run may read of another run's work.

A session is shared on purpose: Compare Both puts a deterministic run and
an AI run on one connection so that both answer the same question against
the same rows. That sharing stops at the result store. A stored snapshot
keeps every value it was computed with, so "every result on this session"
is the wrong set for any single run to read -- it would let the cloud run
be shown cells the local run was allowed to compute, and would let either
half of a comparison cite the other half's numbers as its own evidence.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest

from agentic_analytics.config import Settings
from agentic_analytics.graph.runner import run_analysis
from agentic_analytics.llm.base import LLMRequest
from agentic_analytics.llm.fake import FakeProvider
from agentic_analytics.mcp_layer.client import AnalyticsToolset, ToolCallFailed
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.warehouse.session import (
    AnalysisSession,
    SessionManager,
    open_upload_session,
)

SECRET_NAME = "Kriszti-Anna"
#: The largest amount in the file, so it is the `max_value` of a profile.
#: A profile only computes min/max for numeric columns, so this rather
#: than the name is the raw cell that a profile can actually disclose.
SECRET_AMOUNT = "8675309"


@pytest.fixture
def uploaded(tmp_path: Path) -> Path:
    path = tmp_path / "people.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["record_id", "city", "full_name", "amount"])
        for index in range(40):
            writer.writerow(
                [
                    index,
                    f"City{index % 4}",
                    SECRET_NAME if index == 3 else f"Name{index}",
                    int(SECRET_AMOUNT) if index == 3 else 100 + index,
                ]
            )
    return path


class RemoteProvider(FakeProvider):
    """A provider whose prompts leave the machine, keeping each one."""

    remote_inference = True

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[LLMRequest] = []

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        self.requests.append(request)
        return await super().complete_json(request)


def _session(manager: SessionManager, uploaded: Path) -> AnalysisSession:
    return manager.add(open_upload_session(uploaded, "people.csv", "csv"))


async def test_two_runs_on_one_session_have_disjoint_result_ids(
    uploaded: Path,
) -> None:
    """The defining property: a comparison's halves share no evidence."""
    cfg = Settings(provider_mode="fake", live_analytics_enabled=True)
    manager = SessionManager()
    session = _session(manager, uploaded)
    server = build_server(manager, cfg)
    question = "What is the total amount by city?"
    try:
        first = await run_analysis(question, session, server, settings=cfg)
        second = await run_analysis(question, session, server, settings=cfg)
    finally:
        manager.close_all()

    assert first.results, "the first run produced no results at all"
    assert second.results, "the second run produced no results at all"
    assert not (set(first.results) & set(second.results))


async def test_a_run_reports_only_the_results_it_produced(uploaded: Path) -> None:
    """The session store keeps growing; the run's own view does not.

    After two runs the session holds both sets, so a reader that went to
    the session would report the first run's results as the second's.
    """
    cfg = Settings(provider_mode="fake", live_analytics_enabled=True)
    manager = SessionManager()
    session = _session(manager, uploaded)
    server = build_server(manager, cfg)
    question = "What is the total amount by city?"
    try:
        first = await run_analysis(question, session, server, settings=cfg)
        second = await run_analysis(question, session, server, settings=cfg)
        on_session = {s.result_id for s in session.results.all()}
    finally:
        manager.close_all()

    assert set(first.results) < on_session
    assert set(second.results) < on_session
    for result_id, snapshot in second.results.items():
        assert snapshot.result_id == result_id


async def test_a_run_cannot_fetch_a_sibling_runs_result(uploaded: Path) -> None:
    """`get_result` is scoped to the run, not to the session.

    Both toolsets below hold the same session and the same capability, so
    the only thing standing between them is the run scope.
    """
    cfg = Settings(provider_mode="fake", live_analytics_enabled=True)
    manager = SessionManager()
    session = _session(manager, uploaded)
    server = build_server(manager, cfg)
    try:
        async with AnalyticsToolset(
            server, session_id=session.session_id, session_key=session.session_key
        ) as local_run:
            payload = await local_run.call("profile_table", {"table": "uploaded_data"})
            borrowed = payload["result_id"]
            # Its own result is readable.
            assert await local_run.call("get_result", {"result_id": borrowed})

        async with AnalyticsToolset(
            server,
            session_id=session.session_id,
            session_key=session.session_key,
            remote_inference=True,
        ) as cloud_run:
            with pytest.raises(ToolCallFailed, match="unknown result_id"):
                await cloud_run.call("get_result", {"result_id": borrowed})
    finally:
        manager.close_all()


async def test_rereading_a_result_applies_the_readers_policy(uploaded: Path) -> None:
    """The stored flag is not trusted on the way back out.

    A snapshot keeps every value it was computed with by design -- numeric
    verification checks against the truth, and the visitor may see their own
    file. So the disclosure decision has to be made again for the run doing
    the reading, not read off the snapshot the writing run left behind.
    """
    cfg = Settings(provider_mode="fake", live_analytics_enabled=True)
    manager = SessionManager()
    session = _session(manager, uploaded)
    server = build_server(manager, cfg)
    try:
        async with AnalyticsToolset(
            server, session_id=session.session_id, session_key=session.session_key
        ) as local_run:
            payload = await local_run.call("profile_table", {"table": "uploaded_data"})
            assert SECRET_AMOUNT in str(payload), "the local run should see its own file"
            stored = session.results.get(payload["result_id"])

        # The same stored snapshot, re-read by a run that infers remotely.
        assert stored.withhold_cells is False
        async with AnalyticsToolset(
            server,
            session_id=session.session_id,
            session_key=session.session_key,
            remote_inference=True,
        ) as cloud_run:
            cloud_payload = await cloud_run.call("profile_table", {"table": "uploaded_data"})
            assert SECRET_AMOUNT not in str(cloud_payload)
            reread = await cloud_run.call("get_result", {"result_id": cloud_payload["result_id"]})
            assert SECRET_AMOUNT not in str(reread)
    finally:
        manager.close_all()


async def test_a_local_run_beside_a_cloud_run_does_not_leak_into_it(
    uploaded: Path,
) -> None:
    """The Compare Both case, end to end.

    The deterministic half computes cells it is entitled to. The AI half
    must not be able to read them through the store they share.
    """
    cfg = Settings(provider_mode="fake", live_analytics_enabled=True)
    manager = SessionManager()
    session = _session(manager, uploaded)
    server = build_server(manager, cfg)
    question = "What is the total amount by city?"
    remote = RemoteProvider()
    try:
        local = await run_analysis(question, session, server, settings=cfg)
        cloud = await run_analysis(question, session, server, settings=cfg, provider=remote)
    finally:
        await remote.aclose()
        manager.close_all()

    assert not (set(local.results) & set(cloud.results))
    sent = "\n".join(f"{r.system}\n{r.user}\n{r.context}" for r in remote.requests)
    assert SECRET_AMOUNT not in sent
    assert SECRET_NAME not in sent
