"""API request and response shapes."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentic_analytics.api.modes import RunMode

MAX_QUESTION_LENGTH = 500


ExecutionMode = Literal["recorded", "deterministic_live", "ai_live"]


def execution_mode(live_enabled: bool, ai_available: bool) -> ExecutionMode:
    """The strongest kind of run this deployment can produce.

    The distinction matters: a run driven by the scripted provider executes
    the same graph, MCP calls, SQL and verification as one driven by a
    language model, but the planning decisions are deterministic rules, and
    presenting a scripted run as a model-driven one would be false.

    Derived from what the deployment can offer, not from
    `AAE_PROVIDER_MODE`. The mode is chosen per run now, so a process
    setting describes nothing a visitor can act on -- and reading it here
    contradicted the capabilities published beside it: a deployment with
    AI enabled and the process default left at `fake` reported
    `deterministic_live` while offering AI runs, and told visitors nothing
    derived from their upload left the server.
    """
    if not live_enabled:
        return "recorded"
    return "ai_live" if ai_available else "deterministic_live"


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    build_sha: str
    #: Random, generated once when this application object is built. Two
    #: reads returning different values mean the process was replaced --
    #: which is the only way to see an OOM kill and restart from outside.
    #: Comparing `version` cannot: a restarted process runs the same build.
    #: Not a secret, and not stable across deploys by design.
    instance_id: str
    provider_mode: str
    execution_mode: ExecutionMode
    live_analytics_enabled: bool
    demo_warehouse_ready: bool
    recordings: int


class ReadinessResponse(BaseModel):
    """Whether this process can serve the demo, not merely whether it is up.

    Separate from health because they answer different questions and a
    platform acts on each differently. A process whose demo warehouse never
    got built is alive and completely useless; returning 200 for it means a
    broken deploy goes live and stays live.
    """

    status: str
    demo_warehouse_ready: bool
    recordings_loaded: bool
    detail: str = ""


class ServerConfig(BaseModel):
    """What the frontend needs to decide which affordances to show."""

    version: str
    build_sha: str
    provider_mode: str
    #: recorded | deterministic_live | ai_live. Drives the badge in the UI.
    execution_mode: ExecutionMode
    #: Whether a run on this deployment *can* send prompts to a third-party
    #: model. False is the strong claim. Nothing derived from a dataset
    #: leaves this server, in any mode a visitor can pick, so it is taken
    #: from the published capabilities rather than from a process setting
    #: that no longer decides what a run does.
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
    #: What the mode selector should offer. Replaces inferring availability
    #: from `provider_mode`, which described the process rather than the
    #: choices a visitor has.
    capabilities: Capabilities


class ModeCapability(BaseModel):
    """Whether one execution mode can be offered, and why not if it cannot.

    `reason` is a stable identifier and `message` is the sentence a visitor
    reads. Neither ever carries a credential, a URL, an exception string or
    any deployment detail.
    """

    mode: str
    available: bool
    label: str
    description: str
    reason: str = ""
    message: str = ""


class AILimits(BaseModel):
    """The public ceilings on AI Analytics, safe to disclose."""

    runs_per_session: int
    max_model_calls_per_run: int
    max_runtime_seconds: float


class Capabilities(BaseModel):
    """What this deployment can actually do, for the mode selector."""

    modes: list[ModeCapability]
    compare_available: bool
    ai_limits: AILimits | None = None


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


#: A role change is one of two operations, and saying which is required.
#: An empty string meaning "reset" would make a typo indistinguishable from
#: an instruction.
class RoleAction(StrEnum):
    CONFIRM = "confirm"
    RESET = "reset"


#: The readings a session owner may choose between. Closed, and narrower
#: than `FieldRole`: the close call this engine reports is between a
#: quantity and a code list, and offering `time` or `identifier` would offer
#: conversions no inference class has been tested against.
class ConfirmableRole(StrEnum):
    MEASURE = "measure"
    DIMENSION = "dimension"


#: Enough for any real schema's close calls, small enough that a request
#: cannot be used to make the server do unbounded validation work.
MAX_ROLE_CHANGES = 32


class RoleChange(BaseModel):
    """One column, and what the session owner says it is."""

    model_config = ConfigDict(extra="forbid")

    column: str
    action: RoleAction
    #: Required for `confirm`, forbidden for `reset`. Carrying a role on a
    #: reset would leave the caller's intent ambiguous.
    role: ConfirmableRole | None = None

    @model_validator(mode="after")
    def _role_matches_the_action(self) -> RoleChange:
        if self.action is RoleAction.CONFIRM and self.role is None:
            raise ValueError("confirming a column requires a role")
        if self.action is RoleAction.RESET and self.role is not None:
            raise ValueError("resetting a column takes no role")
        if not self.column.strip():
            raise ValueError("a column name is required")
        return self


class RoleConfirmationRequest(BaseModel):
    """A batch of role changes against a known generation of the schema.

    `expected_revision` is what the browser was looking at. A mismatch means
    the schema moved under it -- another tab, or its own earlier request --
    and confirming against a column list it is no longer showing would apply
    an instruction the person never gave.
    """

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    changes: list[RoleChange] = Field(min_length=1, max_length=MAX_ROLE_CHANGES)

    @model_validator(mode="after")
    def _one_change_per_column(self) -> RoleConfirmationRequest:
        seen = [change.column for change in self.changes]
        if len(set(seen)) != len(seen):
            raise ValueError("each column may appear only once in a batch")
        return self


class AnalysisRequest(BaseModel):
    session_id: str
    question: str
    #: Which decision-maker drives this run. A closed set of two public
    #: names: the browser cannot name a provider class, a model, an endpoint
    #: or any provider configuration, because a request that could would be
    #: a request that could aim the server's credential somewhere else.
    mode: RunMode = RunMode.DETERMINISTIC

    @field_validator("question")
    @classmethod
    def _question_is_reasonable(cls, v: str) -> str:
        text = " ".join(v.split())
        if not text:
            raise ValueError("a question is required")
        if len(text) > MAX_QUESTION_LENGTH:
            raise ValueError(f"the question must be under {MAX_QUESTION_LENGTH} characters")
        return text


class ComparisonRequest(BaseModel):
    """One question, both decision paths, one dataset."""

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


class ComparisonStarted(BaseModel):
    """Two ordinary runs, each independently auditable.

    Deliberately not a merged result. The two sides are different planning
    strategies over the same governed engine, and combining them would
    invent an authority neither has.
    """

    comparison_id: str
    session_id: str
    question: str
    deterministic_run_id: str
    ai_run_id: str


class AnalysisStarted(BaseModel):
    run_id: str
    session_id: str
    question: str


class ErrorResponse(BaseModel):
    """Errors are always this shape, and never carry a stack trace or a path."""

    error: str
    detail: str = ""
