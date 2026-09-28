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


def test_blueprints_are_well_formed() -> None:
    service = _blueprint("render.yaml")
    # Readiness, not liveness: /api/health answers 200 for a container whose
    # demo warehouse never built, which is alive and cannot serve anyone.
    assert service["healthCheckPath"] == "/api/ready"
    assert service["dockerfilePath"] == "./Dockerfile"
    # `autoDeployTrigger: "off"` is the current field; `autoDeploy: false` is
    # deprecated. Quoted, because YAML 1.1 turns a bare `off` into a boolean
    # and this field wants the string.
    assert service["autoDeployTrigger"] == "off"
    assert "autoDeploy" not in service, "the deprecated field is still present"


def test_blueprints_never_carry_a_secret_value() -> None:
    service = _blueprint("render.yaml")
    for entry in service["envVars"]:  # type: ignore[index]
        if entry["key"].endswith(("API_KEY", "TOKEN", "SECRET", "PASSWORD", "REDIS_URL")):
            assert "value" not in entry, f"render.yaml inlines {entry['key']}"
            assert entry.get("sync") is False, f"{entry['key']} must be sync: false"


def test_blueprint_env_vars_are_all_real_settings() -> None:
    """A blueprint that sets a variable nothing reads is a false assurance."""
    service = _blueprint("render.yaml")
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


def test_public_blueprint_needs_no_secret() -> None:
    """The public deployment must deploy as-is, with nothing to configure.

    The credential is declared so the dashboard shows the field, but it
    carries no value and nothing requires it: AI is off, and Deterministic
    Analytics never reads it.
    """
    service = _blueprint("render.yaml")
    by_key = {e["key"]: e for e in service["envVars"]}  # type: ignore[index]
    for key in by_key:
        if key.endswith("API_KEY") or key.endswith("REDIS_URL"):
            assert "value" not in by_key[key], key
    assert by_key["AAE_AI_ANALYTICS_ENABLED"]["value"] == "false"
    # The public demo is the product: visitors ask their own questions of
    # their own data.
    assert by_key["AAE_LIVE_ANALYTICS_ENABLED"]["value"] == "true"
    assert by_key["AAE_UPLOADS_ENABLED"]["value"] == "true"


def test_public_blueprint_binds_to_a_network_interface_and_says_so() -> None:
    """The MCP host policy keys off the bind host, so it has to be declared."""
    service = _blueprint("render.yaml")
    by_key = {e["key"]: e for e in service["envVars"]}  # type: ignore[index]
    assert by_key["AAE_BIND_HOST"]["value"] == "0.0.0.0"
    # Empty means the remote MCP endpoint is withdrawn rather than exposed
    # without Host validation. That is the safe default for a fresh deploy.
    assert by_key["AAE_MCP_ALLOWED_HOSTS"]["value"] == ""


def test_public_blueprint_bounds_uploads_and_abuse() -> None:
    service = _blueprint("render.yaml")
    by_key = {e["key"]: e for e in service["envVars"]}  # type: ignore[index]
    for key in (
        "AAE_BUDGETS__MAX_UPLOAD_BYTES",
        "AAE_BUDGETS__MAX_UPLOAD_COLUMNS",
        "AAE_SESSION_TTL_SECONDS",
        "AAE_MAX_ACTIVE_UPLOAD_SESSIONS",
        "AAE_UPLOADS_PER_IP_PER_HOUR",
        "AAE_ANALYSES_PER_IP_PER_HOUR",
        "AAE_MAX_CONCURRENT_ANALYSES",
    ):
        assert key in by_key, key
        assert int(by_key[key]["value"]) > 0


def test_the_blueprint_does_not_default_to_a_paid_provider() -> None:
    """Deploying this file unchanged spends nothing.

    There is one blueprint now. A second file describing a process-wide
    provider contradicted the per-run architecture, and a stale deployment
    description is a way to deploy something nobody reviewed.
    """
    service = _blueprint("render.yaml")
    by_key = {e["key"]: e for e in service["envVars"]}  # type: ignore[index]
    assert by_key["AAE_AI_ANALYTICS_ENABLED"]["value"] == "false"
    # Secrets are dashboard-only: declared, never valued.
    for secret in ("AAE_CLOUD_API_KEY", "AAE_AI_QUOTA_REDIS_URL"):
        assert by_key[secret].get("sync") is False, secret
        assert "value" not in by_key[secret], secret


def test_every_ai_ceiling_is_declared_and_positive() -> None:
    """AI cannot be enabled into an unbounded configuration by omission."""
    service = _blueprint("render.yaml")
    by_key = {e["key"]: e for e in service["envVars"]}  # type: ignore[index]
    for key in (
        "AAE_AI_MAX_LLM_CALLS",
        "AAE_AI_MAX_INPUT_TOKENS",
        "AAE_AI_MAX_OUTPUT_TOKENS",
        "AAE_AI_MAX_RUNTIME_SECONDS",
        "AAE_AI_MAX_COST_MICRODOLLARS",
        "AAE_AI_RUNS_PER_SESSION",
        "AAE_AI_RUNS_PER_IP_PER_HOUR",
        "AAE_AI_CONCURRENT_RUNS",
        "AAE_AI_DAILY_COST_MICRODOLLARS",
        "AAE_AI_TOTAL_COST_MICRODOLLARS",
    ):
        assert key in by_key, key
        assert int(float(by_key[key]["value"])) > 0, key


def test_the_configured_model_has_a_pricing_entry() -> None:
    """An unpriced model is refused at runtime; catch it at review time."""
    from agentic_analytics.llm.pricing import is_priced

    service = _blueprint("render.yaml")
    by_key = {e["key"]: e for e in service["envVars"]}  # type: ignore[index]
    assert is_priced(by_key["AAE_CLOUD_MODEL"]["value"])


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


def test_blueprints_mark_the_cookie_secure() -> None:
    """Render terminates TLS, so the application cannot infer this."""
    by_key = {e["key"]: e for e in _blueprint("render.yaml")["envVars"]}  # type: ignore[index]
    assert by_key["AAE_SESSION_COOKIE_SECURE"]["value"] == "true"


def test_blueprints_size_duckdb_for_the_instance() -> None:
    """The session envelope has to fit the instance it is deployed on."""
    service = _blueprint("render.yaml")
    by_key = {e["key"]: e for e in service["envVars"]}  # type: ignore[index]
    assert service["plan"] == "free"
    # Parseable by the application, and smaller than the old hardcoded 1 GB.
    settings = Settings(
        duckdb_memory_limit=by_key["AAE_DUCKDB_MEMORY_LIMIT"]["value"],
        duckdb_threads=int(by_key["AAE_DUCKDB_THREADS"]["value"]),
    )
    assert settings.duckdb_memory_limit.upper().endswith("MB")
    assert int(settings.duckdb_memory_limit[:-2]) <= 512
    assert settings.duckdb_threads == 1


def test_the_session_envelope_fits_the_instance() -> None:
    """A bound on the configuration, not a prediction about the instance.

    What this asserts is narrow and worth stating exactly, because the
    obvious stronger reading is wrong.

    `AAE_DUCKDB_MEMORY_LIMIT` is a *ceiling* DuckDB will not exceed, not an
    allocation it makes up front. But an idle session is not free either: it
    has materialised its dataset into a private in-memory database and holds
    those rows for its whole life. So neither `per_session x sessions` nor
    `per_session x concurrent_analyses` is the real figure -- the first
    wildly overstates it, the second ignores every idle session.

    Since the honest number is not derivable from the configuration, this
    test does not pretend to derive it. It checks two things that *are*
    configuration errors: a per-session ceiling large enough that two
    analyses could alone exhaust the box, and an admitted-session count
    beyond what the instance was sized for. The actual behaviour under load
    is measured, not computed -- see `scripts/resource_rehearsal.py`.
    """
    by_key = {e["key"]: e for e in _blueprint("render.yaml")["envVars"]}  # type: ignore[index]
    per_session_mb = int(by_key["AAE_DUCKDB_MEMORY_LIMIT"]["value"].upper().removesuffix("MB"))
    sessions = int(by_key["AAE_MAX_CONCURRENT_SESSIONS"]["value"])
    concurrent = int(by_key["AAE_MAX_CONCURRENT_ANALYSES"]["value"])

    # The plan is 1 CPU / 2 GB. Two analyses at their ceiling must leave
    # room for the interpreter, the loaded datasets and the OS.
    assert per_session_mb * concurrent <= 1024, "two analyses could exhaust the instance"
    # Every admitted session holds its rows whether or not it is running.
    assert sessions <= 16, "more sessions than this instance was sized for"
    assert int(by_key["AAE_MAX_ACTIVE_UPLOAD_SESSIONS"]["value"]) <= sessions


def test_the_public_blueprint_keeps_the_remote_mcp_endpoint_withdrawn() -> None:
    """An anonymous demo has no reason to expose MCP to the internet.

    The website's agents use the in-process transport, so nothing is lost.
    """
    by_key = {e["key"]: e for e in _blueprint("render.yaml")["envVars"]}  # type: ignore[index]
    assert by_key["AAE_MCP_ALLOWED_HOSTS"]["value"] == ""


def test_the_blueprint_does_not_promise_a_local_model() -> None:
    """`local` needs an Ollama this image does not contain or start.

    There is one blueprint now, and it offers Deterministic and AI. Local
    models stay a CLI and evaluation concern.
    """
    text = (REPO / "render.yaml").read_text()
    assert "11434" not in text
    assert "AAE_OLLAMA_BASE_URL" not in text


# -------------------------------------------------- memory-critical ceilings
#
# The deployed instance has 512 MB. Each live session holds a DuckDB
# connection over the warehouse, costing roughly 55 MB, so the session cap
# is the difference between a service that stays up and one that restarts
# under its first bit of attention. Measured, with two analyses running
# against a full session table: 3 sessions 438 MB, 4 sessions 497 MB,
# 6 sessions 600 MB, 8 sessions 708 MB.
#
# These assertions exist so that raising one of these numbers is a decision
# rather than an accident.

#: What the deployed plan provides, in MB. Keep in step with `plan:`.
FREE_PLAN_MB = 512


def _env(service: dict[str, object]) -> dict[str, str]:
    return {e["key"]: str(e.get("value", "")) for e in service["envVars"]}  # type: ignore[union-attr,index]


def test_the_blueprint_declares_the_plan_its_ceilings_were_sized_for() -> None:
    """A plan change without a ceiling change is the failure mode here."""
    assert _blueprint("render.yaml")["plan"] == "free"


def test_the_session_cap_fits_the_instance() -> None:
    """Three sessions measured 438 MB against a 512 MB instance.

    Four measured 497 MB, which is within 15 MB of the limit and leaves
    nothing for a request spike.
    """
    env = _env(_blueprint("render.yaml"))
    assert int(env["AAE_MAX_CONCURRENT_SESSIONS"]) <= 3


def test_concurrency_is_capped_for_the_instance_size() -> None:
    env = _env(_blueprint("render.yaml"))
    assert int(env["AAE_MAX_CONCURRENT_ANALYSES"]) <= 2
    assert int(env["AAE_AI_CONCURRENT_RUNS"]) <= 1


def test_the_duckdb_envelope_leaves_room_for_the_rest_of_the_process() -> None:
    """Two analyses may hold an envelope each, and the rest of the process
    -- Python, the warehouse handles, the HTTP server -- shares what is
    left."""
    env = _env(_blueprint("render.yaml"))
    limit_mb = int(env["AAE_DUCKDB_MEMORY_LIMIT"].removesuffix("MB"))
    concurrent = int(env["AAE_MAX_CONCURRENT_ANALYSES"])
    assert limit_mb * concurrent < FREE_PLAN_MB * 0.7


def test_the_session_ttl_does_not_outlive_the_container() -> None:
    """Free instances spin down after 15 minutes of inactivity.

    A session promised for longer than that is a promise the plan cannot
    keep, and the visitor meets a 404 rather than their dataset.
    """
    env = _env(_blueprint("render.yaml"))
    assert int(env["AAE_SESSION_TTL_SECONDS"]) <= 900


def test_uploads_cannot_exceed_what_the_instance_can_hold() -> None:
    """An upload is profiled in memory before it is anything else."""
    env = _env(_blueprint("render.yaml"))
    assert int(env["AAE_BUDGETS__MAX_UPLOAD_BYTES"]) <= 10 * 1024 * 1024
    assert int(env["AAE_BUDGETS__MAX_UPLOAD_ROWS"]) <= 400_000
