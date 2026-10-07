"""The web application.

One FastAPI app serves the API, streams run events over SSE, hosts the built
frontend, and mounts the analytics MCP server at ``/mcp`` over Streamable
HTTP. Running the MCP server in the same process is what lets the deployment
be a single container while the agent still reaches analytics through a real
MCP client rather than a function call.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings

from agentic_analytics import __version__, build_sha
from agentic_analytics.agents.scope import check_scope
from agentic_analytics.analytics.semantic import (
    RoleConfirmationError,
    effective_schema,
    infer_schema,
    validate_role_confirmations,
)
from agentic_analytics.api.limits import Capacity, RateLimit, client_key
from agentic_analytics.api.models import (
    AILimits,
    AnalysisRequest,
    AnalysisStarted,
    Capabilities,
    ComparisonRequest,
    ComparisonStarted,
    ErrorResponse,
    HealthResponse,
    ModeCapability,
    ReadinessResponse,
    RoleAction,
    RoleConfirmationRequest,
    ServerConfig,
    SessionResponse,
    execution_mode,
)
from agentic_analytics.api.modes import (
    ModeUnavailable,
    RunMode,
    ai_availability,
    build_provider_for_mode,
    provider_kind,
)
from agentic_analytics.api.runs import RunRecord, RunRegistry
from agentic_analytics.config import Settings, get_settings
from agentic_analytics.events import EventType
from agentic_analytics.graph.runner import run_analysis
from agentic_analytics.llm.base import LLMProvider
from agentic_analytics.llm.governed import (
    AIBudgetExceeded,
    PreflightFailed,
    open_governed_cloud_provider,
)
from agentic_analytics.llm.ledger import open_ledger
from agentic_analytics.logging import configure_logging, get_logger
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.recordings.store import RecordingStore
from agentic_analytics.warehouse.session import (
    AnalysisSession,
    DatasetError,
    EngineLimits,
    SessionManager,
    StaleSchemaRevision,
    open_demo_session,
    open_upload_session,
)
from agentic_analytics.warehouse.upload import (
    UploadError,
    UploadLimits,
    inspect_csv_header,
    inspect_parquet,
    store_upload,
)

log = get_logger(__name__)

# Same-origin application and SSE. Vega compiles chart expressions at runtime,
# so `unsafe-eval` is the one explicit exception; restricting script origins
# still blocks third-party script execution and is materially stronger than
# omitting `script-src` altogether.
_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-eval'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
)
_PERMISSIONS_POLICY = ", ".join(
    f"{feature}=()"
    for feature in (
        "accelerometer",
        "autoplay",
        "camera",
        "display-capture",
        "encrypted-media",
        "geolocation",
        "gyroscope",
        "magnetometer",
        "microphone",
        "midi",
        "payment",
        "usb",
        "xr-spatial-tracking",
    )
)

# A question and its session handle serialize to well under 16 KiB. Enforce
# this below the framework so an invalid request cannot become an arbitrary
# memory allocation before Pydantic gets a chance to reject it.
_MAX_JSON_BODY_BYTES = 16 * 1024
_MULTIPART_OVERHEAD_BYTES = 64 * 1024


class _RequestBodyLimitMiddleware:
    """Bound declared and streamed request bytes before framework parsing.

    Uploads get their configured file ceiling plus a small, fixed multipart
    envelope. Every other body-bearing route gets the JSON ceiling. Responses
    are buffered only for these body-bearing requests so a dishonest streamed
    body can be replaced with a 413 before the application starts a response;
    GET event streams remain streaming.
    """

    def __init__(
        self,
        app: Any,
        *,
        json_max_bytes: int,
        upload_max_bytes: int,
    ) -> None:
        self.app = app
        self.json_max_bytes = json_max_bytes
        self.upload_max_bytes = upload_max_bytes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") not in {
            "POST",
            "PUT",
            "PATCH",
        }:
            await self.app(scope, receive, send)
            return

        is_upload = scope.get("path") == "/api/datasets/upload"
        limit = self.upload_max_bytes if is_upload else self.json_max_bytes
        too_large_error = "upload_rejected" if is_upload else "request_too_large"
        headers = {k.decode("latin-1").lower(): v for k, v in scope.get("headers", [])}
        declared = headers.get("content-length")
        if declared is not None:
            try:
                declared_bytes = int(declared)
            except ValueError:
                await self._error(send, 400, "invalid_request", "invalid Content-Length")
                return
            if declared_bytes < 0:
                await self._error(send, 400, "invalid_request", "invalid Content-Length")
                return
            if declared_bytes > limit:
                await self._error(send, 413, too_large_error, "request body is too large")
                return

        received = 0
        exceeded = False
        pending_response: list[dict[str, Any]] = []

        async def counting_receive() -> Any:
            nonlocal received, exceeded
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def buffering_send(message: dict[str, Any]) -> None:
            pending_response.append(message)

        await self.app(scope, counting_receive, buffering_send)
        if exceeded:
            await self._error(send, 413, too_large_error, "request body is too large")
            return
        for message in pending_response:
            await send(message)

    @staticmethod
    async def _error(send: Any, status: int, error: str, detail: str) -> None:
        body = json.dumps({"error": error, "detail": detail}, separators=(",", ":")).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


DEMO_QUESTIONS: list[dict[str, str]] = [
    {
        "id": "margin",
        "question": "Revenue increased in Q3 2025, but gross margin fell. What caused it?",
        "why": "Time series, discounting and product mix, across parallel tasks.",
    },
    {
        "id": "returns",
        # Reworded, because the old wording did not mean one thing.
        #
        # "Which customer segments are driving the increase in return rate?"
        # names no baseline and no comparison window, and "driving" has two
        # readings that give different answers: the groups whose own rate
        # rose most, and the groups that contributed most to the overall
        # change through their rate *and* their share. A suggested question
        # is a promise about what the engine will do, and that one could not
        # keep it. It is kept in `tests/unit/test_question_intent.py` as
        # the case that must surface its own ambiguity instead.
        "question": (
            "Which customer segment has the highest return rate, and are the "
            "differences statistically significant?"
        ),
        "why": "Segmentation with a chi-square test of independence.",
    },
    {
        "id": "shipping",
        "question": "Do shipping delays appear to affect repeat purchasing?",
        "why": "Joined cohorts, a two-proportion z-test, and a rejected causal claim.",
    },
]


#: Where the Streamable HTTP MCP endpoint is served.
MCP_PATH = "/mcp"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application."""
    cfg = settings or get_settings()
    configure_logging(cfg.log_level, cfg.log_json)

    sessions = SessionManager(
        ttl_seconds=cfg.session_ttl_seconds, max_sessions=cfg.max_concurrent_sessions
    )
    runs = RunRegistry()
    upload_limiter = RateLimit(cfg.uploads_per_ip_per_hour, 3600.0)
    analysis_limiter = RateLimit(cfg.analyses_per_ip_per_hour, 3600.0)
    analysis_capacity = Capacity(cfg.max_concurrent_analyses)
    # Opened once. `None` means AI is not offered. There is deliberately no
    # in-memory fallback, because process-local counters are exactly the
    # control the durable ledger exists to replace.
    ledger = open_ledger(cfg.ai_quota_redis_url) if cfg.ai_analytics_enabled else None
    ai_capacity = Capacity(cfg.ai_concurrent_runs)
    # Uploaded bytes live here, never in the repository or the working
    # directory, and every session's directory is removed when it ends.
    upload_root = cfg.upload_dir
    upload_limits = UploadLimits(
        max_bytes=cfg.budgets.max_upload_bytes,
        max_columns=cfg.budgets.max_upload_columns,
        max_column_name_length=cfg.budgets.max_column_name_length,
        max_row_groups=cfg.budgets.max_parquet_row_groups,
        max_metadata_bytes=cfg.budgets.max_parquet_metadata_bytes,
        max_uncompressed_bytes=cfg.budgets.max_parquet_uncompressed_bytes,
    )
    # One source for both: the badge and the privacy claim must not be able
    # to disagree with the mode selector beside them.
    _ai_offered = ai_availability(cfg, ledger_ready=ledger is not None).available
    mode = execution_mode(cfg.live_analytics_enabled, _ai_offered)
    # Identifies this application object for the life of the process. An
    # external checker compares it across a load test: the same id means the
    # process it started with is the process it finished with, which is how
    # an OOM kill and restart is detected from outside. Not a secret.
    instance_id = uuid.uuid4().hex
    revision = build_sha()
    # Every session's DuckDB connection is built with the configured
    # envelope, so the deployment's instance size is what decides it.
    engine_limits = EngineLimits.from_settings(cfg)
    recordings = RecordingStore(cfg.recordings_dir)
    mcp = build_server(sessions, cfg)
    # Streamable HTTP, with JSON responses so a plain HTTP client can talk to
    # it as easily as an SDK client can.
    #
    # This transport is for callers *outside* this process. The website does
    # not use it: `run_analysis` is handed the server object and the agent's
    # `mcp.Client` connects to it in-process.
    #
    # The SDK turns on DNS-rebinding protection by itself only when the server
    # binds to localhost. A container binds to every interface, so the Host
    # allow-list has to be configured explicitly with the hostnames the
    # deployment answers on. A network binding that declares none does not
    # get the endpoint served without validation. It gets no endpoint at
    # all, a 503, which is what `mcp_enabled` below carries. Analysis is
    # unaffected, because it was never using this transport.
    allowed_hosts = cfg.mcp_allowed_host_list
    transport_security, mcp_enabled = _mcp_transport_security(cfg, allowed_hosts)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        # Passed explicitly rather than left to default: the SDK infers
        # localhost-only protection from a loopback host, which would reject
        # every request once the app is behind a real hostname.
        transport_security=transport_security,
        max_request_body_size=2 * 1024 * 1024,
    )

    async def janitor() -> None:
        """Expire sessions on a clock.

        Without this, expiry only happens when some later request calls into
        the manager, so an abandoned upload keeps its DuckDB connection and
        its bytes resident for as long as the demo stays quiet. A sweep that
        raises must not take the application down with it, so the loop logs
        and continues; only cancellation ends it.
        """
        while True:
            await asyncio.sleep(cfg.session_sweep_seconds)
            try:
                # Ask which sessions are due, cancel their runs, and only
                # then close them. Expiring a session out from under a live
                # analysis is the same use-after-close as deleting one.
                # Sessions whose close was deferred because a run was
                # still alive. Retried first, so a stuck teardown finishes
                # as soon as its work does rather than waiting for a TTL.
                for session_id in sessions.closing_session_ids():
                    await _close_session(session_id, "the dataset was closed")
                for session_id in sessions.stale_session_ids():
                    await _close_session(session_id, "the dataset session expired")
                closed = sessions.expire_stale()
            except Exception:  # pragma: no cover - defensive
                log.exception("session_sweep_failed")
            else:
                if closed:
                    log.info("sessions_expired", count=closed)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # The MCP session manager needs its own lifespan to run.
        async with mcp_app.router.lifespan_context(mcp_app):
            recordings.load()
            log.info(
                "startup",
                version=__version__,
                build_sha=revision,
                provider=cfg.provider_mode,
                live=cfg.live_analytics_enabled,
                recordings=len(recordings),
                warehouse_ready=_warehouse_ready(cfg),
            )
            sweeper = asyncio.create_task(janitor())
            try:
                yield
            finally:
                # Cancel *and await*: a task that is only cancelled may not
                # have unwound by the time the process exits.
                sweeper.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sweeper
                await runs.shutdown()
                sessions.close_all()

    app = FastAPI(
        title="Agentic Analytics Engine",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if cfg.api_docs_enabled else None,
        redoc_url="/api/redoc" if cfg.api_docs_enabled else None,
        openapi_url="/api/openapi.json" if cfg.api_docs_enabled else None,
    )
    app.add_middleware(
        _RequestBodyLimitMiddleware,
        json_max_bytes=_MAX_JSON_BODY_BYTES,
        upload_max_bytes=cfg.budgets.max_upload_bytes + _MULTIPART_OVERHEAD_BYTES,
    )

    @app.middleware("http")
    async def _response_policy(request: Request, call_next: Any) -> Response:
        """Stop private responses being cached, and set browser defaults.

        Everything under `/api/` is derived from a particular visitor's
        session -- their uploaded rows, their run, their results, so none
        of it may sit in a shared cache or come back from the bfcache after
        the session has been deleted. Fingerprinted frontend assets are
        deliberately left alone; they are public and immutable, and caching
        them is the point.

        Vega requires `unsafe-eval` for compiled chart expressions. That
        exception is limited to scripts; sources, connections, objects,
        framing, forms and base URIs remain explicitly constrained.
        """
        response: Response = await call_next(request)
        if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", _PERMISSIONS_POLICY)
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if cfg.session_cookie_secure:
            # Do not includeSubDomains: onrender.com is a shared parent that
            # this service neither owns nor controls.
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        response.headers.setdefault("Content-Security-Policy", _CSP)
        return response

    # No CORS middleware. The frontend is served from this same origin, so
    # there is no cross-origin consumer to permit, and the API is not
    # credential-free as an earlier comment here claimed: it authorises on an
    # HttpOnly capability cookie, which is exactly the kind of ambient
    # credential a permissive origin policy exists to protect. A future
    # cross-origin client would get an explicit allow-list, never "*".

    # ------------------------------------------------------------- errors
    @app.exception_handler(DatasetError)
    async def _dataset_error(_: Request, exc: DatasetError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(error="dataset_error", detail=str(exc)).model_dump(),
        )

    @app.exception_handler(UploadError)
    async def _upload_error(_: Request, exc: UploadError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(error="upload_rejected", detail=str(exc)).model_dump(),
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Never leak a stack trace, a path or a provider message to a client.
        log.exception("unhandled_error", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="internal_error", detail="the request could not be completed"
            ).model_dump(),
        )

    # -------------------------------------------------------------- health
    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Liveness. 200 whenever this process can answer at all.

        Deliberately not conditional on the demo warehouse: a platform uses
        this to decide whether to restart, and restarting will not build a
        warehouse. Readiness is `/api/ready`.
        """
        return HealthResponse(
            version=__version__,
            build_sha=revision,
            instance_id=instance_id,
            provider_mode=cfg.provider_mode,
            execution_mode=mode,
            live_analytics_enabled=cfg.live_analytics_enabled,
            demo_warehouse_ready=_warehouse_ready(cfg),
            recordings=len(recordings),
        )

    @app.get("/api/ready", response_model=ReadinessResponse)
    async def ready(response: Response) -> ReadinessResponse:
        """Readiness. 200 only when this process can actually serve the demo.

        `/api/health` answers 200 for a process with no demo warehouse, which
        is alive and useless, and a health check that accepts it lets a
        broken deploy go live and stay live. Both facts checked here are
        local to this container; readiness never depends on the network.
        """
        warehouse = _warehouse_ready(cfg)
        loaded = len(recordings) > 0
        if warehouse and loaded:
            return ReadinessResponse(
                status="ready", demo_warehouse_ready=True, recordings_loaded=True
            )
        missing = [
            name
            for name, present in (("demo warehouse", warehouse), ("recordings", loaded))
            if not present
        ]
        response.status_code = 503
        return ReadinessResponse(
            status="not_ready",
            demo_warehouse_ready=warehouse,
            recordings_loaded=loaded,
            detail=f"not available in this image: {', '.join(missing)}",
        )

    @app.get("/api/config", response_model=ServerConfig)
    async def config() -> ServerConfig:
        return ServerConfig(
            version=__version__,
            build_sha=revision,
            provider_mode=cfg.provider_mode,
            execution_mode=mode,
            # Only an actual language model sends anything off this server.
            model_inference_remote=_ai_offered,
            live_analytics_enabled=cfg.live_analytics_enabled,
            uploads_enabled=cfg.uploads_enabled and cfg.live_analytics_enabled,
            mcp_remote_enabled=mcp_enabled,
            demo_warehouse_ready=_warehouse_ready(cfg),
            max_upload_mb=cfg.budgets.max_upload_bytes // (1024 * 1024),
            max_upload_columns=cfg.budgets.max_upload_columns,
            session_ttl_minutes=int(cfg.session_ttl_seconds // 60),
            budgets=cfg.budgets.model_dump(),
            demo_questions=DEMO_QUESTIONS,
            recordings=recordings.index(),
            capabilities=_capabilities(),
        )

    def _capabilities() -> Capabilities:
        """What the mode selector may offer, recomputed per request.

        Cheap and side-effect free. Whether the *model* resolves is a
        separate cached preflight; this says whether the deployment is
        configured to try.
        """
        availability = ai_availability(cfg, ledger_ready=ledger is not None)
        deterministic = ModeCapability(
            mode=str(RunMode.DETERMINISTIC),
            available=cfg.live_analytics_enabled,
            label="Deterministic Analytics",
            description=(
                "Agent decisions come from a scripted provider, so the same "
                "question produces the same plan every time. No external "
                "language model is used and nothing leaves this server."
            ),
            reason="" if cfg.live_analytics_enabled else "live_analytics_disabled",
            message=(
                ""
                if cfg.live_analytics_enabled
                else "Live analysis is disabled on this server; open a recorded run."
            ),
        )
        ai = ModeCapability(
            mode=str(RunMode.AI),
            available=availability.available and cfg.live_analytics_enabled,
            label="AI Analytics",
            description=(
                "A cloud language model interprets the question and chooses "
                "which analyses to run. The computation, the verification and "
                "the publication checks stay deterministic."
            ),
            reason=availability.reason,
            message=availability.message,
        )
        automatic = ModeCapability(
            mode=str(RunMode.AUTO),
            # Available wherever live analysis is, because the rules are
            # always available. A deployment with no cloud planner answers
            # every question the rules can resolve and says precisely what
            # it could not resolve, which is a weaker product, not a
            # broken one, and so not a reason to withhold the mode.
            available=cfg.live_analytics_enabled,
            label="Governed Analysis",
            description=(
                "Rule-based planning resolves the question when it can, which "
                "costs nothing and happens for most questions. A cloud model "
                "is consulted only when the wording is genuinely ambiguous, "
                "and never to compute, verify or approve a result."
            ),
            reason="" if cfg.live_analytics_enabled else "live_analytics_disabled",
            message=(
                ""
                if cfg.live_analytics_enabled
                else "Live analysis is disabled on this server; open a recorded run."
            ),
        )
        return Capabilities(
            modes=[automatic, deterministic, ai],
            compare_available=deterministic.available and ai.available,
            ai_limits=(
                AILimits(
                    runs_per_session=cfg.ai_runs_per_session,
                    max_model_calls_per_run=cfg.ai_max_llm_calls,
                    max_runtime_seconds=cfg.ai_max_runtime_seconds,
                )
                if ai.available
                else None
            ),
        )

    # ------------------------------------------------------- session cookie
    def _ensure_upload_root() -> Path:
        upload_root.mkdir(parents=True, exist_ok=True)
        return upload_root

    def _issue(response: Response, session: AnalysisSession) -> None:
        """Hand the capability to the browser as an HttpOnly cookie.

        Kept out of the response body and out of URLs, so it does not reach
        browser history, access logs or `Referer` headers.

        `Secure` comes from configuration, not from `request.url.scheme`. A
        TLS-terminating proxy forwards plain HTTP to this process, so the
        scheme the application sees is `http` on a site that is HTTPS for
        every browser that visits it, and the cookie would go out without
        `Secure` on exactly the deployment that needs it. No `Domain`
        attribute, so the cookie stays host-only.
        """
        response.set_cookie(
            cfg.session_cookie_name,
            session.session_key,
            max_age=int(cfg.session_ttl_seconds),
            httponly=True,
            samesite="lax",
            secure=cfg.session_cookie_secure,
            path="/",
        )

    def _clear(response: Response) -> None:
        # Same attributes as `_issue`. A browser matches a deletion against
        # name, path and domain; differing on `Secure` here is what leaves a
        # stale cookie behind on the hosted site.
        response.delete_cookie(
            cfg.session_cookie_name,
            path="/",
            httponly=True,
            samesite="lax",
            secure=cfg.session_cookie_secure,
        )

    async def _close_session(session_id: str, reason: str) -> bool:
        """Stop the session's runs, then close it. Never the other way round.

        Returns True when the session was actually destroyed.

        This is the teardown contract in one place, and the ordering is the
        contract. An analysis holds the `AnalysisSession` and therefore its
        DuckDB connection; closing the session while a run can still make a
        tool call hands that run a closed connection.

        The session is marked *closing* first, so it stops accepting new
        analyses while cancellation is in flight. Then every run is
        cancelled and awaited. If any run is still alive when the grace
        period expires, the session stays open and stays closing: the
        janitor retries. Closing anyway, which is what suppressing the
        timeout amounted to -- is the use-after-close this exists to stop.
        """
        sessions.begin_closing(session_id)
        result = await runs.cancel_session_detailed(
            session_id, reason=reason, grace_seconds=cfg.session_cancel_grace_seconds
        )
        if not result.all_terminal:
            log.warning(
                "session_close_deferred",
                session_id=session_id,
                active_run_ids=result.active_run_ids,
                timed_out=result.timed_out,
            )
            return False
        sessions.drop(session_id)
        return True

    async def _retire_previous(request: Request) -> None:
        """End whatever session this browser already holds.

        Opening a second dataset replaces the capability cookie, so the first
        session becomes unreachable while still holding a DuckDB connection
        and, for an upload, the rows themselves. A visitor clicking through
        four datasets would leave three of those behind until the TTL caught
        them. Keyed on the capability, so it can only ever close a session
        the caller could already reach, and any analysis still running in
        it is cancelled before it is closed.
        """
        key = request.cookies.get(cfg.session_cookie_name)
        for session_id in sessions.session_ids_for_key(key):
            await _close_session(session_id, "the dataset was replaced")

    async def _make_room_for_a_session() -> None:
        """Cancel the runs of whichever session is about to be evicted.

        `SessionManager.add` evicts the least recently used session when it
        is full. Left alone that closes a session an analysis may still be
        using, so the candidate is asked for first and its runs stopped.
        """
        candidate = sessions.next_eviction_candidate()
        if candidate is not None:
            await _close_session(candidate, "the demo reached its session limit")

    def _session_or_404(session_id: str, request: Request) -> AnalysisSession:
        """Resolve a session from its handle plus the capability cookie.

        The same 404 is returned for an unknown handle and for a wrong
        capability, so a caller cannot probe for which handles exist.
        """
        try:
            return sessions.get(session_id, request.cookies.get(cfg.session_cookie_name))
        except KeyError:
            raise HTTPException(
                status_code=404, detail="unknown or expired dataset session"
            ) from None

    def _summary(session: AnalysisSession) -> dict[str, Any] | None:
        """The deterministic profile shown before the first question."""
        try:
            table = next(iter(session.tables))
        except StopIteration:
            return None
        try:
            # The effective schema, not the raw inference: a reader looking
            # at this panel must see the roles the engine will actually use,
            # including any this session has settled.
            schema = effective_schema(session, table)
        except Exception:
            log.warning("profile_failed", kind=session.kind)
            return None
        return schema.as_dict() | {"headline": schema.summary_line()}

    # ------------------------------------------------------------ datasets
    @app.post("/api/datasets/demo", response_model=SessionResponse)
    async def open_demo(request: Request, response: Response) -> SessionResponse:
        if not _warehouse_ready(cfg):
            raise DatasetError("the demo warehouse has not been generated on this server")
        await _retire_previous(request)
        await _make_room_for_a_session()
        session = sessions.add(open_demo_session(cfg.demo_warehouse_dir, limits=engine_limits))
        _issue(response, session)
        return SessionResponse(
            session_id=session.session_id,
            catalog=session.catalog(),
            metrics=session.registry.describe_all() if session.registry else [],
            summary=_summary(session),
            expires_in_seconds=cfg.session_ttl_seconds,
        )

    @app.post("/api/datasets/upload", response_model=SessionResponse)
    async def upload(request: Request, response: Response, file: UploadFile) -> SessionResponse:
        """Accept one CSV or Parquet file into a fresh isolated session.

        The refusal checks run before any bytes are read, so a disabled
        service or a rate-limited client never causes an allocation.
        """
        if not (cfg.uploads_enabled and cfg.live_analytics_enabled):
            raise HTTPException(status_code=403, detail="uploads are disabled on this server")

        client = client_key(
            request.headers.get("x-forwarded-for"), request.client.host if request.client else None
        )
        allowed, retry_after = upload_limiter.check(client)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="too many uploads from this address; try again shortly",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        await _retire_previous(request)
        await _make_room_for_a_session()
        if sessions.upload_count() >= cfg.max_active_upload_sessions:
            raise HTTPException(
                status_code=429,
                detail="the demo is at capacity for uploaded datasets; try again shortly",
            )

        # Every session's bytes live in their own directory, which is removed
        # with the session. The user's filename is never a path component.
        scratch = Path(tempfile.mkdtemp(prefix="aae_", dir=_ensure_upload_root()))
        try:
            stored = store_upload(
                file.file, file.filename or "upload", scratch, cfg.budgets.max_upload_bytes
            )
            if stored.file_format == "parquet":
                shape = inspect_parquet(stored.path, upload_limits)
                if shape["rows"] > cfg.budgets.max_upload_rows:
                    raise UploadError(
                        f"the file has {shape['rows']:,} rows; "
                        f"the limit is {cfg.budgets.max_upload_rows:,}"
                    )
            else:
                inspect_csv_header(stored.path.read_bytes()[:8192], upload_limits)

            session = sessions.add(
                open_upload_session(
                    stored.path,
                    stored.display_name,
                    stored.file_format,
                    max_rows=cfg.budgets.max_upload_rows,
                    scratch_dir=scratch,
                    limits=engine_limits,
                )
            )
        except BaseException:
            shutil.rmtree(scratch, ignore_errors=True)
            raise
        # The rows are in DuckDB now; the file itself is no longer needed.
        stored.unlink()

        if len(session.tables[next(iter(session.tables))].columns) > cfg.budgets.max_upload_columns:
            sessions.drop(session.session_id)
            raise UploadError(f"the file has more than {cfg.budgets.max_upload_columns} columns")

        _issue(response, session)
        log.info("upload_accepted", session_id=session.session_id, format=stored.file_format)
        return SessionResponse(
            session_id=session.session_id,
            catalog=session.catalog(),
            summary=_summary(session),
            expires_in_seconds=cfg.session_ttl_seconds,
        )

    @app.get("/api/datasets/{session_id}")
    async def dataset(session_id: str, request: Request) -> SessionResponse:
        session = _session_or_404(session_id, request)
        return SessionResponse(
            session_id=session.session_id,
            catalog=session.catalog(),
            metrics=session.registry.describe_all() if session.registry else [],
            summary=_summary(session),
            expires_in_seconds=cfg.session_ttl_seconds,
        )

    @app.patch("/api/datasets/{session_id}/schema/roles")
    async def confirm_schema_roles(
        session_id: str, body: RoleConfirmationRequest, request: Request
    ) -> SessionResponse:
        """Settle a role the data cannot decide, for this session only.

        Inference reports a close call when a numeric column sits in the
        band where a code list and a genuine count look the same. The person
        who uploaded the file knows which it is; nothing in the values does.

        Everything is validated before anything is applied, so a batch wrong
        in its third change does not leave the first two in place. The order
        below is the order the refusals matter in: who is asking, whether
        this kind of dataset has anything to confirm, whether the session is
        still taking work, whether a run is relying on the current schema,
        and only then whether the changes themselves make sense.
        """
        session = _session_or_404(session_id, request)

        if session.registry is not None:
            # The demo warehouse has governed metric definitions. Its roles
            # are not inferred, so there is no close call to settle.
            raise HTTPException(
                status_code=422,
                detail="this dataset's roles are governed definitions, not inferences",
                headers={"X-Refusal-Reason": "not_an_upload"},
            )

        if not session.accepts_new_work:
            raise HTTPException(
                status_code=409,
                detail="this dataset session is closing",
                headers={"X-Refusal-Reason": "session_closing"},
            )

        # A run holds a schema snapshot for its whole execution. Changing the
        # roles underneath it would leave evidence describing a schema the run
        # never used. Scoped to this session: another visitor's analysis is
        # none of this session's business.
        if runs.has_active_run_for_session(session_id):
            raise HTTPException(
                status_code=409,
                detail="an analysis is running on this dataset; try again when it finishes",
                headers={"X-Refusal-Reason": "active_run"},
            )

        try:
            table = next(iter(session.tables))
        except StopIteration:
            raise HTTPException(
                status_code=422,
                detail="this session has no table to describe",
                headers={"X-Refusal-Reason": "no_table"},
            ) from None

        inferred = infer_schema(session, table)

        changes: dict[str, str | None] = {}
        confirmations_to_check: dict[str, str] = {}
        for change in body.changes:
            if change.action is RoleAction.RESET:
                changes[change.column] = None
            else:
                role = str(change.role)
                changes[change.column] = role
                confirmations_to_check[change.column] = role

        # Validate the confirmations against the real inferred schema before
        # touching session state.
        try:
            validate_role_confirmations(inferred, confirmations_to_check)
        except RoleConfirmationError as exc:
            raise HTTPException(
                status_code=422,
                detail=str(exc),
                headers={"X-Refusal-Reason": exc.reason},
            ) from None

        # A reset names a column too, and a reset of something that is not a
        # column is as much a mistake as a confirmation of one.
        known = {f.name for f in inferred.fields}
        for column, requested in changes.items():
            if requested is None and column not in known:
                raise HTTPException(
                    status_code=422,
                    detail=f"{column!r} is not a column of this table",
                    headers={"X-Refusal-Reason": "unknown_column"},
                )

        try:
            session.apply_role_confirmation_changes(
                changes, expected_revision=body.expected_revision
            )
        except StaleSchemaRevision as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
                headers={"X-Refusal-Reason": "stale_revision"},
            ) from None

        session.touch()
        return SessionResponse(
            session_id=session.session_id,
            catalog=session.catalog(),
            metrics=session.registry.describe_all() if session.registry else [],
            summary=_summary(session),
            expires_in_seconds=cfg.session_ttl_seconds,
        )

    @app.delete("/api/datasets/{session_id}")
    async def close_dataset(
        session_id: str, request: Request, response: Response
    ) -> dict[str, str]:
        """End a session and erase its data.

        Requires the capability, so one visitor cannot delete another's
        session by guessing a handle.
        """
        _session_or_404(session_id, request)
        destroyed = await _close_session(session_id, "the dataset was deleted")
        _clear(response)
        if destroyed:
            return {"status": "deleted"}
        # Honest about what happened. The analysis is cancelled and the
        # session accepts nothing new, but its rows are still resident
        # because a run has not finished letting go of them. Saying
        # "deleted" here would be a claim about data that still exists.
        response.status_code = 202
        return {
            "status": "closing",
            "detail": (
                "the dataset is closing: its analysis was cancelled and no new "
                "work is accepted, and the data is removed once that finishes"
            ),
        }

    # ------------------------------------------------------------ analyses
    @app.post("/api/analyses", response_model=AnalysisStarted, status_code=202)
    async def start_analysis(request: AnalysisRequest, http_request: Request) -> AnalysisStarted:
        if not cfg.live_analytics_enabled:
            raise HTTPException(
                status_code=403,
                detail="live analysis is disabled on this server; open a recorded run",
            )
        client = client_key(
            http_request.headers.get("x-forwarded-for"),
            http_request.client.host if http_request.client else None,
        )
        allowed, retry_after = analysis_limiter.check(client)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="too many analyses from this address; try again shortly",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        mode = request.mode
        session = _session_or_404(request.session_id, http_request)
        if not session.accepts_new_work:
            # Closing or closed. Starting an analysis here would attach a
            # new run to a connection that is on its way out.
            raise HTTPException(
                status_code=409,
                detail="this dataset is closing and cannot accept new analyses",
            )
        if runs.session_run_count(session.session_id) >= cfg.analyses_per_session:
            raise HTTPException(
                status_code=429,
                detail="this session has reached its analysis limit; start a new one",
            )
        # Before a model call, before a slot, before anything billable: is
        # this a question about the data at all? A public demo that
        # dispatches a model on anything it is handed spends a shared quota
        # on runs that cannot produce a finding, which takes the tool away
        # from people it could have served.
        verdict = check_scope(
            request.question,
            session.catalog(),
            session.registry.describe_all() if session.registry else [],
        )
        if not verdict.in_scope:
            raise HTTPException(
                status_code=422,
                detail=verdict.message,
                headers={"X-AAE-Reason": verdict.reason},
            )
        # Every side-effect-free check happens before anything is acquired.
        # Acquiring first and refusing afterwards leaked a slot on each
        # unavailable request, so a deployment with AI misconfigured lost
        # capacity to visitors who never got a run.
        if mode is RunMode.AI:
            availability = ai_availability(cfg, ledger_ready=ledger is not None)
            if not availability.available:
                raise HTTPException(
                    status_code=503,
                    detail=availability.message,
                    headers={"X-AAE-Reason": availability.reason},
                )

        with _admission(mode) as permits:
            if not permits.ok:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "AI Analytics is at capacity; please try again shortly"
                        if mode is RunMode.AI
                        else "the demo is currently at capacity; please try again shortly"
                    ),
                )
            record = _launch(session, request.question, mode, client_id=client)
            permits.keep()
        return AnalysisStarted(
            run_id=record.run_id, session_id=session.session_id, question=request.question
        )

    @dataclass
    class _Permits:
        """The slots one run holds, with exactly one release path."""

        general: bool
        ai: bool
        mode: RunMode
        kept: bool = False

        @property
        def ok(self) -> bool:
            return self.general and self.ai

        def keep(self) -> None:
            """Hand ownership to the run task, which releases them."""
            self.kept = True

    def _release(mode: RunMode) -> None:
        """Return both permits. One place, so neither is forgotten."""
        if mode is RunMode.AI:
            ai_capacity.release()
        analysis_capacity.release()

    async def _with_ai_deadline(mode: RunMode, coro: Any) -> Any:
        """A hard ceiling on a whole AI run.

        Node-boundary checks are not enough on their own: one slow provider
        call can outlast the run's own budget, and the ceiling has to bound
        the run rather than the gaps between its steps.
        """
        if mode is not RunMode.AI:
            return await coro
        async with asyncio.timeout(cfg.ai_max_runtime_seconds):
            return await coro

    @contextmanager
    def _admission(mode: RunMode) -> Iterator[_Permits]:
        """Acquire what a run needs, releasing unless the task takes over.

        Refusing after acquiring used to leak a slot on every unavailable
        request, so a deployment with AI misconfigured lost capacity to
        visitors who never got a run.
        """
        general = analysis_capacity.acquire()
        ai = ai_capacity.acquire() if general and mode is RunMode.AI else True
        permits = _Permits(general=general, ai=ai, mode=mode)
        try:
            yield permits
        finally:
            if not permits.kept:
                if permits.ai and mode is RunMode.AI:
                    ai_capacity.release()
                if permits.general:
                    analysis_capacity.release()

    def _launch(
        session: AnalysisSession,
        question: str,
        mode: RunMode,
        comparison_id: str | None = None,
        client_id: str = "",
        role_confirmations: Any | None = None,
    ) -> RunRecord:
        """Start one run. Capacity for it has already been acquired.

        Shared by `/api/analyses` and `/api/comparisons` so a comparison
        child is an ordinary run -- same registry, same event bus, same
        cancellation and teardown -- rather than a second orchestration path
        that would have to reimplement all of it.

        `role_confirmations` pins the generation of the session's schema this
        run executes against. A comparison captures it once and passes the
        same snapshot to both children: capturing per child would let a
        confirmation land between them and produce two runs of one question
        under different schemas, presented side by side as comparable.
        """
        if role_confirmations is None:
            role_confirmations = session.role_confirmation_snapshot()
        record = runs.create(
            session.session_id,
            question,
            mode=str(mode),
            provider_kind=provider_kind(mode),
            comparison_id=comparison_id,
        )
        record.engine_version = __version__
        record.build_sha = revision
        record.dataset_fingerprint = session.dataset_fingerprint
        if mode is RunMode.AI:
            record.requested_model = cfg.cloud_model

        async def execute() -> None:
            provider: LLMProvider | None = None
            #: Set for an automatic run that may consult a cloud planner.
            #: `None` means the rules are the only planner available, which
            #: is a refusal with a reason for an ambiguous question and no
            #: obstacle at all for an exact one.
            planner_factory: Any | None = None
            try:
                if mode is RunMode.AI:
                    # The one governed construction site. Preflight runs
                    # here: ledger health, model resolution, an exact
                    # pricing entry, so a run that cannot be bounded is
                    # refused before its first billable request.
                    assert ledger is not None
                    provider = await open_governed_cloud_provider(
                        cfg,
                        run_id=record.run_id,
                        session_id=session.session_id,
                        client_id=client_id,
                        ledger=ledger,
                    )
                    result = provider.preflight_result
                    record.requested_model = result.requested_model
                    record.resolved_model = result.resolved_model
                    record.pricing_source = result.price.source
                    record.pricing_reviewed = result.price.reviewed
                else:
                    provider = build_provider_for_mode(cfg, mode)

                if (
                    mode is RunMode.AUTO
                    and ai_availability(cfg, ledger_ready=ledger is not None).available
                ):
                    # Automatic routing needs a way to build the cloud
                    # planner, not a built one. Constructing it is the
                    # ledger admission, so a question the rules resolve
                    # must never reach this, and an exact question never
                    # does, which is what makes the default affordable.
                    #
                    # Availability is checked in the condition above,
                    # cheaply, so the graph knows whether a planner can be
                    # had at all. A run with none still answers everything
                    # the rules can resolve.

                    async def open_planner() -> LLMProvider:
                        assert ledger is not None
                        built = await open_governed_cloud_provider(
                            cfg,
                            run_id=record.run_id,
                            session_id=session.session_id,
                            client_id=client_id,
                            ledger=ledger,
                        )
                        preflight = built.preflight_result
                        record.requested_model = preflight.requested_model
                        record.resolved_model = preflight.resolved_model
                        record.pricing_source = preflight.price.source
                        record.pricing_reviewed = preflight.price.reviewed
                        return built

                    planner_factory = open_planner
            except (ModeUnavailable, PreflightFailed, AIBudgetExceeded) as exc:
                record.error = str(exc)
                record.bus.emit(EventType.RUN_FAILED, reason=record.error)
                record.bus.close()
                _release(mode)
                return
            except Exception as exc:
                # Any other construction failure must also return the slots.
                log.warning(
                    "provider_construction_failed",
                    run_id=record.run_id,
                    error_type=type(exc).__name__,
                )
                record.error = "the analysis could not be started"
                record.bus.emit(EventType.RUN_FAILED, reason=record.error)
                record.bus.close()
                _release(mode)
                return
            try:
                record.result = await _with_ai_deadline(
                    mode,
                    run_analysis(
                        question,
                        session,
                        mcp,
                        settings=cfg,
                        provider=provider,
                        events=record.bus,
                        run_id=record.run_id,
                        open_planner=planner_factory,
                        role_confirmations=role_confirmations,
                    ),
                )
            except TimeoutError:
                record.error = "This AI run reached its time limit."
                record.bus.emit(EventType.RUN_FAILED, reason=record.error)
                record.bus.close()
            except asyncio.CancelledError:
                # The dataset was deleted, replaced or expired. Mark it and
                # end the stream, so a browser waiting on SSE is told rather
                # than left hanging, then re-raise, or the task is never
                # actually cancelled and `cancel_session` waits for a run
                # that has decided to continue.
                record.cancelled = True
                record.error = record.error or "the dataset was closed"
                record.bus.emit(EventType.RUN_CANCELLED, reason=record.error)
                record.bus.close()
                raise
            except Exception as exc:
                log.exception("analysis_failed", run_id=record.run_id)
                record.error = f"the analysis failed ({type(exc).__name__})"
                record.bus.emit(EventType.RUN_FAILED, reason=record.error)
                record.bus.close()
            finally:
                # Runs on every path, cancellation included: the provider is
                # closed and the capacity slot returned, or one visitor
                # deleting a dataset mid-run costs the demo a slot forever.
                record.input_tokens = int(getattr(provider.usage, "input_tokens", 0))
                record.output_tokens = int(getattr(provider.usage, "output_tokens", 0))
                record.provider_attempts = int(getattr(provider.usage, "attempts", 0))
                resolved = getattr(provider, "resolved_model", None)
                if isinstance(resolved, dict):
                    record.resolved_model = str(resolved.get("id", "")) or None
                if record.provider_kind == "cloud" and ledger is not None:
                    record.cost_microdollars = ledger.spent_microdollars(record.run_id)
                if provider is not None:
                    await provider.aclose()
                _release(mode)

        record.task = asyncio.create_task(execute())
        return record

    @app.post("/api/comparisons", response_model=ComparisonStarted, status_code=202)
    async def start_comparison(
        request: ComparisonRequest, http_request: Request
    ) -> ComparisonStarted:
        """One question, both decision paths, one dataset.

        The two runs share a session, so they read the same tables at the
        same fingerprint. Uploading twice would give each side its own
        snapshot and make the comparison meaningless.

        Only the AI side consumes cloud quota. If AI cannot be admitted the
        deterministic run is still started, because a visitor who asked for
        a comparison and can only have half of it is better served with
        half than with an error.
        """
        if not cfg.live_analytics_enabled:
            raise HTTPException(
                status_code=403,
                detail="live analysis is disabled on this server; open a recorded run",
            )
        client = client_key(
            http_request.headers.get("x-forwarded-for"),
            http_request.client.host if http_request.client else None,
        )
        allowed, retry_after = analysis_limiter.check(client)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="too many analyses from this address; try again shortly",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

        session = _session_or_404(request.session_id, http_request)
        verdict = check_scope(
            request.question,
            session.catalog(),
            session.registry.describe_all() if session.registry else [],
        )
        if not verdict.in_scope:
            raise HTTPException(
                status_code=422,
                detail=verdict.message,
                headers={"X-AAE-Reason": verdict.reason},
            )

        availability = ai_availability(cfg, ledger_ready=ledger is not None)
        if not availability.available:
            raise HTTPException(
                status_code=503,
                detail=availability.message,
                headers={"X-AAE-Reason": availability.reason},
            )

        if not session.accepts_new_work:
            raise HTTPException(
                status_code=409,
                detail="this dataset is closing and cannot accept new analyses",
            )
        # Two runs, so two slots against the session ceiling.
        if runs.session_run_count(session.session_id) + 2 > cfg.analyses_per_session:
            raise HTTPException(
                status_code=429,
                detail="this session has reached its analysis limit; start a new one",
            )
        comparison_id = f"cmp_{uuid.uuid4().hex[:12]}"
        # Both children go through the same admission as a lone run. A
        # second hand-rolled acquire/release here is how the slot leak this
        # guards against got written the first time, and `_launch` raising
        # after a bare `acquire()` would leak one every attempt.
        # One snapshot for both children.
        #
        # The two runs answer the same question against the same rows, and a
        # confirmation landing between their starts would give them different
        # schemas, then present them side by side as a like-for-like
        # comparison. Capturing once is what makes the comparison mean what
        # the page says it means.
        shared_roles = session.role_confirmation_snapshot()

        with _admission(RunMode.DETERMINISTIC) as permits:
            if not permits.ok:
                raise HTTPException(
                    status_code=429,
                    detail="the demo is currently at capacity; please try again shortly",
                )
            deterministic = _launch(
                session,
                request.question,
                RunMode.DETERMINISTIC,
                comparison_id,
                client_id=client,
                role_confirmations=shared_roles,
            )
            permits.keep()

        # The AI side needs its own capacity slot and its own analysis slot.
        # Failing to get either leaves the deterministic run untouched.
        ai_record: RunRecord | None = None
        with _admission(RunMode.AI) as ai_permits:
            if ai_permits.ok:
                # The caller's identity, not the anonymous default. Without
                # it the AI half of a comparison is charged to an empty
                # client bucket, and the per-address hourly ceiling is
                # bypassed by asking for Compare Both instead of AI.
                ai_record = _launch(
                    session,
                    request.question,
                    RunMode.AI,
                    comparison_id,
                    client_id=client,
                    role_confirmations=shared_roles,
                )
                ai_permits.keep()

        if ai_record is None:
            ai_record = runs.create(
                session.session_id,
                request.question,
                mode=str(RunMode.AI),
                provider_kind="cloud",
                comparison_id=comparison_id,
            )
            ai_record.error = "AI Analytics is at capacity; please try again shortly"
            ai_record.bus.emit(EventType.RUN_FAILED, reason=ai_record.error)
            ai_record.bus.close()

        return ComparisonStarted(
            comparison_id=comparison_id,
            session_id=session.session_id,
            question=request.question,
            deterministic_run_id=deterministic.run_id,
            ai_run_id=ai_record.run_id,
        )

    @app.get("/api/comparisons/{comparison_id}")
    async def comparison(comparison_id: str, request: Request) -> dict[str, Any]:
        """Both sides, reported separately.

        No merged verdict and no ranking: the two differ in how the analysis
        was planned, which is not evidence that either is more accurate.
        """
        children = runs.by_comparison(comparison_id)
        if not children:
            raise HTTPException(status_code=404, detail="unknown comparison")
        _session_or_404(children[0].session_id, request)
        payload: dict[str, Any] = {
            "comparison_id": comparison_id,
            "session_id": children[0].session_id,
            "question": children[0].question,
        }
        for record in children:
            payload[f"{record.mode}_run"] = record.public()
        return payload

    @app.get("/api/analyses/{run_id}")
    async def analysis(run_id: str, request: Request) -> dict[str, Any]:
        try:
            record = runs.get(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown run") from None
        # A run carries the dataset's results, so reading one needs the same
        # capability as the session it belongs to.
        _session_or_404(record.session_id, request)
        return record.public()

    @app.get("/api/analyses/{run_id}/events")
    async def analysis_events(run_id: str, request: Request) -> StreamingResponse:
        try:
            record = runs.get(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown run") from None
        _session_or_404(record.session_id, request)

        async def stream() -> AsyncIterator[bytes]:
            try:
                async for event in record.bus.subscribe():
                    if await request.is_disconnected():
                        break
                    payload = json.dumps(event.model_dump(), default=str)
                    yield f"event: {event.type}\ndata: {payload}\n\n".encode()
            except asyncio.CancelledError:  # pragma: no cover - client hang-up
                raise
            yield b"event: stream_end\ndata: {}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                # `no-store` for the same reason as every other private
                # response; `no-transform` so a proxy cannot buffer or
                # rewrite the stream and stall the event feed.
                "Cache-Control": "no-store, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ---------------------------------------------------------- recordings
    @app.get("/api/recordings")
    async def list_recordings() -> dict[str, Any]:
        return {"recordings": recordings.index()}

    @app.get("/api/recordings/{recording_id}")
    async def recording(recording_id: str) -> dict[str, Any]:
        try:
            return recordings.get(recording_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown recording") from None

    # ----------------------------------------------------------------- MCP
    # The SDK's Streamable HTTP app is a single route that accepts every
    # method. Adopting that route directly, rather than mounting the app,
    # keeps the endpoint at exactly `/mcp`: a Starlette mount only matches
    # `/mcp/...`, so a bare `/mcp` would depend on a slash redirect that the
    # single-page fallback below would shadow.
    if mcp_enabled:
        app.router.routes.extend(mcp_app.routes)
    else:
        # Fail closed. Serving the endpoint with Host validation disabled
        # would be worse than not serving it: the agent reaches analytics
        # in-process either way, so nothing is lost but the exposure.
        @app.api_route(
            MCP_PATH,
            methods=["GET", "POST", "DELETE"],
            include_in_schema=False,
        )
        async def mcp_disabled() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content=ErrorResponse(
                    error="mcp_endpoint_disabled",
                    detail=(
                        "the remote MCP endpoint is disabled because this server "
                        "binds to a network interface without AAE_MCP_ALLOWED_HOSTS"
                    ),
                ).model_dump(),
            )

    # ------------------------------------------------------------ frontend
    # Resolved from settings, not from this file's location: an installed
    # wheel has no `web/` beside it, so a path relative to the package puts
    # the frontend inside site-packages and every page 404s. The container
    # sets AAE_FRONTEND_DIR, and the container smoke test fetches `/`.
    frontend_dir = cfg.frontend_dir
    if frontend_dir.is_dir():
        log.info("frontend_mounted", directory=str(frontend_dir))
        app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> FileResponse:
            # Any non-API path serves the app shell; routing happens client side.
            # Unknown or deliberately withdrawn API routes must remain JSON
            # 404s. Serving the SPA for `/api/openapi.json` made disabled
            # documentation look publicly available and made client errors
            # indistinguishable from frontend navigation.
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="not found")
            candidate = (frontend_dir / full_path).resolve()
            if (
                full_path
                and candidate.is_file()
                and candidate.is_relative_to(frontend_dir.resolve())
            ):
                return FileResponse(candidate)
            return FileResponse(frontend_dir / "index.html")

    return app


def _mcp_transport_security(
    cfg: Settings, allowed_hosts: list[str]
) -> tuple[TransportSecuritySettings, bool]:
    """Decide Host/Origin policy for the MCP endpoint, failing closed.

    Three cases, and none of them quietly exposes an unprotected endpoint:

    * **Local development**: the server binds to loopback, so only this
      machine can reach it. Protection is enabled with a localhost allow-list,
      which is what the SDK would do on its own.
    * **Network binding with an allow-list**: protection is enabled with the
      declared hostnames.
    * **Network binding without one**: the remote MCP endpoint is disabled
      rather than served with Host validation off. The agent still reaches
      analytics, because it connects to the server object in-process; what is
      withdrawn is the publicly reachable transport.
    """
    if allowed_hosts:
        return (
            TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[*allowed_hosts, *(f"{h}:*" for h in allowed_hosts)],
                allowed_origins=[
                    *(f"https://{h}" for h in allowed_hosts),
                    *(f"http://{h}" for h in allowed_hosts),
                ],
            ),
            True,
        )

    if cfg.is_local_binding:
        return (
            TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver"],
                allowed_origins=[
                    "http://127.0.0.1:*",
                    "http://localhost:*",
                    "http://[::1]:*",
                ],
            ),
            True,
        )

    log.warning(
        "mcp_remote_endpoint_disabled",
        reason="AAE_MCP_ALLOWED_HOSTS is empty and the server is not bound to loopback",
        bind_host=cfg.bind_host,
    )
    return (
        TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[],
            allowed_origins=[],
        ),
        False,
    )


def _warehouse_ready(cfg: Settings) -> bool:
    return (cfg.demo_warehouse_dir / "orders.parquet").exists()


app = create_app()
