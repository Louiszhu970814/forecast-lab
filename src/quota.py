"""Antigravity CLI (agy) quota (/usage) and AI credits (/credits) querying and verification.

See docs/tasks/M1-W5.md and PLAN.md §7.7 (D41, D52, D54).
"""

from __future__ import annotations

import json
import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from adapters.base import MODEL_CHECKS
from flab import runner as flab_runner
from flab.ids import new_id
from flab.timeutil import parse_iso, to_iso, utc_now

ARGON_BUCKET_ID = "gemini-argon-wtu-weekly"
GEMINI_WEEKLY_BUCKET_ID = "gemini-weekly"
GEMINI_5H_BUCKET_ID = "gemini-5h"
THIRD_PARTY_WEEKLY_BUCKET_ID = "3p-weekly"
THIRD_PARTY_5H_BUCKET_ID = "3p-5h"

BUCKET_GROUPS: dict[str, tuple[str, ...]] = {
    "argon": (ARGON_BUCKET_ID,),
    "gemini": (GEMINI_WEEKLY_BUCKET_ID, GEMINI_5H_BUCKET_ID),
    "3p": (THIRD_PARTY_WEEKLY_BUCKET_ID, THIRD_PARTY_5H_BUCKET_ID),
}

KNOWN_BUCKET_TO_GROUP: dict[str, str] = {
    ARGON_BUCKET_ID: "argon",
    GEMINI_WEEKLY_BUCKET_ID: "gemini",
    GEMINI_5H_BUCKET_ID: "gemini",
    THIRD_PARTY_WEEKLY_BUCKET_ID: "3p",
    THIRD_PARTY_5H_BUCKET_ID: "3p",
}

BUCKET_TO_GROUP: dict[str, str] = {
    **KNOWN_BUCKET_TO_GROUP,
    "argon": "argon",
    "gemini": "gemini",
    "flash": "gemini",
    "pro": "gemini",
    "3p": "3p",
}

# PLAN §7.7 requires comparing both the Argon bucket and the Flash/Pro (Gemini) group buckets.
REQUIRED_COMPARISON_BUCKETS: tuple[str, ...] = (
    ARGON_BUCKET_ID,
    GEMINI_WEEKLY_BUCKET_ID,
    GEMINI_5H_BUCKET_ID,
)

USAGE_ARGV: tuple[str, ...] = ("agy", "-p", "/usage", "--output-format", "json")
CREDITS_ARGV: tuple[str, ...] = ("agy", "-p", "/credits", "--output-format", "json")

# Minimum fraction change treated as a real quota movement rather than float noise.
QUOTA_EPSILON: float = 1e-9

_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "snapshot_id",
        "taken_at",
        "buckets",
        "remaining_credits",
        "concurrent_agy_detected",
        "stale",
        "delayed",
        "command",
        "status",
        "response",
        "duration_seconds",
        "num_turns",
        "usage",
        "conversation_id",
    }
)


def _is_valid_fraction(val: Any) -> bool:
    """Return True iff ``val`` is a finite float/int (not bool) within ``[0.0, 1.0]``."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return False
    try:
        fval = float(val)
    except (OverflowError, ValueError, TypeError):
        return False
    return math.isfinite(fval) and 0.0 <= fval <= 1.0


def _coerce_fraction_for_bucket(val: Any) -> float:
    """Convert raw bucket fraction to float, using NaN for non-numeric or overflowing values so validation catches it."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return float("nan")
    try:
        return float(val)
    except (OverflowError, ValueError, TypeError):
        return float("nan")


@dataclass(frozen=True)
class BucketQuota:
    """Single quota bucket parsed from ``agy -p "/usage" --output-format json``."""

    id: str
    remaining_fraction: float
    reset_time: str | None = None
    window: str | None = None
    name: str | None = None
    group_name: str | None = None

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and hasattr(self, key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "remaining_fraction": self.remaining_fraction,
            "reset_time": self.reset_time,
            "window": self.window,
            "name": self.name,
            "group_name": self.group_name,
        }


@dataclass(frozen=True)
class QuotaSnapshot:
    """Combined ``/usage`` and ``/credits`` snapshot for ``agy``."""

    buckets: dict[str, BucketQuota]
    remaining_credits: int | float | None
    snapshot_id: str = field(default_factory=lambda: new_id("qsnap"))
    taken_at: str = field(default_factory=lambda: to_iso(utc_now()))
    concurrent_agy_detected: bool = False
    stale: bool = False

    def __getitem__(self, key: str) -> Any:
        if key in self.buckets and not hasattr(self, key):
            return self.buckets[key]
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return self.buckets.get(key, default)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and (hasattr(self, key) or key in self.buckets)

    def remaining_fraction(self, bucket_id: str) -> float | None:
        b = self.buckets.get(bucket_id)
        return b.remaining_fraction if b is not None else None

    def reset_time(self, bucket_id: str) -> str | None:
        b = self.buckets.get(bucket_id)
        return b.reset_time if b is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "taken_at": self.taken_at,
            "buckets": {k: v.to_dict() for k, v in self.buckets.items()},
            "remaining_credits": self.remaining_credits,
            "concurrent_agy_detected": self.concurrent_agy_detected,
            "stale": self.stale,
        }


def _load_json_input(raw: str | bytes | Mapping[str, Any] | Path) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, Path):
        text = raw.read_text(encoding="utf-8")
        parsed = json.loads(text)
    elif isinstance(raw, (str, bytes)):
        parsed = json.loads(raw)
    else:
        raise TypeError(f"unsupported JSON input type: {type(raw)!r}")
    if not isinstance(parsed, dict):
        raise ValueError("JSON root must be an object")
    return parsed


def parse_usage_json(raw: str | bytes | Mapping[str, Any] | Path) -> dict[str, BucketQuota]:
    """Parse ``command.data.groups[].buckets[]`` from ``/usage`` JSON output."""
    data = _load_json_input(raw)

    # Support full CLI JSON (`command.data.groups`) or direct `groups` / `buckets` dicts.
    cmd = data.get("command")
    cmd_data = cmd.get("data") if isinstance(cmd, Mapping) else None
    groups = (
        cmd_data.get("groups")
        if isinstance(cmd_data, Mapping)
        else data.get("groups")
    )

    buckets_out: dict[str, BucketQuota] = {}
    if isinstance(groups, list):
        invalid_idx = 0
        for grp in groups:
            if not isinstance(grp, Mapping):
                invalid_idx += 1
                bad_id = f"__invalid_group_{invalid_idx}__"
                buckets_out[bad_id] = BucketQuota(id=bad_id, remaining_fraction=float("nan"))
                continue
            grp_name = str(grp.get("name")) if grp.get("name") is not None else None
            raw_buckets = grp.get("buckets")
            if not isinstance(raw_buckets, list):
                invalid_idx += 1
                bad_id = f"__invalid_buckets_{invalid_idx}__"
                buckets_out[bad_id] = BucketQuota(id=bad_id, remaining_fraction=float("nan"))
                continue
            for b in raw_buckets:
                if not isinstance(b, Mapping):
                    invalid_idx += 1
                    bad_id = f"__invalid_bucket_{invalid_idx}__"
                    buckets_out[bad_id] = BucketQuota(id=bad_id, remaining_fraction=float("nan"))
                    continue
                b_id = b.get("id")
                if not isinstance(b_id, str) or not b_id:
                    invalid_idx += 1
                    bad_id = f"__invalid_bucket_{invalid_idx}__"
                    buckets_out[bad_id] = BucketQuota(id=bad_id, remaining_fraction=float("nan"))
                    continue
                rem = _coerce_fraction_for_bucket(b.get("remaining_fraction"))
                buckets_out[b_id] = BucketQuota(
                    id=b_id,
                    remaining_fraction=rem,
                    reset_time=str(b["reset_time"]) if b.get("reset_time") is not None else None,
                    window=str(b["window"]) if b.get("window") is not None else None,
                    name=str(b["name"]) if b.get("name") is not None else None,
                    group_name=grp_name,
                )
        return buckets_out

    # Also allow already-flattened `{"buckets": {...}}` or `{bucket_id: ...}` structures.
    has_explicit_buckets_map = "buckets" in data
    if has_explicit_buckets_map and not isinstance(data.get("buckets"), Mapping):
        return buckets_out
    raw_map = data["buckets"] if has_explicit_buckets_map else data
    if isinstance(raw_map, Mapping):
        invalid_idx = 0
        for k, v in raw_map.items():
            if not isinstance(k, str) or not k:
                invalid_idx += 1
                bad_id = f"__invalid_bucket_key_{invalid_idx}__"
                buckets_out[bad_id] = BucketQuota(id=bad_id, remaining_fraction=float("nan"))
                continue
            if not has_explicit_buckets_map and k in _METADATA_KEYS:
                continue
            if isinstance(v, BucketQuota):
                buckets_out[k] = v
            elif isinstance(v, Mapping):
                rem = _coerce_fraction_for_bucket(v.get("remaining_fraction"))
                raw_id = v.get("id")
                b_id = raw_id if isinstance(raw_id, str) and raw_id else k
                buckets_out[k] = BucketQuota(
                    id=b_id,
                    remaining_fraction=rem,
                    reset_time=str(v["reset_time"]) if v.get("reset_time") is not None else None,
                    window=str(v["window"]) if v.get("window") is not None else None,
                    name=str(v["name"]) if v.get("name") is not None else None,
                    group_name=str(v["group_name"]) if v.get("group_name") is not None else None,
                )
                if b_id != k:
                    buckets_out[b_id] = buckets_out[k]
            else:
                rem = _coerce_fraction_for_bucket(v)
                buckets_out[k] = BucketQuota(id=k, remaining_fraction=rem)

    return buckets_out


def parse_credits_json(raw: str | bytes | Mapping[str, Any] | Path | None) -> int | float | None:
    """Parse ``command.data.remaining_credits`` from ``/credits`` JSON output."""
    if raw is None:
        return None
    data = _load_json_input(raw)
    cmd = data.get("command")
    cmd_data = cmd.get("data") if isinstance(cmd, Mapping) else None
    val = (
        cmd_data.get("remaining_credits")
        if isinstance(cmd_data, Mapping) and "remaining_credits" in cmd_data
        else data.get("remaining_credits")
    )
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    try:
        if not math.isfinite(float(val)):
            return None
    except (OverflowError, ValueError, TypeError):
        return None
    return val


def parse_snapshot(
    usage_raw: str | bytes | Mapping[str, Any] | Path,
    credits_raw: str | bytes | Mapping[str, Any] | Path | None = None,
    *,
    concurrent_agy_detected: bool = False,
    stale: bool = False,
) -> QuotaSnapshot:
    """Construct a ``QuotaSnapshot`` from raw ``/usage`` and ``/credits`` JSON payloads."""
    data = _load_json_input(usage_raw)
    buckets = parse_usage_json(data)
    remaining_credits = parse_credits_json(credits_raw if credits_raw is not None else data)
    snap_stale = bool(stale or data.get("stale", False) or data.get("delayed", False))
    snap_concurrent = bool(concurrent_agy_detected or data.get("concurrent_agy_detected", False))
    kwargs: dict[str, Any] = {}
    if isinstance(data.get("snapshot_id"), str) and data["snapshot_id"]:
        kwargs["snapshot_id"] = data["snapshot_id"]
    if "taken_at" in data:
        raw_taken_at = data["taken_at"]
        kwargs["taken_at"] = raw_taken_at if isinstance(raw_taken_at, str) else ""
    return QuotaSnapshot(
        buckets=buckets,
        remaining_credits=remaining_credits,
        concurrent_agy_detected=snap_concurrent,
        stale=snap_stale,
        **kwargs,
    )


def _run_slash_json(argv: list[str], *, cwd: Path, timeout_s: float) -> str:
    with tempfile.TemporaryDirectory(prefix="flab-quota-") as tmp:
        tmp_dir = Path(tmp)
        stdout_path = tmp_dir / "stdout.json"
        stderr_path = tmp_dir / "stderr.txt"
        res = flab_runner.run(
            argv,
            cwd=cwd,
            stdin_text=None,
            timeout_s=timeout_s,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            keep_env=(),
        )
        if res.timed_out or res.exit_code != 0:
            err = stderr_path.read_text(encoding="utf-8", errors="replace").strip()
            raise RuntimeError(
                f"command {argv!r} failed (exit={res.exit_code}, timed_out={res.timed_out}): {err}"
            )
        return stdout_path.read_text(encoding="utf-8")


def snapshot(
    *,
    cwd: Path | str | None = None,
    timeout_s: float = 30.0,
    concurrent_agy_detected: bool = False,
    stale: bool = False,
    _run_cmd: Callable[[list[str]], str | Mapping[str, Any]] | None = None,
) -> QuotaSnapshot:
    """Query ``agy -p "/usage" --output-format json`` and ``agy -p "/credits" --output-format json``."""
    if _run_cmd is not None:
        usage_out = _run_cmd(list(USAGE_ARGV))
        credits_out = _run_cmd(list(CREDITS_ARGV))
        return parse_snapshot(
            usage_out,
            credits_out,
            concurrent_agy_detected=concurrent_agy_detected,
            stale=stale,
        )

    if cwd is not None:
        work_cwd = Path(cwd)
        work_cwd.mkdir(parents=True, exist_ok=True)
        usage_out = _run_slash_json(list(USAGE_ARGV), cwd=work_cwd, timeout_s=timeout_s)
        credits_out = _run_slash_json(list(CREDITS_ARGV), cwd=work_cwd, timeout_s=timeout_s)
    else:
        with tempfile.TemporaryDirectory(prefix="flab-quota-cwd-") as tmp_cwd:
            work_cwd = Path(tmp_cwd)
            usage_out = _run_slash_json(list(USAGE_ARGV), cwd=work_cwd, timeout_s=timeout_s)
            credits_out = _run_slash_json(list(CREDITS_ARGV), cwd=work_cwd, timeout_s=timeout_s)

    return parse_snapshot(
        usage_out,
        credits_out,
        concurrent_agy_detected=concurrent_agy_detected,
        stale=stale,
    )


def credits_ok(snap: QuotaSnapshot | Mapping[str, Any] | None) -> bool:
    """Return True iff ``remaining_credits`` is explicitly 0 (PLAN §7.7, D54)."""
    if snap is None:
        return False
    if isinstance(snap, QuotaSnapshot):
        rem = snap.remaining_credits
    elif isinstance(snap, Mapping):
        rem = parse_credits_json(snap)
    else:
        return False
    if rem is None or isinstance(rem, bool) or not isinstance(rem, (int, float)):
        return False
    try:
        return math.isfinite(float(rem)) and rem == 0
    except (OverflowError, ValueError, TypeError):
        return False


def _coerce_snapshot(obj: QuotaSnapshot | Mapping[str, Any] | Path | str | None) -> QuotaSnapshot | None:
    if obj is None:
        return None
    if isinstance(obj, QuotaSnapshot):
        return obj
    if isinstance(obj, Mapping):
        return parse_snapshot(obj)
    if isinstance(obj, (Path, str)):
        return parse_snapshot(obj)
    return None


def _parse_iso_safe(ts: str | None) -> Any:
    if not ts or not isinstance(ts, str):
        return None
    try:
        return parse_iso(ts)
    except (ValueError, TypeError):
        return None


def judge(
    before: QuotaSnapshot | Mapping[str, Any] | Path | str | None,
    after: QuotaSnapshot | Mapping[str, Any] | Path | str | None,
    expected_bucket: str = ARGON_BUCKET_ID,
    *,
    concurrent_activity: bool = False,
    stale: bool = False,
    delayed: bool = False,
    epsilon: float = QUOTA_EPSILON,
) -> str:
    """Judge model execution from before/after ``/usage`` snapshots (PLAN §7.7).

    Rules:
    - ``gemini-weekly`` and ``gemini-5h`` belong to the same group (``"gemini"``: Flash/Pro).
      Both decreasing together counts as consumption of that single group.
    - Returns ``"undetermined"`` or ``"unverified"`` (never ``"verified"`` or ``"mismatch"``) when:
      1. Multiple distinct groups change simultaneously (e.g. Argon + Gemini, or Gemini + 3p).
      2. Any bucket increases (quota reset or replenishment) or an active bucket's ``reset_time`` changes.
      3. Any unknown bucket ID appears in ``before`` or ``after``, or ``expected_bucket`` is unknown.
      4. Required comparison buckets (Argon + Gemini group + target group) are missing, or ``before`` and
         ``after`` do not contain the same set of bucket IDs.
      5. Any bucket's ``remaining_fraction`` is not a finite number in ``[0.0, 1.0]``.
      6. Concurrent ``agy`` activity, stale/delayed snapshots, or reversed timestamps are detected.
    """
    try:
        snap_before = _coerce_snapshot(before)
        snap_after = _coerce_snapshot(after)
    except (ValueError, TypeError, OSError):
        return "unverified"

    if snap_before is None or snap_after is None:
        return "unverified"
    if not snap_before.buckets or not snap_after.buckets:
        return "unverified"

    target_group = BUCKET_TO_GROUP.get(expected_bucket)
    if target_group is None:
        return "undetermined"

    if (
        concurrent_activity
        or stale
        or delayed
        or snap_before.concurrent_agy_detected
        or snap_after.concurrent_agy_detected
        or snap_before.stale
        or snap_after.stale
    ):
        return "undetermined"

    # Both snapshots must have valid, parseable ISO 8601 `taken_at` timestamps with `after >= before`.
    t_before = _parse_iso_safe(snap_before.taken_at)
    t_after = _parse_iso_safe(snap_after.taken_at)
    if t_before is None or t_after is None or t_after < t_before:
        return "undetermined"

    before_ids = set(snap_before.buckets.keys())
    after_ids = set(snap_after.buckets.keys())
    all_ids = before_ids | after_ids

    # 1. Reject any unrecognized bucket IDs.
    if any(b_id not in KNOWN_BUCKET_TO_GROUP for b_id in all_ids):
        return "undetermined"

    # 2. All present buckets must have finite remaining_fraction within [0.0, 1.0].
    for b in (*snap_before.buckets.values(), *snap_after.buckets.values()):
        if not _is_valid_fraction(b.remaining_fraction):
            return "unverified"

    # 3. Both snapshots must have identical bucket keys and include all required comparison buckets.
    required_ids = set(REQUIRED_COMPARISON_BUCKETS) | set(BUCKET_GROUPS[target_group])
    if before_ids != after_ids or not required_ids.issubset(before_ids):
        return "unverified"

    # 4. Inspect every observable bucket for decreases, increases/resets, and reset_time anomalies.
    decreased_groups: set[str] = set()
    any_increased = False
    reset_or_stale_anomaly = False

    for b_id in all_ids:
        b0 = snap_before.buckets[b_id]
        b1 = snap_after.buckets[b_id]

        if (b0.reset_time is None) != (b1.reset_time is None):
            reset_or_stale_anomaly = True
        elif b0.reset_time is not None and b1.reset_time is not None:
            rt0 = _parse_iso_safe(b0.reset_time)
            rt1 = _parse_iso_safe(b1.reset_time)
            if rt0 is None or rt1 is None:
                reset_or_stale_anomaly = True
            elif rt1 < rt0:
                # `after` snapshot has an earlier reset_time than `before` -> stale/out-of-order.
                reset_or_stale_anomaly = True
            elif b0.remaining_fraction < 1.0 - epsilon and rt1 != rt0:
                # Bucket was already partially consumed in `before` and its reset_time changed -> window reset.
                reset_or_stale_anomaly = True

        delta = b0.remaining_fraction - b1.remaining_fraction
        if delta < -epsilon:
            any_increased = True
        elif delta > epsilon:
            decreased_groups.add(KNOWN_BUCKET_TO_GROUP[b_id])

    if any_increased or reset_or_stale_anomaly:
        return "undetermined"

    if len(decreased_groups) == 0:
        return "unverified"

    if len(decreased_groups) > 1:
        return "undetermined"

    # Exactly one group decreased.
    if target_group in decreased_groups:
        return "verified"
    return "mismatch"
