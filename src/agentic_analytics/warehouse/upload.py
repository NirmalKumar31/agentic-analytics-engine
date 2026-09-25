"""Upload validation.

An uploaded file is attacker-controlled in every respect: its name, its
extension, its declared content type and its bytes. So none of those are
trusted:

* the size ceiling is enforced while streaming, not from a header,
* the format is decided by content, not by the extension -- though "by
  content" means different things for the two formats, and the distinction
  is stated rather than glossed: **Parquet** has a real signature (``PAR1``
  at both ends) and self-describing metadata, so it is genuinely validated.
  **CSV has no magic bytes.** It is accepted as bounded delimited text after
  being screened against the signatures of formats it is definitely not, and
  DuckDB's parser is the real arbiter,
* the stored filename is generated here and the user's name is kept only as a
  display label,
* the table name is fixed by the server, so the name never reaches SQL.
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal

FileFormat = Literal["csv", "parquet"]

PARQUET_MAGIC = b"PAR1"
# Read this much to decide whether the bytes are plausible delimited text.
SNIFF_BYTES = 8192

# Signatures of formats that are definitely not a CSV or Parquet file, checked
# so a renamed executable or archive is refused with a clear message rather
# than failing later inside DuckDB.
EXECUTABLE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x7fELF", "an ELF executable"),
    (b"MZ", "a Windows executable"),
    (b"\xca\xfe\xba\xbe", "a Mach-O or Java class file"),
    (b"\xcf\xfa\xed\xfe", "a Mach-O executable"),
    (b"\xfe\xed\xfa\xce", "a Mach-O executable"),
    (b"PK\x03\x04", "a zip archive"),
    (b"\x1f\x8b", "a gzip archive"),
    (b"BZh", "a bzip2 archive"),
    (b"\xfd7zXZ", "an xz archive"),
    (b"%PDF", "a PDF"),
    (b"\x89PNG", "a PNG image"),
    (b"\xff\xd8\xff", "a JPEG image"),
    (b"SQLite format 3", "a SQLite database"),
    (b"<!DOCTYPE", "an HTML document"),
    (b"<html", "an HTML document"),
    (b"<?xml", "an XML document"),
)

_SAFE_LABEL = re.compile(r"[^A-Za-z0-9._ ()\-]")
MAX_LABEL_LENGTH = 80


class UploadError(ValueError):
    """The upload was refused. The message is safe to show a user."""


@dataclass
class UploadLimits:
    """Everything a hostile file could stretch."""

    max_bytes: int = 25 * 1024 * 1024
    max_columns: int = 200
    max_column_name_length: int = 128
    max_row_groups: int = 4096
    max_metadata_bytes: int = 8 * 1024 * 1024
    #: Ceiling on the *declared* uncompressed size in the Parquet footer.
    #: This is the one bound that a file's size on disk does not give you:
    #: Parquet compresses well, so a 15 MB upload can declare gigabytes of
    #: uncompressed data and expand into memory on read. It is an admission
    #: ceiling on a self-declared number, not a prediction of RAM use --
    #: Arrow's in-memory representation differs from the declared size in
    #: both directions.
    max_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024


@dataclass
class StoredUpload:
    """A validated file on disk."""

    path: Path
    display_name: str
    file_format: FileFormat
    size_bytes: int

    def unlink(self) -> None:
        with contextlib.suppress(OSError):  # cleanup is best effort
            self.path.unlink(missing_ok=True)


def safe_display_name(original: str) -> str:
    """A label safe to show in a UI and to put in a log line.

    Strips directory components, control characters and anything outside a
    conservative character set. The result is never used as a path or in SQL;
    it exists so a user can recognise their own file.
    """
    name = unicodedata.normalize("NFKC", original or "")
    # Both separators, because a Windows client may send either.
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    name = _SAFE_LABEL.sub("_", name).strip()
    name = name.lstrip(".") or "upload"
    return name[:MAX_LABEL_LENGTH]


def detect_format(head: bytes, tail: bytes, declared_name: str) -> FileFormat:
    """Decide the format from bytes, using the name only to break a tie."""
    for signature, description in EXECUTABLE_SIGNATURES:
        if head.startswith(signature):
            raise UploadError(
                f"the uploaded file looks like {description}, not a CSV or Parquet file"
            )

    if head.startswith(PARQUET_MAGIC) and tail.endswith(PARQUET_MAGIC):
        return "parquet"
    if head.startswith(PARQUET_MAGIC) or tail.endswith(PARQUET_MAGIC):
        raise UploadError("the file has a Parquet marker but is truncated or corrupt")

    if b"\x00" in head:
        raise UploadError("the uploaded file is binary and is not a valid Parquet file")

    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = head.decode("latin-1")
        except UnicodeDecodeError:  # pragma: no cover - latin-1 decodes anything
            raise UploadError("the uploaded file is not readable as text") from None

    first_line = text.splitlines()[0] if text.splitlines() else ""
    if not first_line.strip():
        raise UploadError("the uploaded file has no header row")
    if not any(delimiter in first_line for delimiter in (",", ";", "\t", "|")):
        raise UploadError(
            "the first line has no delimiter; only CSV and Parquet files are accepted"
        )
    del declared_name
    return "csv"


def store_upload(
    stream: BinaryIO,
    original_name: str,
    directory: Path,
    max_bytes: int,
) -> StoredUpload:
    """Stream an upload to disk under a generated name, enforcing the ceiling.

    The ceiling is checked as bytes arrive, so an oversized or
    length-misreporting upload is stopped rather than buffered.
    """
    directory.mkdir(parents=True, exist_ok=True)
    display = safe_display_name(original_name)
    # Random name, fixed extension. The user's name is never a path component.
    path = directory / f"upload_{secrets.token_hex(16)}.bin"

    size = 0
    head = b""
    tail = b""
    try:
        with path.open("wb") as handle:
            while True:
                chunk = stream.read(1024 * 256)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UploadError(f"the file exceeds the {max_bytes // (1024 * 1024)} MB limit")
                if len(head) < SNIFF_BYTES:
                    head += chunk[: SNIFF_BYTES - len(head)]
                tail = (tail + chunk)[-8:]
                handle.write(chunk)
        if size == 0:
            raise UploadError("the uploaded file is empty")
        file_format = detect_format(head, tail, display)
    except UploadError:
        path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise UploadError(f"the upload could not be stored: {exc.strerror}") from None

    # Owner-only, so nothing else on a shared host can read a user's data.
    os.chmod(path, 0o600)
    return StoredUpload(path=path, display_name=display, file_format=file_format, size_bytes=size)


def inspect_parquet(path: Path, limits: UploadLimits) -> dict[str, Any]:
    """Read Parquet metadata and bound its shape before any data is loaded.

    Parquet is self-describing, so the footer alone reveals the column count,
    the row-group count, the schema depth and the uncompressed size. Checking
    those first means a file crafted to explode on read -- tens of thousands
    of row groups, a deeply nested schema, a petabyte of declared uncompressed
    data -- is refused without decoding a single value.
    """
    import pyarrow.parquet as pq

    try:
        metadata = pq.read_metadata(path)
    except Exception as exc:  # pyarrow raises a family of errors here
        raise UploadError(f"the Parquet metadata could not be read: {type(exc).__name__}") from None

    if metadata.serialized_size > limits.max_metadata_bytes:
        raise UploadError("the Parquet metadata block is larger than this service accepts")
    if metadata.num_columns > limits.max_columns:
        raise UploadError(
            f"the file has {metadata.num_columns} columns; the limit is {limits.max_columns}"
        )
    if metadata.num_row_groups > limits.max_row_groups:
        raise UploadError(
            f"the file has {metadata.num_row_groups} row groups; "
            f"the limit is {limits.max_row_groups}"
        )

    uncompressed = int(
        sum(metadata.row_group(i).total_byte_size for i in range(metadata.num_row_groups))
    )
    if uncompressed > limits.max_uncompressed_bytes:
        # Refused from the footer, before a single value is decoded.
        raise UploadError(
            f"the file declares {uncompressed / (1024 * 1024):,.0f} MB of uncompressed "
            f"data; the limit is {limits.max_uncompressed_bytes / (1024 * 1024):,.0f} MB"
        )

    try:
        schema = metadata.schema.to_arrow_schema()
    except Exception as exc:
        raise UploadError(f"the Parquet schema could not be read: {type(exc).__name__}") from None

    for field_ in schema:
        if len(field_.name) > limits.max_column_name_length:
            raise UploadError(f"a column name exceeds {limits.max_column_name_length} characters")
        # Any nesting at all: a struct of scalars already has depth 1.
        if _arrow_depth(field_.type) > 0:
            raise UploadError(
                f"column {field_.name!r} is a nested type; this service accepts flat "
                "tabular data only"
            )

    return {
        "rows": int(metadata.num_rows),
        "columns": int(metadata.num_columns),
        "row_groups": int(metadata.num_row_groups),
        "uncompressed_bytes": uncompressed,
    }


def _arrow_depth(arrow_type: Any) -> int:
    """Nesting depth of an Arrow type. Flat types are 0."""
    if getattr(arrow_type, "num_fields", 0):
        return 1 + max(
            (_arrow_depth(arrow_type.field(i).type) for i in range(arrow_type.num_fields)),
            default=0,
        )
    return 0


def inspect_csv_header(head: bytes, limits: UploadLimits) -> dict[str, Any]:
    """Bound the header of a delimited text file.

    This is a header check, not a format proof. CSV has no signature; the
    guarantee here is that the first line parses as a bounded set of
    reasonably named fields, and that DuckDB -- not this function -- decides
    whether the body is readable.
    """
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        text = head.decode("latin-1", errors="replace")

    lines = text.splitlines()
    if not lines or not lines[0].strip():
        raise UploadError("the uploaded file has no header row")

    header = lines[0]
    if len(header) > limits.max_columns * limits.max_column_name_length:
        raise UploadError("the header row is longer than this service accepts")

    delimiter = max(",;\t|", key=header.count)
    if header.count(delimiter) == 0:
        raise UploadError(
            "the first line has no delimiter; only CSV and Parquet files are accepted"
        )

    names = [n.strip().strip('"').strip("'") for n in header.split(delimiter)]
    if len(names) > limits.max_columns:
        raise UploadError(f"the file has {len(names)} columns; the limit is {limits.max_columns}")
    for name in names:
        if len(name) > limits.max_column_name_length:
            raise UploadError(f"a column name exceeds {limits.max_column_name_length} characters")

    return {"columns": len(names), "delimiter": delimiter}
