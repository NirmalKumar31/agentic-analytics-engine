"""A confirmed role has to change the arithmetic, not just the label.

Everything here runs the whole path -- upload, DuckDB, real `infer_schema`,
the confirmation endpoint, the effective schema, the resolver, MCP, SQL,
verification, and checks the published numbers against an independent
DuckDB query. A test that asserted only on the schema payload would pass
while the engine grouped by a column it still considered a measure, which is
exactly the failure this feature exists to prevent.

The fixture is deliberately domain-neutral. `reading` is a repeated integer
in the band where a code list and a genuine count are indistinguishable;
`amount` is an unambiguous additive measure; `place` is an ordinary
dimension. No production rule keys on a column name, and the names here
avoid both hint lists so the cardinality rules are what classify them.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.config import Settings

REPO = Path(__file__).resolve().parents[2]

PLACES = ["north", "south", "east", "west"]
ROWS = 400


def rows() -> list[tuple[str, float, int]]:
    return [(PLACES[i % 4], round(100.0 + i * 7.5, 2), 18 + (i % 48)) for i in range(ROWS)]


def csv_bytes() -> bytes:
    lines = ["place,amount,reading"]
    lines += [f"{p},{a},{r}" for p, a, r in rows()]
    return ("\n".join(lines) + "\n").encode()


def oracle() -> duckdb.DuckDBPyConnection:
    """An independent answer, computed outside the engine."""
    con = duckdb.connect()
    con.execute("CREATE TABLE t(place VARCHAR, amount DOUBLE, reading BIGINT)")
    con.executemany("INSERT INTO t VALUES (?,?,?)", rows())
    return con


def _settings(warehouse_dir: Path, tmp_path: Path) -> Settings:
    return Settings(
        provider_mode="fake",
        live_analytics_enabled=True,
        uploads_enabled=True,
        data_dir=warehouse_dir.parent,
        upload_dir=tmp_path / "uploads",
        recordings_dir=REPO / "examples" / "recordings",
        log_json=False,
    )


@pytest.fixture
def client(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    settings = _settings(warehouse_dir, tmp_path)
    monkeypatch.setattr(type(settings), "demo_warehouse_dir", property(lambda self: warehouse_dir))
    with TestClient(create_app(settings)) as c:
        yield c


def upload(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/datasets/upload",
        files={"file": ("rows.csv", io.BytesIO(csv_bytes()), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def confirm(
    client: TestClient, session_id: str, revision: int, column: str, role: str
) -> dict[str, Any]:
    response = client.patch(
        f"/api/datasets/{session_id}/schema/roles",
        json={
            "expected_revision": revision,
            "changes": [{"column": column, "action": "confirm", "role": role}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def reset(client: TestClient, session_id: str, revision: int, column: str) -> dict[str, Any]:
    response = client.patch(
        f"/api/datasets/{session_id}/schema/roles",
        json={
            "expected_revision": revision,
            "changes": [{"column": column, "action": "reset"}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def ask(client: TestClient, session_id: str, question: str) -> dict[str, Any]:
    import time

    started = client.post(
        "/api/analyses",
        json={"session_id": session_id, "question": question, "mode": "deterministic"},
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    payload: dict[str, Any] = {}
    for _ in range(400):
        payload = client.get(f"/api/analyses/{run_id}").json()
        if payload.get("status") != "running":
            break
        time.sleep(0.05)
    assert payload.get("status") != "running", "the run never finished"
    return payload


def field_named(summary: dict[str, Any], name: str) -> dict[str, Any]:
    return next(f for f in summary["fields"] if f["name"] == name)


# ------------------------------------------------------------ the sequence


def test_the_column_starts_as_an_unsettled_close_call(client: TestClient) -> None:
    summary = upload(client)["summary"]
    reading = field_named(summary, "reading")
    assert reading["ambiguous"] is True
    assert reading["role_source"] == "inferred"
    assert summary["schema_revision"] == 0


def test_confirming_a_category_makes_it_groupable(client: TestClient) -> None:
    """The arithmetic has to change, checked against an independent query."""
    session = upload(client)
    sid = session["session_id"]
    summary = confirm(client, sid, 0, "reading", "dimension")["summary"]
    assert "reading" in summary["dimensions"]

    run = ask(client, sid, "What is total amount by reading?")
    assert run["status"] == "completed", run.get("stopped_reason")

    contract = run.get("query_contract") or {}
    assert contract.get("dimensions") == ["reading"], contract

    snapshot = next(iter((run.get("results") or {}).values()))
    produced = {
        str(row[snapshot["columns"].index("reading")]): row[
            snapshot["columns"].index("total_amount")
        ]
        for row in snapshot["rows"]
    }

    con = oracle()
    expected = {
        str(r): round(float(t), 2)
        for r, t in con.execute("SELECT reading, sum(amount) FROM t GROUP BY reading").fetchall()
    }
    assert len(produced) == len(expected), (
        f"grouped into {len(produced)} groups, the data has {len(expected)}"
    )
    for key, value in expected.items():
        assert round(float(produced[key]), 2) == value


def test_the_run_records_the_revision_it_used(client: TestClient) -> None:
    session = upload(client)
    sid = session["session_id"]
    confirm(client, sid, 0, "reading", "dimension")
    run = ask(client, sid, "What is total amount by reading?")
    assert run.get("schema_revision") == 1


def test_provenance_names_the_confirmation(client: TestClient) -> None:
    session = upload(client)
    sid = session["session_id"]
    confirm(client, sid, 0, "reading", "dimension")
    run = ask(client, sid, "What is total amount by reading?")

    evidence = {item["column"]: item for item in run.get("role_evidence") or []}
    assert "reading" in evidence, run.get("role_evidence")
    entry = evidence["reading"]
    assert entry["effective_role"] == "dimension"
    assert entry["inferred_role"] == "measure"
    assert entry["role_source"] == "user_confirmed"
    assert "grouping" in entry["used_as"]


def test_a_reset_returns_the_engine_to_its_own_reading(client: TestClient) -> None:
    session = upload(client)
    sid = session["session_id"]
    confirm(client, sid, 0, "reading", "dimension")
    summary = reset(client, sid, 1, "reading")["summary"]
    reading = field_named(summary, "reading")
    assert reading["role_source"] == "inferred"
    assert reading["role"] == "measure"
    assert "reading" not in summary["dimensions"]


def test_an_older_completed_run_keeps_its_own_evidence(client: TestClient) -> None:
    """A later confirmation must not relabel history.

    A completed run is evidence of what happened. Rewriting its provenance
    when the session's schema moves on would make the audit a description of
    the present rather than a record of the past.
    """
    session = upload(client)
    sid = session["session_id"]
    first = ask(client, sid, "What is total amount by place?")
    assert first["schema_revision"] == 0
    before = first.get("role_evidence")

    confirm(client, sid, 0, "reading", "dimension")

    again = client.get(f"/api/analyses/{first['run_id']}").json()
    assert again["schema_revision"] == 0
    assert again.get("role_evidence") == before


def test_a_new_run_uses_the_new_revision(client: TestClient) -> None:
    session = upload(client)
    sid = session["session_id"]
    ask(client, sid, "What is total amount by place?")
    confirm(client, sid, 0, "reading", "dimension")
    later = ask(client, sid, "What is total amount by place?")
    assert later["schema_revision"] == 1


def test_an_unconfirmed_session_runs_at_revision_zero(client: TestClient) -> None:
    session = upload(client)
    run = ask(client, session["session_id"], "What is total amount by place?")
    assert run["schema_revision"] == 0
    assert run["status"] == "completed"


def test_the_ordinary_answer_is_unchanged_by_the_feature(client: TestClient) -> None:
    """The boundary: a dataset nobody confirms must behave exactly as before."""
    session = upload(client)
    run = ask(client, session["session_id"], "What is total amount by place?")
    snapshot = next(iter((run.get("results") or {}).values()))
    produced = {
        str(row[snapshot["columns"].index("place")]): round(
            float(row[snapshot["columns"].index("total_amount")]), 2
        )
        for row in snapshot["rows"]
    }
    con = oracle()
    expected = {
        str(p): round(float(t), 2)
        for p, t in con.execute("SELECT place, sum(amount) FROM t GROUP BY place").fetchall()
    }
    assert produced == expected
