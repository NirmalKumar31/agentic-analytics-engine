"""Anonymous capability isolation.

There is no authentication. A session is reached with a capability token, and
these tests are the evidence that the capability is actually required: two
visitors upload different datasets and neither can reach the other's file,
schema, results, runs or session.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.config import Settings
from agentic_analytics.warehouse.session import (
    SessionManager,
    new_session_id,
    new_session_key,
    open_demo_session,
)

CSV_A = b"region,amount\nWest,10\nEast,20\nWest,30\nNorth,5\n"
CSV_B = b"country,revenue\nFrance,999\nSpain,111\nItaly,222\n"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        provider_mode="fake",
        live_analytics_enabled=True,
        uploads_enabled=True,
        upload_dir=tmp_path / "uploads",
        log_json=False,
    )


@pytest.fixture
def app(settings: Settings):  # type: ignore[no-untyped-def]
    return create_app(settings)


@pytest.fixture
def visitors(app) -> Iterator[tuple[TestClient, str, TestClient, str]]:  # type: ignore[no-untyped-def]
    """Two independent browsers, each with its own cookie jar and dataset.

    Only one client enters the application lifespan: the MCP session manager
    refuses to start twice on the same instance, which is correct behaviour
    and not something to work around in the application.
    """
    with TestClient(app) as a:
        b = TestClient(app)
        ra = a.post(
            "/api/datasets/upload", files={"file": ("a.csv", io.BytesIO(CSV_A), "text/csv")}
        )
        rb = b.post(
            "/api/datasets/upload", files={"file": ("b.csv", io.BytesIO(CSV_B), "text/csv")}
        )
        assert ra.status_code == 200, ra.text
        assert rb.status_code == 200, rb.text
        yield a, ra.json()["session_id"], b, rb.json()["session_id"]


def test_capability_is_a_cookie_not_a_response_field(visitors) -> None:  # type: ignore[no-untyped-def]
    a, session_a, _, _ = visitors
    assert "aae_session" in a.cookies
    body = a.get(f"/api/datasets/{session_a}").text
    # The secret must not travel in a body, a URL or anything logged.
    assert "session_key" not in body
    assert a.cookies["aae_session"] not in body


def test_session_ids_are_unpredictable() -> None:
    ids = {new_session_id() for _ in range(500)}
    keys = {new_session_key() for _ in range(500)}
    assert len(ids) == 500 and len(keys) == 500
    # An incrementing id would be a guessable capability boundary.
    assert all(len(i) > 16 for i in ids)
    assert all(len(k) > 32 for k in keys)


def test_a_visitor_cannot_read_another_dataset(visitors) -> None:  # type: ignore[no-untyped-def]
    a, session_a, b, session_b = visitors
    assert a.get(f"/api/datasets/{session_b}").status_code == 404
    assert b.get(f"/api/datasets/{session_a}").status_code == 404
    assert a.get(f"/api/datasets/{session_a}").status_code == 200
    assert b.get(f"/api/datasets/{session_b}").status_code == 200


def test_no_capability_reaches_nothing(app, visitors) -> None:  # type: ignore[no-untyped-def]
    _, session_a, _, _ = visitors
    stranger = TestClient(app)
    if True:
        assert stranger.get(f"/api/datasets/{session_a}").status_code == 404
        assert stranger.delete(f"/api/datasets/{session_a}").status_code == 404
        assert (
            stranger.post(
                "/api/analyses", json={"session_id": session_a, "question": "anything?"}
            ).status_code
            == 404
        )


def test_a_tampered_capability_is_refused(app, visitors) -> None:  # type: ignore[no-untyped-def]
    a, session_a, _, _ = visitors
    good = a.cookies["aae_session"]
    forger = TestClient(app)
    if True:
        for bad in (good[:-1] + ("x" if good[-1] != "x" else "y"), good[:10], "", "null"):
            forger.cookies.set("aae_session", bad)
            assert forger.get(f"/api/datasets/{session_a}").status_code == 404


def test_the_same_error_is_returned_for_unknown_and_unauthorised(app, visitors) -> None:  # type: ignore[no-untyped-def]
    """A caller must not be able to probe which handles exist."""
    a, _, _, session_b = visitors
    unknown = a.get("/api/datasets/ses_definitely_not_a_real_handle")
    unauthorised = a.get(f"/api/datasets/{session_b}")
    assert unknown.status_code == unauthorised.status_code == 404
    assert unknown.json()["detail"] == unauthorised.json()["detail"]


def test_a_visitor_cannot_read_another_run(visitors) -> None:  # type: ignore[no-untyped-def]
    a, session_a, b, _ = visitors
    started = a.post(
        "/api/analyses", json={"session_id": session_a, "question": "Total amount by region?"}
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]

    # Drain the event stream so the run finishes inside the test rather than
    # being cancelled at teardown, which would leave the assertion racing an
    # unwinding task.
    with a.stream("GET", f"/api/analyses/{run_id}/events") as response:
        for line in response.iter_lines():
            if line.startswith("event: stream_end"):
                break

    assert b.get(f"/api/analyses/{run_id}").status_code == 404
    assert a.get(f"/api/analyses/{run_id}").status_code == 200


def test_a_visitor_cannot_delete_another_session(visitors) -> None:  # type: ignore[no-untyped-def]
    a, session_a, b, _ = visitors
    assert b.delete(f"/api/datasets/{session_a}").status_code == 404
    assert a.get(f"/api/datasets/{session_a}").status_code == 200


def test_deleting_a_session_erases_it_and_leaves_others_alone(visitors, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    a, session_a, b, session_b = visitors
    before = list(settings.upload_dir.glob("aae_*"))
    assert len(before) == 2

    assert a.delete(f"/api/datasets/{session_a}").status_code == 200
    assert a.get(f"/api/datasets/{session_a}").status_code == 404
    # The other visitor is untouched.
    assert b.get(f"/api/datasets/{session_b}").status_code == 200
    # And the deleted session's bytes are gone from disk.
    assert len(list(settings.upload_dir.glob("aae_*"))) == 1


def test_two_sessions_share_a_table_name_but_not_a_database(tmp_path: Path) -> None:
    """Both uploads become `uploaded_data`; the isolation is the database.

    Asserted at the data layer rather than through a full analysis run: this
    is the property that matters, and testing it directly makes the result
    unambiguous.
    """
    from agentic_analytics.analytics.execute import run_query
    from agentic_analytics.warehouse.session import open_upload_session

    file_a = tmp_path / "a.csv"
    file_b = tmp_path / "b.csv"
    file_a.write_bytes(CSV_A)
    file_b.write_bytes(CSV_B)

    session_a = open_upload_session(file_a, "a.csv", "csv")
    session_b = open_upload_session(file_b, "b.csv", "csv")
    try:
        assert set(session_a.tables) == set(session_b.tables) == {"uploaded_data"}
        assert session_a.session_id != session_b.session_id
        assert session_a.session_key != session_b.session_key
        assert session_a.dataset_fingerprint != session_b.dataset_fingerprint

        rows_a = run_query(session_a, "SELECT * FROM uploaded_data", tool_name="run_readonly_sql")
        rows_b = run_query(session_b, "SELECT * FROM uploaded_data", tool_name="run_readonly_sql")
        assert "region" in rows_a.columns and "country" in rows_b.columns
        assert "France" not in str(rows_a.rows)
        assert "West" not in str(rows_b.rows)

        # A result computed in one session is not retrievable from the other.
        assert not session_b.results.has(rows_a.result_id)
        with pytest.raises(KeyError):
            session_b.results.get(rows_a.result_id)
    finally:
        session_a.close()
        session_b.close()


def test_result_ids_do_not_collide_across_sessions(tmp_path: Path) -> None:
    from agentic_analytics.analytics.execute import run_query
    from agentic_analytics.warehouse.session import open_upload_session

    csv = tmp_path / "x.csv"
    csv.write_bytes(CSV_A)
    sessions_open = [open_upload_session(csv, "x.csv", "csv") for _ in range(8)]
    try:
        ids = [
            run_query(s, "SELECT 1 AS x", tool_name="run_readonly_sql").result_id
            for s in sessions_open
            for _ in range(5)
        ]
        assert len(set(ids)) == len(ids), "result ids collided"
    finally:
        for s in sessions_open:
            s.close()


# --------------------------------------------------------------- MCP layer


def test_mcp_tools_require_the_capability(warehouse_dir: Path) -> None:
    """A handle alone must not reach a session through the MCP tools."""
    import anyio
    from mcp import Client

    from agentic_analytics.mcp_layer.server import build_server

    manager = SessionManager()
    session = manager.add(open_demo_session(warehouse_dir))
    server = build_server(manager)

    async def go() -> None:
        async with Client(server) as client:
            with_key = await client.call_tool(
                "list_tables",
                {"session_id": session.session_id, "session_key": session.session_key},
            )
            assert not with_key.is_error

            for bad in ("", "not-the-key", session.session_id):
                refused = await client.call_tool(
                    "list_tables", {"session_id": session.session_id, "session_key": bad}
                )
                assert refused.is_error, bad

    try:
        anyio.run(go)
    finally:
        manager.close_all()


def test_mcp_resources_refuse_uploaded_sessions(tmp_path: Path) -> None:
    """Resources are keyed by handle only, so they serve demo data only."""
    import anyio
    from mcp import Client

    from agentic_analytics.mcp_layer.server import build_server
    from agentic_analytics.warehouse.session import open_upload_session

    csv = tmp_path / "u.csv"
    csv.write_text("secret_column,value\nconfidential,42\n")
    manager = SessionManager()
    session = manager.add(open_upload_session(csv, "u.csv", "csv"))
    server = build_server(manager)

    async def go() -> None:
        async with Client(server) as client:
            catalog = await client.read_resource("dataset://catalog")
            # An uploaded session must not appear in the public catalogue.
            assert session.session_id not in catalog.contents[0].text
            assert "secret_column" not in catalog.contents[0].text

            # Reading an uploaded session's schema by handle is refused
            # outright, because the handle alone is not a capability.
            from mcp.shared.exceptions import MCPError

            with pytest.raises(MCPError) as excinfo:
                await client.read_resource(f"dataset://schema/{session.session_id}/uploaded_data")
            assert "demo dataset only" in str(excinfo.value)
            assert "secret_column" not in str(excinfo.value)

    try:
        anyio.run(go)
    finally:
        manager.close_all()


def test_session_manager_requires_the_key(warehouse_dir: Path) -> None:
    manager = SessionManager()
    session = manager.add(open_demo_session(warehouse_dir))
    try:
        assert manager.get(session.session_id, session.session_key) is session
        for bad in (None, "", "wrong", session.session_id):
            with pytest.raises(KeyError):
                manager.get(session.session_id, bad)
    finally:
        manager.close_all()
