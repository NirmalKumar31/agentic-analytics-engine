"""The application must serve its frontend wherever it is installed.

An earlier version resolved the built frontend relative to the package file,
so an installed wheel looked for `web/dist` inside site-packages and every
page returned 404. The container build is the only place that showed it,
which is exactly why these assertions exist.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.config import Settings

REPO = Path(__file__).resolve().parents[2]


def _settings(frontend: Path) -> Settings:
    return Settings(provider_mode="fake", frontend_dir=frontend, log_json=False)


def test_the_app_shell_is_served_from_a_configured_directory(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<!doctype html><div id="root"></div>')
    (dist / "assets" / "app.js").write_text("console.log(1)")

    with TestClient(create_app(_settings(dist))) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert '<div id="root">' in root.text
        assert client.get("/assets/app.js").status_code == 200
        # Any unknown path serves the shell; routing is client side.
        assert client.get("/some/route").status_code == 200


def test_the_frontend_directory_is_configurable_by_environment(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dist = tmp_path / "elsewhere"
    dist.mkdir()
    monkeypatch.setenv("AAE_FRONTEND_DIR", str(dist))
    assert Settings().frontend_dir == dist


def test_the_api_still_works_without_a_built_frontend(tmp_path: Path) -> None:
    """A source checkout that has not run `npm run build` must still serve."""
    with TestClient(create_app(_settings(tmp_path / "missing"))) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/").status_code == 404


def test_the_repo_default_points_at_the_checkout() -> None:
    """The default must be right for `make dev`, the common local path."""
    assert Settings().frontend_dir == REPO / "web" / "dist"


def test_traversal_out_of_the_frontend_directory_is_refused(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id='root'></div>")
    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve me")

    with TestClient(create_app(_settings(dist))) as client:
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
            response = client.get(path)
            assert b"do not serve me" not in response.content, path


def test_the_dockerfile_configures_the_frontend_directory() -> None:
    """The image copies the frontend to /app; it must also point there."""
    dockerfile = (REPO / "Dockerfile").read_text()
    assert "AAE_FRONTEND_DIR=/app/web/dist" in dockerfile
    assert "COPY --from=web /build/dist /app/web/dist" in dockerfile
