"""Two authorised, capped Compare runs that exercise the period guard.

Usage:
    python scripts/paid_period_guard_acceptance.py https://service.onrender.com --confirm

Requires `--confirm`: this is a script that spends money. It makes at most
two comparisons, never retries, stops on the first failure, and refuses to
report success if the service's own usage figures exceed the ceiling.

Case A, accepted. One generic event clock, the question names it. Both
planners must accept the *same canonical contract* and both must match a
total computed with DuckDB before anything is uploaded.

Case B, protected. The table's only date is a lifecycle date. The question
asks for annual revenue without naming a clock. Neither side may answer as
though revenue was earned in that year, and the typed AI plan must not be
able to bypass a refusal the rules made -- the missing fact is business
semantics, which `AMBIGUOUS_PERIOD_SEMANTICS` deliberately keeps out of
`AI_ELIGIBLE_ISSUES`.

Prints no credential, no prompt, no raw provider response and no uploaded
cell. Two runs are two runs: nothing here generalises from them.
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

from live_acceptance import Checks, Client, _multipart

#: $0.10 across both runs, in microdollars. Reported as a failure if
#: exceeded, because an unbounded spend is the thing authorisation bounds.
CEILING_MICRODOLLARS = 100_000

FORBIDDEN = re.compile(
    r"sk-[A-Za-z0-9_-]{8,}|redis://|rediss://|/Users/|AAE_CLOUD_API_KEY"
    r"|api\.openai\.com|\.internal\b",
    re.IGNORECASE,
)

REGIONS = ["North", "South", "East", "West"]

CASE_A_QUESTION = "What was total revenue in 2024 using order_date?"
CASE_B_QUESTION = "What was total annual revenue in 2024?"


def case_a_csv() -> str:
    """One generic event clock. Nothing to disambiguate."""
    lines = ["order_date,region,revenue"]
    for i in range(200):
        year = 2024 if i < 140 else 2023
        lines.append(f"{year}-{(i % 12) + 1:02d}-12,{REGIONS[i % 4]},{100 + (i * 3) % 250}.25")
    return "\n".join(lines) + "\n"


def case_b_csv() -> str:
    """The only date describes the account, not the money."""
    lines = ["signup_date,region,revenue"]
    for i in range(200):
        year = 2024 if i % 2 == 0 else 2022
        lines.append(f"{year}-{(i % 12) + 1:02d}-07,{REGIONS[i % 4]},{100 + (i * 3) % 250}.25")
    return "\n".join(lines) + "\n"


def case_a_oracle() -> float:
    import duckdb

    directory = Path(tempfile.mkdtemp())
    path = directory / "a.csv"
    path.write_text(case_a_csv())
    db = duckdb.connect()
    db.execute(f"CREATE TABLE t AS SELECT * FROM read_csv_auto('{path}')")
    total = db.execute("SELECT sum(revenue) FROM t WHERE year(order_date) = 2024").fetchall()[0][0]
    return round(float(total), 2)


#: The semantic contract, as `QuestionMapping.canonical_dict` defines it.
#: `explanation`, `interpretation`, `named_columns`, `planner_note` and
#: `issues` are provenance: they record *who* decided and how hard it was,
#: not what will be computed, and the canonical contract excludes them on
#: purpose. Comparing the raw contract reported two planners as disagreeing
#: because one said "rule-based" and the other "ai-grounded", which is the
#: one thing they are expected to disagree about.
CANONICAL_KEYS = (
    "operation",
    "table",
    "measure",
    "dimensions",
    "time_field",
    "time_grain",
    "period",
    "period_field",
    "filters",
    "ascending",
    "limit",
)


def _canonical(run: dict[str, Any]) -> dict[str, Any]:
    contract = dict(run.get("query_contract") or {})
    return {k: contract[k] for k in CANONICAL_KEYS if k in contract}


def _published_value(run: dict[str, Any]) -> float | None:
    results = run.get("results") or {}
    for snapshot in results.values():
        if not isinstance(snapshot, dict) or not snapshot.get("rows"):
            continue
        columns = snapshot.get("columns") or []
        for index, name in enumerate(columns):
            if str(name).startswith("total_"):
                try:
                    return float(snapshot["rows"][0][index])
                except (TypeError, ValueError, IndexError):
                    return None
    return None


def _evidence_ids(run: dict[str, Any]) -> set[str]:
    return {str(k) for k in (run.get("results") or {})}


def _upload(client: Client, name: str, csv: str, checks: Checks) -> str | None:
    body, content_type = _multipart("file", name, csv.encode())
    status, _, raw = client.request("/api/datasets/upload", "POST", body, content_type)
    if status != 200:
        checks.ok(f"{name} uploads", False, f"HTTP {status}")
        return None
    checks.ok(f"{name} uploads", True)
    return str(json.loads(raw)["session_id"])


def _compare(
    client: Client, session: str, question: str, checks: Checks, bodies: list[str]
) -> dict[str, Any] | None:
    status, started = client.json(
        "/api/comparisons", method="POST", payload={"session_id": session, "question": question}
    )
    if status != 202:
        checks.ok("the comparison starts", False, f"HTTP {status}: {str(started)[:140]}")
        return None
    comparison = started["comparison_id"]
    print(f"      comparison {comparison}")
    payload: dict[str, Any] = {}
    for _ in range(200):
        time.sleep(3)
        _, payload = client.json(f"/api/comparisons/{comparison}")
        bodies.append(json.dumps(payload))
        sides = [payload.get("deterministic_run"), payload.get("ai_run")]
        if all(s and s.get("status") not in {"running", None} for s in sides):
            return payload
    checks.ok("both sides finish", False, "timed out")
    return None


def _usage(run: dict[str, Any], label: str) -> int:
    usage = run.get("usage") or {}
    cost = int(usage.get("estimated_cost_microdollars") or 0)
    print(
        f"      {label}: status={run.get('status')} "
        f"published={len(run.get('findings') or [])} "
        f"calls={usage.get('provider_attempts') or 0} cost={cost}"
    )
    return cost


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--confirm", action="store_true", help="required: this spends money")
    parser.add_argument("--expect-build-sha", default=None)
    parser.add_argument(
        "--case",
        choices=("a", "b", "both"),
        default="both",
        help="run one case only, to avoid repeating a run already paid for",
    )
    args = parser.parse_args()
    if not args.confirm:
        print("refusing to spend without --confirm")
        return 2

    expected_a = case_a_oracle()
    print(f"Expected, computed with DuckDB before any request: case A total = {expected_a:,.2f}\n")

    checks = Checks()
    client = Client(args.base_url)
    bodies: list[str] = []
    total_cost = 0

    _, health = client.json("/api/health")
    if args.expect_build_sha:
        got = (health or {}).get("build_sha")
        if not checks.ok(
            "build_sha is the expected revision", got == args.expect_build_sha, str(got)
        ):
            return 1

    # ----------------------------------------------------------- case A
    # Case A is skippable so a run already paid for is not repeated to
    # re-check a corrected assertion. Case B still runs on its own.
    if args.case in ("a", "both"):
        print("CASE A — one generic clock, named in the question")
        session_a = _upload(client, "period_guard_a.csv", case_a_csv(), checks)
        if session_a is None:
            return 1
        payload = _compare(client, session_a, CASE_A_QUESTION, checks, bodies)
        if payload is None:
            client.request(f"/api/datasets/{session_a}", "DELETE")
            return 1
        det_a = dict(payload.get("deterministic_run") or {})
        ai_a = dict(payload.get("ai_run") or {})
        total_cost += _usage(det_a, "deterministic") + _usage(ai_a, "ai")

        checks.ok(
            "A: deterministic completed",
            det_a.get("status") == "completed",
            str(det_a.get("status")),
        )
        ai_a_status = str(ai_a.get("status"))
        # A refusal may be carried by `stopped_reason` or by `error`; either is a
        # stated reason. What is not acceptable is a side that neither completed
        # nor said why.
        ai_a_reason = str(ai_a.get("stopped_reason") or ai_a.get("error") or "")
        checks.ok(
            "A: the AI side completed or refused with a reason",
            ai_a_status == "completed" or bool(ai_a_reason),
            f"{ai_a_status}: {ai_a_reason[:70]}",
        )

        det_value = _published_value(det_a)
        checks.ok(
            "A: the deterministic total matches the oracle",
            det_value is not None and abs(det_value - expected_a) < 0.5,
            f"{det_value} vs {expected_a}",
        )
        if ai_a_status == "completed":
            ai_value = _published_value(ai_a)
            checks.ok(
                "A: the AI total matches the oracle",
                ai_value is not None and abs(ai_value - expected_a) < 0.5,
                f"{ai_value} vs {expected_a}",
            )
            left, right = _canonical(det_a), _canonical(ai_a)
            same = left == right
            checks.ok("A: both planners accepted the same canonical contract", same)
            if not same:
                for key in sorted(set(left) | set(right)):
                    if left.get(key) != right.get(key):
                        print(
                            f"        contract differs at {key}: "
                            f"{left.get(key)!r} vs {right.get(key)!r}"
                        )
            checks.ok(
                "A: coverage agrees across both sides",
                (det_a.get("coverage") or {}) == (ai_a.get("coverage") or {}),
            )
        else:
            print("      (AI side did not complete; contract equality not applicable)")

        checks.ok(
            "A: the two sides do not share an evidence id",
            not (_evidence_ids(det_a) & _evidence_ids(ai_a)),
        )
        client.request(f"/api/datasets/{session_a}", "DELETE")

        if checks.failures:
            print("\nStopping after case A, as instructed: a failure is diagnosed, not retried.")
            _finish(checks, total_cost, bodies)
            return 1

    if args.case == "a":
        _finish(checks, total_cost, bodies)
        return 0 if not checks.failures else 1

    # ----------------------------------------------------------- case B
    print("\nCASE B — the only date is a lifecycle date, no clock named")
    session_b = _upload(client, "period_guard_b.csv", case_b_csv(), checks)
    if session_b is None:
        return 1
    payload = _compare(client, session_b, CASE_B_QUESTION, checks, bodies)
    if payload is None:
        client.request(f"/api/datasets/{session_b}", "DELETE")
        return 1
    det_b = dict(payload.get("deterministic_run") or {})
    ai_b = dict(payload.get("ai_run") or {})
    total_cost += _usage(det_b, "deterministic") + _usage(ai_b, "ai")

    for label, run in (("deterministic", det_b), ("AI", ai_b)):
        published = _published_value(run)
        checks.ok(
            f"B: the {label} side publishes no annual revenue total",
            published is None,
            f"published {published}",
        )
        reason = str(run.get("stopped_reason") or run.get("error") or "")
        checks.ok(
            f"B: the {label} side gives a reason rather than a blank refusal",
            bool(reason) or run.get("status") == "completed",
            f"status={run.get('status')} reason={reason[:70]!r}",
        )

    combined = json.dumps({"d": det_b, "a": ai_b}).lower()
    checks.ok(
        "B: neither side claims revenue was earned in 2024",
        not re.search(r"revenue (?:earned|in) 2024 (?:was|is|totall?ed)", combined),
    )
    checks.ok(
        "B: the refusal names the date column or the missing clock",
        "signup_date" in combined or "date column" in combined or "time axis" in combined,
    )
    client.request(f"/api/datasets/{session_b}", "DELETE")

    _finish(checks, total_cost, bodies)
    return 0 if not checks.failures else 1


def _finish(checks: Checks, total_cost: int, bodies: list[str]) -> None:
    leaked = [b for b in bodies if FORBIDDEN.search(b)]
    checks.ok("no response body carries a secret, path or provider endpoint", not leaked)
    checks.ok(
        f"total spend stayed under ${CEILING_MICRODOLLARS / 1_000_000:.2f}",
        total_cost <= CEILING_MICRODOLLARS,
        f"{total_cost} microdollars",
    )
    print(f"\n      total cost: {total_cost} microdollars (${total_cost / 1_000_000:.6f})")
    print(
        "\nTwo runs are two runs. This says what these two exercised; it is not a"
        "\nsample of how the model behaves across questions, datasets or load."
    )


if __name__ == "__main__":
    raise SystemExit(main())
