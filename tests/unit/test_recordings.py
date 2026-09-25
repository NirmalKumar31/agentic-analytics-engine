"""Recordings are published artefacts; the acceptance rules are the gate."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from agentic_analytics.recordings.schema import RECORDING_VERSION, validate_recording
from agentic_analytics.recordings.store import RecordingStore

RECORDINGS_DIR = Path(__file__).resolve().parents[2] / "examples" / "recordings"


def _load_all() -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(RECORDINGS_DIR.glob("*.json"))]


@pytest.fixture(scope="module")
def recordings() -> list[dict[str, Any]]:
    loaded = _load_all()
    assert loaded, "no recordings have been produced; run `make record`"
    return loaded


def test_three_recordings_exist(recordings: list[dict[str, Any]]) -> None:
    assert len(recordings) >= 3
    ids = {r["recording_id"] for r in recordings}
    assert {"margin-q3", "returns-segments", "shipping-repeat"} <= ids


@pytest.mark.parametrize("name", [p.name for p in sorted(RECORDINGS_DIR.glob("*.json"))])
def test_each_recording_passes_acceptance(name: str) -> None:
    report = validate_recording(json.loads((RECORDINGS_DIR / name).read_text()))
    assert report.ok, report.errors


def test_recordings_publish_only_supported_findings(
    recordings: list[dict[str, Any]],
) -> None:
    for recording in recordings:
        for finding in recording["findings"]:
            assert finding["verification_status"] == "supported"


def test_recordings_contain_a_real_rejection(recordings: list[dict[str, Any]]) -> None:
    """At least one demo must show the verifier withholding something."""
    assert any(r["rejected"] for r in recordings)


def test_recorded_runs_used_a_credential_free_provider(
    recordings: list[dict[str, Any]],
) -> None:
    for recording in recordings:
        assert recording["provider"] in ("fake", "local")


def test_recordings_declare_what_produced_them(
    recordings: list[dict[str, Any]],
) -> None:
    """A scripted run must never be presented as a model-driven one."""
    for recording in recordings:
        assert recording["provider_kind"] == "scripted-deterministic"
        assert recording["run_kind"] == "recorded deterministic engine run"
        assert "ai" not in recording["run_kind"].lower().split()
        assert recording["engine_version"]
        assert recording["metrics_definition_hash"].startswith("sha256:")
        assert recording["run_id"]
        assert isinstance(recording["git_dirty"], bool)
        assert recording["dataset_seed"] == 20260924


def test_recording_counts_match_their_contents(
    recordings: list[dict[str, Any]],
) -> None:
    for recording in recordings:
        assert recording["published_findings"] == len(recording["findings"])
        assert recording["withheld_findings"] == len(recording["rejected"])
        assert recording["mcp_tool_calls"] == len(recording["mcp_trace"])
        assert recording["task_count"] == len(recording["tasks"])


def test_store_loads_and_indexes(tmp_path: Path) -> None:
    store = RecordingStore(RECORDINGS_DIR)
    store.load()
    assert len(store) >= 3
    index = store.index()
    assert [e["recording_id"] for e in index] == sorted(
        [e["recording_id"] for e in index],
        key=lambda rid: {"margin-q3": 1, "returns-segments": 2, "shipping-repeat": 3}[rid],
    )
    with pytest.raises(KeyError):
        store.get("nope")


def test_store_refuses_to_serve_an_invalid_recording(tmp_path: Path) -> None:
    """A recording that fails acceptance must not reach a public page."""
    broken = copy.deepcopy(_load_all()[0])
    broken["findings"][0]["verification_status"] = "unsupported"
    (tmp_path / "broken.json").write_text(json.dumps(broken))
    store = RecordingStore(tmp_path)
    store.load()
    assert len(store) == 0


def test_store_skips_unreadable_files(tmp_path: Path) -> None:
    (tmp_path / "garbage.json").write_text("{not json")
    store = RecordingStore(tmp_path)
    store.load()
    assert len(store) == 0


BREAKAGES: list[tuple[str, str]] = [
    ("missing result", "cite_missing_result"),
    ("unsupported finding", "unsupported_finding"),
    ("write sql", "write_sql"),
    ("chart field", "bad_chart_field"),
    ("api key", "leak_secret"),
    ("host path", "leak_host_path"),
    ("hidden reasoning", "hidden_reasoning"),
    ("no fingerprint", "strip_fingerprint"),
    ("session id in trace", "session_in_trace"),
    ("bad version", "bad_version"),
    ("missing provider kind", "strip_provider_kind"),
    ("run kind implies a model", "mislabel_run_kind"),
    ("missing metrics hash", "strip_metrics_hash"),
]


def _break(recording: dict[str, Any], kind: str) -> dict[str, Any]:
    r = copy.deepcopy(recording)
    if kind == "cite_missing_result":
        r["findings"][0]["result_ids"] = ["res_does_not_exist"]
    elif kind == "unsupported_finding":
        r["findings"][0]["verification_status"] = "unsupported"
    elif kind == "write_sql":
        first = next(iter(r["results"]))
        r["results"][first]["sql"] = "DROP TABLE orders"
    elif kind == "bad_chart_field":
        if not r["charts"]:
            pytest.skip("this recording has no charts")
        r["charts"][0]["spec"]["encoding"]["x"]["field"] = "not_a_column"
    elif kind == "leak_secret":
        r["report"]["executive_summary"] += " sk-abcdefghijklmnopqrstuvwxyz012345"
    elif kind == "leak_host_path":
        r["report"]["executive_summary"] += " /Users/someone/secret/data.parquet"
    elif kind == "hidden_reasoning":
        r["findings"][0]["reasoning"] = "first I thought..."
    elif kind == "strip_fingerprint":
        r["dataset"]["dataset_fingerprint"] = ""
    elif kind == "session_in_trace":
        r["mcp_trace"][0]["arguments"]["session_id"] = "ses_leak"
    elif kind == "bad_version":
        r["recording_version"] = RECORDING_VERSION + 5
    elif kind == "strip_provider_kind":
        r.pop("provider_kind", None)
    elif kind == "mislabel_run_kind":
        r["run_kind"] = "recorded AI analysis"
    elif kind == "strip_metrics_hash":
        r["metrics_definition_hash"] = ""
    return r


@pytest.mark.parametrize("label,kind", BREAKAGES, ids=[k for _, k in BREAKAGES])
def test_acceptance_catches_each_failure_mode(
    label: str, kind: str, recordings: list[dict[str, Any]]
) -> None:
    report = validate_recording(_break(recordings[0], kind))
    assert not report.ok, f"{label} was not caught"
    assert report.errors


def test_valid_recording_has_no_errors(recordings: list[dict[str, Any]]) -> None:
    report = validate_recording(recordings[0])
    assert report.ok and not report.errors


def test_every_recorded_number_still_verifies(recordings: list[dict[str, Any]]) -> None:
    """Re-verify the published arithmetic straight from the recorded artefact."""
    from agentic_analytics.analytics.results import ResultSnapshot
    from agentic_analytics.verification.numeric import verify_numbers

    for recording in recordings:
        snapshots = {
            rid: ResultSnapshot.model_validate(payload)
            for rid, payload in recording["results"].items()
        }
        for finding in recording["findings"]:
            cited = [snapshots[r] for r in finding["result_ids"] if r in snapshots]
            cells: list[tuple[float, str]] = []
            for cell in finding["evidence_cells"]:
                snapshot = snapshots.get(cell["result_id"])
                if snapshot is None:
                    continue
                value = snapshot.cell(cell["row"], cell["column"])
                if isinstance(value, int | float) and not isinstance(value, bool):
                    cells.append((float(value), cell["column"]))
            verdict = verify_numbers(finding["text"], finding.get("claimed_change"), cells, cited)
            assert verdict.ok, (recording["recording_id"], finding["text"], verdict.reason)
