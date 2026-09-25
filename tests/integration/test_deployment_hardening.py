"""The things a hosted deployment needs that a local run never exercises.

Each test here corresponds to something that is correct locally and wrong
behind a TLS-terminating proxy, or on a box small enough that a session's
resource envelope matters.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.config import Settings
from agentic_analytics.warehouse.session import (
    EngineLimits,
    SessionManager,
    open_demo_session,
    open_upload_session,
)


def _settings(warehouse_dir: Path, tmp_path: Path, **overrides: object) -> Settings:
    """Settings pointed at the shared test warehouse.

    `demo_warehouse_dir` is a property that appends `commerce`, and the
    fixture's directory is already the warehouse, so the property is
    overridden on a throwaway subclass rather than fighting the path.
    """

    class _Pointed(Settings):
        @property
        def demo_warehouse_dir(self) -> Path:
            return warehouse_dir

    return _Pointed(
        upload_dir=tmp_path / "uploads",
        live_analytics_enabled=True,
        uploads_enabled=True,
        recordings_dir=Path(__file__).resolve().parents[2] / "examples" / "recordings",
        log_json=False,
        **overrides,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------- resources
def test_duckdb_limits_come_from_settings(warehouse_dir: Path) -> None:
    """The envelope is configuration, not a constant in the warehouse module."""
    limits = EngineLimits(memory_limit="256MB", threads=1)
    session = open_demo_session(warehouse_dir, limits=limits)
    try:
        applied = dict(
            session.con.execute(
                "select name, value from duckdb_settings() "
                "where name in ('memory_limit', 'threads')"
            ).fetchall()
        )
    finally:
        session.close()
    assert applied["threads"] == "1"
    # DuckDB reports the limit in its own units ("244.1 MiB" for 256MB). What
    # matters is that it is the configured envelope and not the gigabyte the
    # code used to hardcode.
    assert applied["memory_limit"].endswith("MiB"), applied["memory_limit"]
    assert float(applied["memory_limit"].split()[0]) < 400


def test_an_upload_session_gets_the_same_envelope(tmp_path: Path) -> None:
    csv = tmp_path / "x.csv"
    csv.write_text("a,b\n1,2\n3,4\n")
    session = open_upload_session(
        csv, "x.csv", "csv", limits=EngineLimits(memory_limit="128MB", threads=1)
    )
    try:
        threads = session.con.execute(
            "select value from duckdb_settings() where name = 'threads'"
        ).fetchone()
    finally:
        session.close()
    assert threads is not None and threads[0] == "1"


def test_the_environment_can_override_the_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """An override that is silently ignored is worse than no override."""
    monkeypatch.setenv("AAE_DUCKDB_MEMORY_LIMIT", "384MB")
    monkeypatch.setenv("AAE_DUCKDB_THREADS", "1")
    cfg = Settings()
    assert cfg.duckdb_memory_limit == "384MB"
    assert cfg.duckdb_threads == 1
    assert EngineLimits.from_settings(cfg) == EngineLimits("384MB", 1)


def test_an_unparseable_memory_limit_is_rejected_at_startup() -> None:
    with pytest.raises(ValueError, match="duckdb_memory_limit"):
        Settings(duckdb_memory_limit="as much as you like")


def test_deployment_ceilings_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AAE_BUDGETS__MAX_UPLOAD_ROWS", "750000")
    monkeypatch.setenv("AAE_MAX_CONCURRENT_SESSIONS", "12")
    monkeypatch.setenv("AAE_SESSION_TTL_SECONDS", "1800")
    cfg = Settings()
    assert cfg.budgets.max_upload_rows == 750_000
    assert cfg.max_concurrent_sessions == 12
    assert cfg.session_ttl_seconds == 1800


# ----------------------------------------------------------------- expiry
def test_expire_stale_closes_sessions_without_another_request(
    warehouse_dir: Path,
) -> None:
    """TTL has to be enforced on a clock, not on the next visitor."""
    manager = SessionManager(ttl_seconds=0.05)
    session = manager.add(open_demo_session(warehouse_dir))
    time.sleep(0.1)
    assert manager.expire_stale() == 1
    with pytest.raises(KeyError):
        manager.get(session.session_id, session.session_key)


def test_expire_stale_deletes_the_scratch_directory(tmp_path: Path) -> None:
    """The bytes have to go, not just the handle."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    csv = scratch / "x.csv"
    csv.write_text("a,b\n1,2\n3,4\n")
    manager = SessionManager(ttl_seconds=0.05)
    manager.add(open_upload_session(csv, "x.csv", "csv", scratch_dir=scratch))
    assert scratch.exists()
    time.sleep(0.1)
    assert manager.expire_stale() == 1
    assert not scratch.exists()


def test_expire_stale_leaves_live_sessions_alone(warehouse_dir: Path) -> None:
    manager = SessionManager(ttl_seconds=600)
    session = manager.add(open_demo_session(warehouse_dir))
    assert manager.expire_stale() == 0
    assert manager.get(session.session_id, session.session_key) is session
    manager.close_all()


async def test_the_janitor_runs_on_a_timer(warehouse_dir: Path, tmp_path: Path) -> None:
    """No request arrives after the session opens; it must still be reaped."""
    cfg = _settings(warehouse_dir, tmp_path, session_ttl_seconds=0.05, session_sweep_seconds=0.05)
    app = create_app(cfg)
    with TestClient(app) as client:
        opened = client.post("/api/datasets/demo").json()
        # Deliberately no further requests: a lazy sweep would never fire.
        await asyncio.sleep(0.4)
        client.cookies.clear()
        refused = client.get(f"/api/datasets/{opened['session_id']}")
    assert refused.status_code == 404


async def test_a_failing_sweep_does_not_take_the_app_down(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def boom(self: SessionManager) -> int:
        calls["n"] += 1
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(SessionManager, "expire_stale", boom)
    cfg = _settings(warehouse_dir, tmp_path, session_sweep_seconds=0.05)
    with TestClient(create_app(cfg)) as client:
        await asyncio.sleep(0.25)
        assert client.get("/api/health").status_code == 200
    assert calls["n"] >= 2, "the janitor stopped after the first failure"


def test_one_browser_does_not_accumulate_unreachable_sessions(
    warehouse_dir: Path, tmp_path: Path
) -> None:
    """Opening a dataset replaces the cookie, so the old session must end."""
    with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
        first = client.post("/api/datasets/demo").json()["session_id"]
        second = client.post("/api/datasets/demo").json()["session_id"]
        assert first != second
        # The browser now holds the second capability; the first session is
        # gone rather than lingering until its TTL.
        assert client.get(f"/api/datasets/{first}").status_code == 404
        assert client.get(f"/api/datasets/{second}").status_code == 200


def test_retiring_a_session_cannot_touch_another_visitor(
    warehouse_dir: Path, tmp_path: Path
) -> None:
    """Retirement is keyed on the capability, so it is per browser.

    One client with two cookie jars rather than two clients: the MCP session
    manager can only be started once per app instance, and two apps would
    not share the session manager this test is about.
    """
    with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
        alice_session = client.post("/api/datasets/demo").json()["session_id"]
        alice_key = client.cookies["aae_session"]

        client.cookies.clear()  # a second browser arrives
        client.post("/api/datasets/demo")
        client.post("/api/datasets/demo")  # which retires its own first session

        client.cookies.clear()
        still_there = client.get(
            f"/api/datasets/{alice_session}", headers={"Cookie": f"aae_session={alice_key}"}
        )
    assert still_there.status_code == 200


# ----------------------------------------------------------------- cookie
def test_the_cookie_is_not_secure_for_local_development(
    warehouse_dir: Path, tmp_path: Path
) -> None:
    with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
        response = client.post("/api/datasets/demo")
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie
    assert "Domain" not in cookie


def test_the_cookie_is_secure_when_configured_even_over_plain_http(
    warehouse_dir: Path, tmp_path: Path
) -> None:
    """The proxy terminates TLS, so the app sees http on an https site.

    Deciding `Secure` from `request.url.scheme` drops the flag on exactly the
    deployment that needs it.
    """
    cfg = _settings(warehouse_dir, tmp_path, session_cookie_secure=True)
    with TestClient(create_app(cfg)) as client:
        response = client.post("/api/datasets/demo")
    assert client.base_url.scheme == "http"
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie
    assert "HttpOnly" in cookie


def test_deleting_the_cookie_uses_the_same_attributes(warehouse_dir: Path, tmp_path: Path) -> None:
    """A deletion that differs on Secure leaves the cookie in the browser."""
    cfg = _settings(warehouse_dir, tmp_path, session_cookie_secure=True)
    # Over https, because a client will not return a Secure cookie to an
    # http origin -- which is the flag doing its job.
    with TestClient(create_app(cfg), base_url="https://testserver") as client:
        session_id = client.post("/api/datasets/demo").json()["session_id"]
        response = client.delete(f"/api/datasets/{session_id}")
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/" in cookie


def test_the_capability_never_appears_in_a_response_body(
    warehouse_dir: Path, tmp_path: Path
) -> None:
    with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
        response = client.post("/api/datasets/demo")
        key = client.cookies.get("aae_session")
        assert key
        assert key not in response.text
        assert key not in client.get(f"/api/datasets/{response.json()['session_id']}").text


# ---------------------------------------------------------------- headers
def test_private_api_responses_are_not_cacheable(warehouse_dir: Path, tmp_path: Path) -> None:
    with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
        opened = client.post("/api/datasets/demo")
        session_id = opened.json()["session_id"]
        started = client.post(
            "/api/analyses",
            json={"session_id": session_id, "question": "Describe the dataset"},
        )
        run_id = started.json()["run_id"]
        paths = [
            "/api/config",
            f"/api/datasets/{session_id}",
            f"/api/analyses/{run_id}",
        ]
        for path in paths:
            response = client.get(path)
            assert response.headers["cache-control"] == "no-store", path
            assert response.headers["pragma"] == "no-cache", path

        with client.stream("GET", f"/api/analyses/{run_id}/events") as stream:
            assert "no-store" in stream.headers["cache-control"]


def test_browser_security_headers_are_set(warehouse_dir: Path, tmp_path: Path) -> None:
    with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
        headers = client.get("/api/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "same-origin"
    assert headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_the_csp_does_not_restrict_scripts(warehouse_dir: Path, tmp_path: Path) -> None:
    """Vega compiles expressions with `new Function`.

    A `script-src` policy would need `unsafe-eval` to keep charts working,
    which buys nothing. This asserts the policy stays out of that business
    rather than silently breaking rendering.
    """
    with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
        policy = client.get("/api/health").headers["content-security-policy"]
    assert "script-src" not in policy
    assert "unsafe-eval" not in policy


def test_there_is_no_wildcard_cors(warehouse_dir: Path, tmp_path: Path) -> None:
    """The API authorises on an ambient cookie; `*` would undo that."""
    with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
        response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_the_same_origin_flow_still_works_without_cors(warehouse_dir: Path, tmp_path: Path) -> None:
    """Removing CORS must not break the frontend it was never needed for."""
    with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
        opened = client.post("/api/datasets/demo", headers={"Origin": "http://testserver"})
        assert opened.status_code == 200
        session_id = opened.json()["session_id"]
        assert client.get(f"/api/datasets/{session_id}").status_code == 200
        assert client.delete(f"/api/datasets/{session_id}").status_code == 200
