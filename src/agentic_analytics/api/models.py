"""API request and response shapes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

MAX_QUESTION_LENGTH = 500


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    provider_mode: str
    live_analytics_enabled: bool
    demo_warehouse_ready: bool
    recordings: int


class ServerConfig(BaseModel):
    """What the frontend needs to decide which affordances to show."""

    version: str
    provider_mode: str
    live_analytics_enabled: bool
    uploads_enabled: bool
    demo_warehouse_ready: bool
    max_upload_mb: int
    budgets: dict[str, Any]
    demo_questions: list[dict[str, str]]
    recordings: list[dict[str, Any]]


class SessionResponse(BaseModel):
    session_id: str
    catalog: dict[str, Any]
    metrics: list[dict[str, Any]] = Field(default_factory=list)


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
