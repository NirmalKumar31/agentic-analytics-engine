"""How a run reports the way it ended.

The defect: `run_analysis` returns a `RunResult` on its failure path as
well as its success path, and the API derived status from whether that
object existed. So a run that raised internally, emitted RUN_FAILED and
produced no report was reported to the browser as "completed".

The distinction that matters is not success versus failure but *which*
ending. A run that honestly found nothing publishable is complete and says
why; a run that timed out, exhausted its budget, was cancelled or broke is
not, and a reader deciding whether to trust an empty report needs to know
which of those happened.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.api.runs import RunRegistry
from agentic_analytics.config import Settings
from agentic_analytics.graph.runner import RunResult, _outcome_for, _outcome_from_reason

REPO = Path(__file__).resolve().parents[2]


def _settings(warehouse_dir: Path, tmp_path: Path, **overrides: Any) -> Settings:
    class _Pointed(Settings):
        @property
        def demo_warehouse_dir(self) -> Path:
            return warehouse_dir

    defaults: dict[str, Any] = {
        "upload_dir": tmp_path / "uploads",
        "recordings_dir": REPO / "examples" / "recordings",
        "live_analytics_enabled": True,
        "log_json": False,
    }
    return _Pointed(**(defaults | overrides))


def _result(**kw: Any) -> RunResult:
    base: dict[str, Any] = {
        "run_id": "run_1",
        "question": "q",
        "session_id": "ses_1",
        "dataset": {},
        "report": None,
    }
    return RunResult(**(base | kw))


# ──────────────────────────────────────────────────── outcome classification
def test_an_ordinary_run_is_complete() -> None:
    assert _result().outcome == "completed"
    assert _result().failed is False


def test_a_run_that_published_nothing_is_still_complete() -> None:
    """Found nothing is an answer. Calling it a failure would make it
    indistinguishable from broke, which is the distinction this exists for."""
    assert _outcome_from_reason("") == "completed"


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("the run reached its time budget", "timeout"),
        ("This AI run reached its time limit.", "timeout"),
        ("This AI run reached its input token limit.", "budget_exhausted"),
        ("the run exhausted its budget", "budget_exhausted"),
        ("the language model declined to answer", "refused"),
        ("the question could not be mapped safely: ambiguous measure", "refused"),
        ("the dataset was closed", "cancelled"),
        ("no analysis task produced a usable result", "failed"),
    ],
)
def test_each_stop_reason_maps_to_its_own_outcome(reason: str, expected: str) -> None:
    assert _outcome_from_reason(reason) == expected


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (asyncio.CancelledError(), "cancelled"),
        (TimeoutError(), "timeout"),
        (RuntimeError("boom"), "failed"),
    ],
)
def test_each_exception_maps_to_its_own_outcome(exc: BaseException, expected: str) -> None:
    assert _outcome_for(exc) == expected


def test_a_budget_error_is_not_reported_as_a_crash() -> None:
    """Hitting a ceiling is the ceiling working, not the engine breaking."""

    class AIBudgetExceeded(Exception):
        pass

    assert _outcome_for(AIBudgetExceeded("limit")) == "budget_exhausted"


# ─────────────────────────────────────────────── the API's reported status
def test_a_failed_run_is_not_reported_as_completed() -> None:
    """The defect, stated directly.

    A `RunResult` exists on the failure path, so status must come from the
    run's own outcome rather than from that object's existence.
    """
    registry = RunRegistry()
    record = registry.create("ses_1", "q")
    record.result = _result(outcome="failed", stopped_reason="the run failed (RuntimeError)")
    assert record.status == "failed"


@pytest.mark.parametrize(
    "outcome", ["timeout", "budget_exhausted", "refused", "cancelled", "failed"]
)
def test_every_non_completion_survives_to_the_api(outcome: str) -> None:
    registry = RunRegistry()
    record = registry.create("ses_1", "q")
    record.result = _result(outcome=outcome)
    assert record.status == outcome


def test_a_zero_finding_completion_is_reported_as_completed() -> None:
    registry = RunRegistry()
    record = registry.create("ses_1", "q")
    record.result = _result(outcome="completed")
    assert record.status == "completed"


def test_a_running_run_is_neither(warehouse_dir: Path, tmp_path: Path) -> None:
    registry = RunRegistry()
    record = registry.create("ses_1", "q")
    assert record.status == "running"
    assert record.is_terminal is False


def test_a_zero_finding_run_states_why_it_published_nothing(
    warehouse_dir: Path, tmp_path: Path
) -> None:
    """An empty report that explains nothing is indistinguishable from a
    broken one."""
    cfg = _settings(warehouse_dir, tmp_path)
    with TestClient(create_app(cfg)) as client:
        session = client.post("/api/datasets/demo").json()["session_id"]
        started = client.post(
            "/api/analyses",
            json={"session_id": session, "question": "total revenue in 1998"},
        )
        assert started.status_code == 202
        run_id = started.json()["run_id"]
        import time

        payload: dict[str, Any] = {}
        for _ in range(200):
            payload = client.get(f"/api/analyses/{run_id}").json()
            if payload.get("report") or payload.get("error"):
                break
            time.sleep(0.05)

    report = payload.get("report") or {}
    findings = payload.get("findings") or []
    if not findings:
        reasons = list(report.get("limitations") or [])
        assert reasons, "a run published nothing and gave no reason"
        assert report.get("executive_summary"), "no summary explaining the empty report"


# ───────────────────────────────────────── a query that matched nothing
#
# The defect: a filter matching no rows came back `outcome: failed`, reason
# "the executed result could not be turned into a direct answer". The SQL ran
# and the table held no matching rows, which is a result. Reporting it as a
# failure told a reader the system broke when it worked, the exact
# confusion this module's docstring says the outcome vocabulary exists to
# prevent.


def _upload_and_ask(
    client: TestClient, question: str, *, csv: bytes | None = None
) -> dict[str, Any]:
    """Run one question against an uploaded file, through the real path."""
    import io
    import time

    if csv is None:
        rows = [b"region,revenue,order_date,units"]
        for i in range(200):
            region = [b"North", b"South", b"East", b"West"][i % 4]
            rows.append(
                region
                + b","
                + str(100 + i * 7).encode()
                + b",2025-"
                + f"{(i % 12) + 1:02d}".encode()
                + b"-15,"
                + str(2 + (i % 5)).encode()
            )
        csv = b"\n".join(rows) + b"\n"

    upload = client.post(
        "/api/datasets/upload",
        files={"file": ("zero.csv", io.BytesIO(csv), "text/csv")},
    )
    assert upload.status_code == 200, upload.text
    session_id = upload.json()["session_id"]

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


@pytest.mark.parametrize(
    ("label", "question"),
    [
        # Ungrouped: `sum(x) WHERE false` still returns one row, holding a
        # NULL measure and a row count of zero.
        ("ungrouped and filtered", "What is total revenue where region is Atlantis?"),
        # Grouped: GROUP BY emits no rows at all.
        (
            "grouped and filtered",
            "What is total revenue by region where region is Atlantis?",
        ),
        # A period outside the data's range.
        ("filtered by period", "What is total revenue by region in 2019?"),
    ],
)
def test_a_query_that_matched_nothing_is_complete_and_says_why(
    warehouse_dir: Path, tmp_path: Path, label: str, question: str
) -> None:
    cfg = _settings(warehouse_dir, tmp_path, uploads_enabled=True)
    with TestClient(create_app(cfg)) as client:
        payload = _upload_and_ask(client, question)

    assert payload["status"] == "completed", f"{label}: {payload.get('stopped_reason')}"
    assert payload.get("outcome", "completed") == "completed"
    # Nothing published, and nothing withheld: there was no claim to check.
    assert payload.get("findings") == []
    assert payload.get("rejected") == []

    # The reader is told why, in terms of what actually ran.
    limitations = (payload.get("report") or {}).get("limitations") or []
    assert limitations, f"{label}: published nothing and gave no reason"
    assert any("No rows" in item for item in limitations), limitations

    # Provenance survives: the accepted contract and the executed result are
    # both still reportable, which is what makes the empty answer auditable.
    assert payload.get("query_contract"), f"{label}: lost the accepted contract"
    assert payload.get("results"), f"{label}: lost the executed result"


def test_the_explanation_names_the_restriction_that_emptied_the_result(
    warehouse_dir: Path, tmp_path: Path
) -> None:
    cfg = _settings(warehouse_dir, tmp_path, uploads_enabled=True)
    with TestClient(create_app(cfg)) as client:
        payload = _upload_and_ask(
            client, "What is total revenue by region where region is Atlantis?"
        )
    limitations = " ".join((payload.get("report") or {}).get("limitations") or [])
    # Naming the predicate is the difference between a reader looking for a
    # fault in their data and seeing the cause.
    assert "region" in limitations
    assert "Atlantis" in limitations


def test_a_question_that_matches_rows_still_answers(warehouse_dir: Path, tmp_path: Path) -> None:
    """The boundary. Too eager a zero-row check silently withholds answers."""
    cfg = _settings(warehouse_dir, tmp_path, uploads_enabled=True)
    with TestClient(create_app(cfg)) as client:
        payload = _upload_and_ask(client, "What is total revenue by region?")
    assert payload["status"] == "completed"
    assert payload.get("findings"), "a question with matching rows published nothing"


def test_a_real_execution_error_is_still_a_failure(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The invariant the zero-row change must not weaken.

    A query that cannot execute is not a query that matched nothing, and the
    two must not collapse into one reassuring outcome. Driven by making the
    engine's own SQL execution raise, so the failure arrives through the tool
    boundary exactly as a real one would.
    """
    from agentic_analytics.analytics.execute import QueryError
    from agentic_analytics.mcp_layer import server as mcp_server

    def _explode(*_args: Any, **_kwargs: Any) -> Any:
        raise QueryError("relation does not exist")

    monkeypatch.setattr(mcp_server, "run_query", _explode)

    cfg = _settings(warehouse_dir, tmp_path, uploads_enabled=True)
    with TestClient(create_app(cfg)) as client:
        payload = _upload_and_ask(client, "What is total revenue by region?")

    assert payload["status"] == "failed", payload.get("stopped_reason")
    assert payload.get("findings") == []
    # And it must not be dressed up as an empty result.
    limitations = " ".join((payload.get("report") or {}).get("limitations") or [])
    assert "No rows matched" not in limitations
