"""One authorised, capped Compare Both run against the hosted service.

Usage:
    python scripts/paid_compare_acceptance.py https://service.onrender.com --confirm

Requires `--confirm` because this is the only script here that spends money.
It makes exactly one comparison, never retries, and stops on the first
mismatch rather than trying another question.

Prints no credential, no prompt, no raw provider response and no uploaded
cell. Costs and token counts come from the service's own usage figures.
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
from tests.fixtures.retail_weekly import ROWS, as_csv

#: The question. Small enough to inspect completely, and the shape that
#: failed before: a grouped average whose measure name contains a word the
#: intent matcher once read as a request for something else.
QUESTION = "What is the average Weekly_Revenue by Promo_Flag?"

#: Refuse to report success if the service claims more than this.
CEILING_MICRODOLLARS = 60_000

FORBIDDEN = re.compile(
    r"sk-[A-Za-z0-9_-]{8,}|redis://|rediss://|/Users/|AAE_CLOUD_API_KEY"
    r"|api\.openai\.com|\.internal\b",
    re.IGNORECASE,
)


def oracle() -> dict[int, tuple[float, int]]:
    """Expected averages and row counts, computed before anything is sent."""
    import tempfile

    import duckdb

    path = Path(tempfile.mkdtemp()) / "fixture.csv"
    path.write_text(as_csv())
    db = duckdb.connect()
    db.execute(f"CREATE TABLE t AS SELECT * FROM read_csv_auto('{path}', dateformat='%d-%m-%Y')")
    return {
        int(flag): (round(float(avg), 2), int(rows))
        for flag, avg, rows in db.execute(
            "SELECT Promo_Flag, avg(Weekly_Revenue), count(*) FROM t GROUP BY 1 ORDER BY 1"
        ).fetchall()
    }


def _aggregate(run: dict[str, Any]) -> dict[str, Any]:
    for snapshot in (run.get("results") or {}).values():
        if snapshot.get("tool_name") in {"aggregate_for_question", "compute_metric"}:
            return dict(snapshot)
    return {}


def _canonical(run: dict[str, Any]) -> dict[str, Any]:
    contract = run.get("query_contract") or {}
    return dict(contract.get("canonical_contract") or contract)


def _has_exact_dimension(contract: dict[str, Any], expected: str) -> bool:
    """Check the plural, canonical grouping contract.

    ``dimension`` is retained only on the non-canonical compatibility
    projection.  A canonical contract deliberately has one authoritative
    ``dimensions`` list, so an acceptance test must not look for the legacy
    field after calling :func:`_canonical`.
    """
    return contract.get("dimensions") == [expected]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="required: this script makes one paid model request",
    )
    args = parser.parse_args()
    if not args.confirm:
        print("refusing to spend without --confirm")
        return 2

    expected = oracle()
    checks = Checks()
    client = Client(args.base_url)
    raw_bodies: list[bytes] = []

    body, content_type = _multipart("file", "paid_fixture.csv", as_csv().encode())
    status, _, raw = client.request("/api/datasets/upload", "POST", body, content_type)
    raw_bodies.append(raw)
    if not checks.ok("the fixture uploads", status == 200, f"HTTP {status}"):
        return 1
    session = json.loads(raw)["session_id"]

    status, started = client.json(
        "/api/comparisons", method="POST", payload={"session_id": session, "question": QUESTION}
    )
    if status != 202:
        checks.ok("the comparison starts", False, f"HTTP {status}: {str(started)[:160]}")
        client.request(f"/api/datasets/{session}", "DELETE")
        return 1
    comparison = started["comparison_id"]
    print(f"      comparison {comparison}\n      question: {QUESTION}\n")

    payload: dict[str, Any] = {}
    for _ in range(200):
        time.sleep(3)
        status, payload = client.json(f"/api/comparisons/{comparison}")
        raw_bodies.append(json.dumps(payload).encode())
        sides = [payload.get("deterministic_run"), payload.get("ai_run")]
        if all(s and s.get("status") not in {"running", None} for s in sides):
            break
    else:
        checks.ok("both sides finish", False, "timed out")
        client.request(f"/api/datasets/{session}", "DELETE")
        return 1

    deterministic = dict(payload.get("deterministic_run") or {})
    ai = dict(payload.get("ai_run") or {})
    total_cost = 0
    for name, run in (("deterministic", deterministic), ("ai", ai)):
        usage = run.get("usage") or {}
        cost = int(usage.get("estimated_cost_microdollars") or 0)
        total_cost += cost
        print(
            f"      {name}: status={run.get('status')} "
            f"published={len(run.get('findings') or [])} cost={cost}"
        )
        if cost:
            print(
                f"        calls={usage.get('provider_attempts')} "
                f"in={usage.get('input_tokens')} out={usage.get('output_tokens')}"
            )

    # ---------------------------------------------------------------- status
    checks.ok(
        "deterministic completed",
        deterministic.get("status") == "completed",
        str(deterministic.get("status")),
    )
    ai_status = str(ai.get("status"))
    checks.ok(
        "AI either completed or refused honestly, and is not blank",
        ai_status == "completed" or (ai_status == "refused" and bool(ai.get("stopped_reason"))),
        f"{ai_status}: {(ai.get('stopped_reason') or '')[:80]}",
    )
    checks.ok(
        "neither side is labelled complete unless it completed",
        all(
            run.get("status") == "completed" or not (run.get("findings") or [])
            for run in (deterministic, ai)
        ),
    )

    # -------------------------------------------------------------- contract
    left, right = _canonical(deterministic), _canonical(ai)
    for label, contract in (("deterministic", left), ("AI", right)):
        if ai_status != "completed" and label == "AI":
            continue
        checks.ok(
            f"{label}: operation is average",
            contract.get("operation") == "average",
            str(contract.get("operation")),
        )
        checks.ok(
            f"{label}: measure is the one named",
            contract.get("measure") == "Weekly_Revenue",
            str(contract.get("measure")),
        )
        checks.ok(
            f"{label}: grouping is the one named",
            _has_exact_dimension(contract, "Promo_Flag"),
            str(contract.get("dimensions")),
        )
        checks.ok(
            f"{label}: no invented filter",
            not contract.get("filters"),
            str(contract.get("filters")),
        )
        checks.ok(
            f"{label}: no invented period",
            contract.get("period") in (None, []),
            str(contract.get("period")),
        )
        checks.ok(
            f"{label}: no invented ordering",
            contract.get("ascending") is False,
            str(contract.get("ascending")),
        )

    if ai_status == "completed":
        checks.ok(
            "both panes executed the same canonical contract",
            left == right,
            f"differences: {[k for k in set(left) | set(right) if left.get(k) != right.get(k)]}",
        )
        checks.ok(
            "and the same dataset fingerprint",
            deterministic.get("dataset_fingerprint") == ai.get("dataset_fingerprint"),
        )

    # ---------------------------------------------------------------- values
    for label, run in (("deterministic", deterministic), ("AI", ai)):
        if run.get("status") != "completed":
            continue
        snapshot = _aggregate(run)
        got = {int(row[0]): round(float(row[1]), 2) for row in snapshot.get("rows") or []}
        checks.ok(
            f"{label}: both groups returned",
            set(got) == set(expected),
            f"{sorted(got)} vs {sorted(expected)}",
        )
        checks.ok(
            f"{label}: both averages match the oracle",
            all(abs(got.get(f, -1) - v[0]) < 0.01 for f, v in expected.items()),
            f"{got} vs {{f: v[0] for f, v in expected.items()}}",
        )
        coverage = snapshot.get("group_coverage") or {}
        checks.ok(f"{label}: coverage complete", coverage.get("complete") is True, str(coverage))
        checks.ok(
            f"{label}: every row represented",
            coverage.get("rows_represented") == ROWS,
            str(coverage.get("rows_represented")),
        )
        for finding in run.get("findings") or []:
            for cell in finding.get("evidence_cells") or []:
                target = (run.get("results") or {}).get(cell.get("result_id"))
                checks.ok(
                    f"{label}: a cited cell resolves in this run's own results",
                    target is not None,
                    str(cell.get("result_id")),
                )
                break
            break

    # ------------------------------------------------------------ provenance
    left_ids = set((deterministic.get("results") or {}).keys())
    right_ids = set((ai.get("results") or {}).keys())
    checks.ok(
        "the two sides share no result id",
        not (left_ids & right_ids),
        str(left_ids & right_ids),
    )
    checks.ok(
        "the two sides share no finding id",
        not (
            {f.get("finding_id") for f in deterministic.get("findings") or []}
            & {f.get("finding_id") for f in ai.get("findings") or []}
        ),
    )
    checks.ok(
        "the deterministic side consumed no AI allowance",
        int((deterministic.get("usage") or {}).get("estimated_cost_microdollars") or 0) == 0,
    )

    # ------------------------------------------------------- publication gate
    for label, run in (("deterministic", deterministic), ("AI", ai)):
        findings = run.get("findings") or []
        checks.ok(
            f"{label}: nothing published without a supporting verdict",
            all(f.get("verification_status") == "supported" for f in findings),
            str([f.get("verification_status") for f in findings]),
        )
        if run.get("status") != "completed":
            continue
        # Relevance in the reader's terms: a published answer to "average X
        # by Y" has to mention Y. The engine's own gate is upstream of
        # this; the point here is that it was actually applied.
        text = " ".join(f.get("text", "") for f in findings).lower()
        checks.ok(
            f"{label}: the published answer addresses the grouping asked for",
            "promo" in text or "flag" in text,
            text[:90],
        )
        checks.ok(
            f"{label}: exactly one direct answer, not the same figure twice",
            len(findings) == 1,
            f"{len(findings)} published",
        )

    # ------------------------------------------------------------------ cost
    checks.ok(
        "the reported cost is within the ceiling this run assumed",
        total_cost <= CEILING_MICRODOLLARS,
        f"{total_cost} microdollars",
    )
    checks.ok("the AI side actually used the cloud path", total_cost > 0 or ai_status == "refused")

    # ------------------------------------------------------------------ leaks
    blob = b"".join(raw_bodies).decode("utf-8", errors="replace")
    hits = sorted(set(FORBIDDEN.findall(blob)))
    checks.ok(
        "no secret, private path or provider endpoint in any response",
        not hits,
        f"{len(hits)} pattern(s) matched",
    )

    client.request(f"/api/datasets/{session}", "DELETE")
    checks.ok("the session was deleted", True)

    print(f"\n      total cost {total_cost} microdollars (${total_cost / 1_000_000:.6f})")
    print(f"      captured {len(blob):,} bytes of response body")
    if checks.failures:
        print(f"\nFAIL: {len(checks.failures)} of {checks.passed + len(checks.failures)} checks")
        for failure in checks.failures:
            print(f"  - {failure}")
        return 1
    print(f"\nPASS: all {checks.passed} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
