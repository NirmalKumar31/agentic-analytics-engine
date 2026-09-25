"""Loading recorded runs from disk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentic_analytics.logging import get_logger
from agentic_analytics.recordings.schema import validate_recording

log = get_logger(__name__)


class RecordingStore:
    """Recorded runs, validated on load."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._recordings: dict[str, dict[str, Any]] = {}

    def load(self) -> None:
        """Read and validate every recording. Invalid ones are skipped."""
        self._recordings.clear()
        if not self.directory.is_dir():
            return
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("recording_unreadable", file=path.name, error=str(exc))
                continue
            report = validate_recording(payload)
            if not report.ok:
                # A recording that fails acceptance must not be served: the
                # public page would then be showing something unverified.
                log.error("recording_rejected", file=path.name, errors=report.errors[:3])
                continue
            self._recordings[payload["recording_id"]] = payload

    def index(self) -> list[dict[str, Any]]:
        """Summaries for the picker, without the full payload."""
        return [
            {
                "recording_id": r["recording_id"],
                "question": r["question"],
                "title": r.get("title", r["question"]),
                "recorded_at": r["recorded_at"],
                "provider": r["provider"],
                # Carried into the index so a card can say what produced the
                # run without fetching the whole recording.
                "provider_kind": r.get("provider_kind", ""),
                "run_kind": r.get("run_kind", ""),
                "engine_version": r.get("engine_version", ""),
                "findings": len(r["findings"]),
                "rejected": len(r["rejected"]),
                "charts": len(r["charts"]),
                "tasks": len(r["tasks"]),
                "mcp_tool_calls": len(r["mcp_trace"]),
                "dataset_fingerprint": r["dataset"]["dataset_fingerprint"],
                "demonstrates": r.get("demonstrates", ""),
            }
            for r in sorted(self._recordings.values(), key=lambda x: x.get("order", 99))
        ]

    def get(self, recording_id: str) -> dict[str, Any]:
        try:
            return self._recordings[recording_id]
        except KeyError:
            raise KeyError(f"unknown recording {recording_id!r}") from None

    def __len__(self) -> int:
        return len(self._recordings)
