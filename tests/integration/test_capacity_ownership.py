"""Who owns a concurrency slot, and when they give it back.

A slot is acquired by an HTTP handler and released by a background task, so
ownership changes hands in the middle of a request. Every way a run can
fail to start is therefore a way to lose a slot permanently, and a lost
slot is invisible: the deployment simply serves fewer visitors than it is
configured to, and nothing logs an error.

The rule is that a request which does not start a run must leave capacity
exactly as it found it. These tests assert that by the only means that
proves it -- configuring a single slot, provoking the failure, and then
checking that a real run can still start.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.config import Settings
from agentic_analytics.llm.fake import FakeProvider
from agentic_analytics.llm.governed import PreflightFailed

REPO = Path(__file__).resolve().parents[2]


def _settings(warehouse_dir: Path, tmp_path: Path, **overrides: Any) -> Settings:
    class _Pointed(Settings):
        @property
        def demo_warehouse_dir(self) -> Path:
            return warehouse_dir

    defaults: dict[str, Any] = {
        "upload_dir": tmp_path / "uploads",
        "recordings_dir": REPO / "examples" / "recordings",
        "live_analytics_enabled": True,
        "uploads_enabled": True,
        "log_json": False,
    }
    return _Pointed(**(defaults | overrides))


def _ai_settings(warehouse_dir: Path, tmp_path: Path, **overrides: Any) -> Settings:
    """A deployment that believes it can serve AI runs."""
    return _settings(
        warehouse_dir,
        tmp_path,
        ai_analytics_enabled=True,
        cloud_api_key="sk-test-not-a-real-key",
        cloud_model="gpt-6-luna",
        ai_quota_redis_url="redis://localhost:6379/0",
        **overrides,
    )


@pytest.fixture
def fake_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    """A healthy ledger that never touches a network."""
    import agentic_analytics.api.app as app_module
    from agentic_analytics.llm.ledger import CostLedger

    monkeypatch.setattr(
        app_module,
        "open_ledger",
        lambda url, namespace="aae:ai": CostLedger(fakeredis.FakeRedis()) if url else None,
    )


def _hang_every_run(monkeypatch: pytest.MonkeyPatch, started: threading.Event) -> None:
    """Make every analysis hang until cancelled, so a slot stays held.

    A `threading.Event`, not an asyncio one: `TestClient` drives the app on
    its own event loop, so a flag set there is not observable by awaiting
    in the test's loop.
    """

    async def hang(*args: Any, **kwargs: Any) -> Any:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("agentic_analytics.api.app.run_analysis", hang)


def _stub_governed_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """A governed provider that resolves without touching the network.

    Preflight is a real HTTP round trip, and these tests are about slots,
    not about the provider. The stub keeps the attributes `execute()` reads
    off a governed provider so the capacity path runs unchanged.
    """

    class _Price:
        source = "test"
        reviewed = "2026-09-27"

    class _Preflight:
        requested_model = "gpt-6-luna"
        resolved_model = "gpt-6-luna"
        price = _Price()

    class _Stub(FakeProvider):
        remote_inference = True
        preflight_result = _Preflight()

    async def build(*args: object, **kwargs: object) -> _Stub:
        return _Stub()

    monkeypatch.setattr("agentic_analytics.api.app.open_governed_cloud_provider", build)


def _compare(client: TestClient, session: str) -> Any:
    return client.post(
        "/api/comparisons",
        json={"session_id": session, "question": "what changed?"},
    )


def _start(client: TestClient, session: str, mode: str) -> Any:
    return client.post(
        "/api/analyses",
        json={"session_id": session, "question": "what changed?", "mode": mode},
    )


def _deterministic_run_still_starts(client: TestClient, attempts: int = 100) -> None:
    """The probe: one slot is configured, so this passes only if it came back.

    Retried, because a slot released inside a background task comes back a
    moment after the request that provoked the failure returned. A leaked
    slot never comes back, so the loop distinguishes the two rather than
    racing them.
    """
    session = client.post("/api/datasets/demo").json()["session_id"]
    response = _start(client, session, "deterministic")
    for _ in range(attempts):
        if response.status_code == 202:
            return
        time.sleep(0.05)
        response = _start(client, session, "deterministic")
    raise AssertionError(f"a slot leaked: {response.status_code} {response.text[:200]}")


def test_repeated_unavailable_ai_requests_do_not_consume_capacity(
    warehouse_dir: Path, tmp_path: Path
) -> None:
    """The original leak: refuse after acquiring, and every 503 costs a slot.

    AI is off here, so all ten requests are refused. With one slot
    configured, a single un-released acquisition is enough to close the
    deployment to everyone.
    """
    cfg = _settings(warehouse_dir, tmp_path, max_concurrent_analyses=1)
    with TestClient(create_app(cfg)) as client:
        session = client.post("/api/datasets/demo").json()["session_id"]
        for _ in range(10):
            response = _start(client, session, "ai")
            assert response.status_code == 503
            assert response.headers["X-AAE-Reason"] == "ai_disabled"
        _deterministic_run_still_starts(client)


def test_an_unreachable_ledger_refuses_ai_without_taking_a_slot(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Usage accounting down means AI is not offered, not that it is free."""
    import agentic_analytics.api.app as app_module

    monkeypatch.setattr(app_module, "open_ledger", lambda *a, **k: None)
    cfg = _ai_settings(warehouse_dir, tmp_path, max_concurrent_analyses=1)
    with TestClient(create_app(cfg)) as client:
        session = client.post("/api/datasets/demo").json()["session_id"]
        for _ in range(5):
            response = _start(client, session, "ai")
            assert response.status_code == 503
            assert response.headers["X-AAE-Reason"] == "ai_quota_storage_unavailable"
        _deterministic_run_still_starts(client)


def test_a_failed_preflight_returns_the_slot(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ledger: None
) -> None:
    """Preflight runs inside the task, after the slot has changed hands.

    This is the hand-off the `finally` exists for: the handler has already
    returned 202, so nothing else is in a position to give the slot back.
    """
    import agentic_analytics.api.app as app_module

    async def refuse(*args: object, **kwargs: object) -> None:
        raise PreflightFailed("the model could not be resolved")

    monkeypatch.setattr(app_module, "open_governed_cloud_provider", refuse)

    cfg = _ai_settings(warehouse_dir, tmp_path, max_concurrent_analyses=1)
    with TestClient(create_app(cfg)) as client:
        session = client.post("/api/datasets/demo").json()["session_id"]
        assert _start(client, session, "ai").status_code == 202
        # The handler already returned 202, so nothing but the task's own
        # `finally` can give this slot back.
        _deterministic_run_still_starts(client)


def test_a_request_refused_at_capacity_leaves_the_holder_untouched(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 429 must not release the slot the *running* analysis holds.

    Over-releasing is the mirror of leaking and is worse: capacity climbs
    above the configured ceiling and the limit silently stops being one.
    With a single slot held by a hanging run, every later request must be
    refused -- one accepted request would mean a refusal handed out a slot
    it never owned.
    """
    started = threading.Event()
    _hang_every_run(monkeypatch, started)

    cfg = _settings(warehouse_dir, tmp_path, max_concurrent_analyses=1)
    with TestClient(create_app(cfg)) as client:
        session = client.post("/api/datasets/demo").json()["session_id"]
        assert _start(client, session, "deterministic").status_code == 202
        assert started.wait(5)

        for attempt in range(6):
            assert _start(client, session, "deterministic").status_code == 429, (
                f"attempt {attempt} was admitted, so a refusal released a slot it did not hold"
            )


def test_a_comparison_refused_at_capacity_does_not_leak_either_slot(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ledger: None
) -> None:
    """A comparison acquires twice, so it can leak twice.

    With one slot, already held, the comparison cannot start at all. The
    deployment must be neither poorer nor richer afterwards.
    """
    started = threading.Event()
    _hang_every_run(monkeypatch, started)

    cfg = _ai_settings(warehouse_dir, tmp_path, max_concurrent_analyses=1)
    with TestClient(create_app(cfg)) as client:
        session = client.post("/api/datasets/demo").json()["session_id"]
        assert _start(client, session, "deterministic").status_code == 202
        assert started.wait(5)

        for _ in range(5):
            assert _compare(client, session).status_code == 429

        # Neither poorer (the held slot is still the only one) nor richer.
        assert _start(client, session, "deterministic").status_code == 429


def test_a_comparison_short_of_ai_capacity_returns_the_analysis_slot(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ledger: None
) -> None:
    """The fiddly acquisition: take an analysis slot, fail to get the AI
    slot, and give the analysis slot back.

    The AI half is then refused in the payload rather than by silently
    costing the deployment a slot. Three analysis slots exist; the hanging
    AI run holds one and the comparison's deterministic half holds another,
    so exactly one must remain.
    """
    started = threading.Event()
    _hang_every_run(monkeypatch, started)
    _stub_governed_provider(monkeypatch)

    cfg = _ai_settings(warehouse_dir, tmp_path, max_concurrent_analyses=3, ai_concurrent_runs=1)
    with TestClient(create_app(cfg)) as client:
        session = client.post("/api/datasets/demo").json()["session_id"]
        assert _start(client, session, "ai").status_code == 202  # the only AI slot
        assert started.wait(5)

        response = _compare(client, session)
        assert response.status_code == 202
        body = response.json()
        assert body["deterministic_run_id"]
        assert body["ai_run_id"], "the refused half still gets a run id to report"

        # Exactly one analysis slot left: the AI half's was returned.
        assert _start(client, session, "deterministic").status_code == 202
        assert _start(client, session, "deterministic").status_code == 429


def test_the_refused_ai_half_is_reported_as_at_capacity(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ledger: None
) -> None:
    """A visitor is told which half did not run, and why."""
    started = threading.Event()
    _hang_every_run(monkeypatch, started)
    _stub_governed_provider(monkeypatch)

    cfg = _ai_settings(warehouse_dir, tmp_path, max_concurrent_analyses=3, ai_concurrent_runs=1)
    with TestClient(create_app(cfg)) as client:
        session = client.post("/api/datasets/demo").json()["session_id"]
        assert _start(client, session, "ai").status_code == 202
        assert started.wait(5)

        comparison_id = _compare(client, session).json()["comparison_id"]
        payload = client.get(f"/api/comparisons/{comparison_id}").json()

    rendered = str(payload)
    assert "at capacity" in rendered
    assert "sk-test-not-a-real-key" not in rendered
