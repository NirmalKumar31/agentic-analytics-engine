#!/usr/bin/env python3
"""Rehearse the configured session envelope against a constrained container.

    docker run -d --name aae-rehearsal --memory=2g --cpus=1 -p 8000:8000 \\
      -e AAE_LIVE_ANALYTICS_ENABLED=true -e AAE_UPLOADS_ENABLED=true \\
      -e AAE_DUCKDB_MEMORY_LIMIT=384MB -e AAE_DUCKDB_THREADS=1 \\
      -e AAE_MAX_CONCURRENT_SESSIONS=12 -e AAE_MAX_ACTIVE_UPLOAD_SESSIONS=6 \\
      -e AAE_MAX_CONCURRENT_ANALYSES=2 -e AAE_UPLOADS_PER_IP_PER_HOUR=60 \\
      -e AAE_ANALYSES_PER_IP_PER_HOUR=200 agentic-analytics-engine:local

    python scripts/resource_rehearsal.py http://127.0.0.1:8000 \\
      --container aae-rehearsal

Opt-in, never part of CI, and **not** a benchmark: it reports no requests per
second and nothing it prints belongs in documentation as a performance claim.

Why it exists. The blueprint's numbers -- 384 MB per session, 12 sessions, 2
concurrent analyses -- cannot be validated by arithmetic. The memory limit is
a ceiling DuckDB will not exceed rather than an allocation it makes, and an
idle session is not free either, because it has already materialised its rows
into a private in-memory database and holds them for its whole life. So the
real figure is neither `384 x 12` nor `384 x 2`. It has to be measured.

What it does: fills a meaningful part of the envelope -- several demo
sessions, several uploaded files of non-trivial size, two analyses at once --
reads container memory at four points, then deletes everything and checks the
service is the same process it started as.

Pass means: no OOM, no restart, no timeout, capacity released, no session saw
another session's rows. Memory figures are printed for a human to read and
are deliberately not asserted against a threshold: one run on one machine
does not establish a bound.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from typing import Any

#: Materially larger than the capacity smoke's 150 rows, and large enough
#: that the per-session database is a real allocation rather than noise --
#: while still finishing in a reasonable time on one CPU.
UPLOAD_ROWS = 120_000
UPLOAD_SESSIONS = 4
DEMO_SESSIONS = 4
CONCURRENT_ANALYSES = 2
ANALYSIS_TIMEOUT_SECONDS = 240.0
REQUEST_TIMEOUT_SECONDS = 120.0


@dataclass
class Report:
    results: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, label: str, ok: bool, detail: str = "") -> bool:
        self.results.append((label, ok, detail))
        print(
            f"  {'ok  ' if ok else 'FAIL'} {label}" + (f": {detail}" if detail and not ok else "")
        )
        return ok

    @property
    def failures(self) -> list[tuple[str, bool, str]]:
        return [r for r in self.results if not r[1]]


class Client:
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
    ) -> tuple[int, bytes]:
        request = urllib.request.Request(self.base + path, data=body, method=method)
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with self.opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except OSError as exc:
            return 0, str(exc).encode()

    def json(self, path: str, method: str = "GET", payload: Any = None) -> tuple[int, Any]:
        body = json.dumps(payload).encode() if payload is not None else None
        status, raw = self.request(path, method, body, "application/json" if body else None)
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, raw.decode(errors="replace")


def process_memory(pid: str) -> str:
    """Resident set size of a process, via `ps`.

    The fallback for running against a plain uvicorn rather than a container.
    It measures the same thing `docker stats` would for a single-process
    container, minus the container's own overhead.
    """
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", pid],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        kilobytes = int(out.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return f"(unavailable for pid {pid})"
    return f"{kilobytes / 1024:.0f} MiB RSS"


def measure(container: str | None, pid: str | None) -> str:
    """Whichever measurement this run can actually take."""
    if pid:
        return process_memory(pid)
    return container_memory(container)


def container_memory(container: str | None) -> str:
    """Current memory use of the container, or a note that it is unavailable.

    Read from `docker stats` rather than estimated. If docker is not on the
    path, or no container name was given, this degrades to a printed note --
    the correctness checks below do not depend on it.
    """
    if not container:
        return "(pass --container or --pid to measure memory)"
    try:
        out = subprocess.run(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.MemUsage}} ({{.MemPerc}})",
                container,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"(unavailable: {type(exc).__name__})"
    return out.stdout.strip() or f"(unavailable: {out.stderr.strip()[:80]})"


def _csv_for(marker: str, rows: int) -> bytes:
    """A file big enough to matter, whose values identify its session."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["row_id", "region", "category", "order_date", "revenue"])
    regions = [f"{marker}-N", f"{marker}-S", f"{marker}-E", f"{marker}-W"]
    categories = ["Books", "Toys", "Home", "Garden", "Auto"]
    for index in range(rows):
        writer.writerow(
            [
                index,
                regions[index % 4],
                categories[index % 5],
                f"2025-{(index % 12) + 1:02d}-15",
                round(10 + (index * 7) % 990, 2),
            ]
        )
    return buffer.getvalue().encode()


def _multipart(filename: str, payload: bytes) -> tuple[bytes, str]:
    boundary = "----aae-resource-rehearsal"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: text/csv\r\n\r\n",
            payload,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return body, f"multipart/form-data; boundary={boundary}"


def _await(client: Client, run_id: str) -> dict[str, Any]:
    deadline = time.time() + ANALYSIS_TIMEOUT_SECONDS
    while time.time() < deadline:
        status, run = client.json(f"/api/analyses/{run_id}")
        if status != 200:
            return {"status": "unreachable", "http": status}
        if run.get("status") in {"completed", "failed"}:
            return dict(run)
        time.sleep(1.0)
    return {"status": "timeout"}


def _analyse(session: tuple[Client, str], question: str) -> dict[str, Any]:
    client, session_id = session
    status, started = client.json(
        "/api/analyses", method="POST", payload={"session_id": session_id, "question": question}
    )
    if status == 429:
        return {"status": "rate_limited"}
    if status != 202:
        return {"status": "no_run", "http": status, "detail": str(started)[:160]}
    return _await(client, started["run_id"])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("--container", help="container name, for docker stats")
    parser.add_argument("--pid", help="process id, for ps; use when the target is not a container")
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")
    report = Report()
    opened: list[tuple[Client, str]] = []

    print(f"resource rehearsal against {base}")
    print(
        f"  {DEMO_SESSIONS} demo sessions, {UPLOAD_SESSIONS} uploads of "
        f"{UPLOAD_ROWS:,} rows, {CONCURRENT_ANALYSES} concurrent analyses."
    )
    print("  Breakage check, not a benchmark. No throughput figure is reported.\n")

    status, health = Client(base).json("/api/health")
    if not report.add("the service is up", status == 200, f"HTTP {status}"):
        return 1
    before_id = health.get("instance_id")
    report.add("the health endpoint reports an instance id", bool(before_id))

    status, ready = Client(base).json("/api/ready")
    report.add("the service reports ready", status == 200, f"HTTP {status}: {ready}")

    baseline = measure(args.container, args.pid)
    print(f"      memory, baseline:            {baseline}")

    # ------------------------------------------------- fill the envelope
    for index in range(DEMO_SESSIONS):
        client = Client(base)
        status, body = client.json("/api/datasets/demo", method="POST")
        if status != 200:
            report.add(f"demo session {index} opens", False, f"HTTP {status}: {str(body)[:120]}")
            break
        opened.append((client, body["session_id"]))
    report.add("every demo session opened", len(opened) == DEMO_SESSIONS, f"{len(opened)} opened")

    markers = [f"ZONE{index}" for index in range(UPLOAD_SESSIONS)]
    uploads: list[tuple[Client, str, str]] = []
    for marker in markers:
        client = Client(base)
        body, content_type = _multipart(f"{marker}.csv", _csv_for(marker, UPLOAD_ROWS))
        status, raw = client.request("/api/datasets/upload", "POST", body, content_type)
        if status != 200:
            report.add(
                f"upload {marker} accepted", False, f"HTTP {status}: {raw[:160].decode('replace')}"
            )
            continue
        uploads.append((client, json.loads(raw)["session_id"], marker))
    report.add(
        "every upload was accepted", len(uploads) == UPLOAD_SESSIONS, f"{len(uploads)} accepted"
    )

    after_open = measure(args.container, args.pid)
    print(f"      memory, sessions open:       {after_open}")

    # --------------------------------------------- two analyses at once
    if len(uploads) >= CONCURRENT_ANALYSES:
        targets = [(c, s) for c, s, _ in uploads[:CONCURRENT_ANALYSES]]
        with ThreadPoolExecutor(max_workers=CONCURRENT_ANALYSES) as pool:
            runs = list(
                pool.map(lambda t: _analyse(t, "What is the total revenue by region?"), targets)
            )
        peak = measure(args.container, args.pid)
        print(f"      memory, during analyses:     {peak}")
        report.add(
            "concurrent analyses completed or were cleanly rate-limited",
            all(r.get("status") in {"completed", "rate_limited"} for r in runs),
            str([r.get("status") for r in runs]),
        )
        report.add(
            "no analysis timed out or became unreachable",
            not any(r.get("status") in {"timeout", "unreachable"} for r in runs),
        )
        completed = [r for r in runs if r.get("status") == "completed"]
        report.add(
            "findings from concurrent runs are all supported",
            all(
                f.get("verification_status") == "supported"
                for r in completed
                for f in r.get("findings", [])
            )
            and all(r.get("findings") for r in completed),
        )
        # Cross-talk is detectable because each file's values name its session.
        crosstalk = []
        for run, (_, _, marker) in zip(completed, uploads[:CONCURRENT_ANALYSES], strict=False):
            blob = json.dumps(run.get("findings", [])) + json.dumps(run.get("results", {}))
            crosstalk += [
                f"{marker} saw {other}" for other in markers if other != marker and other in blob
            ]
        report.add("no session saw another session's data", not crosstalk, "; ".join(crosstalk))
    else:
        report.add("enough uploads to run concurrent analyses", False, f"{len(uploads)}")

    # ------------------------------------------------------- tear it down
    for client, session_id, _ in uploads:
        client.json(f"/api/datasets/{session_id}", method="DELETE")
    for client, session_id in opened:
        client.json(f"/api/datasets/{session_id}", method="DELETE")

    time.sleep(3.0)
    after_cleanup = measure(args.container, args.pid)
    print(f"      memory, after cleanup:       {after_cleanup}")

    # Capacity must be available again now that nothing is running.
    client = Client(base)
    status, body = client.json("/api/datasets/demo", method="POST")
    if report.add("a session still opens after the load", status == 200, f"HTTP {status}"):
        after_run = _analyse((client, body["session_id"]), "Describe the dataset")
        report.add(
            "capacity is available again once the load stops",
            after_run.get("status") == "completed",
            str(after_run.get("status")),
        )
        client.json(f"/api/datasets/{body['session_id']}", method="DELETE")

    status, health_after = Client(base).json("/api/health")
    report.add("the service is still healthy", status == 200)
    report.add(
        "the service did not restart under load",
        bool(before_id) and health_after.get("instance_id") == before_id,
        f"{before_id} -> {health_after.get('instance_id')}",
    )
    status, _ = Client(base).json("/api/ready")
    report.add("the service is still ready", status == 200, f"HTTP {status}")

    print()
    print("  memory readings are for a human to read; one run on one machine")
    print("  does not establish a bound, so none is asserted.")
    print()
    if report.failures:
        print(f"FAIL: {len(report.failures)} of {len(report.results)} checks")
        for label, _, detail in report.failures:
            print(f"  - {label}{': ' + detail if detail else ''}")
        return 1
    print(f"PASS: all {len(report.results)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
