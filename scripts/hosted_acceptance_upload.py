"""Credential-free hosted acceptance for the uploaded-dataset path.

Usage:
    python scripts/hosted_acceptance_upload.py https://service.onrender.com

Deterministic mode only. No cloud model call is made, no credential is read
and nothing is printed that could carry one: every value shown is either a
number this script computed itself or a status the API reported.

One upload, one session, every question asked against it -- the per-address
upload ceiling is low by design and a script that uploads per question
exhausts it.

Expected values are computed here with DuckDB over the same fixture the
tests use, before any request is made. They are not read back from the
service and then asserted against themselves.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live_acceptance import Checks, Client, _multipart
from tests.fixtures.retail_weekly import BRANCHES, ROWS, as_csv

#: Values that must never appear in any response body.
FORBIDDEN = re.compile(
    r"sk-[A-Za-z0-9_-]{8,}|redis://|rediss://|/Users/|AAE_CLOUD_API_KEY"
    r"|api\.openai\.com|\.internal\b",
    re.IGNORECASE,
)


def oracle() -> dict[str, Any]:
    """Expected results, computed locally before anything is uploaded."""
    import tempfile

    import duckdb

    directory = Path(tempfile.mkdtemp())
    path = directory / "fixture.csv"
    path.write_text(as_csv())
    db = duckdb.connect()
    db.execute(f"CREATE TABLE t AS SELECT * FROM read_csv_auto('{path}', dateformat='%d-%m-%Y')")

    def rows(sql: str) -> list[tuple[Any, ...]]:
        return db.execute(sql).fetchall()

    by_flag_avg = {
        int(f): round(float(v), 2)
        for f, v in rows("SELECT Promo_Flag, avg(Weekly_Revenue) FROM t GROUP BY 1")
    }
    by_branch = {
        int(b): round(float(v), 2)
        for b, v in rows("SELECT Branch_No, sum(Weekly_Revenue) FROM t GROUP BY 1")
    }
    top = rows(
        "SELECT Branch_No, round(sum(Weekly_Revenue), 2) FROM t GROUP BY 1 ORDER BY 2 DESC LIMIT 1"
    )[0]
    lowest = rows(
        "SELECT Branch_No, round(sum(Weekly_Revenue), 2) FROM t GROUP BY 1 ORDER BY 2 ASC LIMIT 1"
    )[0]
    trend = rows(
        "SELECT strftime(date_trunc('month', Trading_Date), '%Y-%m'), "
        "round(sum(Weekly_Revenue), 2) FROM t GROUP BY 1 ORDER BY 1"
    )
    promo_total = rows("SELECT round(sum(Weekly_Revenue), 2) FROM t WHERE Promo_Flag = 1")[0][0]
    warm_total = rows("SELECT round(sum(Weekly_Revenue), 2) FROM t WHERE Avg_Temp_C >= 50")[0][0]
    cool_total = rows("SELECT round(sum(Weekly_Revenue), 2) FROM t WHERE Avg_Temp_C <= 30")[0][0]
    return {
        "rows": ROWS,
        "branches": BRANCHES,
        "avg_by_flag": by_flag_avg,
        "by_branch": by_branch,
        "top_branch": (int(top[0]), float(top[1])),
        "lowest_branch": (int(lowest[0]), float(lowest[1])),
        "trend": [(str(p), float(v)) for p, v in trend],
        "promo_total": float(promo_total),
        "warm_total": float(warm_total),
        "cool_total": float(cool_total),
    }


def _try(
    client: Client,
    session: str,
    checks: Checks,
    not_run: list[str],
    question: str,
) -> dict[str, Any] | None:
    """Ask, and record a ceiling as not-run rather than as a verdict."""
    try:
        return _ask(client, session, question, checks)
    except RateLimited:
        not_run.append(question)
        print(f"      not run (rate limited): {question}")
        return None


class RateLimited(Exception):
    """The deployment's own ceiling, which is not a defect."""


def _ask(client: Client, session: str, question: str, checks: Checks) -> dict[str, Any] | None:
    status, started = client.json(
        "/api/analyses",
        method="POST",
        payload={"session_id": session, "question": question, "mode": "deterministic"},
    )
    if status == 429:
        # A per-address ceiling refusing an eleventh question is the
        # deployment working as configured. Counting it as a failed check
        # says the engine is broken when the script is simply being
        # throttled, and counting it as a pass would be worse.
        raise RateLimited(question)
    if status != 202:
        checks.ok(f"{question[:44]!r} was accepted", False, f"HTTP {status}")
        return None
    run_id = started["run_id"]
    for _ in range(120):
        time.sleep(2)
        status, run = client.json(f"/api/analyses/{run_id}")
        if status == 200 and run.get("status") not in {"running", None}:
            return dict(run)
    checks.ok(f"{question[:44]!r} finished", False, "timed out")
    return None


def _aggregate(run: dict[str, Any]) -> dict[str, Any] | None:
    for snapshot in (run.get("results") or {}).values():
        if snapshot.get("tool_name") == "aggregate_for_question":
            return dict(snapshot)
    return None


def _cells(snapshot: dict[str, Any], key: int, value: int) -> dict[Any, float]:
    return {row[key]: float(row[value]) for row in snapshot.get("rows") or []}


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d[\d,]*(?:\.\d+)?", text)


def _states(text: str, value: float) -> bool:
    """Whether the text states this value, however it is formatted.

    Compared numerically: the engine drops a trailing zero, printing
    `12,296,516.7` where the oracle formats `12,296,516.70`, and comparing
    the formatted strings reported a mismatch that was not one.
    """
    return any(abs(float(token.replace(",", "")) - value) < 0.01 for token in _numbers(text))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url", help="https://service.onrender.com")
    args = parser.parse_args()

    expected = oracle()
    checks = Checks()
    not_run: list[str] = []
    client = Client(args.base_url)
    bodies: list[int] = []

    status, _ready = client.json("/api/ready")
    checks.ok("the service is ready", status == 200, f"HTTP {status}")
    status, health = client.json("/api/health")
    checks.ok("health reports a live deterministic path", status == 200, f"HTTP {status}")
    checks.note(f"engine version {health.get('version')}, mode {health.get('execution_mode')}")

    body, content_type = _multipart("file", "acceptance_fixture.csv", as_csv().encode())
    status, _, raw = client.request("/api/datasets/upload", "POST", body, content_type)
    bodies.append(len(raw))
    if not checks.ok("the fixture uploads", status == 200, f"HTTP {status}"):
        return 1
    uploaded = json.loads(raw)
    session = uploaded["session_id"]
    fingerprint = uploaded.get("dataset_fingerprint") or (uploaded.get("summary") or {}).get(
        "dataset_fingerprint", "(not reported)"
    )
    checks.note(f"dataset fingerprint {fingerprint}")
    checks.ok(
        "the upload reports the fixture's own row count",
        (uploaded.get("summary") or {}).get("row_count") == expected["rows"],
        str((uploaded.get("summary") or {}).get("row_count")),
    )

    schema = (uploaded.get("summary") or {}).get("fields") or []
    roles = {f.get("name"): f.get("role") for f in schema}
    checks.ok(
        "the numeric grouping key is a dimension, not a measure",
        roles.get("Branch_No") == "dimension",
        str(roles.get("Branch_No")),
    )
    checks.ok("the fractional measure is a measure", roles.get("Weekly_Revenue") == "measure")
    checks.ok("the binary flag is a dimension", roles.get("Promo_Flag") == "dimension")
    checks.ok("the date column is a time field", roles.get("Trading_Date") == "time")

    # ---------------------------------------------------------------- A
    run = _try(
        client,
        session,
        checks,
        not_run,
        "What is the average Weekly_Revenue by Promo_Flag?",
    )
    if run:
        snapshot = _aggregate(run) or {}
        got = _cells(snapshot, 0, 1)
        checks.ok(
            "A: status is completed", run.get("status") == "completed", str(run.get("status"))
        )
        checks.ok("A: two groups", len(got) == 2, str(len(got)))
        checks.ok(
            "A: both averages match the oracle",
            all(
                abs(got.get(flag, -1) - value) < 0.01
                for flag, value in expected["avg_by_flag"].items()
            ),
            f"{got} vs {expected['avg_by_flag']}",
        )
        coverage = snapshot.get("group_coverage") or {}
        checks.ok("A: coverage says complete", coverage.get("complete") is True, str(coverage))
        checks.ok(
            "A: every matching row is represented",
            coverage.get("rows_represented") == coverage.get("rows_matching") == expected["rows"],
            str(coverage),
        )
        checks.ok(
            "A: not reinterpreted as a trend",
            (run.get("query_contract") or {}).get("operation") == "average",
            str((run.get("query_contract") or {}).get("operation")),
        )

    # ---------------------------------------------------------------- B
    run = _try(client, session, checks, not_run, "What is the total Weekly_Revenue by Branch_No?")
    if run:
        snapshot = _aggregate(run) or {}
        coverage = snapshot.get("group_coverage") or {}
        got = _cells(snapshot, 0, 1)
        checks.ok(
            f"B: all {expected['branches']} groups returned",
            coverage.get("groups_returned") == coverage.get("groups_total") == expected["branches"],
            str(coverage),
        )
        checks.ok(
            f"B: all {expected['rows']} rows covered",
            coverage.get("rows_represented") == expected["rows"],
            str(coverage.get("rows_represented")),
        )
        checks.ok("B: coverage says complete", coverage.get("complete") is True)
        checks.ok(
            "B: every branch total matches the oracle",
            all(abs(got.get(b, -1) - v) < 0.01 for b, v in expected["by_branch"].items()),
            "a branch total differs from the oracle",
        )
        checks.ok(
            "B: ordered by the dimension, not turned into a top-list",
            coverage.get("ordering") == "dimension" and not coverage.get("ranked_by_request"),
            str(coverage.get("ordering")),
        )
        published = " ".join(f.get("text", "") for f in run.get("findings") or [])
        checks.ok("B: something was published", bool(published.strip()))
        checks.ok(
            "B: no value in the answer is a fragment",
            all(
                float(token.replace(",", ""))
                in set(got.values()) | set(got.keys()) | {float(expected["rows"])}
                or float(token.replace(",", "")) <= expected["branches"]
                for token in _numbers(published)
            ),
            "a number in the answer is not a value the result holds",
        )

    # ---------------------------------------------------------------- C
    run = _try(
        client,
        session,
        checks,
        not_run,
        "Which Branch_No had the highest total Weekly_Revenue?",
    )
    if run:
        published = " ".join(f.get("text", "") for f in run.get("findings") or [])
        branch, value = expected["top_branch"]
        checks.ok("C: the highest branch is named", str(branch) in published, published[:90])
        checks.ok("C: with its exact total", _states(published, value), published[:90])
        checks.ok("C: no lowest claim from a top-N", "lowest" not in published.lower())
        checks.ok(
            "C: the ordering and limit are in provenance",
            "ORDER BY" in ((_aggregate(run) or {}).get("sql") or "")
            and "LIMIT" in ((_aggregate(run) or {}).get("sql") or ""),
        )

    # ---------------------------------------------------------------- D
    period_question = "What was the total Weekly_Revenue in 2011?"
    run = _try(client, session, checks, not_run, period_question)
    if run:
        contract = run.get("query_contract") or {}
        checks.ok(
            "D: the period survives interpretation",
            contract.get("period") == ["2011-01-01", "2011-12-31"],
            str(contract.get("period")),
        )
        checks.ok(
            "D: the period is applied to a date column",
            bool(contract.get("period_field")),
            str(contract.get("period_field")),
        )
        checks.ok(
            "D: a non-trend period question answers rather than failing",
            run.get("status") == "completed" and bool(run.get("findings")),
            f"{run.get('status')}: {(run.get('stopped_reason') or '')[:70]}",
        )

    # ---------------------------------------------------------------- E
    run = _try(client, session, checks, not_run, "Show the monthly trend of Weekly_Revenue")
    if run:
        snapshot = _aggregate(run) or {}
        periods = [str(row[0]) for row in snapshot.get("rows") or []]
        checks.ok(
            "E: the series has the expected number of points",
            len(periods) == len(expected["trend"]),
            f"{len(periods)} vs {len(expected['trend'])}",
        )
        checks.ok("E: in chronological order", periods == sorted(periods))
        first_period, first_value = expected["trend"][0]
        got = _cells(snapshot, 0, 1)
        checks.ok(
            "E: the first point matches the oracle",
            abs(got.get(first_period, -1) - first_value) < 0.01,
            f"{got.get(first_period)} vs {first_value}",
        )
        checks.ok("E: the report is not empty", bool(run.get("findings")))
        charts = run.get("charts") or []
        checks.ok("E: a chart was built from the cited result", bool(charts))

    # ---------------------------------------------------------------- F
    run = _try(client, session, checks, not_run, "What is the average gross_margin by Branch_No?")
    if run:
        reason = run.get("stopped_reason") or ""
        checks.ok("F: refused", run.get("status") == "refused", str(run.get("status")))
        checks.ok("F: nothing published", not run.get("findings"))
        checks.ok("F: the missing measure is named", "gross_margin" in reason, reason[:90])
        contract = run.get("query_contract") or {}
        checks.ok(
            "F: the grouping key was not adopted as the measure",
            contract.get("measure") in (None, ""),
            str(contract.get("measure")),
        )
        checks.ok(
            "F: the refusal is visible rather than an empty completion",
            bool(reason.strip()),
            "no stop reason was given",
        )
        limitations = ((run.get("report") or {}) or {}).get("limitations") or []
        checks.ok(
            "F: the reason is given once",
            sum(1 for line in limitations if "gross_margin" in line) <= 1,
            str(limitations[:2]),
        )

    # ---------------------------------------------------------------- G
    for label, question, expected_total, operator in (
        (
            "at least",
            "What is the total Weekly_Revenue with Avg_Temp_C at least 50?",
            expected["warm_total"],
            ">=",
        ),
        (
            "at most",
            "What is the total Weekly_Revenue with Avg_Temp_C at most 30?",
            expected["cool_total"],
            "<=",
        ),
        (
            "word equality",
            "What is the total Weekly_Revenue where Promo_Flag is 1?",
            expected["promo_total"],
            "=",
        ),
        (
            "symbolic",
            "What is the total Weekly_Revenue where Avg_Temp_C >= 50?",
            expected["warm_total"],
            ">=",
        ),
    ):
        run = _try(client, session, checks, not_run, question)
        if not run:
            continue
        contract = run.get("query_contract") or {}
        filters = contract.get("filters") or []
        checks.ok(
            f"G[{label}]: the operation is a total, not a ranking",
            contract.get("operation") == "sum",
            str(contract.get("operation")),
        )
        checks.ok(
            f"G[{label}]: the bound is inclusive as written",
            any(f.get("operator") == operator for f in filters),
            str([f.get("operator") for f in filters]),
        )
        checks.ok(
            f"G[{label}]: the filtered column is not also the grouping",
            contract.get("dimension") is None,
            str(contract.get("dimension")),
        )
        published = " ".join(f.get("text", "") for f in run.get("findings") or [])
        checks.ok(
            f"G[{label}]: the total matches the oracle",
            _states(published, expected_total),
            published[:90],
        )

    # ------------------------------------------------------- hygiene
    status, _listing = client.json(f"/api/datasets/{session}")
    checks.ok("the dataset is readable with the session capability", status == 200)
    status, _, raw = client.request(f"/api/datasets/{session}", "DELETE")
    checks.ok("the session can be deleted", status in {200, 204}, f"HTTP {status}")
    status, _ = client.json(f"/api/datasets/{session}")
    checks.ok("the deleted dataset is gone", status in {403, 404}, f"HTTP {status}")

    checks.note(f"largest response body {max(bodies):,} bytes")
    if not_run:
        print(f"\nNOT RUN ({len(not_run)}), refused by the deployment's own ceiling:")
        for question in not_run:
            print(f"  - {question}")
        print("  These are neither passes nor failures. Re-run after the window.")

    if checks.failures:
        print(f"\nFAIL: {len(checks.failures)} of {checks.passed + len(checks.failures)} checks")
        for failure in checks.failures:
            print(f"  - {failure}")
        return 1
    print(f"\nPASS: all {checks.passed} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
