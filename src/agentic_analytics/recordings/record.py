"""Capturing a real run as a publishable recording.

A recording is written only if it passes every acceptance check in
:mod:`agentic_analytics.recordings.schema`. Nothing is edited to make it pass:
if a run publishes an unsupported finding or references a missing result, the
recording is refused and the bug gets fixed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from agentic_analytics.graph.runner import RunResult
from agentic_analytics.recordings.schema import (
    RECORDING_VERSION,
    ValidationReport,
    validate_recording,
)


class RecordingRejected(RuntimeError):
    """The run did not meet the acceptance criteria for publication."""

    def __init__(self, report: ValidationReport) -> None:
        super().__init__("; ".join(report.errors[:5]))
        self.report = report


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
    return {
        "recording_version": RECORDING_VERSION,
        "recording_id": recording_id,
        "title": title,
        "demonstrates": demonstrates,
        "order": order,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": result.metrics.get("provider", "unknown"),
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
