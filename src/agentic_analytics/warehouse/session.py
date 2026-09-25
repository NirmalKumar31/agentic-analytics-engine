"""Per-session DuckDB isolation and the anonymous capability model.

There is no authentication. A session is reached with a **capability**: a
cryptographically random bearer token issued when the dataset is opened. The
token is the only thing that authorises access to that session's data.

Two identifiers, deliberately separate:

* ``session_id`` -- a public opaque handle. It appears in MCP resource URIs
  and in tool arguments. On its own it authorises nothing.
* ``session_key`` -- the private capability. It travels in an HttpOnly cookie
  to the browser and is injected by the MCP client into every tool call. A
  model never chooses it and it is redacted from the trace.

This is capability-based isolation, not authentication: anyone holding the
token is the session. That is an accurate description of what it provides and
the documentation says so rather than implying more.

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
import json
import secrets
import shutil
import threading
import time
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

# Identifier sizes. Both are generated with `secrets`, so neither is
# guessable and neither is derived from the other. A counter would make the
# handle predictable, and the handle appears in URIs.
SESSION_ID_BYTES = 12
SESSION_KEY_BYTES = 32


def new_session_id() -> str:
    """A public opaque session handle."""
    return f"ses_{secrets.token_urlsafe(SESSION_ID_BYTES)}"


def new_session_key() -> str:
    """A private session capability."""
    return secrets.token_urlsafe(SESSION_KEY_BYTES)


class DatasetError(RuntimeError):
    """The dataset could not be loaded. Message is safe to show a user."""


@dataclass
class TableInfo:
    """What a session knows about one of its tables."""

    name: str
    row_count: int
    columns: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class EngineLimits:
    """The DuckDB resource envelope given to one session's connection.

    Held here rather than hardcoded so a deployment can size it: these
    numbers are multiplied by the number of sessions admitted, and the right
    product depends on the instance, not on the code.
    """

    memory_limit: str = "1GB"
    threads: int = 2

    @classmethod
    def from_settings(cls, cfg: Any) -> EngineLimits:
        return cls(memory_limit=cfg.duckdb_memory_limit, threads=cfg.duckdb_threads)


DEFAULT_ENGINE_LIMITS = EngineLimits()


def _base_config(limits: EngineLimits | None = None) -> dict[str, Any]:
    """Connection config applied before any data is loaded.

    Extension autoloading is off from the very start: it is the one capability
    that could otherwise reintroduce filesystem or network access during the
    load phase.
    """
    limits = limits or DEFAULT_ENGINE_LIMITS
    return {
        "memory_limit": limits.memory_limit,
        "threads": limits.threads,
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
        session_key: str | None = None,
        scratch_dir: Path | None = None,
    ) -> None:
        self.session_id = session_id
        # The capability. Compared in constant time on every access.
        self.session_key = session_key or new_session_key()
        # Anything written for this session lives here and is removed with it.
        self.scratch_dir = scratch_dir
        self.kind = kind
        self.con = con
        self.tables = tables
        self.dataset_fingerprint = fingerprint
        self.registry = registry
        self.source_label = source_label
        #: Generator seed, for the demo warehouse only. An uploaded file has
        #: no seed; its fingerprint is the hash of the bytes.
        self.dataset_seed: int | None = None
        #: Whether this dataset's individual cells may be shown to a remote
        #: model. Set by whoever knows the provider configuration; the
        #: session itself only carries it so that every snapshot it produces
        #: inherits the same answer.
        self.withhold_raw_cells = False
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
        # Uploaded bytes are ephemeral: the scratch directory goes with the
        # session, whether it ended by TTL, by eviction or by an explicit
        # delete from the user.
        if self.scratch_dir is not None:
            with contextlib.suppress(OSError):
                shutil.rmtree(self.scratch_dir, ignore_errors=True)

    def authorises(self, session_key: str | None) -> bool:
        """True when the presented capability matches this session."""
        if not session_key:
            return False
        return secrets.compare_digest(self.session_key, session_key)

    def catalog(self) -> dict[str, Any]:
        """The dataset description an agent is given up front."""
        return {
            "dataset_kind": self.kind,
            "source": self.source_label,
            "dataset_fingerprint": self.dataset_fingerprint,
            "dataset_seed": self.dataset_seed,
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


def open_demo_session(
    warehouse_dir: Path,
    session_id: str | None = None,
    limits: EngineLimits | None = None,
) -> AnalysisSession:
    """Materialise the built-in warehouse into a locked private database."""
    missing = [n for n in TABLE_NAMES if not (warehouse_dir / f"{n}.parquet").exists()]
    if missing:
        raise DatasetError(
            f"demo warehouse is not built (missing: {', '.join(missing)}); run `make data`"
        )
    con = duckdb.connect(":memory:", config=_base_config(limits))
    for name in TABLE_NAMES:
        path = warehouse_dir / f"{name}.parquet"
        # The path is server-controlled, but it is still passed as a bound
        # parameter rather than interpolated.
        con.execute(f'create table "{name}" as select * from read_parquet(?)', [str(path)])
    _lock_down(con)
    tables = {n: _read_table_info(con, n) for n in TABLE_NAMES}
    session = AnalysisSession(
        session_id=session_id or new_session_id(),
        kind="demo",
        con=con,
        tables=tables,
        fingerprint=warehouse_fingerprint(warehouse_dir),
        registry=load_registry(),
        source_label="Commerce demo warehouse",
    )
    manifest = warehouse_dir / "manifest.json"
    if manifest.exists():
        with contextlib.suppress(OSError, ValueError, KeyError):
            session.dataset_seed = int(json.loads(manifest.read_text())["seed"])
    return session


def open_upload_session(
    file_path: Path,
    original_name: str,
    file_format: Literal["csv", "parquet"],
    session_id: str | None = None,
    max_rows: int | None = None,
    scratch_dir: Path | None = None,
    limits: EngineLimits | None = None,
) -> AnalysisSession:
    """Load a single uploaded file into a locked private database.

    The table name is fixed by the server. ``original_name`` is recorded for
    display only and never reaches SQL.
    """
    con = duckdb.connect(":memory:", config=_base_config(limits))
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
        session_id=session_id or new_session_id(),
        kind="upload",
        scratch_dir=scratch_dir,
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

    def get(self, session_id: str, session_key: str | None = None) -> AnalysisSession:
        """Resolve a session, checking the capability.

        The same error is raised for an unknown handle and a wrong capability,
        so a caller cannot use the response to learn which handles exist.
        """
        with self._lock:
            self._evict_locked()
            session = self._sessions.get(session_id)
            if session is None or not session.authorises(session_key):
                raise KeyError(f"unknown or expired session {session_id!r}")
            session.touch()
            return session

    def get_unchecked(self, session_id: str) -> AnalysisSession:
        """Resolve without a capability. Callers must gate on `kind`.

        Used only by the MCP resource handlers, which serve the built-in demo
        dataset -- identical for every visitor and containing no user data.
        An uploaded session is refused there; its data is reachable only
        through the tools, which carry the capability.
        """
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

    def drop_by_key(self, session_key: str | None) -> int:
        """End whatever session the presented capability already opens.

        One browser holds one capability cookie. Opening a second dataset
        replaces that cookie, which would otherwise leave the first session
        alive but unreachable -- holding a DuckDB connection and a scratch
        directory until the TTL expires -- for as long as the visitor keeps
        clicking. Retiring it at the moment the cookie is replaced keeps a
        single browser to a single session, without touching anyone else's:
        the capability is what identifies it.
        """
        if not session_key:
            return 0
        with self._lock:
            doomed = [
                sid for sid, session in self._sessions.items() if session.authorises(session_key)
            ]
            for sid in doomed:
                self._drop_locked(sid)
            return len(doomed)

    def expire_stale(self) -> int:
        """Close every session past its TTL. Returns how many were closed.

        Called on a timer, not only when a request happens to arrive. An
        abandoned upload session otherwise keeps its connection and its bytes
        in memory until some other visitor's request triggers a sweep -- on a
        quiet demo, indefinitely.
        """
        with self._lock:
            before = len(self._sessions)
            self._evict_locked()
            return before - len(self._sessions)

    def close_all(self) -> None:
        with self._lock:
            for sid in list(self._sessions):
                self._drop_locked(sid)

    def upload_count(self) -> int:
        """Live uploaded sessions, for the global upload ceiling."""
        with self._lock:
            self._evict_locked()
            return sum(1 for s in self._sessions.values() if s.kind == "upload")

    def describe_all(self) -> list[dict[str, Any]]:
        """Catalogue for the ``dataset://catalog`` resource.

        Only demo sessions are listed. Enumerating uploaded sessions would
        disclose other visitors' schemas and handles to anyone who can read
        the resource.
        """
        with self._lock:
            self._evict_locked()
            return [
                s.catalog() | {"session_id": s.session_id}
                for s in self._sessions.values()
                if s.kind == "demo"
            ]

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
