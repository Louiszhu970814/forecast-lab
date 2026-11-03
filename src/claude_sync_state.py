"""Separate validated Claude sync state from content without approving a baseline.

Only the two exact metadata paths below are recognized. In the round marker only
the UUID token is state: ``listedFor`` is content. In the marketplace manifest
only the etag and row timestamps are state; routing, schema/version, row order,
unknown fields and the existence/types of the state fields remain content.
"""
from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

VERSION = "claude_sync_state_v1"
MAX_MARKER_BYTES = 8192
MAX_LISTED_FOR = 64
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ROWS = 1024
MAX_JSON_DEPTH = 64

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_PATH = re.compile(rf"synced/({_UUID}_{_UUID})/(\.last-complete-round|\.marketplaces\.json)")
_ROUND = re.compile(rf"({_UUID}) ([A-Za-z0-9_.,-]*)")
_LISTED_FOR = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
                   r"(?:\.[0-9]{1,9})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])")


def _read_regular(path: Path, rel: str, limit: int) -> bytes:
    """Bound reads and reject symlinks/FIFOs, including either sync subdirectory.

    Open each component beneath the inventory root through directory descriptors;
    replacing a bucket with a symlink during the read cannot redirect the open.
    The caller inventories the source root itself; this also refuses a symlink at
    that root's final component.
    """
    parts = tuple(rel.split("/"))
    if tuple(path.parts[-3:]) != parts:
        raise ValueError("metadata path does not match its relative name")
    descriptors: list[int] = []
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptors.append(os.open(path.parents[2], directory_flags))
        for part in parts[:-1]:
            descriptors.append(os.open(part, directory_flags, dir_fd=descriptors[-1]))
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=descriptors[-1])
        descriptors.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("sync metadata must be a regular file")
        if before.st_size > limit:
            raise ValueError("sync metadata exceeds size limit")
        chunks: list[bytes] = []
        size = 0
        while size <= limit:
            block = os.read(fd, min(65536, limit + 1 - size))
            if not block:
                break
            chunks.append(block)
            size += len(block)
        if size > limit:
            raise ValueError("sync metadata exceeds size limit")
        after = os.fstat(fd)
        current = os.stat(parts[-1], dir_fd=descriptors[-2], follow_symlinks=False)
        signature = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if signature(before) != signature(after) or signature(after) != signature(current):
            raise OSError("sync metadata changed during read")
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode)
        for index, part in enumerate(parts[:-1], start=1):
            current_dir = os.stat(part, dir_fd=descriptors[index - 1], follow_symlinks=False)
            if identity(current_dir) != identity(os.fstat(descriptors[index])):
                raise OSError("sync metadata directory changed during read")
        current_root = os.stat(path.parents[2], follow_symlinks=False)
        if identity(current_root) != identity(os.fstat(descriptors[0])):
            raise OSError("sync metadata root changed during read")
        return b"".join(chunks)
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key in sync metadata")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("non-finite number in sync metadata")


def _json_values(value, depth=0):
    if depth > MAX_JSON_DEPTH:
        raise ValueError("sync metadata nesting exceeds limit")
    if isinstance(value, dict):
        for key, child in value.items():
            key.encode("utf-8", errors="strict")
            _json_values(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _json_values(child, depth + 1)
    elif isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite number in sync metadata")


def _timestamp(value):
    if not isinstance(value, str) or _TIME.fullmatch(value) is None:
        raise ValueError("sync row timestamp must be an ISO timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError
    except ValueError:
        raise ValueError("invalid sync row timestamp") from None


def metadata_parts(path: Path, rel: str) -> tuple[dict, dict] | None:
    """Return (protected content, informational state), or None for another path.

    This parser never reads unrecognized paths and never authorizes old/new hashes.
    Invalid recognized metadata raises ValueError or OSError. Raw bytes are not
    returned; their hash is available in state for auditing serialization changes.
    """
    match = _PATH.fullmatch(rel)
    if match is None:
        return None
    kind = "round" if match[2] == ".last-complete-round" else "marketplaces"
    raw = _read_regular(path, rel, MAX_MARKER_BYTES if kind == "round" else MAX_MANIFEST_BYTES)
    text = raw.decode("utf-8", errors="strict")
    state = {"version": VERSION, "raw_sha256": hashlib.sha256(raw).hexdigest()}
    if kind == "round":
        marker = _ROUND.fullmatch(text)
        if marker is None:
            raise ValueError("invalid sync round token/listedFor marker")
        listed_for = marker[2].split(",") if marker[2] else []
        if len(listed_for) > MAX_LISTED_FOR or any(_LISTED_FOR.fullmatch(v) is None for v in listed_for):
            raise ValueError("invalid or excessive sync listedFor entries")
        content = {"version": VERSION, "kind": kind,
                   "token_type": "uuid", "listedFor": listed_for}
        state["token"] = marker[1]
        return content, state
    try:
        data = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
        _json_values(data)
    except RecursionError:
        raise ValueError("sync metadata nesting exceeds limit") from None
    if not isinstance(data, dict) or not {"etag", "rows", "parserVersion"} <= data.keys():
        raise ValueError("sync marketplace manifest lacks required fields")
    if not isinstance(data["etag"], str) or not data["etag"] or any(c in data["etag"] for c in "\x00\r\n"):
        raise ValueError("sync marketplace etag must be a nonempty header string")
    if type(data["parserVersion"]) is not int or data["parserVersion"] <= 0:
        raise ValueError("sync marketplace parserVersion must be a positive integer")
    if not isinstance(data["rows"], list) or len(data["rows"]) > MAX_ROWS:
        raise ValueError("invalid or excessive sync marketplace rows")
    normalized = copy.deepcopy(data)
    normalized["etag"] = {"state_type": "string"}
    state["etag"] = data["etag"]
    state["updated_at"] = []
    for row, preserved in zip(data["rows"], normalized["rows"]):
        if not isinstance(row, dict) or not {"name", "display_name", "scope", "source", "id", "updated_at"} <= row.keys():
            raise ValueError("sync marketplace row lacks required fields")
        if any(not isinstance(row[key], str) or not row[key] for key in ("name", "display_name", "scope", "id")):
            raise ValueError("sync marketplace row has invalid identity fields")
        source = row["source"]
        if not isinstance(source, dict) or not isinstance(source.get("source"), str) or not source["source"]:
            raise ValueError("sync marketplace row has an invalid source")
        _timestamp(row["updated_at"])
        preserved["updated_at"] = {"state_type": "iso_timestamp_string"}
        state["updated_at"].append(row["updated_at"])
    return {"version": VERSION, "kind": kind, "manifest": normalized}, state
