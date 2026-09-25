#!/usr/bin/env python3
"""A small, bounded concurrency check. Opt-in; not part of CI.

    python scripts/capacity_smoke.py https://service.onrender.com

What this is for: catching an out-of-memory restart, a deadlock, a capacity
counter that never releases, or two sessions seeing each other's data. Those
are failures that only appear when more than one thing happens at a time and
that a sequential acceptance run cannot find.

What this is **not** for: measuring throughput. It does not report requests
per second and no number it prints should end up in documentation as a
performance claim. The load is deliberately tiny -- a couple of simultaneous
analyses and a few small uploads -- because the goal is to find breakage,
not to find a limit.

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
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from typing import Any

#: Deliberately small. Two concurrent analyses is what the public blueprint
#: admits, so this exercises the ceiling rather than exceeding it.
CONCURRENT_ANALYSES = 2
UPLOAD_SESSIONS = 3
ANALYSIS_TIMEOUT_SECONDS = 180.0
REQUEST_TIMEOUT_SECONDS = 60.0


@dataclass
class Result:
    label: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def add(self, label: str, ok: bool, detail: str = "") -> bool:
        self.results.append(Result(label, ok, detail))
        # Detail only on failure: a passing line with a trailing "HTTP 200"
        # reads like a warning.
        print(
            f"  {'ok  ' if ok else 'FAIL'} {label}" + (f": {detail}" if detail and not ok else "")
        )
        return ok

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if not r.ok]


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
        except OSError as exc:  # connection reset, timeout, DNS
            return 0, str(exc).encode()

    def json(self, path: str, method: str = "GET", payload: Any = None) -> tuple[int, Any]:
        body = json.dumps(payload).encode() if payload is not None else None
        status, raw = self.request(path, method, body, "application/json" if body else None)
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, raw.decode(errors="replace")


def _csv_for(marker: str, rows: int = 150) -> bytes:
    """A file whose values identify which session uploaded it.

    Cross-talk is only detectable if each session's data is distinguishable,
    so every row carries the session's marker.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["row_id", "region", "revenue"])
    for index in range(rows):
        writer.writerow([index, f"{marker}-{index % 4}", 100 + index])
    return buffer.getvalue().encode()


def _multipart(filename: str, payload: bytes) -> tuple[bytes, str]:
    boundary = "----aae-capacity-smoke"
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


def _one_demo_analysis(base: str, question: str) -> dict[str, Any]:
    client = Client(base)
    status, opened = client.json("/api/datasets/demo", method="POST")
    if status != 200:
        return {"status": "no_session", "http": status, "detail": str(opened)[:160]}
    status, started = client.json(
        "/api/analyses",
        method="POST",
        payload={"session_id": opened["session_id"], "question": question},
    )
    if status == 429:
        # Being turned away at the capacity ceiling is correct behaviour,
        # not a failure. What matters is that the ceiling releases.
        return {"status": "rate_limited"}
    if status != 202:
        return {"status": "no_run", "http": status, "detail": str(started)[:160]}
    run = _await(client, started["run_id"])
    client.json(f"/api/datasets/{opened['session_id']}", method="DELETE")
    return run


def _one_upload_session(base: str, marker: str) -> dict[str, Any]:
    client = Client(base)
    body, content_type = _multipart(f"{marker}.csv", _csv_for(marker))
    status, raw = client.request("/api/datasets/upload", "POST", body, content_type)
    if status == 429:
        return {"status": "rate_limited", "marker": marker}
    if status != 200:
        return {"status": "rejected", "http": status, "detail": raw[:160].decode("replace")}
    opened = json.loads(raw)
    status, started = client.json(
        "/api/analyses",
        method="POST",
        payload={
            "session_id": opened["session_id"],
            "question": "What is the total revenue by region?",
        },
    )
    if status == 429:
        client.json(f"/api/datasets/{opened['session_id']}", method="DELETE")
        return {"status": "rate_limited", "marker": marker}
    if status != 202:
        return {"status": "no_run", "http": status, "marker": marker}
    run = _await(client, started["run_id"])
    run["marker"] = marker
    run["session_id"] = opened["session_id"]
    client.json(f"/api/datasets/{opened['session_id']}", method="DELETE")
    return run


def _text_of(run: dict[str, Any]) -> str:
    return json.dumps(run.get("findings", [])) + json.dumps(run.get("results", {}))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")
    report = Report()

    print(f"capacity smoke against {base}")
    print(
        f"  {CONCURRENT_ANALYSES} concurrent analyses, {UPLOAD_SESSIONS} upload "
        "sessions. This is a breakage check, not a throughput measurement.\n"
    )

    status, health = Client(base).json("/api/health")
    if not report.add("the service is up", status == 200, f"HTTP {status}"):
        return 1

    # ------------------------------------------- concurrent demo analyses
    started_at = time.time()
    with ThreadPoolExecutor(max_workers=CONCURRENT_ANALYSES) as pool:
        runs = list(
            pool.map(
                lambda index: _one_demo_analysis(
                    base, "Which customer segments are driving the increase in return rate?"
                ),
                range(CONCURRENT_ANALYSES),
            )
        )
    elapsed = time.time() - started_at
    accepted = [r for r in runs if r.get("status") not in {"rate_limited"}]
    report.add(
        "every concurrent analysis either ran or was cleanly rate-limited",
        all(r.get("status") in {"completed", "rate_limited"} for r in runs),
        str([r.get("status") for r in runs]),
    )
    report.add(
        "nothing timed out or became unreachable",
        not any(r.get("status") in {"timeout", "unreachable", "no_session"} for r in runs),
        str([r for r in runs if r.get("status") in {"timeout", "unreachable", "no_session"}])[:200],
    )
    report.add(
        "concurrent runs still published supported findings",
        all(
            r.get("findings")
            and all(f.get("verification_status") == "supported" for f in r["findings"])
            for r in accepted
            if r.get("status") == "completed"
        ),
    )
    print(f"      {len(accepted)} ran in {elapsed:.1f}s wall clock (not a throughput figure)")

    # ------------------------------------------------- concurrent uploads
    markers = [f"ZONE{index}" for index in range(UPLOAD_SESSIONS)]
    with ThreadPoolExecutor(max_workers=UPLOAD_SESSIONS) as pool:
        uploads = list(pool.map(lambda m: _one_upload_session(base, m), markers))

    report.add(
        "every upload session either ran or was cleanly rate-limited",
        all(u.get("status") in {"completed", "rate_limited"} for u in uploads),
        str([u.get("status") for u in uploads]),
    )

    completed = [u for u in uploads if u.get("status") == "completed"]
    crosstalk: list[str] = []
    for upload in completed:
        mine = str(upload.get("marker"))
        blob = _text_of(upload)
        for other in markers:
            if other != mine and other in blob:
                crosstalk.append(f"{mine} saw {other}")
    report.add("no session saw another session's data", not crosstalk, "; ".join(crosstalk))

    # --------------------------------------------- the ceiling must release
    # If the concurrency counter leaked, a request now would be refused even
    # though nothing is running.
    time.sleep(2.0)
    after = _one_demo_analysis(
        base, "Which customer segments are driving the increase in return rate?"
    )
    report.add(
        "capacity is available again once the load stops",
        after.get("status") == "completed",
        str(after.get("status")),
    )

    status, health_after = Client(base).json("/api/health")
    report.add("the service is still healthy", status == 200 and health_after.get("status") == "ok")
    # `instance_id` is random per process, so a change means the process was
    # replaced -- which is how an OOM kill looks from outside. Comparing
    # `version` could never detect that: a restarted process runs the same
    # build and reports the same version.
    before_id = health.get("instance_id")
    after_id = health_after.get("instance_id")
    report.add(
        "the health endpoint reports an instance id",
        bool(before_id),
        "no instance_id; this build cannot prove it did not restart",
    )
    report.add(
        "the service did not restart under load",
        bool(before_id) and before_id == after_id,
        f"{before_id} -> {after_id}",
    )

    print()
    if report.failures:
        print(f"FAIL: {len(report.failures)} of {len(report.results)} checks")
        for failure in report.failures:
            print(f"  - {failure.label}{': ' + failure.detail if failure.detail else ''}")
        return 1
    print(f"PASS: all {len(report.results)} checks")
    print("No throughput figure is reported, and none should be inferred.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
