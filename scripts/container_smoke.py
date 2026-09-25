"""Prove a running container actually works.

`docker build` succeeding says the image assembles. This says the thing
inside it serves the application: the app shell, the API, the recorded runs,
the MCP endpoint over its real transport, and -- when live analysis is
enabled -- a full analysis driven through HTTP.

Run against a container:

    python scripts/container_smoke.py http://127.0.0.1:8000

Exits non-zero on the first failure, with the reason.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 30.0


class SmokeFailure(RuntimeError):
    """A check failed. The message names what was expected."""


def _request(
    url: str,
    method: str = "GET",
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/json, text/event-stream")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def _json(url: str, method: str = "GET", body: dict[str, object] | None = None) -> dict:
    status, text = _request(url, method, body)
    if status != 200:
        raise SmokeFailure(f"{method} {url} returned {status}: {text[:200]}")
    return json.loads(text)


def wait_for_health(base: str, attempts: int = 60) -> dict:
    for _ in range(attempts):
        try:
            status, text = _request(f"{base}/api/health")
            if status == 200:
                return json.loads(text)
        except OSError:
            pass
        time.sleep(2)
    raise SmokeFailure("the container never became healthy")


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise SmokeFailure(f"{label}: {detail or 'failed'}")
    print(f"  ok  {label}")


def main(base: str) -> int:
    base = base.rstrip("/")
    print(f"container smoke test against {base}")

    # --- the application is up and serving its own assets
    health = wait_for_health(base)
    check("health endpoint", health["status"] == "ok", str(health))
    check(
        "health reports an instance id",
        bool(health.get("instance_id")),
        "without one, no caller can tell a restart from a healthy run",
    )
    ready_status, ready_text = _request(f"{base}/api/ready")
    check("readiness endpoint returns 200", ready_status == 200, ready_text[:200])
    ready = json.loads(ready_text)
    check(
        "readiness confirms the demo can be served",
        ready["demo_warehouse_ready"] is True and ready["recordings_loaded"] is True,
        str(ready),
    )
    check(
        "demo warehouse baked into the image",
        health["demo_warehouse_ready"] is True,
        str(health),
    )
    check("recordings present", health["recordings"] == 3, str(health))
    check(
        "no credential required",
        health["provider_mode"] in ("fake", "local"),
        health["provider_mode"],
    )

    status, shell = _request(f"{base}/")
    check("app shell served", status == 200 and '<div id="root">' in shell)
    asset = shell.split('src="')[1].split('"')[0] if 'src="' in shell else ""
    if asset:
        asset_status, _ = _request(f"{base}{asset}")
        check(f"frontend asset {asset}", asset_status == 200)

    config = _json(f"{base}/api/config")
    check("config exposes the execution mode", "execution_mode" in config, str(config)[:200])
    check(
        "config declares whether inference is remote",
        config["model_inference_remote"] is False,
        "the image must not default to a remote provider",
    )

    # --- recorded runs are served and complete
    recordings = _json(f"{base}/api/recordings")["recordings"]
    check("recording index", len(recordings) == 3, str(len(recordings)))
    first = recordings[0]["recording_id"]
    replay = _json(f"{base}/api/recordings/{first}")
    check(f"recording replay {first}", bool(replay["findings"]), "no findings")
    check(
        "replay carries provenance",
        replay["dataset"]["dataset_fingerprint"].startswith("sha256:")
        and replay["provider_kind"] == "scripted-deterministic",
        str(replay.get("provider_kind")),
    )
    check(
        "every replayed finding is supported",
        all(f["verification_status"] == "supported" for f in replay["findings"]),
    )
    check("replay has a real MCP trace", len(replay["mcp_trace"]) > 0)

    # --- MCP over its real transport
    init_status, init_body = _request(
        f"{base}/mcp",
        "POST",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2026-07-28",
                "capabilities": {},
                "clientInfo": {"name": "container-smoke", "version": "1"},
            },
        },
    )
    if init_status == 503:
        # Fail-closed by design when no host allow-list is configured.
        print("  --  MCP remote endpoint withdrawn (no AAE_MCP_ALLOWED_HOSTS); expected")
    else:
        check("MCP initialize", init_status == 200, f"{init_status}: {init_body[:200]}")
        check(
            "MCP advertises this server",
            "agentic-analytics" in init_body,
            init_body[:200],
        )

    # --- a full analysis through HTTP, when the deployment allows it
    if config["live_analytics_enabled"]:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        urllib.request.install_opener(opener)

        session = _json(f"{base}/api/datasets/demo", "POST")
        check("demo session opened", session["session_id"].startswith("ses_"))

        started_status, started_text = _request(
            f"{base}/api/analyses",
            "POST",
            {
                "session_id": session["session_id"],
                "question": "Revenue increased in Q3 2025, but gross margin fell. What caused it?",
            },
        )
        check("analysis accepted", started_status == 202, started_text[:200])
        run_id = json.loads(started_text)["run_id"]

        deadline = time.time() + 120
        run: dict = {}
        while time.time() < deadline:
            run = _json(f"{base}/api/analyses/{run_id}")
            if run["status"] != "running":
                break
            time.sleep(2)
        check("analysis completed", run.get("status") == "completed", str(run.get("status")))
        check("findings published", len(run["findings"]) > 0)
        check(
            "every published finding is supported",
            all(f["verification_status"] == "supported" for f in run["findings"]),
        )
        check(
            "every finding cites a result present in the run",
            all(rid in run["results"] for f in run["findings"] for rid in f["result_ids"]),
        )
        check(
            "all SQL is read-only",
            all(
                (s["sql"] or "select").lstrip().lower().startswith(("select", "with"))
                for s in run["results"].values()
            ),
        )
        check("MCP tools were actually called", len(run["mcp_trace"]) > 0)
        check(
            "no session capability leaked into the trace",
            all("session_key" not in c["arguments"] for c in run["mcp_trace"]),
        )
        print(
            f"      {len(run['findings'])} findings, "
            f"{len(run['mcp_trace'])} MCP calls, "
            f"{run['metrics']['runtime_seconds']}s"
        )
    else:
        print("  --  live analysis disabled; skipped the end-to-end run")

    print("\nall container checks passed")
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    try:
        sys.exit(main(target))
    except SmokeFailure as failure:
        print(f"\nFAILED: {failure}", file=sys.stderr)
        sys.exit(1)
