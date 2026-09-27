"""The provider is chosen per run, not per process.

The server used to pick one provider at startup, so a deployment could offer
deterministic analytics or AI analytics but not both. The mode now arrives
with the request, from a closed set the browser cannot widen.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.api.modes import (
    AI_DISABLED,
    AI_NOT_CONFIGURED,
    AI_QUOTA_UNAVAILABLE,
    AI_REQUIRES_GOVERNED_BUILD,
    ModeUnavailable,
    RunMode,
    ai_availability,
    build_provider_for_mode,
    provider_kind,
)
from agentic_analytics.config import Settings


def _settings(**kw: object) -> Settings:
    return Settings(provider_mode="fake", log_json=False, live_analytics_enabled=True, **kw)


@pytest.fixture
def client(demo_data_dir: Path) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(data_dir=demo_data_dir))) as c:
        yield c


# ------------------------------------------------------ provider selection
def test_deterministic_builds_only_the_scripted_provider() -> None:
    provider = build_provider_for_mode(_settings(), RunMode.DETERMINISTIC)
    assert provider.name == "fake"
    assert provider_kind(RunMode.DETERMINISTIC) == "scripted"


def test_deterministic_works_with_no_cloud_key_and_no_ledger() -> None:
    """A broken or absent AI configuration must not disable deterministic."""
    cfg = _settings(cloud_api_key=None, ai_analytics_enabled=False)
    provider = build_provider_for_mode(cfg, RunMode.DETERMINISTIC, ledger_ready=False)
    assert provider.name == "fake"


def test_deterministic_never_reads_the_cloud_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructing a cloud provider would validate a key it must not need."""
    import agentic_analytics.llm.cloud as cloud

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("a deterministic run constructed the cloud provider")

    monkeypatch.setattr(cloud, "CloudProvider", explode)
    build_provider_for_mode(_settings(cloud_api_key="sk-should-not-be-read"), RunMode.DETERMINISTIC)


@pytest.mark.parametrize(
    ("kwargs", "ledger_ready", "reason"),
    [
        ({"ai_analytics_enabled": False}, True, AI_DISABLED),
        ({"ai_analytics_enabled": True, "cloud_api_key": None}, True, AI_NOT_CONFIGURED),
        ({"ai_analytics_enabled": True, "cloud_api_key": "k"}, False, AI_QUOTA_UNAVAILABLE),
    ],
)
def test_ai_is_refused_before_any_paid_call(
    kwargs: dict[str, object], ledger_ready: bool, reason: str
) -> None:
    cfg = _settings(**kwargs)
    assert ai_availability(cfg, ledger_ready=ledger_ready).reason == reason
    with pytest.raises(ModeUnavailable) as raised:
        build_provider_for_mode(cfg, RunMode.AI, ledger_ready=ledger_ready)
    assert raised.value.reason == reason


def test_a_paid_provider_cannot_be_built_without_its_ledger() -> None:
    """Even a perfectly configured deployment is refused here.

    A cloud provider built outside `open_governed_cloud_provider` has no
    reservation, no run ceiling and no reconciliation, so it is a run that
    spends without a bound. The refusal names that rather than pretending
    the deployment is misconfigured.
    """
    cfg = _settings(ai_analytics_enabled=True, cloud_api_key="k", cloud_model="claude-sonnet-5")
    assert ai_availability(cfg).available is True
    with pytest.raises(ModeUnavailable) as raised:
        build_provider_for_mode(cfg, RunMode.AI)
    assert raised.value.reason == AI_REQUIRES_GOVERNED_BUILD
    assert provider_kind(RunMode.AI) == "cloud"


# -------------------------------------------------------------- the request
def test_the_browser_cannot_name_a_provider(client: TestClient) -> None:
    session = client.post("/api/datasets/demo").json()["session_id"]
    for attempt in ("cloud", "local", "fake", "openai", "http://evil.example"):
        response = client.post(
            "/api/analyses",
            json={"session_id": session, "question": "What is total revenue?", "mode": attempt},
        )
        assert response.status_code == 422, attempt


def test_an_analysis_defaults_to_deterministic(client: TestClient) -> None:
    session = client.post("/api/datasets/demo").json()["session_id"]
    started = client.post(
        "/api/analyses", json={"session_id": session, "question": "What is total revenue?"}
    )
    assert started.status_code == 202
    run = client.get(f"/api/analyses/{started.json()['run_id']}").json()
    assert run["mode"] == "deterministic"
    assert run["provider_kind"] == "scripted"


def test_ai_is_refused_with_a_sanitized_reason(client: TestClient) -> None:
    session = client.post("/api/datasets/demo").json()["session_id"]
    response = client.post(
        "/api/analyses",
        json={"session_id": session, "question": "What is total revenue?", "mode": "ai"},
    )
    assert response.status_code == 503
    body = response.text
    assert "AI Analytics" in response.json()["detail"]
    for leak in ("sk-", "redis://", "Traceback", "api_key"):
        assert leak not in body


def test_a_finished_run_records_how_it_was_executed(client: TestClient) -> None:
    """Never inferred from the server's current configuration."""
    session = client.post("/api/datasets/demo").json()["session_id"]
    run_id = client.post(
        "/api/analyses", json={"session_id": session, "question": "What is total revenue?"}
    ).json()["run_id"]
    run = client.get(f"/api/analyses/{run_id}").json()
    assert run["mode"] == "deterministic"
    assert run["engine_version"]
    assert run["dataset_fingerprint"]
    assert "requested_model" not in run
    assert "resolved_model" not in run


# ------------------------------------------------------------ capabilities
def test_capabilities_describe_both_modes(client: TestClient) -> None:
    caps = client.get("/api/config").json()["capabilities"]
    modes = {m["mode"]: m for m in caps["modes"]}
    assert set(modes) == {"deterministic", "ai"}
    assert modes["deterministic"]["available"] is True
    assert modes["ai"]["available"] is False
    assert modes["ai"]["reason"] == AI_DISABLED
    assert caps["compare_available"] is False
    assert caps["ai_limits"] is None


def test_capabilities_never_carry_a_secret(demo_data_dir: Path) -> None:
    cfg = _settings(
        data_dir=demo_data_dir,
        ai_analytics_enabled=True,
        cloud_api_key="sk-ant-secret-value",
        ai_quota_redis_url="redis://user:password@example.internal:6379/0",
    )
    with TestClient(create_app(cfg)) as client:
        body = client.get("/api/config").text
    for leak in ("sk-ant-secret-value", "password", "example.internal", "redis://"):
        assert leak not in body, leak


def test_public_copy_never_calls_deterministic_mode_fake(client: TestClient) -> None:
    caps = client.get("/api/config").json()["capabilities"]
    text = " ".join(f"{m['label']} {m['description']} {m['message']}" for m in caps["modes"])
    assert "fake" not in text.lower()
    assert "Deterministic Analytics" in text
    assert "scripted provider" in text
