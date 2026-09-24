"""The HTTP surface, exercised against a real app instance."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.config import Budgets, Settings


@pytest.fixture
def settings(warehouse_dir: Path, tmp_path: Path) -> Settings:
    return Settings(
        provider_mode="fake",
        live_analytics_enabled=True,
        uploads_enabled=True,
        data_dir=warehouse_dir.parent,
        upload_dir=tmp_path / "uploads",
        recordings_dir=Path(__file__).resolve().parents[2] / "examples" / "recordings",
        budgets=Budgets(max_upload_bytes=1024 * 1024),
        log_json=False,
    )


@pytest.fixture
def client(
    settings: Settings, warehouse_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    # The demo warehouse lives at <data_dir>/commerce; point data_dir at its parent.
    monkeypatch.setattr(type(settings), "demo_warehouse_dir", property(lambda self: warehouse_dir))
    with TestClient(create_app(settings)) as c:
        yield c


def test_health(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["provider_mode"] == "fake"
    assert body["demo_warehouse_ready"] is True


def test_config_exposes_budgets_and_demos(client: TestClient) -> None:
    body = client.get("/api/config").json()
    assert body["budgets"]["max_analysis_tasks"] > 0
    assert len(body["demo_questions"]) == 3
    assert body["max_upload_mb"] == 1


def test_open_demo_dataset(client: TestClient) -> None:
    body = client.post("/api/datasets/demo").json()
    assert body["session_id"].startswith("ses_")
    assert body["catalog"]["dataset_fingerprint"].startswith("sha256:")
    assert {t["name"] for t in body["catalog"]["tables"]} >= {"orders", "customers"}
    assert len(body["metrics"]) > 10


def test_unknown_session_is_404(client: TestClient) -> None:
    assert client.get("/api/datasets/ses_nope").status_code == 404


def test_full_analysis_round_trip(client: TestClient) -> None:
    session_id = client.post("/api/datasets/demo").json()["session_id"]
    started = client.post(
        "/api/analyses",
        json={
            "session_id": session_id,
            "question": "Revenue increased in Q3 2025, but gross margin fell. What caused it?",
        },
    )
    assert started.status_code == 202
    run_id = started.json()["run_id"]

    # Draining the SSE stream also waits for the run to finish.
    events = _drain_events(client, run_id)
    kinds = {e["type"] for e in events}
    assert "run_started" in kinds
    assert "plan_generated" in kinds
    assert "mcp_tool_called" in kinds
    assert "run_completed" in kinds

    body = client.get(f"/api/analyses/{run_id}").json()
    assert body["status"] == "completed"
    assert body["report"] is not None
    assert body["findings"]
    for finding in body["findings"]:
        assert finding["verification_status"] == "supported"
        for result_id in finding["result_ids"]:
            assert result_id in body["results"]


def _drain_events(client: TestClient, run_id: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    with client.stream("GET", f"/api/analyses/{run_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                payload = line[len("data: ") :]
                if payload.strip() in ("{}", ""):
                    continue
                events.append(json.loads(payload))
            if line.startswith("event: stream_end"):
                break
    return events


def test_question_length_is_bounded(client: TestClient) -> None:
    session_id = client.post("/api/datasets/demo").json()["session_id"]
    response = client.post("/api/analyses", json={"session_id": session_id, "question": "x" * 2000})
    assert response.status_code == 422


def test_empty_question_is_rejected(client: TestClient) -> None:
    session_id = client.post("/api/datasets/demo").json()["session_id"]
    response = client.post("/api/analyses", json={"session_id": session_id, "question": "   "})
    assert response.status_code == 422


def test_csv_upload_creates_a_session(client: TestClient) -> None:
    payload = b"region,amount\nWest,10\nEast,25\n"
    response = client.post(
        "/api/datasets/upload",
        files={"file": ("sales.csv", io.BytesIO(payload), "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["catalog"]["tables"][0]["name"] == "uploaded_data"
    assert "sales.csv" in body["catalog"]["source"]


def test_oversized_upload_is_rejected(client: TestClient) -> None:
    payload = b"a,b\n" + b"1,2\n" * 400_000
    response = client.post(
        "/api/datasets/upload",
        files={"file": ("big.csv", io.BytesIO(payload), "text/csv")},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "upload_rejected"


def test_executable_upload_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/datasets/upload",
        files={"file": ("sales.csv", io.BytesIO(b"\x7fELF" + b"\x00" * 500), "text/csv")},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "executable" in detail


def test_upload_filename_never_appears_as_a_path(client: TestClient) -> None:
    response = client.post(
        "/api/datasets/upload",
        files={"file": ("../../etc/passwd.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
    )
    assert response.status_code == 200
    source = response.json()["catalog"]["source"]
    assert ".." not in source and "/etc/" not in source


def test_recordings_are_served_and_valid(client: TestClient) -> None:
    index = client.get("/api/recordings").json()["recordings"]
    assert len(index) == 3
    for entry in index:
        body = client.get(f"/api/recordings/{entry['recording_id']}").json()
        assert body["question"]
        assert body["findings"]
        assert body["mcp_trace"]
        assert body["dataset"]["dataset_fingerprint"].startswith("sha256:")


def test_unknown_recording_is_404(client: TestClient) -> None:
    assert client.get("/api/recordings/nope").status_code == 404


def test_mcp_endpoint_is_mounted(client: TestClient) -> None:
    """The MCP server answers on /mcp in the same process as the API."""
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2026-07-28",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 200, response.text
    assert "agentic-analytics" in response.text


def test_errors_never_leak_internals(client: TestClient) -> None:
    body = client.get("/api/analyses/run_nope")
    assert body.status_code == 404
    assert "Traceback" not in body.text
    assert "/Users/" not in body.text


class TestRecordedOnlyMode:
    """With live analysis disabled the server must refuse to start runs."""

    @pytest.fixture
    def recorded_client(
        self, settings: Settings, warehouse_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[TestClient]:
        monkeypatch.setattr(
            type(settings), "demo_warehouse_dir", property(lambda self: warehouse_dir)
        )
        locked = settings.model_copy(update={"live_analytics_enabled": False})
        with TestClient(create_app(locked)) as c:
            yield c

    def test_analysis_is_refused(self, recorded_client: TestClient) -> None:
        session_id = recorded_client.post("/api/datasets/demo").json()["session_id"]
        response = recorded_client.post(
            "/api/analyses", json={"session_id": session_id, "question": "Why?"}
        )
        assert response.status_code == 403

    def test_upload_is_refused(self, recorded_client: TestClient) -> None:
        response = recorded_client.post(
            "/api/datasets/upload",
            files={"file": ("x.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
        )
        assert response.status_code == 403

    def test_recordings_are_still_available(self, recorded_client: TestClient) -> None:
        assert len(recorded_client.get("/api/recordings").json()["recordings"]) == 3

    def test_config_reports_the_mode(self, recorded_client: TestClient) -> None:
        body = recorded_client.get("/api/config").json()
        assert body["live_analytics_enabled"] is False
        assert body["uploads_enabled"] is False
