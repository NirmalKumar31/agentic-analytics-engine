"""`/api/config` must not contradict itself.

Three fields describe what this deployment does with a dataset:
`execution_mode` drives the badge, `model_inference_remote` drives the
sentence a visitor reads before uploading a file, and `capabilities` drives
the mode selector. They were derived from two different sources -- the
first two from the process-wide `AAE_PROVIDER_MODE`, the third from what
the deployment can actually offer -- and the product moved the decision to
the run, which left the process setting describing nothing.

The result was not a cosmetic mismatch. A deployment with AI enabled and
the process default left at `fake` told a visitor uploading a spreadsheet
that "nothing derived from your file is sent to an external model
provider", and then offered them an AI run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.config import Settings

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


@pytest.fixture
def fake_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    import agentic_analytics.api.app as app_module
    from agentic_analytics.llm.ledger import CostLedger

    monkeypatch.setattr(
        app_module,
        "open_ledger",
        lambda url, namespace="aae:ai": CostLedger(fakeredis.FakeRedis()) if url else None,
    )


def _config(cfg: Settings) -> dict[str, Any]:
    with TestClient(create_app(cfg)) as client:
        return client.get("/api/config").json()


def _ai_available(payload: dict[str, Any]) -> bool:
    modes = {m["mode"]: m for m in payload["capabilities"]["modes"]}
    return bool(modes["ai"]["available"])


#: Every deployment shape the product supports, by the settings that
#: distinguish them. `provider_mode` is varied deliberately: it must no
#: longer change any of these answers.
DEPLOYMENTS: list[tuple[str, dict[str, Any], bool]] = [
    ("deterministic only", {"provider_mode": "fake"}, False),
    ("deterministic, process set to local", {"provider_mode": "local"}, False),
    (
        "ai enabled, process default left at fake",
        {
            "provider_mode": "fake",
            "ai_analytics_enabled": True,
            "cloud_api_key": "sk-test-not-a-real-key",
            "cloud_model": "claude-sonnet-5",
            "ai_quota_redis_url": "redis://localhost:6379/0",
        },
        True,
    ),
    (
        "ai enabled, process set to cloud",
        {
            "provider_mode": "cloud",
            "ai_analytics_enabled": True,
            "cloud_api_key": "sk-test-not-a-real-key",
            "cloud_model": "claude-sonnet-5",
            "ai_quota_redis_url": "redis://localhost:6379/0",
        },
        True,
    ),
    (
        "ai configured but switched off",
        {
            "provider_mode": "cloud",
            "ai_analytics_enabled": False,
            "cloud_api_key": "sk-test-not-a-real-key",
            "cloud_model": "claude-sonnet-5",
        },
        False,
    ),
]


@pytest.mark.parametrize(
    ("name", "overrides", "expect_ai"),
    DEPLOYMENTS,
    ids=[d[0] for d in DEPLOYMENTS],
)
def test_the_privacy_claim_matches_what_the_selector_offers(
    name: str,
    overrides: dict[str, Any],
    expect_ai: bool,
    warehouse_dir: Path,
    tmp_path: Path,
    fake_ledger: None,
) -> None:
    """`model_inference_remote` is a promise, so it must be the strong one.

    False means nothing derived from a dataset leaves this server in any
    mode a visitor can pick. If AI is on the selector, that is not true.
    """
    payload = _config(_settings(warehouse_dir, tmp_path, **overrides))
    assert _ai_available(payload) is expect_ai, name
    assert payload["model_inference_remote"] is _ai_available(payload), name


@pytest.mark.parametrize(
    ("name", "overrides", "expect_ai"),
    DEPLOYMENTS,
    ids=[d[0] for d in DEPLOYMENTS],
)
def test_the_badge_matches_what_the_selector_offers(
    name: str,
    overrides: dict[str, Any],
    expect_ai: bool,
    warehouse_dir: Path,
    tmp_path: Path,
    fake_ledger: None,
) -> None:
    payload = _config(_settings(warehouse_dir, tmp_path, **overrides))
    expected = "ai_live" if expect_ai else "deterministic_live"
    assert payload["execution_mode"] == expected, name


def test_the_process_setting_no_longer_changes_any_answer(
    warehouse_dir: Path, tmp_path: Path, fake_ledger: None
) -> None:
    """The defect in one assertion.

    Two deployments identical but for `AAE_PROVIDER_MODE` must describe
    themselves identically, because the mode is chosen per run and the
    setting decides nothing a visitor can see.
    """
    ai = {
        "ai_analytics_enabled": True,
        "cloud_api_key": "sk-test-not-a-real-key",
        "cloud_model": "claude-sonnet-5",
        "ai_quota_redis_url": "redis://localhost:6379/0",
    }
    as_fake = _config(_settings(warehouse_dir, tmp_path, provider_mode="fake", **ai))
    as_cloud = _config(_settings(warehouse_dir, tmp_path, provider_mode="cloud", **ai))

    for field in ("execution_mode", "model_inference_remote", "capabilities"):
        assert as_fake[field] == as_cloud[field], field


def test_a_recorded_deployment_says_so_whatever_else_is_configured(
    warehouse_dir: Path, tmp_path: Path, fake_ledger: None
) -> None:
    """Live analytics off means nothing runs, so no mode is on offer."""
    payload = _config(
        _settings(
            warehouse_dir,
            tmp_path,
            live_analytics_enabled=False,
            ai_analytics_enabled=True,
            cloud_api_key="sk-test-not-a-real-key",
            cloud_model="claude-sonnet-5",
            ai_quota_redis_url="redis://localhost:6379/0",
        )
    )
    assert payload["execution_mode"] == "recorded"


def test_an_unreachable_ledger_withdraws_the_privacy_risk_too(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI cannot be started without its ledger, so nothing leaves the server.

    The three fields have to move together here as well, or the deployment
    warns about a disclosure that cannot happen.
    """
    import agentic_analytics.api.app as app_module

    monkeypatch.setattr(app_module, "open_ledger", lambda *a, **k: None)
    payload = _config(
        _settings(
            warehouse_dir,
            tmp_path,
            provider_mode="cloud",
            ai_analytics_enabled=True,
            cloud_api_key="sk-test-not-a-real-key",
            cloud_model="claude-sonnet-5",
            ai_quota_redis_url="redis://localhost:6379/0",
        )
    )
    assert _ai_available(payload) is False
    assert payload["model_inference_remote"] is False
    assert payload["execution_mode"] == "deterministic_live"


def test_the_config_never_echoes_a_credential(
    warehouse_dir: Path, tmp_path: Path, fake_ledger: None
) -> None:
    """The fields above are published to every visitor."""
    payload = _config(
        _settings(
            warehouse_dir,
            tmp_path,
            ai_analytics_enabled=True,
            cloud_api_key="sk-test-not-a-real-key",
            cloud_model="claude-sonnet-5",
            ai_quota_redis_url="redis://user:password@example.invalid:6379/0",
        )
    )
    rendered = str(payload)
    assert "sk-test-not-a-real-key" not in rendered
    assert "password" not in rendered
    assert "example.invalid" not in rendered
