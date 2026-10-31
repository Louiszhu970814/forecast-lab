"""UTC time utilities with strict ISO 8601 formatting and parsing."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    """Format a timezone-aware datetime as YYYY-MM-DDTHH:MM:SS.mmmZ in UTC."""
    if not isinstance(dt, datetime):
        raise TypeError(f"Expected datetime, got {type(dt).__name__}")
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError("Naive datetime without timezone is not allowed")
    dt_utc = dt.astimezone(timezone.utc)
    ms = dt_utc.microsecond // 1000
    return f"{dt_utc.strftime('%Y-%m-%dT%H:%M:%S')}.{ms:03d}Z"


def parse_iso(s: str) -> datetime:
    """Parse an ISO 8601 datetime string with timezone ('Z' or offset) into a UTC datetime.

    Rejects strings without a time component or without timezone information.
    """
    if not isinstance(s, str):
        raise TypeError(f"Expected str, got {type(s).__name__}")
    text = s.strip()
    if not text or "T" not in text and "t" not in text and " " not in text:
        raise ValueError(f"Invalid ISO 8601 datetime string: {s!r}")
    if text.endswith("z"):
        raise ValueError(f"Lowercase 'z' timezone suffix is not supported: {s!r}")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO 8601 datetime string: {s!r}") from exc

    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"Datetime string must include a timezone ('Z' or offset): {s!r}")
    return dt.astimezone(timezone.utc)
