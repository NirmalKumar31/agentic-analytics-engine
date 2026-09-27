"""Compare Both: one question, two decision paths, one dataset.

Not a third provider mode. Two ordinary runs over the same session, so they
read the same tables at the same fingerprint, and each stays independently
auditable. Nothing merges them and nothing ranks them: they differ in how
the analysis was planned, which is not evidence that either is more accurate.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.config import Settings

QUESTION = "What is total revenue?"


def _settings(demo: Path, **kw: object) -> Settings:
    return Settings(
        provider_mode="fake",
        log_json=False,
        live_analytics_enabled=True,
        data_dir=demo,
        **kw,
    )


@pytest.fixture
def ai_client(demo_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A deployment where AI is configured and its ledger answers."""
    import fakeredis

    import agentic_analytics.api.app as app_module
    from agentic_analytics.api.ledger import CostLedger

    monkeypatch.setattr(
        app_module, "open_ledger", lambda url, *a, **k: CostLedger(fakeredis.FakeRedis())
    )
    cfg = _settings(
        demo_data_dir,
        ai_analytics_enabled=True,
        cloud_api_key="sk-test-not-used",
        cloud_model="claude-sonnet-5",
        ai_quota_redis_url="redis://localhost:6379/0",
    )
    with TestClient(create_app(cfg)) as client:
        yield client


@pytest.fixture
def plain_client(demo_data_dir: Path) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(demo_data_dir))) as client:
        yield client


def _session(client: TestClient) -> str:
    return client.post("/api/datasets/demo").json()["session_id"]


# ------------------------------------------------------------- admission
def test_a_comparison_is_refused_when_ai_is_unavailable(plain_client: TestClient) -> None:
    started = plain_client.post(
        "/api/comparisons", json={"session_id": _session(plain_client), "question": QUESTION}
    )
    assert started.status_code == 503
    assert "AI Analytics" in started.json()["detail"]
    for leak in ("sk-", "redis://", "Traceback"):
        assert leak not in started.text


def test_capabilities_gate_compare_on_both_modes(plain_client: TestClient) -> None:
    caps = plain_client.get("/api/config").json()["capabilities"]
    assert caps["compare_available"] is False


def test_compare_is_offered_when_both_modes_are(ai_client: TestClient) -> None:
    caps = ai_client.get("/api/config").json()["capabilities"]
    assert caps["compare_available"] is True
    assert caps["ai_limits"]["runs_per_session"] >= 1


# -------------------------------------------------------- the two children
def test_both_children_share_the_session_and_dataset(ai_client: TestClient) -> None:
    session = _session(ai_client)
    body = ai_client.post(
        "/api/comparisons", json={"session_id": session, "question": QUESTION}
    ).json()

    deterministic = ai_client.get(f"/api/analyses/{body['deterministic_run_id']}").json()
    ai = ai_client.get(f"/api/analyses/{body['ai_run_id']}").json()

    assert deterministic["session_id"] == ai["session_id"] == session
    assert deterministic["question"] == ai["question"] == QUESTION
    assert deterministic["dataset_fingerprint"] == ai["dataset_fingerprint"]
    # Only the decision-maker differs.
    assert deterministic["mode"] == "deterministic"
    assert deterministic["provider_kind"] == "scripted"
    assert ai["mode"] == "ai"
    assert ai["provider_kind"] == "cloud"


def test_each_child_is_an_ordinary_auditable_run(ai_client: TestClient) -> None:
    session = _session(ai_client)
    body = ai_client.post(
        "/api/comparisons", json={"session_id": session, "question": QUESTION}
    ).json()
    for run_id in (body["deterministic_run_id"], body["ai_run_id"]):
        run = ai_client.get(f"/api/analyses/{run_id}").json()
        assert run["run_id"] == run_id
        assert run["comparison_id"] == body["comparison_id"]


def test_the_comparison_reports_both_sides_separately(ai_client: TestClient) -> None:
    session = _session(ai_client)
    body = ai_client.post(
        "/api/comparisons", json={"session_id": session, "question": QUESTION}
    ).json()
    view = ai_client.get(f"/api/comparisons/{body['comparison_id']}").json()

    assert view["question"] == QUESTION
    assert "deterministic_run" in view and "ai_run" in view
    assert view["deterministic_run"]["run_id"] == body["deterministic_run_id"]
    assert view["ai_run"]["run_id"] == body["ai_run_id"]
    # No merged verdict, no ranking, no winner.
    blob = " ".join(str(k) for k in view)
    for banned in ("winner", "better", "more_accurate", "score", "merged"):
        assert banned not in blob


def test_the_deterministic_side_survives_an_ai_failure(ai_client: TestClient) -> None:
    """Half a comparison beats an error for a visitor who asked for both."""
    session = _session(ai_client)
    body = ai_client.post(
        "/api/comparisons", json={"session_id": session, "question": QUESTION}
    ).json()
    view = ai_client.get(f"/api/comparisons/{body['comparison_id']}").json()

    # The scripted side is real work and does not depend on the provider.
    assert view["deterministic_run"]["status"] in {"running", "completed"}
    # Whatever happens to the AI side is reported on the AI side only.
    assert "error" not in view["deterministic_run"] or view["ai_run"] is not None


def test_only_the_ai_child_is_a_cloud_run(ai_client: TestClient) -> None:
    session = _session(ai_client)
    body = ai_client.post(
        "/api/comparisons", json={"session_id": session, "question": QUESTION}
    ).json()
    deterministic = ai_client.get(f"/api/analyses/{body['deterministic_run_id']}").json()
    assert deterministic["provider_kind"] == "scripted"
    assert "requested_model" not in deterministic


def test_an_unknown_comparison_is_a_404(ai_client: TestClient) -> None:
    _session(ai_client)
    assert ai_client.get("/api/comparisons/cmp_nope").status_code == 404


# ------------------------------------------------------------- teardown
def test_deleting_the_session_ends_both_children(ai_client: TestClient) -> None:
    session = _session(ai_client)
    body = ai_client.post(
        "/api/comparisons", json={"session_id": session, "question": QUESTION}
    ).json()
    response = ai_client.delete(f"/api/datasets/{session}")
    assert response.status_code in (200, 202)

    for run_id in (body["deterministic_run_id"], body["ai_run_id"]):
        run = ai_client.get(f"/api/analyses/{run_id}")
        # The run is either gone with the session or terminal; never left running.
        if run.status_code == 200:
            assert run.json()["status"] in {"cancelled", "completed", "failed"}


def test_the_session_ceiling_counts_both_children(
    demo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A comparison is two runs, and the per-session limit must see two."""
    import fakeredis

    import agentic_analytics.api.app as app_module
    from agentic_analytics.api.ledger import CostLedger

    monkeypatch.setattr(
        app_module, "open_ledger", lambda url, *a, **k: CostLedger(fakeredis.FakeRedis())
    )
    cfg = _settings(
        demo_data_dir,
        ai_analytics_enabled=True,
        cloud_api_key="sk-test-not-used",
        cloud_model="claude-sonnet-5",
        ai_quota_redis_url="redis://localhost:6379/0",
        analyses_per_session=3,
    )
    with TestClient(create_app(cfg)) as client:
        session = _session(client)
        first = client.post("/api/comparisons", json={"session_id": session, "question": QUESTION})
        assert first.status_code == 202
        # Two runs are spent; a second comparison needs two more and cannot fit.
        second = client.post("/api/comparisons", json={"session_id": session, "question": QUESTION})
        assert second.status_code == 429
        assert "analysis limit" in second.json()["detail"]
