#!/usr/bin/env python3
"""Acceptance checks against a deployed instance.

    python scripts/live_acceptance.py https://service.onrender.com

Needs no credential, because the deployment needs none. Exercises the same
paths a visitor takes -- open a recording, open the demo warehouse, run an
analysis, upload a file, analyse it, end the session -- and then checks that
the session is really gone and that the response headers a browser relies on
are present.

This is not a load test. It creates one session at a time and cleans up
after itself; `scripts/capacity_smoke.py` is the separate, opt-in script for
concurrency.

Exit code is 0 only if every check passed.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from typing import Any

#: How long to wait for one analysis. A cold Render instance can take a
#: while to serve its first request; the analysis itself is fast.
ANALYSIS_TIMEOUT_SECONDS = 180.0
REQUEST_TIMEOUT_SECONDS = 60.0


class Checks:
    """Records pass/fail so one failure does not hide the rest."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passed = 0

    def ok(self, label: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            print(f"  ok  {label}")
        else:
            self.failures.append(f"{label}{': ' + detail if detail else ''}")
            print(f"  FAIL {label}" + (f": {detail}" if detail else ""))
        return condition

    def note(self, text: str) -> None:
        print(f"      {text}")


def _lower(headers: Any) -> dict[str, str]:
    """Header names, lowercased.

    HTTP header names are case-insensitive and this server sends them
    lowercase. Looking one up as `Cache-Control` found nothing, which is a
    silent pass into a false failure -- or, worse, a false pass.
    """
    return {str(k).lower(): str(v) for k, v in headers.items()}


class Client:
    """Minimal HTTP client with a cookie jar, so the capability persists."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(
        self,
        path: str,
        method: str = "GET",
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(self.base + path, data=body, method=method)
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with self.opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return response.status, _lower(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, _lower(exc.headers), exc.read()

    def json(self, path: str, method: str = "GET", payload: Any = None) -> tuple[int, Any]:
        body = json.dumps(payload).encode() if payload is not None else None
        status, _, raw = self.request(path, method, body, "application/json" if body else None)
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, raw.decode(errors="replace")

    def cookie(self, name: str) -> Any:
        return next((c for c in self.jar if c.name == name), None)


def _sample_csv() -> bytes:
    """A small file with columns the deterministic resolver can map."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["order_id", "order_date", "region", "revenue"])
    regions = ["North", "South", "East", "West"]
    for index in range(200):
        writer.writerow(
            [
                index,
                f"2025-{(index % 12) + 1:02d}-15",
                regions[index % len(regions)],
                round(10 + (index * 7) % 490, 2),
            ]
        )
    return buffer.getvalue().encode()


def _multipart(field: str, filename: str, payload: bytes) -> tuple[bytes, str]:
    boundary = "----aae-live-acceptance"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: text/csv\r\n\r\n",
            payload,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return body, f"multipart/form-data; boundary={boundary}"


def _await_analysis(client: Client, run_id: str, checks: Checks) -> dict[str, Any] | None:
    deadline = time.time() + ANALYSIS_TIMEOUT_SECONDS
    while time.time() < deadline:
        status, run = client.json(f"/api/analyses/{run_id}")
        if status != 200:
            checks.ok("analysis is readable", False, f"HTTP {status}")
            return None
        if run.get("status") in {"completed", "failed"}:
            return dict(run)
        time.sleep(1.5)
    checks.ok("analysis finished within the timeout", False)
    return None


def _check_findings(run: dict[str, Any], checks: Checks, label: str) -> None:
    findings = run.get("findings", [])
    results = run.get("results", {})
    checks.ok(f"{label}: published at least one finding", bool(findings))
    checks.ok(
        f"{label}: every finding is supported",
        all(f.get("verification_status") == "supported" for f in findings),
        str([f.get("verification_status") for f in findings]),
    )
    checks.ok(
        f"{label}: every cited result exists in the run",
        all(
            cell["result_id"] in results
            for finding in findings
            for cell in finding.get("evidence_cells", [])
        ),
    )
    checks.ok(f"{label}: the MCP trace is not empty", bool(run.get("mcp_trace")))
    checks.ok(
        f"{label}: all SQL is read-only",
        all(
            (snapshot.get("sql") or "select").lstrip().lower().startswith(("select", "with"))
            for snapshot in results.values()
        ),
    )


def _check_no_capability(client: Client, run: dict[str, Any], checks: Checks) -> None:
    cookie = client.cookie("aae_session")
    if cookie is None or not cookie.value:
        checks.ok("a session capability cookie was issued", False)
        return
    blob = json.dumps(run)
    checks.ok("the capability is absent from the run payload", cookie.value not in blob)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="https://service.onrender.com")
    parser.add_argument(
        "--expect-secure-cookie",
        action="store_true",
        default=None,
        help="require Secure on the session cookie (default: on for https)",
    )
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    expect_secure = (
        args.expect_secure_cookie
        if args.expect_secure_cookie is not None
        else base.startswith("https://")
    )
    checks = Checks()
    client = Client(base)
    print(f"live acceptance against {base}\n")

    # ------------------------------------------------------------- health
    status, health = client.json("/api/health")
    if not checks.ok("health endpoint answers", status == 200, f"HTTP {status}"):
        print("\nFAIL: the service is not answering; nothing else can be checked")
        return 1
    checks.ok("health reports ok", health.get("status") == "ok", str(health))
    checks.ok("the demo warehouse is present", health.get("demo_warehouse_ready") is True)
    checks.note(
        f"provider={health.get('provider_mode')} mode={health.get('execution_mode')} "
        f"live={health.get('live_analytics_enabled')} recordings={health.get('recordings')}"
    )

    status, _, body = client.request("/")
    checks.ok("the app shell is served", status == 200 and b'<div id="root"' in body)

    status, config = client.json("/api/config")
    checks.ok("config is served", status == 200)
    checks.ok(
        "config declares whether inference is remote",
        "model_inference_remote" in config,
    )
    checks.ok("live analysis is enabled", config.get("live_analytics_enabled") is True)
    checks.ok("uploads are enabled", config.get("uploads_enabled") is True)

    # --------------------------------------------------------- recordings
    status, listing = client.json("/api/recordings")
    recordings = listing.get("recordings", []) if status == 200 else []
    checks.ok("all three recordings are listed", len(recordings) == 3, str(len(recordings)))
    for entry in recordings:
        status, recording = client.json(f"/api/recordings/{entry['recording_id']}")
        checks.ok(
            f"recording {entry['recording_id']} replays",
            status == 200 and bool(recording.get("findings")),
        )

    # ------------------------------------------------------- demo session
    status, opened = client.json("/api/datasets/demo", method="POST")
    if not checks.ok("a demo session opens", status == 200, str(opened)[:200]):
        return 1
    demo_session = opened["session_id"]
    checks.ok("the demo session has a catalogue", bool(opened.get("catalog")))

    cookie = client.cookie("aae_session")
    checks.ok("the capability arrives as a cookie", cookie is not None)
    if cookie is not None:
        checks.ok("the cookie is HttpOnly", cookie.has_nonstandard_attr("HttpOnly"))
        checks.ok(
            "the cookie is Secure" if expect_secure else "cookie Secure flag matches the scheme",
            bool(cookie.secure) == expect_secure,
            f"secure={cookie.secure}, expected {expect_secure}",
        )
        checks.ok(
            "the capability is not in the response body", cookie.value not in json.dumps(opened)
        )

    question = (config.get("demo_questions") or [{}])[0].get(
        "question", "Which customer segments are driving the increase in return rate?"
    )
    status, started = client.json(
        "/api/analyses", method="POST", payload={"session_id": demo_session, "question": question}
    )
    if not checks.ok("an analysis starts", status == 202, str(started)[:200]):
        return 1
    run = _await_analysis(client, started["run_id"], checks)
    if run is None:
        return 1
    checks.ok("the analysis completed", run.get("status") == "completed", str(run.get("error")))
    _check_findings(run, checks, "demo")
    _check_no_capability(client, run, checks)

    # ------------------------------------------------------------ headers
    status, headers, _ = client.request(f"/api/datasets/{demo_session}")
    checks.ok(
        "private responses are not cacheable",
        headers.get("cache-control", "").lower().startswith("no-store"),
        headers.get("cache-control", "(absent)"),
    )
    checks.ok("nosniff is set", headers.get("x-content-type-options") == "nosniff")
    checks.ok("a referrer policy is set", bool(headers.get("referrer-policy")))
    checks.ok(
        "framing is refused",
        headers.get("x-frame-options") == "DENY"
        or "frame-ancestors" in headers.get("content-security-policy", ""),
    )

    # --------------------------------------------------------------- MCP
    status, _, mcp_body = client.request(
        "/mcp",
        "POST",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode(),
        "application/json",
    )
    # Asserted against the server's own declared policy rather than guessed
    # from the URL: connecting to 127.0.0.1 says nothing about how the
    # server bound, and a container published on a loopback port binds to
    # 0.0.0.0 inside. Guessing made this check fail on a correct service.
    remote_enabled = bool(config.get("mcp_remote_enabled"))
    if remote_enabled:
        checks.ok(
            "the MCP endpoint is served, as the configuration says",
            status != 503,
            f"HTTP {status}: {mcp_body[:120]!r}",
        )
    else:
        checks.ok(
            "the MCP endpoint is withdrawn (503), as the configuration says",
            status == 503,
            f"HTTP {status}: {mcp_body[:120]!r}",
        )
    checks.note(
        f"mcp_remote_enabled={remote_enabled}; the website reaches the same "
        "MCP server over the in-process transport either way"
    )

    # ------------------------------------------------------------ upload
    body, content_type = _multipart("file", "live_acceptance.csv", _sample_csv())
    status, _, raw = client.request("/api/datasets/upload", "POST", body, content_type)
    try:
        uploaded = json.loads(raw)
    except ValueError:
        uploaded = {}
    if not checks.ok("a CSV upload is accepted", status == 200, raw[:200].decode(errors="replace")):
        return 1
    upload_session = uploaded["session_id"]
    checks.ok("the upload is profiled", bool(uploaded.get("summary")))
    summary = uploaded.get("summary") or {}
    checks.ok(
        "the profile marks itself inferred",
        summary.get("status") == "inferred",
        str(summary.get("status")),
    )

    status, started = client.json(
        "/api/analyses",
        method="POST",
        payload={"session_id": upload_session, "question": "What is the total revenue by region?"},
    )
    if not checks.ok("an upload analysis starts", status == 202, str(started)[:200]):
        return 1
    run = _await_analysis(client, started["run_id"], checks)
    if run is None:
        return 1
    checks.ok("the upload analysis completed", run.get("status") == "completed")
    _check_findings(run, checks, "upload")

    # A question the rules cannot map must not be answered anyway.
    status, started = client.json(
        "/api/analyses",
        method="POST",
        payload={
            "session_id": upload_session,
            "question": "Explain the root cause of customer churn in this file",
        },
    )
    if checks.ok("an unmappable question is accepted", status == 202):
        run = _await_analysis(client, started["run_id"], checks)
        if run is not None:
            report = run.get("report") or {}
            limitations = " ".join(report.get("limitations", []))
            checks.ok(
                "an unmappable question is refused rather than answered",
                "could not be mapped" in limitations,
                limitations[:200] or "(no limitation recorded)",
            )

    # ----------------------------------------------------- end of session
    status, deleted = client.json(f"/api/datasets/{upload_session}", method="DELETE")
    checks.ok("the session can be deleted", status == 200, str(deleted)[:120])
    status, _ = client.json(f"/api/datasets/{upload_session}")
    checks.ok("the deleted dataset is gone", status == 404, f"HTTP {status}")
    status, _ = client.json(f"/api/analyses/{started['run_id']}")
    checks.ok("its run is unreachable too", status == 404, f"HTTP {status}")

    print()
    if checks.failures:
        print(f"FAIL: {len(checks.failures)} of {checks.passed + len(checks.failures)} checks")
        for failure in checks.failures:
            print(f"  - {failure}")
        return 1
    print(f"PASS: all {checks.passed} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
