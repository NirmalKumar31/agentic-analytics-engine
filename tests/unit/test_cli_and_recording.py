"""The command line and the recorder.

`make record` must refuse to write a recording that would fail publication
acceptance, so those paths are exercised here rather than trusted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentic_analytics.cli import app
from agentic_analytics.data.cli import build
from agentic_analytics.data.generator import GeneratorConfig, generate_warehouse
from agentic_analytics.graph.runner import RunResult
from agentic_analytics.recordings.record import (
    RecordingRejected,
    build_recording,
    write_recording,
)

runner = CliRunner()
SMALL = GeneratorConfig(n_customers=1_500, n_products=80, seed=5)


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_generate_data_writes_a_manifest(tmp_path: Path) -> None:
    result = runner.invoke(app, ["generate-data", "--out", str(tmp_path), "--seed", "123"])
    assert result.exit_code == 0, result.stdout
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["seed"] == 123
    assert manifest["dataset_fingerprint"].startswith("sha256:")
    assert manifest["row_counts"]["orders"] > 0


def test_build_returns_a_typed_manifest(tmp_path: Path) -> None:
    manifest = build(tmp_path, seed=7)
    assert manifest.seed == 7
    assert manifest.dataset_fingerprint.startswith("sha256:")
    assert set(manifest.row_counts) >= {"orders", "customers"}


def test_validate_recordings_passes_on_the_committed_set() -> None:
    result = runner.invoke(app, ["validate-recordings"])
    assert result.exit_code == 0, result.stdout
    assert "pass" in result.stdout


def test_validate_recordings_fails_on_a_broken_one(tmp_path: Path) -> None:
    source = Path("examples/recordings/margin-q3.json")
    payload = json.loads(source.read_text())
    payload["findings"][0]["verification_status"] = "unsupported"
    (tmp_path / "broken.json").write_text(json.dumps(payload))
    result = runner.invoke(app, ["validate-recordings", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert "FAIL" in result.stdout


def test_validate_recordings_fails_on_an_empty_directory(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate-recordings", "--directory", str(tmp_path)])
    assert result.exit_code == 1


def _run_result(warehouse: Path) -> RunResult:
    import anyio

    from agentic_analytics.graph.runner import run_analysis
    from agentic_analytics.mcp_layer.server import build_server
    from agentic_analytics.warehouse.session import SessionManager, open_demo_session

    manager = SessionManager()
    session = manager.add(open_demo_session(warehouse))
    server = build_server(manager)

    async def go() -> RunResult:
        return await run_analysis("How did revenue change over 2025?", session, server)

    try:
        return anyio.run(go)
    finally:
        manager.close_all()


@pytest.fixture(scope="module")
def recording_source(tmp_path_factory: pytest.TempPathFactory) -> RunResult:
    warehouse = tmp_path_factory.mktemp("rec")
    generate_warehouse(warehouse, SMALL)
    return _run_result(warehouse)


def test_a_real_run_produces_a_valid_recording(recording_source: RunResult, tmp_path: Path) -> None:
    recording = build_recording(
        recording_source, recording_id="test-run", title="Test", demonstrates="a test"
    )
    path = write_recording(recording, tmp_path)
    assert path.exists()
    written = json.loads(path.read_text())
    assert written["recording_id"] == "test-run"
    assert written["provider"] == "fake"
    assert written["findings"]


def test_a_recording_that_would_fail_acceptance_is_refused(
    recording_source: RunResult, tmp_path: Path
) -> None:
    """Nothing is edited to make a recording pass; it is refused."""
    recording = build_recording(recording_source, recording_id="bad", title="Bad", demonstrates="x")
    recording["findings"][0]["result_ids"] = ["res_missing"]
    with pytest.raises(RecordingRejected) as excinfo:
        write_recording(recording, tmp_path)
    assert "missing result" in str(excinfo.value)
    assert not (tmp_path / "bad.json").exists(), "a refused recording must not be written"


def test_recording_carries_the_dataset_fingerprint(recording_source: RunResult) -> None:
    recording = build_recording(recording_source, recording_id="fp", title="fp", demonstrates="x")
    assert recording["dataset"]["dataset_fingerprint"].startswith("sha256:")
    for snapshot in recording["results"].values():
        assert snapshot["dataset_fingerprint"] == recording["dataset"]["dataset_fingerprint"]
