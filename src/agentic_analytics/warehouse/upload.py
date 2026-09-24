"""Upload validation.

An uploaded file is attacker-controlled in every respect: its name, its
extension, its declared content type and its bytes. So none of those are
trusted:

* the size ceiling is enforced while streaming, not from a header,
* the format is decided by content, not by the extension,
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
from typing import BinaryIO, Literal

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
