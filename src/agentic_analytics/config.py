"""Runtime settings and the budgets that bound every run.

Budgets are not advisory. Each one is enforced at the point where the
corresponding resource is consumed, and exceeding one degrades the run
(a task stops early, a result is truncated) rather than failing it.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderMode = Literal["fake", "local", "cloud"]

#: Accepted spellings for a DuckDB memory limit.
_MEMORY_LIMIT = re.compile(r"\d+(\.\d+)?\s?(kb|mb|gb|tb|kib|mib|gib|tib)", re.IGNORECASE)

# Repository root, derived from this file's location rather than the process
# working directory, so the demo warehouse resolves the same way under
# pytest, uvicorn and the container entrypoint.
PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent.parent


class Budgets(BaseModel):
    """Hard ceilings on a single analysis run."""

    max_analysis_tasks: int = Field(default=6, ge=1, le=24)
    max_tool_calls_per_task: int = Field(default=6, ge=1, le=32)
    max_total_tool_calls: int = Field(default=48, ge=1, le=512)
    max_llm_calls: int = Field(default=40, ge=1, le=400)
    max_output_tokens: int = Field(default=2048, ge=128, le=32768)
    max_runtime_seconds: float = Field(default=300.0, gt=0)
    max_followup_rounds: int = Field(default=1, ge=0, le=1)

    # Result shaping. `max_result_rows` is what a tool will return to an
    # agent; `max_sample_rows` is the much smaller ceiling on raw row
    # disclosure, which is the only path by which unaggregated user data
    # reaches a prompt.
    max_result_rows: int = Field(default=500, ge=1, le=1000)
    max_sample_rows: int = Field(default=20, ge=1, le=20)
    max_chart_rows: int = Field(default=200, ge=1, le=1000)
    max_sql_length: int = Field(default=8000, ge=100, le=64000)
    query_timeout_seconds: float = Field(default=20.0, gt=0)

    # Uploads. Bounded on every axis a hostile file could stretch.
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    max_upload_rows: int = Field(default=2_000_000, ge=1)
    max_upload_columns: int = Field(default=200, ge=1, le=2048)
    max_column_name_length: int = Field(default=128, ge=8, le=1024)
    max_parquet_row_groups: int = Field(default=4096, ge=1)
    max_parquet_metadata_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)
    #: Ceiling on the uncompressed size a Parquet footer declares. Checked
    #: before any value is decoded, because compression means the size on
    #: disk says nothing about what a read will allocate. An admission
    #: ceiling on a declared number, not a measurement of memory use.
    max_parquet_uncompressed_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1024)


class Settings(BaseSettings):
    """Process configuration, all overridable by environment variable."""

    model_config = SettingsConfigDict(
        env_prefix="AAE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Budgets are a nested model, so a deployment overrides one with
        # AAE_BUDGETS__MAX_ANALYSIS_TASKS. Without this delimiter those
        # variables are accepted and silently ignored, which is the worst
        # possible behaviour for a ceiling.
        env_nested_delimiter="__",
    )

    # Provider selection. `fake` is the default precisely so that tests, CI,
    # the frontend and recorded replay never need a credential.
    provider_mode: ProviderMode = "fake"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    #: Let a reasoning-capable local model emit its thinking. Off because
    #: every role asks for schema-bound JSON within a bounded output budget,
    #: and a thinking model spends that budget before it starts answering --
    #: producing truncated, unparseable JSON rather than a slower reply.
    #: See `llm/ollama.py`.
    ollama_think: bool = False
    #: Ceiling on one local model call. The default is generous because a
    #: cold load of a 7B model can take minutes before a single token is
    #: produced, and a first call that times out looks like a model failure
    #: when it is a startup cost.
    ollama_timeout_seconds: float = Field(default=300.0, gt=0)
    cloud_model: str = "claude-sonnet-5"
    cloud_api_key: str | None = None
    cloud_base_url: str = "https://api.anthropic.com"
    #: Ceiling on one cloud model call, enforced by us and not only by the
    #: HTTP client. Lower than the local default because there is no cold
    #: model load to wait through: a hosted endpoint that has sent nothing
    #: in two minutes is not about to start.
    cloud_timeout_seconds: float = Field(default=120.0, gt=0)

    # When false the API refuses to start new live analyses and serves
    # recordings only. This is the safe public-demo posture.
    live_analytics_enabled: bool = False
    uploads_enabled: bool = True

    #: Whether an uploaded dataset's raw cells may be shown to a *remote*
    #: model. Off by default: schema, profile and aggregated results are
    #: enough to plan an analysis, and a visitor uploading a spreadsheet to a
    #: demo has not agreed to have their rows forwarded to a third party.
    #: The demo warehouse is synthetic and public, so it is unaffected.
    allow_upload_row_disclosure: bool = False

    # Storage. `var/` is gitignored; the demo warehouse is regenerated by
    # `make data` and the per-session databases are ephemeral.
    data_dir: Path = REPO_ROOT / "var" / "warehouse"
    upload_dir: Path = REPO_ROOT / "var" / "uploads"
    recordings_dir: Path = REPO_ROOT / "examples" / "recordings"
    #: Built frontend. The repo-relative default only exists in a source
    #: checkout; an installed wheel has no `web/` beside it, so the container
    #: sets this explicitly. Getting it wrong serves a 404 for every page,
    #: which is why the container smoke test fetches `/`.
    frontend_dir: Path = REPO_ROOT / "web" / "dist"

    session_ttl_seconds: float = 2700.0
    max_concurrent_sessions: int = 32
    max_worker_concurrency: int = 4
    #: How often the janitor sweeps for sessions past their TTL. Expiry has
    #: to happen on a clock rather than on the next request, or an abandoned
    #: session keeps its DuckDB connection and its uploaded bytes until
    #: somebody else happens to arrive.
    session_sweep_seconds: float = Field(default=45.0, gt=0)
    #: How long a teardown waits for a cancelled analysis to unwind before
    #: giving up and leaving the session in `closing` for the janitor to
    #: retry. Short enough that a delete feels responsive, long enough that
    #: an ordinary run finishes inside it.
    session_cancel_grace_seconds: float = Field(default=5.0, gt=0)

    # Per-session DuckDB resource envelope. These belong in settings, not in
    # the warehouse module: the right values follow from the instance size
    # and the number of sessions admitted, both of which are deployment
    # facts. 1 GB per session is wrong on a 2 GB box that admits a dozen.
    duckdb_memory_limit: str = "1GB"
    duckdb_threads: int = Field(default=2, ge=1, le=32)

    @field_validator("duckdb_memory_limit")
    @classmethod
    def _memory_limit_is_parseable(cls, v: str) -> str:
        """Reject a value DuckDB would reject, at startup rather than at
        the first query."""
        if not _MEMORY_LIMIT.fullmatch(v.strip()):
            raise ValueError(f"duckdb_memory_limit must look like '512MB' or '1.5GB', got {v!r}")
        return v.strip()

    # Abuse controls for an unauthenticated public deployment. In-process
    # counters, not durable quotas; see api/limits.py.
    uploads_per_ip_per_hour: int = 6
    analyses_per_ip_per_hour: int = 30
    analyses_per_session: int = 12
    max_concurrent_analyses: int = 4
    max_active_upload_sessions: int = 24

    # HttpOnly cookie carrying the session capability.
    session_cookie_name: str = "aae_session"
    #: Whether to mark the capability cookie ``Secure``. This cannot be
    #: inferred from the request scheme: a TLS-terminating proxy forwards
    #: plain HTTP to the application, so the app sees ``http`` on a site that
    #: is HTTPS end to end for the browser. Any deployment behind TLS must
    #: set this to true; local development leaves it false so the cookie
    #: works over http://127.0.0.1.
    session_cookie_secure: bool = False

    # MCP. The agent always talks to analytics through a real `mcp.Client`.
    # In the web application that client connects to the server object
    # in-process, which is what makes the deployment a single container; the
    # Streamable HTTP endpoint below is a separate, optional transport for
    # external callers. There is deliberately no setting to switch the web
    # application onto HTTP: it would mean the process making a network round
    # trip to itself.
    #
    # Host header allow-list for the mounted MCP endpoint. The SDK enables
    # DNS-rebinding protection automatically only when the server binds to
    # localhost; a container binds to 0.0.0.0, so the protection has to be
    # asked for explicitly with the hostnames the deployment answers on.
    # Comma-separated, e.g. "example.onrender.com,example.onrender.com:443".
    mcp_allowed_hosts: str = ""
    #: Where the server binds. Loopback is treated as local development and
    #: gets a localhost allow-list automatically; anything else is a network
    #: binding and must declare its hostnames or the remote endpoint is
    #: withdrawn.
    bind_host: str = "127.0.0.1"

    @property
    def mcp_allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.mcp_allowed_hosts.split(",") if h.strip()]

    @property
    def is_local_binding(self) -> bool:
        """True when the server is reachable only from this machine."""
        return self.bind_host in {"127.0.0.1", "localhost", "::1", ""}

    log_level: str = "INFO"
    log_json: bool = True

    budgets: Budgets = Field(default_factory=Budgets)

    @property
    def demo_warehouse_dir(self) -> Path:
        return self.data_dir / "commerce"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, read once."""
    return Settings()
