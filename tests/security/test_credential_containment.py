"""Where a provider credential must never appear.

The key is configuration, not data, so nothing in the engine has a reason
to render it. That makes containment testable directly: set a key with a
recognisable value, drive every surface that could echo configuration, and
assert the value and its meaningful fragments are absent.

Fragments matter as much as the whole. A log line that truncates a key to
its first twenty characters has still published twenty characters of a
secret, and an error that reports `sk-proj-abcd...` has named the project.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs
from typer.testing import CliRunner

from agentic_analytics.api.app import create_app
from agentic_analytics.cli import app as cli_app
from agentic_analytics.config import Settings
from agentic_analytics.llm.base import LLMError, LLMRequest
from agentic_analytics.llm.cloud import CloudProvider

REPO = Path(__file__).resolve().parents[2]

#: Distinctive enough that any substring of it is unambiguous.
KEY = "sk-proj-Zq7Wv3NnKdLpXr9TtYbM2Gh4JcF6sA8eR1uI0oP5"
REDIS_URL = "redis://admin:hunter2@cache.internal.example:6379/0"

#: Fragments that must not appear either. A prefix is still a secret.
FRAGMENTS = (KEY, KEY[:24], KEY[8:32], "hunter2", "cache.internal.example")


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
        "ai_analytics_enabled": True,
        "cloud_api_key": KEY,
        "cloud_model": "gpt-6-luna",
        "ai_quota_redis_url": REDIS_URL,
    }
    return _Pointed(**(defaults | overrides))


def _assert_clean(blob: str, where: str) -> None:
    for fragment in FRAGMENTS:
        assert fragment not in blob, f"{fragment[:12]}... leaked into {where}"


@pytest.fixture
def fake_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    import agentic_analytics.api.app as app_module
    from agentic_analytics.llm.ledger import CostLedger

    monkeypatch.setattr(
        app_module,
        "open_ledger",
        lambda url, namespace="aae:ai": CostLedger(fakeredis.FakeRedis()) if url else None,
    )


# --------------------------------------------------------------- the API
def test_the_config_endpoint_never_echoes_the_credential(
    warehouse_dir: Path, tmp_path: Path, fake_ledger: None
) -> None:
    """`/api/config` is public and describes the deployment."""
    with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
        payload = client.get("/api/config").json()
    _assert_clean(json.dumps(payload), "/api/config")


def test_the_health_and_readiness_endpoints_are_clean(
    warehouse_dir: Path, tmp_path: Path, fake_ledger: None
) -> None:
    with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
        blob = client.get("/api/health").text + client.get("/api/ready").text
    _assert_clean(blob, "health/readiness")


def test_a_failing_ai_run_reports_nothing_sensitive(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ledger: None
) -> None:
    """The error path is where configuration usually escapes.

    Preflight fails here with a message that contains the key, exactly as a
    careless provider error would, and the run record must not carry it.
    """
    import agentic_analytics.api.app as app_module

    async def explode(*args: object, **kwargs: object) -> None:
        raise LLMError(f"upstream said: bad key {KEY} for {REDIS_URL}")

    monkeypatch.setattr(app_module, "open_governed_cloud_provider", explode)

    cfg = _settings(warehouse_dir, tmp_path)
    with TestClient(create_app(cfg)) as client:
        session = client.post("/api/datasets/demo").json()["session_id"]
        started = client.post(
            "/api/analyses",
            json={"session_id": session, "question": "what changed?", "mode": "ai"},
        )
        assert started.status_code == 202
        run_id = started.json()["run_id"]
        import time

        blob = ""
        failed = False
        for _ in range(200):
            payload = client.get(f"/api/analyses/{run_id}").json()
            blob = json.dumps(payload)
            if payload.get("error"):
                failed = True
                break
            time.sleep(0.05)
    # Without this the loop could time out and the assertion below would
    # pass against an empty record, proving nothing.
    assert failed, f"the run never reported a failure: {blob[:200]}"
    _assert_clean(blob, "the run record")


# --------------------------------------------------------------- the CLI
def test_cloud_preflight_output_never_shows_the_credential(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one command whose whole job is to talk about the credential."""
    monkeypatch.setenv("AAE_AI_ANALYTICS_ENABLED", "true")
    monkeypatch.setenv("AAE_CLOUD_API_KEY", KEY)
    monkeypatch.setenv("AAE_CLOUD_MODEL", "gpt-6-luna")
    monkeypatch.setenv("AAE_AI_QUOTA_REDIS_URL", REDIS_URL)
    from agentic_analytics.config import get_settings

    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(cli_app, ["cloud-preflight"])
    finally:
        get_settings.cache_clear()
    # It will fail: the ledger is unreachable. The failure is the point --
    # this is the path that reports what could not be contacted.
    _assert_clean(result.output, "cloud-preflight output")


# ---------------------------------------------------------- the provider
async def test_the_provider_never_logs_its_own_headers() -> None:
    """The authorization header is constructed here and nowhere else."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"code": "boom", "message": KEY}})

    provider = CloudProvider(api_key=KEY, model="gpt-6-luna")
    provider._client = httpx.AsyncClient(
        base_url="http://cloud",
        transport=httpx.MockTransport(handler),
        headers={"authorization": f"Bearer {KEY}"},
    )
    with capture_logs() as logs:
        try:
            with pytest.raises(LLMError) as raised:
                await provider.complete_json(
                    LLMRequest(role="planner", system="s", user="u", schema={"type": "object"})
                )
        finally:
            await provider.aclose()
    _assert_clean(json.dumps(logs) + str(raised.value), "provider logs")


async def test_a_transport_exception_carrying_the_key_is_sanitised() -> None:
    """Some clients put the request URL, and its query, into the message."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed to connect: {KEY}", request=request)

    provider = CloudProvider(api_key=KEY, model="gpt-6-luna")
    provider._client = httpx.AsyncClient(
        base_url="http://cloud",
        transport=httpx.MockTransport(handler),
        headers={"authorization": f"Bearer {KEY}"},
    )
    try:
        with pytest.raises(LLMError) as raised:
            await provider.complete_json(
                LLMRequest(role="planner", system="s", user="u", schema={"type": "object"})
            )
    finally:
        await provider.aclose()
    _assert_clean(str(raised.value), "the sanitised error")


# ------------------------------------------------- deterministic isolation
def test_a_deterministic_run_never_reads_the_credential(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ledger: None
) -> None:
    """Deterministic analytics must work on a deployment with a broken key.

    Constructing the cloud provider at all would read and validate the
    credential, so a deterministic run is proven not to by making that
    construction fatal.
    """
    import agentic_analytics.llm.cloud as cloud_module

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("a deterministic run constructed the cloud provider")

    monkeypatch.setattr(cloud_module, "CloudProvider", explode)

    cfg = _settings(warehouse_dir, tmp_path)
    with TestClient(create_app(cfg)) as client:
        session = client.post("/api/datasets/demo").json()["session_id"]
        response = client.post(
            "/api/analyses",
            json={
                "session_id": session,
                "question": "total amount by city",
                "mode": "deterministic",
            },
        )
    assert response.status_code == 202


def test_the_frontend_bundle_contains_no_credential_shaped_value() -> None:
    """Whatever is built is served to every visitor."""
    dist = REPO / "web" / "dist"
    if not dist.exists():  # pragma: no cover - only when the build is absent
        pytest.skip("frontend is not built")
    for path in dist.rglob("*"):
        if path.is_file() and path.suffix in (".js", ".css", ".html", ".map"):
            text = path.read_text(errors="ignore")
            assert "sk-proj-" not in text, f"a credential-shaped value is in {path.name}"
            assert "AAE_CLOUD_API_KEY" not in text
