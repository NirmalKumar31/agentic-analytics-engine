"""Session isolation, the engine lockdown, and the single execution path."""

from __future__ import annotations

import time
from pathlib import Path

import duckdb
import pytest

from agentic_analytics.analytics.catalog import (
    TableNotFound,
    describe_table,
    list_tables,
    profile_table,
    sample_rows,
)
from agentic_analytics.analytics.execute import QueryError, run_query
from agentic_analytics.warehouse.session import (
    AnalysisSession,
    DatasetError,
    SessionManager,
    open_demo_session,
    open_upload_session,
)

LOCKED_DOWN = [
    "select * from read_csv_auto('/etc/passwd')",
    "select * from read_parquet('https://evil.example/x.parquet')",
    "install httpfs",
    "load httpfs",
    "set enable_external_access = true",
    "set lock_configuration = false",
    "copy (select 1) to '/tmp/aae_leak.csv'",
    "attach '/tmp/aae_evil.db' as evil",
]


@pytest.mark.parametrize("sql", LOCKED_DOWN, ids=[s.split()[0] + s[:18] for s in LOCKED_DOWN])
def test_engine_refuses_host_access_even_bypassing_the_guard(
    session: AnalysisSession, sql: str
) -> None:
    """Second layer: the guard is bypassed here on purpose."""
    with pytest.raises(duckdb.Error):
        session.con.execute(sql)


def test_lockdown_is_irreversible(session: AnalysisSession) -> None:
    with pytest.raises(duckdb.Error):
        session.con.execute("SET enable_external_access = true")
    with pytest.raises(duckdb.Error):
        session.con.execute("select * from read_csv_auto('/etc/hosts')")


def test_sessions_are_isolated(warehouse_dir: Path) -> None:
    a = open_demo_session(warehouse_dir)
    b = open_demo_session(warehouse_dir)
    try:
        assert a.session_id != b.session_id
        run_query(a, "select 1 as x", tool_name="run_readonly_sql")
        assert len(a.results) == 1
        assert len(b.results) == 0
    finally:
        a.close()
        b.close()


def test_missing_warehouse_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="not built"):
        open_demo_session(tmp_path)


def test_result_snapshot_records_provenance(session: AnalysisSession) -> None:
    snapshot = run_query(
        session,
        "select count(*) as n from orders",
        tool_name="run_readonly_sql",
        task_id="task-1",
    )
    assert snapshot.result_id.startswith("res_")
    assert snapshot.task_id == "task-1"
    assert snapshot.dataset_fingerprint == session.dataset_fingerprint
    assert snapshot.sql and "orders" in snapshot.sql
    assert snapshot.duration_ms >= 0
    assert session.results.get(snapshot.result_id) is snapshot


def test_truncation_is_flagged_and_warned(session: AnalysisSession) -> None:
    snapshot = run_query(session, "select * from orders", tool_name="run_readonly_sql", max_rows=10)
    assert snapshot.truncated is True
    assert snapshot.row_count == 10
    assert any("truncated" in w for w in snapshot.warnings)


def test_untruncated_result_is_not_flagged(session: AnalysisSession) -> None:
    snapshot = run_query(
        session,
        "select status from orders group by 1",
        tool_name="run_readonly_sql",
        max_rows=100,
    )
    assert snapshot.truncated is False


def test_guard_rejects_writes_through_the_execution_path(session: AnalysisSession) -> None:
    with pytest.raises(QueryError, match="not permitted"):
        run_query(session, "drop table orders", tool_name="run_readonly_sql")


def test_timeout_cancels_a_runaway_query(session: AnalysisSession) -> None:
    # Composed internal SQL bypasses the guard, so this exercises the
    # engine-level cancellation rather than the static generator bound.
    with pytest.raises(QueryError, match="time limit"):
        run_query(
            session,
            "select count(*) from range(200000000) t(i) where i % 7 = 0",
            tool_name="run_readonly_sql",
            timeout_seconds=0.4,
            guard=False,
        )
    # The connection must survive a cancellation.
    assert run_query(session, "select 1 as x", tool_name="run_readonly_sql").rows == [[1]]


def test_values_are_json_safe(session: AnalysisSession) -> None:
    snapshot = run_query(
        session,
        "select order_date, 'a'::varchar s, 1.5::double d, true b, null n from orders limit 2",
        tool_name="run_readonly_sql",
    )
    import json

    json.dumps(snapshot.rows)
    assert isinstance(snapshot.rows[0][0], str), "date became an ISO string"


def test_nan_and_infinity_become_null(session: AnalysisSession) -> None:
    snapshot = run_query(
        session,
        "select 'nan'::double a, 'inf'::double b",
        tool_name="run_readonly_sql",
        guard=False,
    )
    assert snapshot.rows == [[None, None]]


def test_catalog_tools(session: AnalysisSession) -> None:
    tables = list_tables(session)
    assert {t["name"] for t in tables} >= {"orders", "customers", "products"}
    described = describe_table(session, "ORDERS")
    assert described["table"] == "orders"
    assert any(c["name"] == "order_date" for c in described["columns"])
    with pytest.raises(TableNotFound):
        describe_table(session, "not_a_table")


def test_profile_reports_one_row_per_column(session: AnalysisSession) -> None:
    snapshot = profile_table(session, "orders")
    assert snapshot.row_count == len(session.tables["orders"].columns)
    assert snapshot.columns[:2] == ["column_name", "data_type"]
    names = {r[0] for r in snapshot.rows}
    assert "order_date" in names and "discount_rate" in names


def test_sample_rows_is_hard_capped(session: AnalysisSession) -> None:
    snapshot = sample_rows(session, "orders", limit=5_000, max_sample_rows=20)
    assert snapshot.row_count == 20
    assert any("capped" in w for w in snapshot.warnings)


def test_result_store_is_bounded() -> None:
    from agentic_analytics.analytics.results import ResultSnapshot, ResultStore

    store = ResultStore(max_results=3)
    ids = []
    for _ in range(5):
        ids.append(store.put(ResultSnapshot(tool_name="t")).result_id)
    assert len(store) == 3
    with pytest.raises(KeyError):
        store.get(ids[0])
    assert store.get(ids[-1]).tool_name == "t"


def test_result_cell_lookup() -> None:
    from agentic_analytics.analytics.results import ResultSnapshot

    snapshot = ResultSnapshot(tool_name="t", columns=["a", "b"], rows=[[1, 2], [3, 4]], row_count=2)
    assert snapshot.cell(1, "b") == 4
    assert snapshot.to_records() == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    with pytest.raises(KeyError):
        snapshot.cell(0, "missing")
    with pytest.raises(IndexError):
        snapshot.cell(9, "a")


def test_session_manager_expires_and_bounds(warehouse_dir: Path) -> None:
    manager = SessionManager(ttl_seconds=3600, max_sessions=2)
    a = manager.add(open_demo_session(warehouse_dir))
    b = manager.add(open_demo_session(warehouse_dir))
    assert len(manager) == 2
    time.sleep(0.01)
    manager.get(b.session_id, b.session_key)
    c = manager.add(open_demo_session(warehouse_dir))
    assert len(manager) == 2
    with pytest.raises(KeyError):
        manager.get(a.session_id, a.session_key)
    assert manager.get(c.session_id, c.session_key) is c
    manager.close_all()
    assert len(manager) == 0


def test_expired_session_is_dropped(warehouse_dir: Path) -> None:
    manager = SessionManager(ttl_seconds=0.05, max_sessions=4)
    s = manager.add(open_demo_session(warehouse_dir))
    time.sleep(0.1)
    with pytest.raises(KeyError, match="unknown or expired"):
        manager.get(s.session_id, s.session_key)


def test_upload_session_uses_a_server_chosen_table_name(tmp_path: Path) -> None:
    csv = tmp_path / "Q3 report (final).csv"
    csv.write_text("region,amount\nWest,10\nEast,20\n")
    s = open_upload_session(csv, original_name=csv.name, file_format="csv")
    try:
        assert set(s.tables) == {"uploaded_data"}
        assert s.registry is None
        assert s.dataset_fingerprint.startswith("sha256:")
        assert csv.name in s.source_label
        result = run_query(s, "select count(*) n from uploaded_data", tool_name="run_readonly_sql")
        assert result.rows == [[2]]
    finally:
        s.close()


def test_upload_of_an_unreadable_file_is_a_clear_error(tmp_path: Path) -> None:
    bogus = tmp_path / "x.parquet"
    bogus.write_bytes(b"this is definitely not parquet")
    with pytest.raises(DatasetError, match="could not read"):
        open_upload_session(bogus, original_name="x.parquet", file_format="parquet")


def test_empty_upload_is_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "e.csv"
    empty.write_text("a,b\n")
    with pytest.raises(DatasetError, match="no rows"):
        open_upload_session(empty, original_name="e.csv", file_format="csv")
