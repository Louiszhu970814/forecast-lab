"""Credential-free, append-only API cooldown shared by reads and writes.

Production clients share a project-wide ledger. Mock openers use private in-memory
state unless a path is supplied. No request is issued while a cooldown is active.

D98: production clients also pace every request (reads and writes) like the official
forecasting-tools client, which sleeps 3.5 s plus up to 1 s of jitter before each call.
The pace is shared across processes through the lock file's modification time, so no
extra ledger is written. A client sleep is our own politeness choice, not a known
server limit.
"""
from __future__ import annotations

import fcntl
import os
import random
import threading
import time
import urllib.error
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import parse_iso, to_iso, utc_now

VERSION = "api_backoff_v3"
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data/api_cooldowns.jsonl"
MAX_DATE = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
DEFAULT_429_SECONDS = 60
MIN_429_SECONDS = 10  # Luke's D115 reply: Cloudflare blocks the shared IP for at least 10s.
PACE_SECONDS = 3.5
PACE_JITTER_SECONDS = 1.0


def retry_deadline(headers, now):
    """Parse RFC 9110 delay-seconds/HTTP-date; never return remote strings."""
    value = headers.get("Retry-After") if headers else None
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value and value.isascii() and value.isdecimal():
        # Arbitrarily large integer values must not shorten a server's delay.
        if len(value.lstrip("0")) > 12:
            return MAX_DATE
        try:
            return min(MAX_DATE, now + timedelta(seconds=int(value)))
        except (OverflowError, ValueError):
            return MAX_DATE
    if len(value) > 128 or any(c in value for c in "\r\n"):
        return None
    try:
        result = parsedate_to_datetime(value)
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)  # obsolete HTTP asctime date
        return max(now, result.astimezone(timezone.utc))
    except (TypeError, ValueError, OverflowError):
        return None


class Deferred(RuntimeError):
    def __init__(self, retry_at, status=None):
        self.retry_at, self.status = retry_at, status
        super().__init__("API cooldown active")


class Backoff:
    def __init__(self, path=None, *, now=utc_now, pace=False, pace_seconds=PACE_SECONDS,
                 jitter_seconds=PACE_JITTER_SECONDS, sleep=time.sleep, wall=time.time, jitter=random.uniform):
        self.path = Path(path) if path is not None else None
        self.now = now
        self._records = []
        self._lock = threading.Lock()
        if pace and (not 0 <= pace_seconds <= 60 or not 0 <= jitter_seconds <= 60):
            raise ValueError("invalid request pacing")
        self.pace, self.pace_seconds, self.jitter_seconds = bool(pace), float(pace_seconds), float(jitter_seconds)
        self._sleep, self._wall, self._jitter = sleep, wall, jitter
        self._last_start = None

    def _last_request_start(self):
        if self.path is None:
            return self._last_start
        try:
            stat = self.path.with_suffix(".lock").stat()
        except FileNotFoundError:
            return None
        # Creating the lock file sets its mtime too: an empty lock has never marked a request.
        return stat.st_mtime if stat.st_size else None

    def _pace_then_mark(self):
        """Called under the lock: wait out the shared spacing, then record this request's start."""
        if not self.pace:
            return
        gap = self.pace_seconds + self._jitter(0.0, self.jitter_seconds)
        last = self._last_request_start()
        if last is not None:
            # A clock step can make `last` look far in the future; never wait more than one gap.
            wait = min(gap, last + gap - self._wall())
            if wait > 0:
                self._sleep(wait)
        started = self._wall()
        if self.path is None:
            self._last_start = started
            return
        lock = self.path.with_suffix(".lock")
        if not lock.stat().st_size:
            with lock.open("a") as marker:  # one byte, once: the lock now carries a request time
                marker.write("p")
        os.utime(lock, (started, started))

    @contextmanager
    def _locked(self):
        with self._lock:
            if self.path is None:
                yield
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix(".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def _check(self):
        records = read_complete(self.path) if self.path else self._records
        active = [r for r in records if parse_iso(r["not_before"]) > self.now()]
        if active:
            latest = max(active, key=lambda r: parse_iso(r["not_before"]))
            raise Deferred(latest["not_before"], latest["http_status"])

    def _observe(self, status, headers):
        now = self.now()
        until = retry_deadline(headers, now)
        if status == 429:
            if until is None or until <= now:
                until = now + timedelta(seconds=DEFAULT_429_SECONDS)
            else:
                until = max(until, now + timedelta(seconds=MIN_429_SECONDS))
        if until is None:
            return None
        # Millisecond ISO output rounds down: round the deadline UP first.
        if until.microsecond % 1000:
            until += timedelta(microseconds=1000 - until.microsecond % 1000)
        record = {"type": "api_cooldown", "version": VERSION, "at": to_iso(now),
                  "http_status": status, "not_before": to_iso(until)}
        if self.path:
            append_record(self.path, record)
        else:
            self._records.append(record)
        return record["not_before"]

    def open(self, opener, req, timeout):
        # Lock through response headers so concurrent clients cannot slip past a 429.
        with self._locked():
            self._check()
            # Holding the lock while pacing keeps every process in one queue; no cooldown can be
            # written by another holder meanwhile, so the check above stays valid.
            self._pace_then_mark()
            try:
                response = opener(req, timeout)
            except urllib.error.HTTPError as exc:
                status = exc.code
                if type(status) is not int or not 100 <= status <= 599:
                    exc.close()
                    raise RuntimeError("unrecognized HTTP status") from None
                try:
                    retry_at = self._observe(status, exc.headers)
                finally:
                    exc.close()
                clean = urllib.error.HTTPError(req.full_url, status, "API request failed", {}, None)
                clean.retry_at = retry_at
                raise clean from None
            status = getattr(response, "status", 200)
            if type(status) is not int or not 100 <= status <= 599:
                response.close()
                raise RuntimeError("unrecognized HTTP status")
            if not 200 <= status < 300:
                try:
                    retry_at = self._observe(status, getattr(response, "headers", None))
                finally:
                    response.close()
                clean = urllib.error.HTTPError(req.full_url, status, "API request failed", {}, None)
                clean.retry_at = retry_at
                raise clean
            return response
