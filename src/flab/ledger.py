"""Short, reentrant cross-process transactions for related append-only local ledgers."""
from __future__ import annotations

import fcntl
import threading
from contextlib import contextmanager
from pathlib import Path

from flab.jsonl import read_records

_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}
_local = threading.local()


@contextmanager
def transaction(root: Path):
    root = root.resolve()
    key = str(root)
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        active = getattr(_local, "active", set())
        if key in active:
            yield
            return
        root.mkdir(parents=True, exist_ok=True)
        with (root / "ledger.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            _local.active = active | {key}
            try:
                yield
            finally:
                _local.active = active
                fcntl.flock(stream, fcntl.LOCK_UN)


def read_complete(path: Path) -> list[dict]:
    if not path.exists():
        return []
    if path.stat().st_size:
        with path.open("rb") as stream:
            stream.seek(-1, 2)
            if stream.read(1) != b"\n":
                raise ValueError(f"unterminated ledger: {path.name}")
    rows = list(read_records(path))
    if any(not isinstance(r, dict) for r in rows):
        raise ValueError(f"non-object ledger row: {path.name}")
    return rows
