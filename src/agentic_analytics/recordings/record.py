"""Capturing a real run as a publishable recording.

A recording is written only if it passes every acceptance check in
:mod:`agentic_analytics.recordings.schema`. Nothing is edited to make it pass:
if a run publishes an unsupported finding or references a missing result, the
recording is refused and the bug gets fixed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from agentic_analytics import __version__
from agentic_analytics.graph.runner import RunResult
from agentic_analytics.recordings.schema import (
    RECORDING_VERSION,
    ValidationReport,
    validate_recording,
)
from agentic_analytics.warehouse.metrics import METRICS_PATH


class RecordingRejected(RuntimeError):
    """The run did not meet the acceptance criteria for publication."""

    def __init__(self, report: ValidationReport) -> None:
        super().__init__("; ".join(report.errors[:5]))
        self.report = report


def _git(*args: str) -> str:
    """Run a git command, returning an empty string outside a repository."""
    try:
        return subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            cwd=Path(__file__).resolve().parents[3],
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _metrics_hash() -> str:
    """Content hash of the metric layer this run used.

    A recording's numbers are reproducible only against the definitions that
    produced them, so the definitions are fingerprinted alongside the data.
    """
    try:
        return "sha256:" + hashlib.sha256(METRICS_PATH.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def build_recording(
    result: RunResult,
    *,
    recording_id: str,
    title: str,
    demonstrates: str,
    order: int = 99,
) -> dict[str, Any]:
    """Assemble the recording payload from a run result."""
    payload = result.to_public_dict()
    provider = str(result.metrics.get("provider", "unknown"))
    # A recording driven by the scripted provider is a deterministic engine
    # run, not a model-driven one. Stating that in the artefact means the UI
    # and the README cannot describe it as something it is not.
    provider_kind = "scripted-deterministic" if provider == "fake" else "language-model"
    return {
        "recording_version": RECORDING_VERSION,
        "recording_id": recording_id,
        "title": title,
        "demonstrates": demonstrates,
        "order": order,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": provider,
        "provider_kind": provider_kind,
        "run_kind": (
            "recorded deterministic engine run"
            if provider_kind == "scripted-deterministic"
            else "recorded model-driven run"
        ),
        "engine_version": __version__,
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "dataset_seed": payload["dataset"].get("dataset_seed"),
        "metrics_definition_hash": _metrics_hash(),
        "run_id": result.run_id,
        "task_count": len(result.tasks),
        "mcp_tool_calls": len(result.mcp_trace),
        "published_findings": len(result.published),
        "withheld_findings": len(result.rejected),
        "question": result.question,
        "dataset": payload["dataset"],
        "report": payload["report"],
        "findings": payload["findings"],
        "rejected": payload["rejected"],
        "charts": payload["charts"],
        "tasks": payload["tasks"],
        "results": payload["results"],
        "mcp_trace": payload["mcp_trace"],
        "events": payload["events"],
        "metrics": payload["metrics"],
        "stopped_reason": payload["stopped_reason"],
        # --- what the live API serves and a recording used to drop.
        #
        # A recording is replayed through the same components a live run
        # is, so a key the API returns and the recording omits is a feature
        # the replay loses without saying so. These fill the evidence
        # drawer, which read "not recorded" against Route, Accepted
        # contract, Coverage and Timings for every recorded run.
        #
        # `presentation` among them, now that the layer it comes from is
        # correct. It was held back while a replayed report rendered
        # `Order date None ['2025-01-01', '2025-12-31']` as its scope line
        # and `2025-01-01T00:00:00` in its period column, so carrying the
        # snapshot then would have traded one reader-facing defect for
        # two. Those are fixed at the presentation layer, for live runs and
        # replays alike, so the snapshot is now the better artefact: a
        # replayed report reads exactly as the run that produced it did.
        "presentation": payload["presentation"],
        "query_contract": payload["query_contract"],
        "question_coverage": payload["question_coverage"],
        "planner_interpretation": payload["planner_interpretation"],
        "planner_fallback": payload["planner_fallback"],
        "timings": payload["timings"],
        "chart_decision": payload["chart_decision"],
        "outcome": payload["outcome"],
        "schema_revision": payload["schema_revision"],
        "role_evidence": payload["role_evidence"],
    }


def write_recording(recording: dict[str, Any], directory: Path) -> Path:
    """Validate and write. Raises :class:`RecordingRejected` on any failure."""
    report = validate_recording(recording)
    if not report.ok:
        raise RecordingRejected(report)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{recording['recording_id']}.json"
    path.write_text(json.dumps(recording, indent=2, sort_keys=False, default=str) + "\n")
    return path
