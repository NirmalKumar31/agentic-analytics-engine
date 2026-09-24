"""Per-session DuckDB isolation.

Each analysis session owns a private in-memory DuckDB database. The database
is built in two phases:

1. **Load.** Parquet or CSV is read from disk. This needs filesystem access.
2. **Lock.** ``enable_external_access`` is set to false and
   ``lock_configuration`` to true.

After phase 2 the engine refuses filesystem reads, network reads, ``COPY``,
``ATTACH`` and extension loading, and refuses to have those settings turned
back on -- so even a statement that somehow got past
:mod:`agentic_analytics.warehouse.sqlguard` cannot reach the host. Phase 2 is
irreversible for the life of the connection, which is why it is done once at
construction rather than per query.
"""

from __future__ import annotations

import contextlib
import hashlib
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import duckdb

from agentic_analytics.analytics.results import ResultStore
from agentic_analytics.data.generator import TABLE_NAMES, warehouse_fingerprint
from agentic_analytics.logging import get_logger
from agentic_analytics.warehouse.metrics import MetricRegistry, load_registry

log = get_logger(__name__)

DatasetKind = Literal["demo", "upload"]

# The single table an uploaded file becomes. The name is fixed by the server;
# a user-supplied filename never reaches SQL.
UPLOAD_TABLE = "uploaded_data"


class DatasetError(RuntimeError):
    """The dataset could not be loaded. Message is safe to show a user."""


@dataclass
class TableInfo:
    """What a session knows about one of its tables."""

    name: str
    row_count: int
    columns: list[dict[str, str]] = field(default_factory=list)


def _base_config() -> dict[str, Any]:
    """Connection config applied before any data is loaded.

    Extension autoloading is off from the very start: it is the one capability
    that could otherwise reintroduce filesystem or network access during the
    load phase.
    """
    return {
        "memory_limit": "1GB",
        "threads": 2,
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "allow_community_extensions": False,
        "allow_unsigned_extensions": False,
        "allow_persistent_secrets": False,
    }


def _lock_down(con: duckdb.DuckDBPyConnection) -> None:
    """Phase 2. Irreversible for this connection."""
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")


class AnalysisSession:
    """One isolated dataset plus the results computed against it."""

    def __init__(
        self,
        session_id: str,
        kind: DatasetKind,
        con: duckdb.DuckDBPyConnection,
        tables: dict[str, TableInfo],
        fingerprint: str,
        registry: MetricRegistry | None,
        source_label: str,
    ) -> None:
        self.session_id = session_id
        self.kind = kind
        self.con = con
        self.tables = tables
        self.dataset_fingerprint = fingerprint
        self.registry = registry
        self.source_label = source_label
        self.results = ResultStore()
        self.created_at = time.time()
        self.last_used_at = self.created_at
        # DuckDB connections are not safe to use from several threads at
        # once, and analysis workers run concurrently.
        self.lock = threading.Lock()

    @property
    def table_names(self) -> set[str]:
        return set(self.tables)

    @property
    def has_metrics(self) -> bool:
        return self.registry is not None

    def touch(self) -> None:
        self.last_used_at = time.time()

    def close(self) -> None:
        with contextlib.suppress(Exception):  # close is best effort
            self.con.close()

    def catalog(self) -> dict[str, Any]:
        """The dataset description an agent is given up front."""
        return {
            "dataset_kind": self.kind,
            "source": self.source_label,
            "dataset_fingerprint": self.dataset_fingerprint,
            "tables": [
                {
                    "name": t.name,
                    "row_count": t.row_count,
                    "columns": t.columns,
                }
                for t in self.tables.values()
            ],
            "metrics_available": sorted(self.registry.metrics) if self.registry else [],
        }


def _read_table_info(con: duckdb.DuckDBPyConnection, name: str) -> TableInfo:
    rows = con.execute(
        "select column_name, data_type from information_schema.columns "
        "where table_name = ? order by ordinal_position",
        [name],
    ).fetchall()
    count_row = con.execute(f'select count(*) from "{name}"').fetchone()
    return TableInfo(
        name=name,
        row_count=int(count_row[0]) if count_row else 0,
        columns=[{"name": str(r[0]), "type": str(r[1])} for r in rows],
    )


def open_demo_session(warehouse_dir: Path, session_id: str | None = None) -> AnalysisSession:
    """Materialise the built-in warehouse into a locked private database."""
    missing = [n for n in TABLE_NAMES if not (warehouse_dir / f"{n}.parquet").exists()]
    if missing:
        raise DatasetError(
            f"demo warehouse is not built (missing: {', '.join(missing)}); run `make data`"
        )
    con = duckdb.connect(":memory:", config=_base_config())
    for name in TABLE_NAMES:
        path = warehouse_dir / f"{name}.parquet"
        # The path is server-controlled, but it is still passed as a bound
        # parameter rather than interpolated.
        con.execute(f'create table "{name}" as select * from read_parquet(?)', [str(path)])
    _lock_down(con)
    tables = {n: _read_table_info(con, n) for n in TABLE_NAMES}
    return AnalysisSession(
        session_id=session_id or f"ses_{uuid.uuid4().hex[:12]}",
        kind="demo",
        con=con,
        tables=tables,
        fingerprint=warehouse_fingerprint(warehouse_dir),
        registry=load_registry(),
        source_label="Commerce demo warehouse",
    )


def open_upload_session(
    file_path: Path,
    original_name: str,
    file_format: Literal["csv", "parquet"],
    session_id: str | None = None,
    max_rows: int | None = None,
) -> AnalysisSession:
    """Load a single uploaded file into a locked private database.

    The table name is fixed by the server. ``original_name`` is recorded for
    display only and never reaches SQL.
    """
    con = duckdb.connect(":memory:", config=_base_config())
    try:
        reader = "read_parquet(?)" if file_format == "parquet" else "read_csv_auto(?)"
        limit = f" limit {int(max_rows)}" if max_rows else ""
        con.execute(
            f'create table "{UPLOAD_TABLE}" as select * from {reader}{limit}', [str(file_path)]
        )
    except duckdb.Error as exc:
        con.close()
        raise DatasetError(f"could not read the uploaded file as {file_format}: {exc}") from None
    _lock_down(con)

    info = _read_table_info(con, UPLOAD_TABLE)
    if info.row_count == 0:
        con.close()
        raise DatasetError("the uploaded file contains no rows")

    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()[:32]
    return AnalysisSession(
        session_id=session_id or f"ses_{uuid.uuid4().hex[:12]}",
        kind="upload",
        con=con,
        tables={UPLOAD_TABLE: info},
        fingerprint=f"sha256:{digest}",
        # Uploads have no semantic metric layer: the file is one arbitrary
        # table, so agents profile it and write guarded SQL instead.
        registry=None,
        source_label=f"Uploaded file: {original_name}",
    )


class SessionManager:
    """Creates, tracks and expires analysis sessions."""

    def __init__(self, ttl_seconds: float = 3600.0, max_sessions: int = 32) -> None:
        self._sessions: dict[str, AnalysisSession] = {}
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._lock = threading.Lock()

    def add(self, session: AnalysisSession) -> AnalysisSession:
        with self._lock:
            self._evict_locked()
            if len(self._sessions) >= self._max:
                oldest = min(self._sessions.values(), key=lambda s: s.last_used_at)
                self._drop_locked(oldest.session_id)
            self._sessions[session.session_id] = session
        log.info(
            "session_opened",
            session_id=session.session_id,
            kind=session.kind,
            fingerprint=session.dataset_fingerprint,
        )
        return session

    def get(self, session_id: str) -> AnalysisSession:
        with self._lock:
            self._evict_locked()
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(f"unknown or expired session {session_id!r}")
            session.touch()
            return session

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._drop_locked(session_id)

    def close_all(self) -> None:
        with self._lock:
            for sid in list(self._sessions):
                self._drop_locked(sid)

    def describe_all(self) -> list[dict[str, Any]]:
        """Catalogue of every live session, for the ``dataset://catalog`` resource."""
        with self._lock:
            self._evict_locked()
            return [s.catalog() | {"session_id": s.session_id} for s in self._sessions.values()]

    def __len__(self) -> int:
        with self._lock:
            self._evict_locked()
            return len(self._sessions)

    def _evict_locked(self) -> None:
        cutoff = time.time() - self._ttl
        for sid, session in list(self._sessions.items()):
            if session.last_used_at < cutoff:
                self._drop_locked(sid)

    def _drop_locked(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        session.close()
        log.info("session_closed", session_id=session_id)
