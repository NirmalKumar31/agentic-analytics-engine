"""The in-flight window: what a run must not notice.

Three invariants here survived a mutation run with every other test
passing, which is the only reliable way to discover that an invariant is
documented rather than enforced. Each concerns the gap between a run being
admitted and the session's schema moving on underneath it:

  - a run reads the schema it was admitted with, not the live one;
  - a Compare reads one schema for both branches, not one each;
  - a tool call compiled against an older schema fails closed.

None can be provoked through the public endpoint, because the endpoint
refuses a confirmation while a run is active. That is the point. These
test the mechanism that makes the guarantee rather than the path already
blocked: defence in depth is only depth if the inner layer holds when the
outer one is bypassed, and a direct call on the session object bypasses
it.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, cast

import anyio
import pytest
from fastapi.testclient import TestClient

from agentic_analytics.analytics.semantic import effective_schema
from agentic_analytics.api.app import create_app
from agentic_analytics.config import Settings
from agentic_analytics.graph.build import RunContext
from agentic_analytics.mcp_layer.client import (
    AnalyticsToolset,
    ToolBudget,
    ToolCallFailed,
)
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.warehouse.session import (
    AnalysisSession,
    SessionManager,
    open_upload_session,
)

REPO = Path(__file__).resolve().parents[2]
ROWS = 400


def csv_text() -> str:
    places = ["north", "south", "east", "west"]
    lines = ["place,amount,reading"]
    lines += [f"{places[i % 4]},{round(100.0 + i * 7.5, 2)},{18 + (i % 48)}" for i in range(ROWS)]
    return "\n".join(lines) + "\n"


@pytest.fixture
def session(tmp_path: Path) -> Any:
    """An uploaded session, built without the API.

    The SessionManager lives in `create_app`'s closure and is not on
    `app.state`, which is the right design and means these tests reach the
    layer below the endpoint directly.
    """
    path = tmp_path / "rows.csv"
    path.write_text(csv_text())
    return open_upload_session(path, "rows.csv", "csv", scratch_dir=tmp_path / "scratch")


# ------------------------------------------------- a run keeps its own schema


def test_a_run_reads_the_schema_it_was_admitted_with(session: Any) -> None:
    """A confirmation landing after admission must not reach the run.

    Dropping the pinned snapshot from `RunContext.schema_for` and reading
    live session state left every other test green: every one of them
    confirmed before the run began, so nothing exercised the window.
    """
    session.apply_role_confirmation_changes({"reading": "dimension"})

    # `schema_for` reads only the session and the pinned snapshot, so the
    # collaborators are stubs. Constructing a real toolset and provider here
    # would test those instead of the pinning.
    ctx = RunContext(
        session=session,
        toolset=cast(Any, object()),
        provider=cast(Any, object()),
        events=cast(Any, object()),
        budgets=cast(Any, object()),
    )
    assert ctx.schema_revision == 1
    assert "reading" in ctx.schema_for("uploaded_data").dimensions

    # The session moves on the way a second actor on it would move it:
    # straight at the object, past the endpoint that refuses.
    session.apply_role_confirmation_changes({"reading": None})
    assert session.role_confirmation_snapshot().revision == 2

    # The run is unmoved.
    assert ctx.schema_revision == 1, "the run followed the session instead of its snapshot"
    assert "reading" in ctx.schema_for("uploaded_data").dimensions, (
        "the run's schema changed underneath it; its evidence would describe "
        "a schema that never existed as a whole"
    )

    # And the live schema really did change, so the assertion above is not
    # passing for want of anything happening.
    assert "reading" not in effective_schema(session, "uploaded_data").dimensions


# ------------------------------------------------------- MCP fails closed


def test_mcp_refuses_a_tool_call_compiled_against_an_older_schema(session: Any) -> None:
    """A stale `expected_schema_revision` must stop before arithmetic.

    Removing the check left every test green: nothing passed a mismatched
    revision, so the fail-closed path was never entered.
    """
    session.apply_role_confirmation_changes({"reading": "dimension"})
    manager = SessionManager()
    manager.add(session)

    async def exercise() -> tuple[str, bool]:
        server = build_server(manager)
        async with AnalyticsToolset(
            server,
            session_id=session.session_id,
            session_key=session.session_key,
            budget=ToolBudget(max_total=20, max_per_task=20),
        ) as toolset:
            request = {
                "table": "uploaded_data",
                "question": "What is total amount by reading?",
            }
            # Fails closed: the call raises rather than returning a payload
            # that a caller might read past.
            with pytest.raises(ToolCallFailed) as refused:
                await toolset.call(
                    "aggregate_for_question", {**request, "expected_schema_revision": 0}
                )

            # The matching revision goes through, so the gate is the
            # revision and not the tool being broken for both.
            fresh = await toolset.call(
                "aggregate_for_question", {**request, "expected_schema_revision": 1}
            )
            return str(refused.value), fresh is not None

    message, fresh_succeeded = anyio.run(exercise)

    # The refusal says which revision was assumed and which is current, so a
    # caller can refetch rather than guess.
    assert "revision 1" in message, message
    assert "assumed 0" in message, message
    assert fresh_succeeded


# ----------------------------------------------- one schema for both branches


def test_a_comparison_captures_one_snapshot_for_both_branches(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two captures could straddle a confirmation.

    The halves of a comparison would then disagree about what a column
    meant, which is the one thing a comparison must never do. Counted
    rather than raced: capturing once is the mechanism, and a per-branch
    capture is exactly what the mutation introduced.
    """
    # A comparison is only offered where AI is configured. The provider
    # stays `fake` and the key is a placeholder, so this is credential-free
    # and makes no provider request -- it is the repository's existing
    # pattern for exercising the Compare admission path.
    import fakeredis

    import agentic_analytics.api.app as app_module
    from agentic_analytics.llm.ledger import CostLedger

    monkeypatch.setattr(
        app_module, "open_ledger", lambda url, *a, **k: CostLedger(fakeredis.FakeRedis())
    )
    settings = Settings(
        provider_mode="fake",
        live_analytics_enabled=True,
        uploads_enabled=True,
        ai_analytics_enabled=True,
        cloud_api_key="sk-test-not-used",
        cloud_model="gpt-6-luna",
        ai_quota_redis_url="redis://localhost:6379/0",
        data_dir=warehouse_dir.parent,
        upload_dir=tmp_path / "uploads",
        recordings_dir=REPO / "examples" / "recordings",
        log_json=False,
    )
    monkeypatch.setattr(type(settings), "demo_warehouse_dir", property(lambda self: warehouse_dir))

    captures: list[int] = []
    original = AnalysisSession.role_confirmation_snapshot

    def counted(self: Any) -> Any:
        snapshot = original(self)
        captures.append(snapshot.revision)
        return snapshot

    # Patched on the class, because the manager holding the instance is
    # private to `create_app` -- which is the correct boundary, so the test
    # works with it rather than opening it.
    monkeypatch.setattr(AnalysisSession, "role_confirmation_snapshot", counted)

    with TestClient(create_app(settings)) as client:
        uploaded = client.post(
            "/api/datasets/upload",
            files={"file": ("rows.csv", io.BytesIO(csv_text().encode()), "text/csv")},
        )
        assert uploaded.status_code == 200, uploaded.text
        session_id = uploaded.json()["session_id"]

        confirmed = client.patch(
            f"/api/datasets/{session_id}/schema/roles",
            json={
                "expected_revision": 0,
                "changes": [{"column": "reading", "action": "confirm", "role": "dimension"}],
            },
        )
        assert confirmed.status_code == 200, confirmed.text

        captures.clear()
        started = client.post(
            "/api/comparisons",
            json={
                "session_id": session_id,
                "question": "What is total amount by reading?",
            },
        )
        assert started.status_code in (200, 202), started.text

    assert captures, "the comparison never captured a snapshot"
    assert len(captures) == 1, (
        f"the comparison captured {len(captures)} snapshots; two captures can "
        f"straddle a confirmation and leave the branches with different schemas"
    )
