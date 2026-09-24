"""The recorded-run format and its acceptance checks.

A recording is a verbatim capture of one real run. The checks below are what
make it safe to publish: they are run when a recording is written and again in
CI, so a recording that drifted out of conformance fails the build rather than
appearing on a public page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

RECORDING_VERSION = 2

REQUIRED_TOP_LEVEL = (
    "recording_version",
    "recording_id",
    "recorded_at",
    "provider",
    "question",
    "dataset",
    "report",
    "findings",
    "rejected",
    "charts",
    "tasks",
    "results",
    "mcp_trace",
    "events",
    "metrics",
)

# Anything that would be a secret, a host path, or a raw provider error.
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"sk-[A-Za-z0-9_\-]{16,}", "an API key"),
    (r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}", "a bearer token"),
    (r"(?i)\b(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*\S{8,}", "a credential"),
    (r"AKIA[0-9A-Z]{16}", "an AWS access key id"),
    (r"(?i)\bghp_[A-Za-z0-9]{20,}", "a GitHub token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
)

HOST_PATH_PATTERNS: tuple[str, ...] = (
    r"/Users/[A-Za-z0-9._\-]+/",
    r"/home/[A-Za-z0-9._\-]+/",
    r"[A-Za-z]:\\\\Users\\\\",
    r"/private/var/folders/",
    r"/tmp/[A-Za-z0-9._\-]{6,}",
)

# Text that would indicate hidden model reasoning leaked into the artefact.
REASONING_KEYS = ("reasoning", "thinking", "chain_of_thought", "scratchpad", "raw_response")


@dataclass
class ValidationReport:
    """Outcome of validating a recording."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def _walk_strings(node: Any, path: str = "$") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(node, str):
        out.append((path, node))
    elif isinstance(node, dict):
        for key, value in node.items():
            out.extend(_walk_strings(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            out.extend(_walk_strings(value, f"{path}[{index}]"))
    return out


def _walk_keys(node: Any, path: str = "$") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            out.append((path, key))
            out.extend(_walk_keys(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            out.extend(_walk_keys(value, f"{path}[{index}]"))
    return out


def validate_recording(recording: dict[str, Any]) -> ValidationReport:
    """Apply every acceptance rule. Returns all failures, not just the first."""
    errors: list[str] = []
    warnings: list[str] = []

    for key in REQUIRED_TOP_LEVEL:
        if key not in recording:
            errors.append(f"missing required field {key!r}")
    if errors:
        return ValidationReport(ok=False, errors=errors)

    if recording["recording_version"] != RECORDING_VERSION:
        errors.append(
            f"recording_version is {recording['recording_version']}, expected {RECORDING_VERSION}"
        )

    results: dict[str, Any] = recording["results"]
    findings: list[dict[str, Any]] = recording["findings"]
    charts: list[dict[str, Any]] = recording["charts"]

    # --- every published finding is verified and traceable
    finding_ids = {f["finding_id"] for f in findings}
    for finding in findings:
        if finding.get("verification_status") != "supported":
            errors.append(
                f"finding {finding['finding_id']} is published with status "
                f"{finding.get('verification_status')!r}"
            )
        if not finding.get("result_ids"):
            errors.append(f"finding {finding['finding_id']} cites no result")
        for result_id in finding.get("result_ids", []):
            if result_id not in results:
                errors.append(f"finding {finding['finding_id']} cites missing result {result_id}")
        for cell in finding.get("evidence_cells", []):
            snapshot = results.get(cell["result_id"])
            if snapshot is None:
                errors.append(f"evidence cell references missing result {cell['result_id']}")
                continue
            if cell["column"] not in snapshot["columns"]:
                errors.append(
                    f"evidence cell names column {cell['column']!r}, which is not in "
                    f"result {cell['result_id']}"
                )
            elif not 0 <= cell["row"] < len(snapshot["rows"]):
                errors.append(
                    f"evidence cell row {cell['row']} is out of range for "
                    f"result {cell['result_id']}"
                )

    # --- charts derive from real results and reference real fields
    for chart in charts:
        snapshot = results.get(chart["result_id"])
        if snapshot is None:
            errors.append(f"chart {chart['chart_id']} references missing result")
            continue
        columns = set(snapshot["columns"])
        for channel, encoding in chart["spec"].get("encoding", {}).items():
            entries = encoding if isinstance(encoding, list) else [encoding]
            for entry in entries:
                field_name = entry.get("field") if isinstance(entry, dict) else None
                if field_name and field_name not in columns:
                    errors.append(
                        f"chart {chart['chart_id']} encoding {channel} references "
                        f"{field_name!r}, not a column of {chart['result_id']}"
                    )
        if not set(chart.get("finding_ids", [])) <= finding_ids:
            errors.append(f"chart {chart['chart_id']} references an unpublished finding")

    # --- all SQL is read-only
    for result_id, snapshot in results.items():
        sql = snapshot.get("sql")
        if not sql:
            continue
        head = sql.lstrip().lower()
        if not head.startswith(("select", "with")):
            errors.append(f"result {result_id} SQL is not a read-only query")
        if not snapshot.get("dataset_fingerprint"):
            errors.append(f"result {result_id} has no dataset fingerprint")

    # --- the MCP trace is real
    if not recording["mcp_trace"]:
        errors.append("the recording contains no MCP tool calls")
    traced_results = {c.get("result_id") for c in recording["mcp_trace"] if c.get("result_id")}
    if not traced_results & set(results):
        errors.append("no MCP tool call in the trace produced any recorded result")
    for call in recording["mcp_trace"]:
        if "session_id" in (call.get("arguments") or {}):
            errors.append("the MCP trace contains a session id")

    # --- dataset fingerprint
    if not str(recording["dataset"].get("dataset_fingerprint", "")).startswith("sha256:"):
        errors.append("the recording has no dataset fingerprint")

    # --- nothing secret, no host path, no hidden reasoning
    for path, text in _walk_strings(recording):
        for pattern, description in SECRET_PATTERNS:
            if re.search(pattern, text):
                errors.append(f"{description} appears at {path}")
        for pattern in HOST_PATH_PATTERNS:
            if re.search(pattern, text):
                errors.append(f"a host filesystem path appears at {path}")
    for path, key in _walk_keys(recording):
        if key.lower() in REASONING_KEYS:
            errors.append(f"hidden model reasoning key {key!r} appears at {path}")

    # --- events
    seqs = [e["seq"] for e in recording["events"]]
    if seqs != sorted(seqs):
        errors.append("events are not in sequence order")
    kinds = {e["type"] for e in recording["events"]}
    for required in ("run_started", "plan_generated", "mcp_tool_called", "run_completed"):
        if required not in kinds:
            errors.append(f"the event stream is missing {required!r}")

    if not findings:
        warnings.append("the recording publishes no findings")

    return ValidationReport(ok=not errors, errors=errors, warnings=warnings)
