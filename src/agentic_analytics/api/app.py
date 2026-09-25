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
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings

from agentic_analytics import __version__
from agentic_analytics.analytics.semantic import infer_schema
from agentic_analytics.api.limits import Capacity, RateLimit, client_key
from agentic_analytics.api.models import (
    AnalysisRequest,
    AnalysisStarted,
    ErrorResponse,
    HealthResponse,
    ServerConfig,
    SessionResponse,
    execution_mode,
)
from agentic_analytics.api.runs import RunRegistry
from agentic_analytics.config import Settings, get_settings
from agentic_analytics.events import EventType
from agentic_analytics.graph.runner import run_analysis
from agentic_analytics.llm.registry import build_provider
from agentic_analytics.logging import configure_logging, get_logger
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.recordings.store import RecordingStore
from agentic_analytics.warehouse.session import (
    AnalysisSession,
    DatasetError,
    EngineLimits,
    SessionManager,
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

DEMO_QUESTIONS: list[dict[str, str]] = [
    {
        "id": "margin",
        "question": "Revenue increased in Q3 2025, but gross margin fell. What caused it?",
        "why": "Time series, discounting and product mix, across parallel tasks.",
    },
    {
        "id": "returns",
        "question": "Which customer segments are driving the increase in return rate?",
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
    # Uploaded bytes live here, never in the repository or the working
    # directory, and every session's directory is removed when it ends.
    upload_root = cfg.upload_dir
    upload_limits = UploadLimits(
        max_bytes=cfg.budgets.max_upload_bytes,
        max_columns=cfg.budgets.max_upload_columns,
        max_column_name_length=cfg.budgets.max_column_name_length,
        max_row_groups=cfg.budgets.max_parquet_row_groups,
        max_metadata_bytes=cfg.budgets.max_parquet_metadata_bytes,
    )
    mode = execution_mode(cfg.live_analytics_enabled, cfg.provider_mode)
    # Every session's DuckDB connection is built with the configured
    # envelope, so the deployment's instance size is what decides it.
    engine_limits = EngineLimits.from_settings(cfg)
    recordings = RecordingStore(cfg.recordings_dir)
    mcp = build_server(sessions, cfg)
    # Streamable HTTP, with JSON responses so a plain HTTP client can talk to
    # it as easily as an SDK client can.
    #
    # The SDK turns on DNS-rebinding protection by itself only when the server
    # binds to localhost. A container binds to every interface, so the Host
    # allow-list is configured explicitly when the deployment knows its own
    # hostname. With no allow-list the protection is off, which is the
    # documented default: the endpoint is read-only, holds no credential, and
    # every tool requires a session id the caller must already have.
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
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    @app.middleware("http")
    async def _response_policy(request: Request, call_next: Any) -> Response:
        """Stop private responses being cached, and set browser defaults.

        Everything under `/api/` is derived from a particular visitor's
        session -- their uploaded rows, their run, their results -- so none
        of it may sit in a shared cache or come back from the bfcache after
        the session has been deleted. Fingerprinted frontend assets are
        deliberately left alone; they are public and immutable, and caching
        them is the point.

        The CSP here restricts framing and base URIs only. A `script-src`
        policy would need `unsafe-eval` for Vega, which compiles chart
        expressions with `new Function`, and a CSP that has to allow eval to
        work is not buying protection worth the risk of breaking charts.
        """
        response: Response = await call_next(request)
        if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'"
        )
        return response

    # No CORS middleware. The frontend is served from this same origin, so
    # there is no cross-origin consumer to permit -- and the API is not
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
        return HealthResponse(
            version=__version__,
            provider_mode=cfg.provider_mode,
            execution_mode=mode,
            live_analytics_enabled=cfg.live_analytics_enabled,
            demo_warehouse_ready=_warehouse_ready(cfg),
            recordings=len(recordings),
        )

    @app.get("/api/config", response_model=ServerConfig)
    async def config() -> ServerConfig:
        return ServerConfig(
            version=__version__,
            provider_mode=cfg.provider_mode,
            execution_mode=mode,
            # Only an actual language model sends anything off this server.
            model_inference_remote=cfg.provider_mode == "cloud",
            live_analytics_enabled=cfg.live_analytics_enabled,
            uploads_enabled=cfg.uploads_enabled and cfg.live_analytics_enabled,
            demo_warehouse_ready=_warehouse_ready(cfg),
            max_upload_mb=cfg.budgets.max_upload_bytes // (1024 * 1024),
            max_upload_columns=cfg.budgets.max_upload_columns,
            session_ttl_minutes=int(cfg.session_ttl_seconds // 60),
            budgets=cfg.budgets.model_dump(),
            demo_questions=DEMO_QUESTIONS,
            recordings=recordings.index(),
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
        every browser that visits it -- and the cookie would go out without
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

    def _retire_previous(request: Request) -> None:
        """End whatever session this browser already holds.

        Opening a second dataset replaces the capability cookie, so the first
        session becomes unreachable while still holding a DuckDB connection
        and, for an upload, the rows themselves. A visitor clicking through
        four datasets would leave three of those behind until the TTL caught
        them. Keyed on the capability, so it can only ever close a session
        the caller could already reach.
        """
        retired = sessions.drop_by_key(request.cookies.get(cfg.session_cookie_name))
        if retired:
            log.info("previous_session_retired", count=retired)

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
            schema = infer_schema(session, table)
        except Exception:
            log.warning("profile_failed", kind=session.kind)
            return None
        return schema.as_dict() | {"headline": schema.summary_line()}

    # ------------------------------------------------------------ datasets
    @app.post("/api/datasets/demo", response_model=SessionResponse)
    async def open_demo(request: Request, response: Response) -> SessionResponse:
        if not _warehouse_ready(cfg):
            raise DatasetError("the demo warehouse has not been generated on this server")
        _retire_previous(request)
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
        _retire_previous(request)
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

    @app.delete("/api/datasets/{session_id}")
    async def close_dataset(
        session_id: str, request: Request, response: Response
    ) -> dict[str, str]:
        """End a session and erase its data.

        Requires the capability, so one visitor cannot delete another's
        session by guessing a handle.
        """
        _session_or_404(session_id, request)
        sessions.drop(session_id)
        _clear(response)
        return {"status": "deleted"}

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
        session = _session_or_404(request.session_id, http_request)
        if runs.session_run_count(session.session_id) >= cfg.analyses_per_session:
            raise HTTPException(
                status_code=429,
                detail="this session has reached its analysis limit; start a new one",
            )
        if not analysis_capacity.acquire():
            raise HTTPException(
                status_code=429,
                detail="the demo is currently at capacity; please try again shortly",
            )
        record = runs.create(session.session_id, request.question)

        async def execute() -> None:
            provider = build_provider(cfg)
            try:
                record.result = await run_analysis(
                    request.question,
                    session,
                    mcp,
                    settings=cfg,
                    provider=provider,
                    events=record.bus,
                    run_id=record.run_id,
                )
            except Exception as exc:
                log.exception("analysis_failed", run_id=record.run_id)
                record.error = f"the analysis failed ({type(exc).__name__})"
                record.bus.emit(EventType.RUN_FAILED, reason=record.error)
                record.bus.close()
            finally:
                await provider.aclose()
                analysis_capacity.release()

        record.task = asyncio.create_task(execute())
        return AnalysisStarted(
            run_id=record.run_id, session_id=session.session_id, question=request.question
        )

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

    * **Local development** -- the server binds to loopback, so only this
      machine can reach it. Protection is enabled with a localhost allow-list,
      which is what the SDK would do on its own.
    * **Network binding with an allow-list** -- protection is enabled with the
      declared hostnames.
    * **Network binding without one** -- the remote MCP endpoint is disabled
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
