"""Small FastAPI interface over the analytical workflow."""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from engine import AnalysisError, Dataset, load_data, schema
from workflow import analyze

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Agentic Analytics Engine")
sessions: dict[str, tuple[str, Dataset]] = {}
MAX_SESSIONS = 3


class Question(BaseModel):
    session_id: str
    question: str


def add_session(dataset: Dataset) -> dict[str, Any]:
    while len(sessions) >= MAX_SESSIONS:
        oldest = next(iter(sessions))
        sessions.pop(oldest)[1].close()
    session_id = secrets.token_urlsafe(24)
    sessions[session_id] = (secrets.token_urlsafe(24), dataset)
    return {"session_id": session_id, "schema": schema(dataset)}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/ready")
def ready() -> dict[str, str]:
    return {"status": "ok" if (ROOT / "data" / "demo.csv").is_file() else "not ready"}


@app.post("/api/demo")
def demo() -> dict[str, Any]:
    try:
        return add_session(load_data((ROOT / "data" / "demo.csv").read_bytes(), "demo.csv"))
    except AnalysisError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read(2_000_001)
    try:
        return add_session(load_data(raw, file.filename or ""))
    except AnalysisError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/analyze")
async def run(body: Question) -> dict[str, Any]:
    item = sessions.get(body.session_id)
    if item is None:
        raise HTTPException(404, "session expired; reload the demo or file")
    try:
        return await analyze(body.question, body.session_id, item[0], item[1], sessions)
    except AnalysisError as exc:
        raise HTTPException(400, str(exc)) from exc
