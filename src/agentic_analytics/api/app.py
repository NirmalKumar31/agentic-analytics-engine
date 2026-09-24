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
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings

from agentic_analytics import __version__
from agentic_analytics.api.models import (
    AnalysisRequest,
    AnalysisStarted,
    ErrorResponse,
    HealthResponse,
    ServerConfig,
    SessionResponse,
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
    DatasetError,
    SessionManager,
    open_demo_session,
    open_upload_session,
)
from agentic_analytics.warehouse.upload import UploadError, store_upload

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

FRONTEND_DIR = Path(__file__).resolve().parents[3] / "web" / "dist"

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
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(allowed_hosts),
        allowed_hosts=[*allowed_hosts, *(f"{h}:*" for h in allowed_hosts)],
        allowed_origins=[f"https://{h}" for h in allowed_hosts],
    )
    mcp_app = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        # Passed explicitly rather than left to default: the SDK infers
        # localhost-only protection from a loopback host, which would reject
        # every request once the app is behind a real hostname.
        transport_security=transport_security,
        max_request_body_size=2 * 1024 * 1024,
    )

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
            try:
                yield
            finally:
                await runs.shutdown()
                sessions.close_all()

    app = FastAPI(
        title="Agentic Analytics Engine",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        # The API carries no credentials and no authentication, so a
        # permissive origin policy exposes nothing a direct request would not.
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

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
            live_analytics_enabled=cfg.live_analytics_enabled,
            demo_warehouse_ready=_warehouse_ready(cfg),
            recordings=len(recordings),
        )

    @app.get("/api/config", response_model=ServerConfig)
    async def config() -> ServerConfig:
        return ServerConfig(
            version=__version__,
            provider_mode=cfg.provider_mode,
            live_analytics_enabled=cfg.live_analytics_enabled,
            uploads_enabled=cfg.uploads_enabled and cfg.live_analytics_enabled,
            demo_warehouse_ready=_warehouse_ready(cfg),
            max_upload_mb=cfg.budgets.max_upload_bytes // (1024 * 1024),
            budgets=cfg.budgets.model_dump(),
            demo_questions=DEMO_QUESTIONS,
            recordings=recordings.index(),
        )

    # ------------------------------------------------------------ datasets
    @app.post("/api/datasets/demo", response_model=SessionResponse)
    async def open_demo() -> SessionResponse:
        if not _warehouse_ready(cfg):
            raise DatasetError("the demo warehouse has not been generated on this server")
        session = sessions.add(open_demo_session(cfg.demo_warehouse_dir))
        return SessionResponse(
            session_id=session.session_id,
            catalog=session.catalog(),
            metrics=session.registry.describe_all() if session.registry else [],
        )

    @app.post("/api/datasets/upload", response_model=SessionResponse)
    async def upload(file: UploadFile) -> SessionResponse:
        if not (cfg.uploads_enabled and cfg.live_analytics_enabled):
            raise HTTPException(status_code=403, detail="uploads are disabled on this server")
        stored = store_upload(
            file.file,
            file.filename or "upload",
            cfg.upload_dir,
            cfg.budgets.max_upload_bytes,
        )
        try:
            session = sessions.add(
                open_upload_session(
                    stored.path,
                    stored.display_name,
                    stored.file_format,
                    max_rows=2_000_000,
                )
            )
        finally:
            # The rows are in DuckDB now; the file itself is not kept.
            stored.unlink()
        return SessionResponse(session_id=session.session_id, catalog=session.catalog())

    @app.get("/api/datasets/{session_id}")
    async def dataset(session_id: str) -> SessionResponse:
        session = _session_or_404(session_id)
        return SessionResponse(
            session_id=session.session_id,
            catalog=session.catalog(),
            metrics=session.registry.describe_all() if session.registry else [],
        )

    @app.delete("/api/datasets/{session_id}")
    async def close_dataset(session_id: str) -> dict[str, str]:
        sessions.drop(session_id)
        return {"status": "closed"}

    # ------------------------------------------------------------ analyses
    @app.post("/api/analyses", response_model=AnalysisStarted, status_code=202)
    async def start_analysis(request: AnalysisRequest) -> AnalysisStarted:
        if not cfg.live_analytics_enabled:
            raise HTTPException(
                status_code=403,
                detail="live analysis is disabled on this server; open a recorded run",
            )
        if runs.active_count() >= 4:
            raise HTTPException(status_code=429, detail="too many analyses are already running")
        session = _session_or_404(request.session_id)
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

        record.task = asyncio.create_task(execute())
        return AnalysisStarted(
            run_id=record.run_id, session_id=session.session_id, question=request.question
        )

    @app.get("/api/analyses/{run_id}")
    async def analysis(run_id: str) -> dict[str, Any]:
        try:
            return runs.get(run_id).public()
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown run") from None

    @app.get("/api/analyses/{run_id}/events")
    async def analysis_events(run_id: str, request: Request) -> StreamingResponse:
        try:
            record = runs.get(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown run") from None

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
                "Cache-Control": "no-cache, no-transform",
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
    app.router.routes.extend(mcp_app.routes)

    # ------------------------------------------------------------ frontend
    if FRONTEND_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> FileResponse:
            # Any non-API path serves the app shell; routing happens client side.
            candidate = (FRONTEND_DIR / full_path).resolve()
            if (
                full_path
                and candidate.is_file()
                and candidate.is_relative_to(FRONTEND_DIR.resolve())
            ):
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIR / "index.html")

    def _session_or_404(session_id: str) -> Any:
        try:
            return sessions.get(session_id)
        except KeyError:
            raise HTTPException(
                status_code=404, detail="unknown or expired dataset session"
            ) from None

    return app


def _warehouse_ready(cfg: Settings) -> bool:
    return (cfg.demo_warehouse_dir / "orders.parquet").exists()


app = create_app()
