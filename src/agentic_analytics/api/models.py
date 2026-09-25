"""API request and response shapes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MAX_QUESTION_LENGTH = 500


ExecutionMode = Literal["recorded", "deterministic_live", "ai_live"]


def execution_mode(live_enabled: bool, provider_mode: str) -> ExecutionMode:
    """Which of the three modes the server is actually in.

    The distinction matters: a run driven by the scripted provider executes
    the same graph, MCP calls, SQL and verification as one driven by a
    language model, but the planning decisions are deterministic rules. The
    UI labels these differently so a scripted run is never presented as a
    model-driven one.
    """
    if not live_enabled:
        return "recorded"
    return "deterministic_live" if provider_mode == "fake" else "ai_live"


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    provider_mode: str
    execution_mode: ExecutionMode
    live_analytics_enabled: bool
    demo_warehouse_ready: bool
    recordings: int


class ServerConfig(BaseModel):
    """What the frontend needs to decide which affordances to show."""

    version: str
    provider_mode: str
    #: recorded | deterministic_live | ai_live. Drives the badge in the UI.
    execution_mode: ExecutionMode
    #: True only when a language model makes the agent decisions. When false,
    #: derived schema and results never leave this server.
    model_inference_remote: bool
    live_analytics_enabled: bool
    uploads_enabled: bool
    #: Whether the Streamable HTTP MCP endpoint at /mcp is served. False
    #: means it answers 503 by design -- a network binding with no Host
    #: allow-list withdraws the transport rather than serving it unvalidated.
    #: Reported so an external check can assert the endpoint matches the
    #: policy instead of inferring the policy from the URL it dialled, which
    #: says nothing about how the server bound.
    mcp_remote_enabled: bool
    demo_warehouse_ready: bool
    max_upload_mb: int
    max_upload_columns: int
    session_ttl_minutes: int
    budgets: dict[str, Any]
    demo_questions: list[dict[str, str]]
    recordings: list[dict[str, Any]]


class SessionResponse(BaseModel):
    """A dataset session.

    The capability that authorises this session is **not** in this body. It
    is set as an HttpOnly cookie, so it stays out of browser history, server
    access logs and `Referer` headers.
    """

    session_id: str
    catalog: dict[str, Any]
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    #: Deterministic profile shown before the first question is asked.
    summary: dict[str, Any] | None = None
    expires_in_seconds: float = 0.0


class AnalysisRequest(BaseModel):
    session_id: str
    question: str

    @field_validator("question")
    @classmethod
    def _question_is_reasonable(cls, v: str) -> str:
        text = " ".join(v.split())
        if not text:
            raise ValueError("a question is required")
        if len(text) > MAX_QUESTION_LENGTH:
            raise ValueError(f"the question must be under {MAX_QUESTION_LENGTH} characters")
        return text


class AnalysisStarted(BaseModel):
    run_id: str
    session_id: str
    question: str


class ErrorResponse(BaseModel):
    """Errors are always this shape, and never carry a stack trace or a path."""

    error: str
    detail: str = ""
