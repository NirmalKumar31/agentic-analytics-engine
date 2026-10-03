"""Hosted acceptance for the time, null and provenance semantics.

Usage:
    python scripts/hosted_acceptance_semantics.py https://service.onrender.com

Deterministic mode only. No credential is read and no model call is made.

One upload, every question asked against it: the deployment allows four
uploads an hour and eight analyses per session, and a script that uploads
per question exhausts the first ceiling before it reaches the interesting
cases.

Every expected number is computed here with DuckDB, from the same fixture,
*before* the service is contacted. Nothing is read back from the service and
then asserted against itself.

A refusal by the deployment's own rate limiter is reported as NOT RUN. It is
neither a pass nor a failure, and calling it either would be a lie about
coverage.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live_acceptance import Client, _multipart

FORBIDDEN = re.compile(
    r"sk-[A-Za-z0-9_-]{8,}|redis://|rediss://|/Users/|AAE_CLOUD_API_KEY"
    r"|api\.openai\.com|\.internal\b",
    re.IGNORECASE,
)

REGIONS = ["North", "South", "East", "West"]


def as_csv() -> str:
    """A table with two plausible clocks, nulls, and an all-null measure.

    `order_date` is when revenue was earned; `signup_date` is when the
    account began. Filtering revenue by the second answers a different
    question, which is the whole point of the first case below.
    """
    lines = ["order_date,signup_date,region,revenue,refund_value,notes"]
    for i in range(240):
        region = REGIONS[i % 4]
        # 2024 orders for the first 160 rows, 2023 for the rest.
        year = 2024 if i < 160 else 2023
        month = (i % 12) + 1
        order = "" if i % 20 == 19 else f"{year}-{month:02d}-15"
        # Signup is deliberately offset: a 2024 signup cohort is not the
        # same set of rows as 2024 orders.
        signup_year = 2024 if i % 3 == 0 else 2022
        signup = f"{signup_year}-{(i % 12) + 1:02d}-03"
        revenue = "" if i % 15 == 14 else f"{100 + (i * 7) % 400}.50"
        # `refund_value` is numeric everywhere except West, where every
        # row is null. An entirely empty column will not do: DuckDB types
        # it VARCHAR and the profiler gives it no analytical role, so the
        # question never reaches an aggregate. What this case needs is an
        # aggregate whose *matching* rows hold no value.
        refund = "" if region == "West" else f"{10 + (i % 9)}.25"
        # `notes` is empty in every row: a real column a reader may name
        # that no analytical role can use.
        lines.append(f"{order},{signup},{region},{revenue},{refund},")
    return "\n".join(lines) + "\n"


def oracle() -> dict[str, Any]:
    """Every expected value, computed before anything is uploaded."""
    import duckdb

    directory = Path(tempfile.mkdtemp())
    path = directory / "semantics.csv"
    path.write_text(as_csv())
    db = duckdb.connect()
    db.execute(f"CREATE TABLE t AS SELECT * FROM read_csv_auto('{path}')")

    def one(sql: str) -> Any:
        return db.execute(sql).fetchall()[0][0]

    def rows(sql: str) -> list[tuple[Any, ...]]:
        return db.execute(sql).fetchall()

    return {
        "order_2024_total": round(
            float(one("SELECT sum(revenue) FROM t WHERE year(order_date) = 2024")), 2
        ),
        "order_2024_rows": int(one("SELECT count(*) FROM t WHERE year(order_date) = 2024")),
        "order_2024_values": int(one("SELECT count(revenue) FROM t WHERE year(order_date) = 2024")),
        "signup_2024_total": round(
            float(one("SELECT sum(revenue) FROM t WHERE year(signup_date) = 2024")), 2
        ),
        "avg_by_region": {
            str(r): round(float(v), 2)
            for r, v in rows("SELECT region, avg(revenue) FROM t GROUP BY 1")
        },
        "region_population": {
            str(r): int(v) for r, v in rows("SELECT region, count(*) FROM t GROUP BY 1")
        },
        "region_observations": {
            str(r): int(v) for r, v in rows("SELECT region, count(revenue) FROM t GROUP BY 1")
        },
        "null_order_dates": int(one("SELECT count(*) FROM t WHERE order_date IS NULL")),
        "null_revenues": int(one("SELECT count(*) FROM t WHERE revenue IS NULL")),
        "refund_west_rows": int(one("SELECT count(*) FROM t WHERE region = 'West'")),
        "refund_west_values": int(one("SELECT count(refund_value) FROM t WHERE region = 'West'")),
        "total_rows": int(one("SELECT count(*) FROM t")),
    }


class Report:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []
        self.not_run: list[str] = []

    def ok(self, label: str) -> None:
        self.passed.append(label)
        print(f"  ok   {label}")

    def bad(self, label: str, detail: str = "") -> None:
        self.failed.append(f"{label}{(' — ' + detail) if detail else ''}")
        print(f"  FAIL {label}" + (f"\n         {detail}" if detail else ""))

    def skip(self, label: str, why: str) -> None:
        self.not_run.append(f"{label} ({why})")
        print(f"       not run ({why}): {label}")


def ask(client: Client, session: str, question: str) -> tuple[str, dict[str, Any]]:
    """Run one analysis. Returns (state, payload). state is ok|limited|error."""
    status, started = client.json(
        "/api/analyses",
        "POST",
        {"session_id": session, "question": question, "mode": "deterministic"},
    )
    if status == 429:
        return "limited", {}
    if status != 202:
        return "error", {"status": status, "body": started}
    run_id = started["run_id"]
    for _ in range(200):
        _, payload = client.json(f"/api/analyses/{run_id}")
        if payload.get("status") != "running":
            return "ok", payload
        time.sleep(1.0)
    return "error", {"status": "never finished"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--expect-build-sha", default=None)
    parser.add_argument(
        "--allow-unknown-build-sha",
        action="store_true",
        help="for a local dry run, where there is no built revision to report",
    )
    args = parser.parse_args()

    expected = oracle()
    print("Expected values, computed locally with DuckDB before any request:")
    print(
        f"  revenue in 2024 by order_date   {expected['order_2024_total']:,.2f}"
        f"  over {expected['order_2024_rows']} rows,"
        f" {expected['order_2024_values']} with a value"
    )
    print(f"  revenue for the 2024 signup cohort {expected['signup_2024_total']:,.2f}")
    print(
        f"  null order dates {expected['null_order_dates']}"
        f" · null revenues {expected['null_revenues']}"
        f" · West rows {expected['refund_west_rows']},"
        f" of which {expected['refund_west_values']} hold a refund value"
    )
    print()

    client = Client(args.base_url)
    report = Report()

    status, health = client.json("/api/health")
    if status != 200:
        report.bad("the service answers /api/health", f"status {status}")
        return 1
    sha = health.get("build_sha")
    if args.expect_build_sha:
        if sha == args.expect_build_sha:
            report.ok(f"build_sha is the expected revision ({sha})")
        else:
            report.bad("build_sha is the expected revision", f"got {sha!r}")
            return 1
    if sha in (None, "", "unknown"):
        if args.allow_unknown_build_sha:
            report.skip("the deployment reports a build revision", "local dry run")
        else:
            report.bad("the deployment reports a build revision", f"got {sha!r}")

    body, content_type = _multipart("file", "semantics.csv", as_csv().encode())
    status, _, raw = client.request("/api/datasets/upload", "POST", body, content_type)
    if status == 429:
        report.skip("upload the semantics fixture", "upload rate limit")
        print("\nNOT RUN: the upload ceiling refused this attempt. Re-run after the window.")
        return 2
    if status != 200:
        report.bad("upload the semantics fixture", f"status {status}")
        return 1
    uploaded = json.loads(raw)
    session = uploaded["session_id"]
    summary = uploaded["summary"]
    report.ok("the semantics fixture uploads")

    if summary.get("row_count") == expected["total_rows"]:
        report.ok(f"the profile counts {expected['total_rows']} rows")
    else:
        report.bad("the profile counts every row", f"got {summary.get('row_count')}")

    bodies: list[str] = []

    # ---------------------------------------------------------------- cases

    # 1. Two plausible clocks and the question names neither.
    state, run = ask(client, session, "What was total revenue in 2024?")
    if state == "limited":
        report.skip("1. two clocks and no named one refuses", "analysis rate limit")
    elif state != "ok":
        report.bad("1. two clocks and no named one refuses", json.dumps(run)[:160])
    else:
        bodies.append(json.dumps(run))
        text = json.dumps(run).lower()
        refused = run.get("status") != "completed" or not (run.get("results") or {})
        names_a_date = "order_date" in text or "signup_date" in text or "date column" in text
        if refused and names_a_date:
            report.ok("1. two clocks and no named one refuses, naming the choice")
        else:
            report.bad(
                "1. two clocks and no named one refuses, naming the choice",
                f"status={run.get('status')} stopped={run.get('stopped_reason')!r}",
            )

    # 2. The clock is named: the number must be the oracle's.
    state, run = ask(client, session, "What was total revenue in 2024 using order_date?")
    if state == "limited":
        report.skip("2. the named order_date total matches the oracle", "analysis rate limit")
    elif state != "ok":
        report.bad("2. the named order_date total matches the oracle", json.dumps(run)[:160])
    else:
        bodies.append(json.dumps(run))
        value = _single_value(run)
        if value is not None and abs(value - expected["order_2024_total"]) < 0.5:
            report.ok(f"2. total revenue in 2024 by order_date = {value:,.2f}, as computed")
        else:
            report.bad(
                "2. the named order_date total matches the oracle",
                f"service said {value}, DuckDB says {expected['order_2024_total']}",
            )

    # 3. The other clock, named explicitly. A different, also-correct number.
    state, run = ask(client, session, "What was total revenue in 2024 using signup_date?")
    if state == "limited":
        report.skip("3. the named signup_date total matches its own oracle", "analysis rate limit")
    elif state != "ok":
        report.bad("3. the named signup_date total matches its own oracle", json.dumps(run)[:160])
    else:
        bodies.append(json.dumps(run))
        value = _single_value(run)
        if value is None:
            report.bad(
                "3. the named signup_date total matches its own oracle", "no value published"
            )
        elif abs(value - expected["signup_2024_total"]) < 0.5:
            report.ok(f"3. the 2024 signup cohort = {value:,.2f}, as computed")
            if abs(value - expected["order_2024_total"]) < 0.5:
                report.bad(
                    "3. the two clocks give different answers",
                    "the signup cohort equals the order-date total; the fixture cannot "
                    "distinguish them",
                )
            else:
                report.ok("3. the two clocks give different answers, as they must")
        else:
            report.bad(
                "3. the named signup_date total matches its own oracle",
                f"service said {value}, DuckDB says {expected['signup_2024_total']}",
            )

    # 4. Population and contributing observations are different numbers.
    state, run = ask(client, session, "What is average revenue by region?")
    if state == "limited":
        report.skip("4. the average by region discloses both counts", "analysis rate limit")
    elif state != "ok":
        report.bad("4. the average by region discloses both counts", json.dumps(run)[:160])
    else:
        bodies.append(json.dumps(run))
        text = json.dumps(run)
        has_value_count = "value_count" in text or "observations" in text.lower()
        if has_value_count:
            report.ok("4. the result carries the observation count, not only row counts")
        else:
            report.bad(
                "4. the result carries the observation count",
                "neither value_count nor an observation count appears in the payload",
            )
        visible = ((run.get("presentation") or {}).get("table") or {}).get("visible_columns") or []
        if visible and "value_count" not in visible:
            report.ok(f"4. value_count is not a business column (shown: {visible})")
        elif not visible:
            report.skip("4. value_count is not a business column", "no presentation table")
        else:
            report.bad("4. value_count is not a business column", f"visible: {visible}")

    # 5. A trend must exclude null time axes from result and coverage alike.
    state, run = ask(client, session, "Show the monthly trend of revenue using order_date.")
    if state == "limited":
        report.skip("5. the trend excludes null time axes consistently", "analysis rate limit")
    elif state != "ok":
        report.bad("5. the trend excludes null time axes consistently", json.dumps(run)[:160])
    else:
        bodies.append(json.dumps(run))
        snapshot = _first_snapshot(run)
        if snapshot is None:
            report.bad("5. the trend publishes a result", "no snapshot")
        else:
            rows = snapshot.get("rows") or []
            counted = _column_total(snapshot, "row_count")
            covered_cap = expected["total_rows"] - expected["null_order_dates"]
            if counted is not None and counted <= covered_cap:
                report.ok(
                    f"5. the trend covers {counted} rows, excluding the "
                    f"{expected['null_order_dates']} with no order date"
                )
            else:
                report.bad(
                    "5. the trend excludes rows with no order date",
                    f"covered {counted} of {expected['total_rows']}",
                )
            if rows:
                report.ok(f"5. the trend published {len(rows)} periods")

    # 6. An all-null measure completes with nothing published; it does not fail.
    state, run = ask(client, session, "What is the total refund_value where region is West?")
    if state == "limited":
        report.skip("6. an all-null measure is no findings, not a failure", "analysis rate limit")
    elif state != "ok":
        report.bad("6. an all-null measure is no findings, not a failure", json.dumps(run)[:160])
    else:
        bodies.append(json.dumps(run))
        text = json.dumps(run).lower()
        status_value = str(run.get("status"))
        if status_value == "completed" and "failed" not in status_value:
            report.ok("6. the all-null measure run completed rather than failing")
        else:
            report.bad(
                "6. the all-null measure run completed rather than failing",
                f"status={status_value} stopped={run.get('stopped_reason')!r}",
            )
        if "no non-null" in text or "had no" in text or "no values" in text:
            report.ok("6. it says the matching rows held no value")
        else:
            report.bad("6. it says the matching rows held no value", "no such explanation found")

    # 7. A question this engine cannot map refuses once, clearly.
    state, run = ask(
        client, session, "Predict next quarter's revenue and explain the causal drivers."
    )
    if state == "limited":
        report.skip("7. an unmappable question refuses once, clearly", "analysis rate limit")
    elif state != "ok":
        report.bad("7. an unmappable question refuses once, clearly", json.dumps(run)[:160])
    else:
        bodies.append(json.dumps(run))
        published = run.get("results") or {}
        reason = str(run.get("stopped_reason") or "")
        if not published and reason:
            report.ok(f"7. refused with a reason, nothing published ({reason[:60]})")
        else:
            report.bad(
                "7. an unmappable question refuses once, clearly",
                f"published={bool(published)} reason={reason!r}",
            )

    # 8. A named column that can serve no role must not be swapped for one
    #    that can. This is the defect hosted verification found on 242b2f4:
    #    the question named `notes` and the service answered total revenue.
    state, run = ask(client, session, "What is the total notes?")
    if state == "limited":
        report.skip("8. a named unusable column refuses, not substitutes", "analysis rate limit")
    elif state != "ok":
        report.bad("8. a named unusable column refuses, not substitutes", json.dumps(run)[:160])
    else:
        bodies.append(json.dumps(run))
        published = run.get("results") or {}
        value = _single_value(run)
        if not published and value is None:
            report.ok("8. 'total notes' refuses rather than answering with another column")
        else:
            report.bad(
                "8. a named unusable column refuses, not substitutes",
                f"published a value ({value}) for a question about 'notes'",
            )

    # ------------------------------------------------------------ hygiene

    leaked = [b for b in bodies if FORBIDDEN.search(b)]
    if leaked:
        report.bad("no response body carries a secret, path or provider endpoint")
    else:
        report.ok("no response body carries a secret, path or provider endpoint")

    # ------------------------------------------------------------- summary

    print()
    if report.not_run:
        print(f"NOT RUN ({len(report.not_run)}), refused by the deployment's own ceiling:")
        for item in report.not_run:
            print(f"  - {item}")
        print("  These are neither passes nor failures. Re-run after the window.")
        print()
    if report.failed:
        print(f"FAIL: {len(report.failed)} of {len(report.passed) + len(report.failed)} checks")
        for item in report.failed:
            print(f"  - {item}")
        return 1
    print(f"PASS: all {len(report.passed)} checks that ran")
    return 0 if not report.not_run else 2


def _single_value(run: dict[str, Any]) -> float | None:
    snapshot = _first_snapshot(run)
    if not snapshot or not snapshot.get("rows"):
        return None
    columns = snapshot.get("columns") or []
    for index, name in enumerate(columns):
        if str(name).startswith("total_") or str(name).startswith("average_"):
            try:
                return float(snapshot["rows"][0][index])
            except (TypeError, ValueError, IndexError):
                return None
    return None


def _first_snapshot(run: dict[str, Any]) -> dict[str, Any] | None:
    results = run.get("results") or {}
    for value in results.values():
        if isinstance(value, dict) and value.get("columns"):
            return value
    return None


def _column_total(snapshot: dict[str, Any], column: str) -> int | None:
    columns = snapshot.get("columns") or []
    if column not in columns:
        return None
    index = columns.index(column)
    try:
        return sum(int(row[index] or 0) for row in snapshot.get("rows") or [])
    except (TypeError, ValueError, IndexError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
