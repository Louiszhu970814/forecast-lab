"""Append-only JSON Lines storage with flock synchronization."""

from __future__ import annotations

import fcntl
import json
import os
import warnings
from pathlib import Path
from typing import Any, Callable


class JsonlParseError(ValueError):
    """Raised when a JSONL line cannot be parsed."""

    def __init__(self, message: str, *, path: Path, lineno: int, line: str) -> None:
        super().__init__(message)
        self.path = path
        self.lineno = lineno
        self.line_number = lineno
        self.line = line


class IncompleteLineWarning(UserWarning):
    """Issued when strict=False encounters an incomplete trailing line."""


class RecordList(list):
    """A list of parsed JSONL records that also carries incomplete-last-line metadata."""

    def __init__(
        self,
        iterable: list[Any] | None = None,
        *,
        incomplete_line: str | None = None,
        incomplete_lineno: int | None = None,
    ) -> None:
        super().__init__(iterable or [])
        self.incomplete_line = incomplete_line
        self.incomplete_lineno = incomplete_lineno


def append_record(path: str | Path, record: Any) -> None:
    """Append a single JSON record as one line to ``path`` using O_APPEND, flock, and fsync.

    Creates parent directories if they do not exist.
    No overwrite or delete functions are provided by design.
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    serialized = json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    payload = (serialized + "\n").encode("utf-8")

    fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            written = os.write(fd, payload)
            if written != len(payload):
                raise OSError(f"Partial write to {file_path}: wrote {written} of {len(payload)} bytes")
            os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def read_records(
    path: str | Path,
    strict: bool = True,
    *,
    on_incomplete: Callable[[int, str], None] | None = None,
) -> RecordList:
    """Read and parse records line by line from a JSONL file.

    If ``strict=True``, any unparseable line raises :class:`JsonlParseError` reporting
    the 1-based line number.
    If ``strict=False``, only the final line is tolerated if incomplete/unparseable;
    it is reported via :class:`IncompleteLineWarning`, ``on_incomplete`` callback (if given),
    and ``result.incomplete_line`` / ``result.incomplete_lineno`` attributes.
    """
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if not text:
        return RecordList()

    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    records = RecordList()

    for lineno, raw_line in enumerate(lines, start=1):
        stripped = raw_line.rstrip("\r\n")
        is_last = lineno == total_lines
        try:
            if not stripped.strip():
                raise json.JSONDecodeError("Empty line", stripped, 0)
            if not strict and is_last and not raw_line.endswith("\n"):
                # Even if a truncated prefix happens to be valid JSON (e.g. a number),
                # missing trailing newline on the last line indicates an interrupted write.
                obj = json.loads(stripped)
                if not isinstance(obj, dict):
                    raise json.JSONDecodeError("Unterminated line without newline", stripped, len(stripped))
            else:
                obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            if not strict and is_last:
                records.incomplete_line = raw_line
                records.incomplete_lineno = lineno
                if on_incomplete is not None:
                    on_incomplete(lineno, raw_line)
                warnings.warn(
                    f"{file_path}:{lineno}: incomplete last line at line {lineno}: {raw_line!r}",
                    IncompleteLineWarning,
                    stacklevel=2,
                )
                break
            raise JsonlParseError(
                f"{file_path}:{lineno}: failed to parse JSON at line {lineno}: {exc.msg}",
                path=file_path,
                lineno=lineno,
                line=raw_line,
            ) from exc

        records.append(obj)

    return records
