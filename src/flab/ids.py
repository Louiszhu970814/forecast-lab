"""Deterministic JSON serialization, SHA-256 hashing, and time-ordered ID generation."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any


def new_id(prefix: str) -> str:
    """Return a time-ordered unique identifier starting with ``prefix``.

    Format: ``<prefix>_<13-digit-epoch-ms>_<8-hex-random>``.
    """
    if not isinstance(prefix, str):
        raise TypeError(f"Expected str for prefix, got {type(prefix).__name__}")
    cleaned = prefix.strip().rstrip("_")
    if not cleaned:
        raise ValueError("prefix must be a non-empty string")
    ts_ms = time.time_ns() // 1_000_000
    rand_hex = secrets.token_hex(4)
    return f"{cleaned}_{ts_ms:013d}_{rand_hex}"


def canonical_json(obj: Any) -> str:
    """Serialize ``obj`` to canonical JSON (sorted keys, compact separators, UTF-8 characters)."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_hex(data: str | bytes) -> str:
    """Compute lowercase hexadecimal SHA-256 digest of ``str`` (UTF-8) or ``bytes``."""
    if isinstance(data, str):
        raw = data.encode("utf-8")
    elif isinstance(data, bytes):
        raw = data
    else:
        raise TypeError(f"Expected str or bytes, got {type(data).__name__}")
    return hashlib.sha256(raw).hexdigest()


def hash_obj(obj: Any) -> str:
    """Compute SHA-256 hex digest of the canonical JSON representation of ``obj``."""
    return sha256_hex(canonical_json(obj))
