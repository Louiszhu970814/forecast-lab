"""Shared submission identity rules for resolution polling and scoring.

Only exact local/trial modes establish a snapshotless historical probe. Invalid
or contradictory metadata never inherits that exemption from another row.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

VERSION = "submission_identity_v1"
MODES = frozenset({"local", "trial", "live"})
PROBE_MODES = frozenset({"local", "trial"})


def submission_status(row: Mapping[str, Any]) -> str:
    """Read legacy state/current status using the established ledger convention."""
    value = row.get("status") or row.get("state")
    return value.strip().lower() if isinstance(value, str) else ""


def normalize_snapshot_id(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def normalize_mode(value: Any) -> str | None:
    # A misspelling, case change, or whitespace is malformed evidence, not trial.
    return value if isinstance(value, str) and value in MODES else None


def submission_identity(
    row: Mapping[str, Any], intent: Mapping[str, Any] | None = None,
) -> dict[str, str | None]:
    """Merge sparse receipt identity with its intent without erasing conflicts.

    Missing/blank snapshot IDs may be supplied by the intent. Non-string IDs or
    two different nonempty IDs fail closed. A receipt *omitting* mode can inherit
    it; an explicitly malformed mode cannot. Contradictory modes or snapshots
    return no identity so callers cannot mistake them for a historical probe.
    """
    intent = intent or {}
    sources = (intent, row)
    snapshots = {v for s in sources if (v := normalize_snapshot_id(s.get("question_snapshot_id"))) is not None}
    invalid_snapshot = any(
        "question_snapshot_id" in s and s["question_snapshot_id"] is not None
        and not isinstance(s["question_snapshot_id"], str) for s in sources
    )
    raw_modes = [s["mode"] for s in sources if "mode" in s]
    modes = [normalize_mode(v) for v in raw_modes]
    invalid_mode = any(v is None for v in modes)
    conflicting_mode = len({v for v in modes if v is not None}) > 1
    if invalid_snapshot or len(snapshots) > 1 or conflicting_mode:
        return {"question_snapshot_id": None, "mode": None}
    mode = None if invalid_mode or not modes else modes[-1]
    return {"question_snapshot_id": next(iter(snapshots), None), "mode": mode}


def is_snapshotless_probe(identity: Mapping[str, Any]) -> bool:
    """Only confirmed historical local/trial rows may omit their snapshot."""
    return identity.get("question_snapshot_id") is None and normalize_mode(identity.get("mode")) in PROBE_MODES
