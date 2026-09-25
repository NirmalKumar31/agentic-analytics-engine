"""Concurrency safety.

Workers run in parallel against one DuckDB connection per session. A DuckDB
connection carries cursor state, so any read of `description` or `fetchall`
outside the session lock returns whichever query finished last -- a bug this
suite exists to catch, and did catch once.
"""

from __future__ import annotations

import threading
from pathlib import Path

import anyio
import pytest

from agentic_analytics.analytics.compute import compute_metric
from agentic_analytics.analytics.execute import run_query
from agentic_analytics.analytics.stats import statistical_test
from agentic_analytics.mcp_layer.client import AnalyticsToolset, ToolBudget
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.warehouse.session import (
    AnalysisSession,
    SessionManager,
    open_demo_session,
)

ROUNDS = 6
WORKERS = 8


def test_parallel_queries_do_not_mix_up_results(session: AnalysisSession) -> None:
    """Each thread must get back the columns of its own query."""
    queries = {
        "a": ("select 1 as alpha, 2 as beta", ["alpha", "beta"]),
        "b": ("select 'x' as gamma", ["gamma"]),
        "c": ("select status, count(*) as n from orders group by 1", ["status", "n"]),
        "d": ("select order_date, order_id from orders limit 3", ["order_date", "order_id"]),
    }
    errors: list[str] = []

    def work(key: str) -> None:
        sql, expected = queries[key]
        for _ in range(ROUNDS):
            snapshot = run_query(session, sql, tool_name="run_readonly_sql")
            if snapshot.columns != expected:
                errors.append(f"{key}: expected {expected}, got {snapshot.columns}")

    threads = [threading.Thread(target=work, args=(k,)) for k in queries for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors[:5]


def test_parallel_statistical_tests_read_their_own_relation(
    session: AnalysisSession,
) -> None:
    """The bug that motivated this file: relation inspection under contention."""
    errors: list[str] = []

    def stat() -> None:
        for _ in range(ROUNDS):
            try:
                snapshot = statistical_test(
                    session,
                    "chi_square",
                    {
                        "model": "sales",
                        "row_column": "customer_segment",
                        "column_column": "return_reason",
                    },
                )
                if snapshot.statistical_result is None:
                    errors.append("no statistical result")
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

    def metric() -> None:
        for _ in range(ROUNDS):
            compute_metric(session, "revenue", dimensions=["region"], time_grain="quarter")

    threads = [threading.Thread(target=stat) for _ in range(3)]
    threads += [threading.Thread(target=metric) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors, errors[:5]


def test_result_ids_are_unique_under_contention(session: AnalysisSession) -> None:
    collected: list[str] = []
    lock = threading.Lock()

    def work() -> None:
        for _ in range(ROUNDS):
            snapshot = run_query(session, "select 1 as x", tool_name="run_readonly_sql")
            with lock:
                collected.append(snapshot.result_id)

    threads = [threading.Thread(target=work) for _ in range(WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(collected) == WORKERS * ROUNDS
    assert len(set(collected)) == len(collected), "result ids collided"


async def test_concurrent_mcp_calls_return_matching_results(warehouse_dir: Path) -> None:
    """Every concurrent tool call gets its own result, with the right shape."""
    manager = SessionManager()
    session = manager.add(open_demo_session(warehouse_dir))
    server = build_server(manager)

    requests = [
        ("compute_metric", {"metric": "revenue", "dimensions": ["region"]}, "region"),
        ("compute_metric", {"metric": "orders", "dimensions": ["category"]}, "category"),
        ("compare_segments", {"metric": "revenue", "dimension": "brand"}, "brand"),
        ("analyze_timeseries", {"metric": "revenue", "grain": "quarter"}, "period"),
    ]
    outcomes: list[tuple[str, str, str]] = []

    try:
        async with AnalyticsToolset(
            server,
            session_id=session.session_id,
            session_key=session.session_key,
            budget=ToolBudget(max_total=200, max_per_task=200),
        ) as toolset:

            async def one(index: int) -> None:
                tool, args, expected = requests[index % len(requests)]
                payload = await toolset.call(tool, dict(args), task_id=f"t{index}")
                outcomes.append((payload["result_id"], payload["columns"][0], expected))

            async with anyio.create_task_group() as tg:
                for i in range(WORKERS * 2):
                    tg.start_soon(one, i)
    finally:
        manager.close_all()

    assert len(outcomes) == WORKERS * 2
    ids = [o[0] for o in outcomes]
    assert len(set(ids)) == len(ids), "MCP calls shared a result id"
    for _, got, expected in outcomes:
        assert got == expected, f"a call returned another call's columns: {got} != {expected}"


async def test_repeated_parallel_runs_agree(warehouse_dir: Path) -> None:
    """The same question, run repeatedly with parallel workers, agrees."""
    from agentic_analytics.graph.runner import run_analysis

    manager = SessionManager(max_sessions=8)
    server = build_server(manager)
    question = "Revenue increased in Q3 2025, but gross margin fell. What caused it?"
    signatures: list[tuple[str, ...]] = []
    try:
        for _ in range(3):
            session = manager.add(open_demo_session(warehouse_dir))
            result = await run_analysis(question, session, server)
            signatures.append(tuple(f.text for f in result.published))
            manager.drop(session.session_id)
    finally:
        manager.close_all()

    assert len(set(signatures)) == 1, "parallel runs disagreed"
    assert signatures[0], "no findings were published"


def test_session_manager_is_safe_under_concurrent_use(warehouse_dir: Path) -> None:
    manager = SessionManager(max_sessions=64)
    created: list[AnalysisSession] = []
    lock = threading.Lock()
    errors: list[str] = []

    def work() -> None:
        try:
            for _ in range(3):
                session = manager.add(open_demo_session(warehouse_dir))
                with lock:
                    created.append(session)
                assert manager.get(session.session_id, session.session_key) is session
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    try:
        assert not errors, errors[:3]
        ids = [s.session_id for s in created]
        keys = [s.session_key for s in created]
        assert len(set(ids)) == len(ids), "session ids collided"
        assert len(set(keys)) == len(keys), "session keys collided"
    finally:
        manager.close_all()


@pytest.mark.parametrize("attempt", range(3))
def test_decomposition_is_stable_under_contention(session: AnalysisSession, attempt: int) -> None:
    """Driver analysis must not drift when other queries run alongside it."""
    from agentic_analytics.analytics.compute import decompose_change

    baseline = ("2025-04-01", "2025-06-30")
    current = ("2025-07-01", "2025-09-30")
    reference = decompose_change(
        session, "gross_margin_pct", "category", baseline, current
    ).parameters["decomposition"]

    results: list[dict[str, object]] = []
    lock = threading.Lock()

    def work() -> None:
        snapshot = decompose_change(session, "gross_margin_pct", "category", baseline, current)
        with lock:
            results.append(snapshot.parameters["decomposition"])

    def noise() -> None:
        for _ in range(ROUNDS):
            compute_metric(session, "revenue", dimensions=["brand"])

    threads = [threading.Thread(target=work) for _ in range(4)]
    threads += [threading.Thread(target=noise) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert results
    for found in results:
        assert found["observed_change"] == reference["observed_change"]
        assert found["rate_effect_total"] == reference["rate_effect_total"]
        assert found["reconciled"] is True
