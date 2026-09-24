"""Configuration must actually be configurable, and the deployment files must
set ceilings the application really reads."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentic_analytics.config import Budgets, Settings

REPO = Path(__file__).resolve().parents[2]


def test_defaults_need_no_credential() -> None:
    settings = Settings()
    assert settings.provider_mode == "fake"
    assert settings.cloud_api_key is None
    # The safe public posture: recorded runs only until explicitly enabled.
    assert settings.live_analytics_enabled is False


def test_nested_budget_overrides_are_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ceiling that is silently ignored is worse than no ceiling."""
    monkeypatch.setenv("AAE_BUDGETS__MAX_ANALYSIS_TASKS", "2")
    monkeypatch.setenv("AAE_BUDGETS__MAX_TOTAL_TOOL_CALLS", "7")
    monkeypatch.setenv("AAE_BUDGETS__QUERY_TIMEOUT_SECONDS", "3.5")
    settings = Settings()
    assert settings.budgets.max_analysis_tasks == 2
    assert settings.budgets.max_total_tool_calls == 7
    assert settings.budgets.query_timeout_seconds == 3.5
    # Unset budgets keep their defaults.
    assert settings.budgets.max_result_rows == Budgets().max_result_rows


def test_budget_bounds_are_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AAE_BUDGETS__MAX_RESULT_ROWS", "100000")
    with pytest.raises(ValueError):
        Settings()


def test_only_one_followup_round_is_permitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AAE_BUDGETS__MAX_FOLLOWUP_ROUNDS", "5")
    with pytest.raises(ValueError):
        Settings()


def test_sample_rows_cannot_be_raised_above_twenty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raw row disclosure is the one path unaggregated data reaches a prompt."""
    monkeypatch.setenv("AAE_BUDGETS__MAX_SAMPLE_ROWS", "5000")
    with pytest.raises(ValueError):
        Settings()


def test_mcp_allowed_hosts_parse() -> None:
    settings = Settings(mcp_allowed_hosts=" a.example.com , b.example.com:443 ,")
    assert settings.mcp_allowed_host_list == ["a.example.com", "b.example.com:443"]
    assert Settings().mcp_allowed_host_list == []


def _blueprint(name: str) -> dict[str, object]:
    return yaml.safe_load((REPO / name).read_text())["services"][0]


@pytest.mark.parametrize("name", ["render.yaml", "deploy/render-live.yaml"])
def test_blueprints_are_well_formed(name: str) -> None:
    service = _blueprint(name)
    assert service["healthCheckPath"] == "/api/health"
    assert service["dockerfilePath"] == "./Dockerfile"
    assert service["autoDeploy"] is False


@pytest.mark.parametrize("name", ["render.yaml", "deploy/render-live.yaml"])
def test_blueprints_never_carry_a_secret_value(name: str) -> None:
    service = _blueprint(name)
    for entry in service["envVars"]:  # type: ignore[index]
        if entry["key"].endswith(("API_KEY", "TOKEN", "SECRET", "PASSWORD")):
            assert "value" not in entry, f"{name} inlines {entry['key']}"
            assert entry.get("sync") is False, f"{name} must mark {entry['key']} sync: false"


@pytest.mark.parametrize("name", ["render.yaml", "deploy/render-live.yaml"])
def test_blueprint_env_vars_are_all_real_settings(name: str) -> None:
    """A blueprint that sets a variable nothing reads is a false assurance."""
    service = _blueprint(name)
    fields = set(Settings.model_fields)
    budget_fields = set(Budgets.model_fields)
    for entry in service["envVars"]:  # type: ignore[index]
        key: str = entry["key"]
        assert key.startswith("AAE_"), key
        name_part = key.removeprefix("AAE_").lower()
        if "__" in name_part:
            parent, child = name_part.split("__", 1)
            assert parent == "budgets", key
            assert child in budget_fields, f"{key} is not a budget"
        else:
            assert name_part in fields, f"{key} is not a setting"


def test_recorded_blueprint_needs_no_secret() -> None:
    service = _blueprint("render.yaml")
    keys = {e["key"] for e in service["envVars"]}  # type: ignore[index]
    assert not any(k.endswith("API_KEY") for k in keys)
    assert {"AAE_PROVIDER_MODE", "AAE_LIVE_ANALYTICS_ENABLED"} <= keys
    by_key = {e["key"]: e for e in service["envVars"]}  # type: ignore[index]
    assert by_key["AAE_PROVIDER_MODE"]["value"] == "fake"
    assert by_key["AAE_LIVE_ANALYTICS_ENABLED"]["value"] == "false"


def test_live_blueprint_does_not_default_to_a_paid_provider() -> None:
    service = _blueprint("deploy/render-live.yaml")
    by_key = {e["key"]: e for e in service["envVars"]}  # type: ignore[index]
    assert by_key["AAE_PROVIDER_MODE"]["value"] == "fake"
    assert by_key["AAE_LIVE_ANALYTICS_ENABLED"]["value"] == "true"
    assert by_key["AAE_UPLOADS_ENABLED"]["value"] == "false"


def test_env_example_documents_only_real_settings() -> None:
    fields = set(Settings.model_fields)
    budget_fields = set(Budgets.model_fields)
    for raw in (REPO / ".env.example").read_text().splitlines():
        line = raw.strip().lstrip("#").strip()
        if not line or "=" not in line or not line.startswith("AAE_"):
            continue
        key = line.split("=", 1)[0]
        name_part = key.removeprefix("AAE_").lower()
        if "__" in name_part:
            parent, child = name_part.split("__", 1)
            assert parent == "budgets" and child in budget_fields, key
        else:
            assert name_part in fields, key


def test_dockerfile_runs_as_an_unprivileged_user() -> None:
    dockerfile = (REPO / "Dockerfile").read_text()
    assert "USER app" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    # The image must not default to live analysis.
    assert "AAE_LIVE_ANALYTICS_ENABLED=false" in dockerfile
    assert "AAE_PROVIDER_MODE=fake" in dockerfile
