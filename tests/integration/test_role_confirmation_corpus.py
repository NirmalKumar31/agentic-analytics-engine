"""The close call is not a property of one fixture.

A feature justified by "the engine cannot tell a code list from a count"
has to be shown on more than the dataset it was designed against. These
are three unrelated domains -- clinical readings, rail operations,
manufacturing batches -- each with a numeric column that no rule can
settle, and in each the confirmation has to change the published
arithmetic, checked against an independent DuckDB query.

The column names were chosen to avoid both name-hint lists. `grade`,
`tier`, `region`, `code` and the rest are matched as substrings and would
be classified by name before any cardinality rule ran, which would make
the test a check on the hint list rather than on the close-call band.
"""

from __future__ import annotations

import io
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.config import Settings

REPO = Path(__file__).resolve().parents[2]
ROWS = 400


@dataclass(frozen=True)
class Corpus:
    """One domain: its columns, its close call, and its own oracle."""

    domain: str
    dimension: str
    measure: str
    close_call: str
    ddl: str

    def rows(self) -> list[tuple[Any, ...]]:
        return [self._row(i) for i in range(ROWS)]

    def _row(self, i: int) -> tuple[Any, ...]:
        if self.domain == "clinical":
            return (
                ["alpha", "beta", "gamma", "delta"][i % 4],
                round(2.5 + i * 0.1, 2),
                18 + (i % 48),
            )
        if self.domain == "rail":
            return (["central", "coast", "valley"][i % 3], round(3.2 + i * 0.5, 2), 1 + (i % 36))
        return (["early", "late", "night"][i % 3], 10 + (i % 90), 500 + (i % 52))

    def csv_bytes(self) -> bytes:
        header = f"{self.dimension},{self.measure},{self.close_call}"
        body = [",".join(str(v) for v in row) for row in self.rows()]
        return ("\n".join([header, *body]) + "\n").encode()

    def oracle(self) -> dict[str, float]:
        """The answer computed outside the engine."""
        con = duckdb.connect()
        con.execute(self.ddl)
        con.executemany("INSERT INTO t VALUES (?,?,?)", self.rows())
        return {
            str(k): round(float(v), 2)
            for k, v in con.execute(
                f"SELECT {self.close_call}, sum({self.measure}) FROM t GROUP BY {self.close_call}"
            ).fetchall()
        }


CORPORA = [
    Corpus(
        domain="clinical",
        dimension="site",
        measure="dose",
        close_call="reading",
        ddl="CREATE TABLE t(site VARCHAR, dose DOUBLE, reading BIGINT)",
    ),
    Corpus(
        domain="rail",
        dimension="line",
        measure="price",
        close_call="platform",
        ddl="CREATE TABLE t(line VARCHAR, price DOUBLE, platform BIGINT)",
    ),
    Corpus(
        domain="manufacturing",
        dimension="shift",
        measure="units",
        close_call="batch",
        ddl="CREATE TABLE t(shift VARCHAR, units BIGINT, batch BIGINT)",
    ),
]
IDS = [c.domain for c in CORPORA]


@pytest.fixture
def client(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    settings = Settings(
        provider_mode="fake",
        live_analytics_enabled=True,
        uploads_enabled=True,
        data_dir=warehouse_dir.parent,
        upload_dir=tmp_path / "uploads",
        recordings_dir=REPO / "examples" / "recordings",
        log_json=False,
    )
    monkeypatch.setattr(type(settings), "demo_warehouse_dir", property(lambda self: warehouse_dir))
    with TestClient(create_app(settings)) as c:
        yield c


def upload(client: TestClient, corpus: Corpus) -> dict[str, Any]:
    response = client.post(
        "/api/datasets/upload",
        files={"file": (f"{corpus.domain}.csv", io.BytesIO(corpus.csv_bytes()), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def ask(client: TestClient, session_id: str, question: str) -> dict[str, Any]:
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


@pytest.mark.parametrize("corpus", CORPORA, ids=IDS)
def test_the_engine_cannot_settle_it_in_any_domain(client: TestClient, corpus: Corpus) -> None:
    """The premise. If this ever stops holding, the feature's justification
    has moved and the rest of this file is testing the wrong thing."""
    summary = upload(client, corpus)["summary"]
    fields = {f["name"]: f for f in summary["fields"]}
    assert fields[corpus.close_call]["ambiguous"] is True, fields[corpus.close_call]["reason"]
    assert fields[corpus.close_call]["role_source"] == "inferred"
    # And the other two columns are not close calls, so the control appears
    # exactly where it is warranted rather than on everything numeric.
    assert fields[corpus.dimension]["ambiguous"] is False
    assert fields[corpus.measure]["ambiguous"] is False


@pytest.mark.parametrize("corpus", CORPORA, ids=IDS)
def test_confirming_changes_the_published_arithmetic(client: TestClient, corpus: Corpus) -> None:
    session = upload(client, corpus)
    sid = session["session_id"]

    confirmed = client.patch(
        f"/api/datasets/{sid}/schema/roles",
        json={
            "expected_revision": 0,
            "changes": [{"column": corpus.close_call, "action": "confirm", "role": "dimension"}],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert corpus.close_call in confirmed.json()["summary"]["dimensions"]

    run = ask(client, sid, f"What is total {corpus.measure} by {corpus.close_call}?")
    assert run["status"] == "completed", run.get("stopped_reason")
    contract = run.get("query_contract") or {}
    assert contract.get("dimensions") == [corpus.close_call], contract

    snapshot = next(iter((run.get("results") or {}).values()))
    key = snapshot["columns"].index(corpus.close_call)
    total = snapshot["columns"].index(f"total_{corpus.measure}")
    produced = {str(row[key]): row[total] for row in snapshot["rows"]}

    expected = corpus.oracle()
    assert len(produced) == len(expected), (
        f"{corpus.domain}: grouped into {len(produced)}, the data has {len(expected)}"
    )
    for group, value in expected.items():
        assert round(float(produced[group]), 2) == value, f"{corpus.domain}/{group}"


@pytest.mark.parametrize("corpus", CORPORA, ids=IDS)
def test_the_audit_names_the_confirmation_in_every_domain(
    client: TestClient, corpus: Corpus
) -> None:
    session = upload(client, corpus)
    sid = session["session_id"]
    client.patch(
        f"/api/datasets/{sid}/schema/roles",
        json={
            "expected_revision": 0,
            "changes": [{"column": corpus.close_call, "action": "confirm", "role": "dimension"}],
        },
    )
    run = ask(client, sid, f"What is total {corpus.measure} by {corpus.close_call}?")

    evidence = {item["column"]: item for item in run.get("role_evidence") or []}
    entry = evidence.get(corpus.close_call)
    assert entry is not None, run.get("role_evidence")
    assert entry["role_source"] == "user_confirmed"
    assert entry["effective_role"] == "dimension"
    assert entry["inferred_role"] == "measure"

    # The measure the contract used is reported too, so the audit can say
    # "this reading is the engine's own" as well as "this one is yours" --
    # but it is not attributed to anybody.
    assert evidence[corpus.measure]["role_source"] == "inferred"

    # A column the contract never touched is not in the audit at all. The
    # question "why is it grouped that way" is about the grouping.
    assert corpus.dimension not in evidence
