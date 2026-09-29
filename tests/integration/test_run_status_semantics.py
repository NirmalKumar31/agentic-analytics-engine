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
