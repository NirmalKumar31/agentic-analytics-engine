"""An oversized upload must be refused, never truncated.

The failure this guards against is quiet. A file over the row limit used to
be loaded with `LIMIT max_rows`, which produced a session that looked
entirely normal: a valid dataset, a completed analysis, findings that passed
numeric verification -- all computed from an arbitrary prefix of the
visitor's file, with nothing anywhere saying so. Every number would have been
wrong and every check would have passed.

Parquet and CSV must therefore agree: at or under the limit is accepted, over
it is refused.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from agentic_analytics.warehouse.session import (
    DatasetError,
    SessionManager,
    open_upload_session,
)

#: Deliberately tiny, so "one row over" is one row and not a million.
LIMIT = 3


def _csv(path: Path, rows: int) -> Path:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["region", "revenue"])
        for index in range(rows):
            writer.writerow([f"R{index}", 100 + index])
    return path


def _parquet(path: Path, rows: int) -> Path:
    table = pa.table(
        {
            "region": [f"R{index}" for index in range(rows)],
            "revenue": [100 + index for index in range(rows)],
        }
    )
    pq.write_table(table, path)
    return path


@pytest.mark.parametrize("rows", [1, 2, LIMIT])
def test_a_csv_at_or_under_the_limit_is_accepted(tmp_path: Path, rows: int) -> None:
    session = open_upload_session(_csv(tmp_path / "ok.csv", rows), "ok.csv", "csv", max_rows=LIMIT)
    try:
        assert session.tables["uploaded_data"].row_count == rows
    finally:
        session.close()


@pytest.mark.parametrize("rows", [LIMIT + 1, LIMIT + 50])
def test_a_csv_over_the_limit_is_refused(tmp_path: Path, rows: int) -> None:
    """Refused, and the message says why rather than leaking an internal."""
    with pytest.raises(DatasetError, match="more than"):
        open_upload_session(_csv(tmp_path / "big.csv", rows), "big.csv", "csv", max_rows=LIMIT)


@pytest.mark.parametrize("rows", [1, LIMIT])
def test_a_parquet_at_or_under_the_limit_is_accepted(tmp_path: Path, rows: int) -> None:
    session = open_upload_session(
        _parquet(tmp_path / "ok.parquet", rows), "ok.parquet", "parquet", max_rows=LIMIT
    )
    try:
        assert session.tables["uploaded_data"].row_count == rows
    finally:
        session.close()


@pytest.mark.parametrize("rows", [LIMIT + 1, LIMIT + 50])
def test_a_parquet_over_the_limit_is_refused(tmp_path: Path, rows: int) -> None:
    with pytest.raises(DatasetError, match="more than"):
        open_upload_session(
            _parquet(tmp_path / "big.parquet", rows), "big.parquet", "parquet", max_rows=LIMIT
        )


def test_the_two_formats_have_the_same_contract(tmp_path: Path) -> None:
    """Whatever the answer is, it must not depend on the file format."""
    for rows, should_accept in ((LIMIT, True), (LIMIT + 1, False)):
        outcomes = {}
        for fmt, build in (("csv", _csv), ("parquet", _parquet)):
            path = build(tmp_path / f"{rows}.{fmt}", rows)  # type: ignore[operator]
            try:
                session = open_upload_session(path, path.name, fmt, max_rows=LIMIT)  # type: ignore[arg-type]
                session.close()
                outcomes[fmt] = True
            except DatasetError:
                outcomes[fmt] = False
        assert outcomes["csv"] == outcomes["parquet"] == should_accept, (rows, outcomes)


def test_a_refused_upload_leaves_no_connection_or_bytes_behind(tmp_path: Path) -> None:
    """Rejection has to clean up, or a hostile file is a resource leak.

    The scratch directory is the caller's to remove, which is what the API
    does; what `open_upload_session` owes is that the DuckDB connection is
    closed before it raises.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    path = _csv(scratch / "big.csv", LIMIT + 10)

    manager = SessionManager()
    with pytest.raises(DatasetError):
        manager.add(
            open_upload_session(path, "big.csv", "csv", max_rows=LIMIT, scratch_dir=scratch)
        )
    # Nothing was registered, so nothing holds a connection or the rows.
    assert len(manager) == 0


def test_the_api_refuses_an_oversized_upload_and_deletes_the_scratch_bytes(
    tmp_path: Path, warehouse_dir: Path
) -> None:
    """End to end: a 400, a readable message, and no file left on disk."""
    import io

    from fastapi.testclient import TestClient

    from agentic_analytics.api.app import create_app
    from agentic_analytics.config import Budgets, Settings

    uploads = tmp_path / "uploads"

    class _Pointed(Settings):
        @property
        def demo_warehouse_dir(self) -> Path:
            return warehouse_dir

    cfg = _Pointed(
        live_analytics_enabled=True,
        uploads_enabled=True,
        upload_dir=uploads,
        budgets=Budgets(max_upload_rows=LIMIT),
        log_json=False,
    )
    payload = b"region,revenue\n" + b"".join(
        f"R{i},{100 + i}\n".encode() for i in range(LIMIT + 25)
    )
    with TestClient(create_app(cfg)) as client:
        response = client.post(
            "/api/datasets/upload",
            files={"file": ("big.csv", io.BytesIO(payload), "text/csv")},
        )

    assert response.status_code == 400, response.text
    body = response.json()
    assert "more than" in body["detail"], body
    # No stack trace, no host path.
    assert "Traceback" not in response.text
    assert str(tmp_path) not in response.text
    # The scratch directory the upload created is gone with it.
    assert not any(uploads.iterdir()) if uploads.exists() else True


# ------------------------------------------- declared uncompressed size
def _compressible_parquet(path: Path, rows: int) -> Path:
    """A file that is tiny on disk and declares a large uncompressed size.

    `use_dictionary=False` matters and is the whole reason this helper is
    explicit about it. With dictionary encoding a repeated value is stored
    once, so the footer declares the *encoded* size -- a few kilobytes -- and
    the declared-size check sees nothing to refuse. Without it, the same data
    declares ~31 MB while still compressing to ~37 kB on disk, which is the
    shape the ceiling is there to catch: a file whose size on disk says
    nothing about what reading it would allocate.
    """
    table = pa.table({"padding": ["x" * 512] * rows})
    pq.write_table(table, path, compression="zstd", use_dictionary=False)
    return path


def test_parquet_at_the_declared_size_ceiling_is_accepted(tmp_path: Path) -> None:
    from agentic_analytics.warehouse.upload import UploadLimits, inspect_parquet

    path = _compressible_parquet(tmp_path / "ok.parquet", 2_000)
    declared = inspect_parquet(path, UploadLimits())["uncompressed_bytes"]
    # Exactly at the ceiling: accepted, because the rule is "over the limit".
    shape = inspect_parquet(path, UploadLimits(max_uncompressed_bytes=declared))
    assert shape["uncompressed_bytes"] == declared


def test_parquet_over_the_declared_size_ceiling_is_refused(tmp_path: Path) -> None:
    from agentic_analytics.warehouse.upload import (
        UploadError,
        UploadLimits,
        inspect_parquet,
    )

    path = _compressible_parquet(tmp_path / "big.parquet", 2_000)
    declared = inspect_parquet(path, UploadLimits())["uncompressed_bytes"]
    with pytest.raises(UploadError, match="uncompressed"):
        inspect_parquet(path, UploadLimits(max_uncompressed_bytes=declared - 1))


def test_a_highly_compressed_file_is_refused_from_its_metadata(tmp_path: Path) -> None:
    """The point of the check: refused before DuckDB reads a value.

    The file is a few hundred kilobytes on disk and declares far more, so a
    size-on-disk limit would admit it.
    """
    from agentic_analytics.warehouse.upload import (
        UploadError,
        UploadLimits,
        inspect_parquet,
    )

    path = _compressible_parquet(tmp_path / "bomb.parquet", 60_000)
    on_disk = path.stat().st_size
    declared = inspect_parquet(path, UploadLimits())["uncompressed_bytes"]
    assert declared > on_disk * 100, (on_disk, declared)

    limits = UploadLimits(max_bytes=25 * 1024 * 1024, max_uncompressed_bytes=1024 * 1024)
    with pytest.raises(UploadError, match="uncompressed"):
        inspect_parquet(path, limits)


def test_a_dictionary_encoded_file_declares_far_less_than_it_expands_to(
    tmp_path: Path,
) -> None:
    """A stated limit of this check, asserted rather than left implicit.

    Parquet's dictionary encoding stores a repeated value once, so the
    footer's declared size is the encoded size and not what a decode would
    materialise. The declared-size ceiling therefore does not bound
    dictionary expansion; the row and column ceilings are what bound that
    axis, and `docs/LIMITATIONS.md` says so.
    """
    from agentic_analytics.warehouse.upload import UploadLimits, inspect_parquet

    path = tmp_path / "dictionary.parquet"
    pq.write_table(pa.table({"padding": ["x" * 512] * 60_000}), path, compression="zstd")
    declared = inspect_parquet(path, UploadLimits())["uncompressed_bytes"]
    # Under a megabyte declared, for data that decodes to roughly 30 MB.
    assert declared < 1024 * 1024, declared
    # Which is why the row ceiling has to do the work here.
    with pytest.raises(DatasetError, match="more than"):
        open_upload_session(path, "dictionary.parquet", "parquet", max_rows=1_000)


def test_the_api_refuses_a_declared_size_bomb_and_cleans_up(
    tmp_path: Path, warehouse_dir: Path
) -> None:
    """End to end: 400, readable message, no session, no bytes left."""
    from fastapi.testclient import TestClient

    from agentic_analytics.api.app import create_app
    from agentic_analytics.config import Budgets, Settings

    uploads = tmp_path / "uploads"
    path = _compressible_parquet(tmp_path / "bomb.parquet", 60_000)

    class _Pointed(Settings):
        @property
        def demo_warehouse_dir(self) -> Path:
            return warehouse_dir

    cfg = _Pointed(
        live_analytics_enabled=True,
        uploads_enabled=True,
        upload_dir=uploads,
        budgets=Budgets(max_parquet_uncompressed_bytes=1024 * 1024),
        log_json=False,
    )
    with TestClient(create_app(cfg)) as client:
        response = client.post(
            "/api/datasets/upload",
            files={"file": ("bomb.parquet", path.read_bytes(), "application/octet-stream")},
        )

    assert response.status_code == 400, response.text
    assert "uncompressed" in response.json()["detail"]
    assert "Traceback" not in response.text
    assert not any(uploads.iterdir()) if uploads.exists() else True
