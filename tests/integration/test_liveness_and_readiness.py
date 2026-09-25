"""Liveness, readiness, and how a restart is detected from outside.

Two separate mistakes are guarded here.

The first is a health check that returns 200 for a process which cannot serve
anybody. A container whose demo warehouse never built is alive; a platform
watching `/api/health` would keep it in rotation indefinitely.

The second is a load test claiming to detect an out-of-memory restart by
comparing `version` across it. A restarted process runs the same build and
reports the same version, so that comparison can never fail -- which makes it
worse than no check, because it reads like one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.config import Settings

REPO = Path(__file__).resolve().parents[2]


def _settings(warehouse_dir: Path, tmp_path: Path, **overrides: object) -> Settings:
    class _Pointed(Settings):
        @property
        def demo_warehouse_dir(self) -> Path:
            return warehouse_dir

    defaults: dict[str, object] = {
        "upload_dir": tmp_path / "uploads",
        "recordings_dir": REPO / "examples" / "recordings",
        "log_json": False,
    }
    return _Pointed(**(defaults | overrides))  # type: ignore[arg-type]


# ------------------------------------------------------------- instance id
def test_the_instance_id_is_stable_for_one_process(warehouse_dir: Path, tmp_path: Path) -> None:
    with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
        first = client.get("/api/health").json()["instance_id"]
        second = client.get("/api/health").json()["instance_id"]
        third = client.get("/api/health").json()["instance_id"]
    assert first == second == third
    assert re.fullmatch(r"[0-9a-f]{32}", first), first


def test_two_application_instances_have_different_ids(warehouse_dir: Path, tmp_path: Path) -> None:
    """Which is what makes a replaced process visible to a caller."""
    ids = []
    for _ in range(2):
        with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
            ids.append(client.get("/api/health").json()["instance_id"])
    assert ids[0] != ids[1]


def test_version_alone_cannot_detect_a_restart(warehouse_dir: Path, tmp_path: Path) -> None:
    """States the reason the old check was vacuous.

    Two separate application instances of the same build report the same
    version. Anything comparing only that would have passed across a restart.
    """
    payloads = []
    for _ in range(2):
        with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
            payloads.append(client.get("/api/health").json())
    assert payloads[0]["version"] == payloads[1]["version"]
    assert payloads[0]["instance_id"] != payloads[1]["instance_id"]


def test_the_capacity_smoke_compares_instance_id_not_version() -> None:
    """The script has to use the field that can actually change."""
    source = (REPO / "scripts" / "capacity_smoke.py").read_text()
    restart_check = source[source.index("did not restart under load") - 600 :]
    assert "instance_id" in restart_check
    assert 'health_after.get("version") == health.get("version")' not in source


def test_the_resource_rehearsal_also_checks_the_instance_id() -> None:
    source = (REPO / "scripts" / "resource_rehearsal.py").read_text()
    assert "did not restart under load" in source
    assert "instance_id" in source


# -------------------------------------------------------------- readiness
def test_ready_is_200_when_the_demo_can_be_served(warehouse_dir: Path, tmp_path: Path) -> None:
    with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
        response = client.get("/api/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["demo_warehouse_ready"] is True
    assert body["recordings_loaded"] is True


def test_ready_is_503_without_a_demo_warehouse(tmp_path: Path) -> None:
    """The case a liveness check cannot distinguish from a working service."""
    cfg = _settings(tmp_path / "absent", tmp_path)
    with TestClient(create_app(cfg)) as client:
        health = client.get("/api/health")
        ready = client.get("/api/ready")

    assert health.status_code == 200, "the process is alive and should say so"
    assert ready.status_code == 503, "but it cannot serve the demo"
    assert "demo warehouse" in ready.json()["detail"]


def test_ready_is_503_without_recordings(warehouse_dir: Path, tmp_path: Path) -> None:
    cfg = _settings(warehouse_dir, tmp_path, recordings_dir=tmp_path / "none")
    with TestClient(create_app(cfg)) as client:
        ready = client.get("/api/ready")
    assert ready.status_code == 503
    assert ready.json()["recordings_loaded"] is False
    assert "recordings" in ready.json()["detail"]


def test_readiness_is_not_cacheable(warehouse_dir: Path, tmp_path: Path) -> None:
    """A cached readiness response would outlive the condition it reports."""
    with TestClient(create_app(_settings(warehouse_dir, tmp_path))) as client:
        headers = client.get("/api/ready").headers
    assert headers["cache-control"] == "no-store"


@pytest.mark.parametrize("name", ["render.yaml", "deploy/render-live.yaml"])
def test_the_blueprints_health_check_uses_readiness(name: str) -> None:
    service = yaml.safe_load((REPO / name).read_text())["services"][0]
    assert service["healthCheckPath"] == "/api/ready"


def test_the_container_health_check_uses_readiness() -> None:
    dockerfile = (REPO / "Dockerfile").read_text()
    healthcheck = dockerfile[dockerfile.index("HEALTHCHECK") :]
    assert "/api/ready" in healthcheck
    assert "/api/health" not in healthcheck
