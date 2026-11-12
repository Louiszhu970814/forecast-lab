"""Antigravity CLI (agy) quota (/usage) and AI credits (/credits) querying and verification.

See docs/tasks/M1-W5.md and PLAN.md §7.7 (D41, D52, D54).
"""

from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping

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
        "concurrent_agy_kinds",
        "concurrent_agy_counts",
        "concurrent_agy_reason",
        "quota_layout",
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
    concurrent_agy_kinds: tuple[str, ...] = ()
    concurrent_agy_counts: dict[str, int] = field(default_factory=dict)
    concurrent_agy_reason: str | None = None

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
            "concurrent_agy_kinds": list(self.concurrent_agy_kinds),
            "concurrent_agy_counts": dict(self.concurrent_agy_counts),
            "concurrent_agy_reason": self.concurrent_agy_reason,
            "quota_layout": argon_buckets(self).layout,
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
            if not isinstance(raw_buckets, list) or not raw_buckets:
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
                if b_id in buckets_out:
                    buckets_out["__duplicate_bucket__"] = BucketQuota("__duplicate_bucket__", float("nan"))
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
        concurrent_agy_detected=snap_concurrent or "headless" in (data.get("concurrent_agy_kinds") or ())
            or bool((data.get("concurrent_agy_counts") or {}).get("headless")),
        stale=snap_stale,
        concurrent_agy_kinds=tuple(data.get("concurrent_agy_kinds") or ()),
        concurrent_agy_counts=dict(data.get("concurrent_agy_counts") or {}),
        concurrent_agy_reason=data.get("concurrent_agy_reason"),
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
    from flab.fail_soft import QUOTA_QUERY_ATTEMPTS
    import time
    activity_before = detect_agy_activity()
    until = time.monotonic() + timeout_s

    def query(argv, work_cwd=None):
        for attempt in range(QUOTA_QUERY_ATTEMPTS):
            try:
                remaining = until - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("quota query deadline elapsed")
                out = (_run_cmd(list(argv)) if _run_cmd is not None else
                       _run_slash_json(list(argv), cwd=work_cwd, timeout_s=remaining))
                if argv == USAGE_ARGV:
                    parse_usage_json(out)
                elif parse_credits_json(out) is None:
                    raise ValueError("credits query returned no numeric balance")
                return out
            except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError):
                if attempt + 1 == QUOTA_QUERY_ATTEMPTS:
                    raise

    def finish(usage_out, credits_out):
        activity_after = detect_agy_activity()
        counts = {k: max(activity_before[0].get(k, 0), activity_after[0].get(k, 0))
                  for k in activity_before[0].keys() | activity_after[0].keys()}
        snap = parse_snapshot(usage_out, credits_out, concurrent_agy_detected=concurrent_agy_detected
                              or bool(counts.get("headless")), stale=stale)
        return replace(snap, concurrent_agy_kinds=tuple(sorted(counts)), concurrent_agy_counts=counts,
                       concurrent_agy_reason=activity_before[1] or activity_after[1])

    if _run_cmd is not None:
        usage_out = query(USAGE_ARGV)
        credits_out = query(CREDITS_ARGV)
        return finish(usage_out, credits_out)

    if cwd is not None:
        work_cwd = Path(cwd)
        work_cwd.mkdir(parents=True, exist_ok=True)
        usage_out = query(USAGE_ARGV, work_cwd)
        credits_out = query(CREDITS_ARGV, work_cwd)
    else:
        with tempfile.TemporaryDirectory(prefix="flab-quota-cwd-") as tmp_cwd:
            work_cwd = Path(tmp_cwd)
            usage_out = query(USAGE_ARGV, work_cwd)
            credits_out = query(CREDITS_ARGV, work_cwd)

    return finish(usage_out, credits_out)


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


def fresh(snapshot, now) -> bool:
    """Admission freshness includes every applicable reset and explicit free credits."""
    from flab.fail_soft import SNAPSHOT_MAX_AGE_S
    try:
        buckets = argon_buckets(snapshot)
        return bool(snapshot and not snapshot.get("stale") and credits_ok(snapshot)
                    and buckets.layout != "unknown"
                    and 0 <= (now - parse_iso(snapshot["taken_at"])).total_seconds() <= SNAPSHOT_MAX_AGE_S
                    and all(now < parse_iso(b.reset_time) for b in (buckets.weekly, buckets.five_hour) if b))
    except (KeyError, TypeError, ValueError):
        return False


def usage_fresh(snapshot, now) -> bool:
    """Whether /usage needs rereading; credit validity is a separate admission gate."""
    if not observation_fresh(snapshot, now):
        return False
    try:
        buckets = argon_buckets(snapshot)
        return all(now < parse_iso(b.reset_time) for b in (buckets.weekly, buckets.five_hour) if b)
    except (KeyError, TypeError, ValueError):
        return False


def observation_fresh(snapshot, now) -> bool:
    from flab.fail_soft import SNAPSHOT_MAX_AGE_S
    try:
        return bool(snapshot and not snapshot.get("stale") and
                    0 <= (now - parse_iso(snapshot["taken_at"])).total_seconds() <= SNAPSHOT_MAX_AGE_S)
    except (KeyError, TypeError, ValueError):
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
        or has_concurrent_activity(snap_before)
        or has_concurrent_activity(snap_after)
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


@dataclass(frozen=True)
class ArgonBuckets:
    """Only this module knows which quota units govern Argon on each plan."""

    layout: str = "unknown"
    weekly: BucketQuota | None = None
    five_hour: BucketQuota | None = None


UNKNOWN_LAYOUT_REASON = "quota_layout_unknown"


def argon_buckets(snapshot: QuotaSnapshot | Mapping | None) -> ArgonBuckets:
    snap = _coerce_snapshot(snapshot)
    if snap is None or not snap.buckets:
        return ArgonBuckets()
    expected_names = {"argon": "Gemini 4 Argon", "gemini": "Gemini Models", "3p": "Claude and GPT models"}
    for key, bucket in snap.buckets.items():
        group = KNOWN_BUCKET_TO_GROUP.get(key)
        if (group is None or bucket.id != key or not _is_valid_fraction(bucket.remaining_fraction)
                or (bucket.group_name is not None and bucket.group_name != expected_names[group])):
            return ArgonBuckets()
    if not {GEMINI_WEEKLY_BUCKET_ID, GEMINI_5H_BUCKET_ID} <= snap.buckets.keys():
        return ArgonBuckets()
    third_party = {THIRD_PARTY_WEEKLY_BUCKET_ID, THIRD_PARTY_5H_BUCKET_ID}
    if snap.buckets.keys() & third_party and not third_party <= snap.buckets.keys():
        return ArgonBuckets()
    if ARGON_BUCKET_ID in snap.buckets:
        return ArgonBuckets("legacy_argon", snap.buckets[ARGON_BUCKET_ID])
    if {GEMINI_WEEKLY_BUCKET_ID, GEMINI_5H_BUCKET_ID} <= snap.buckets.keys():
        return ArgonBuckets("ultra_shared", snap.buckets[GEMINI_WEEKLY_BUCKET_ID], snap.buckets[GEMINI_5H_BUCKET_ID])
    return ArgonBuckets()


def has_concurrent_activity(snapshot) -> bool:
    return snapshot is not None and bool(snapshot.get("concurrent_agy_detected")
        or snapshot.get("concurrent_agy_kinds") or snapshot.get("concurrent_agy_counts")
        or snapshot.get("concurrent_agy_reason"))


def quota_drop(before, after, *, period: str = "weekly", current=None) -> float | None:
    """A cost in one quota unit, within one reset window; never mix plan fractions."""
    if period not in ("weekly", "five_hour"):
        raise ValueError("unknown quota period")
    if before is None or after is None:
        return None
    if any(s.get("stale") or has_concurrent_activity(s) for s in (before, after)):
        return None
    a, b = argon_buckets(before), argon_buckets(after)
    if a.layout == "unknown" or a.layout != b.layout:
        return None
    if current is not None and a.layout != argon_buckets(current).layout:
        return None
    x, y = getattr(a, period), getattr(b, period)
    if x is None or y is None or x.id != y.id or x.reset_time != y.reset_time or _parse_iso_safe(x.reset_time) is None:
        return None
    # Old synthetic records may omit observation timestamps; when present they must be ordered.
    t0, t1 = before.get("taken_at"), after.get("taken_at")
    if t0 is not None or t1 is not None:
        if _parse_iso_safe(t0) is None or _parse_iso_safe(t1) is None or parse_iso(t1) < parse_iso(t0):
            return None
    return x.remaining_fraction - y.remaining_fraction if x.remaining_fraction >= y.remaining_fraction else None


def attempt_drop(attempt, snapshots, *, period="weekly", current=None):
    if attempt.get("quota_cost_status") == "undetermined":
        return None
    pair = {r.get("when"): r for r in snapshots if r.get("attempt_id") == attempt.get("attempt_id")}
    return quota_drop(pair.get("before"), pair.get("after"), period=period, current=current)


WINDOW_ESTIMATE_VERSION = "quota_window_estimate_v1"
WINDOW_SECONDS = {"weekly": 7 * 86400, "five_hour": 5 * 3600}
DEFAULT_WINDOW_MULTIPLIER = 3.0
DEFAULT_WINDOW_MIN_CALLS = 5
DEFAULT_TIER2_SCALE = 3.0


def window_usage(attempts, snapshots, *, current, period="weekly") -> dict | None:
    """Window-level Gemini usage from every reading of the current reset window (D96/D97).

    `/usage` reads are not a consistent, monotonic counter, so a single before/after delta is
    never a per-call cost. A reading never reports a future state, so every reading is at least
    the true remaining fraction at its own time, and the true value only falls within one fixed
    window. The minimum reading is therefore an upper bound on the current remaining fraction,
    and `1 - minimum` a lower bound on all usage since the window opened. That usage includes
    development, research and personal use, so dividing by forecast calls overstates the mean
    forecast cost. Concurrency does not invalidate a reading, only per-call attribution.
    """
    if period not in WINDOW_SECONDS:
        raise ValueError("unknown quota period")
    cur = _coerce_snapshot(current)
    buckets = argon_buckets(cur)
    bucket = getattr(buckets, period) if buckets.layout != "unknown" else None
    reset = _parse_iso_safe(bucket.reset_time) if bucket is not None else None
    if bucket is None or reset is None or cur.stale:
        return None
    readings = [bucket.remaining_fraction]
    seen = {cur.snapshot_id}  # `current` is often a row of `snapshots`; count each reading once
    for row in snapshots:
        if row.get("stale") or row.get("delayed"):
            continue
        sid = row.get("snapshot_id")
        if isinstance(sid, str) and sid:
            if sid in seen:
                continue
            seen.add(sid)
        # Cheap pre-filter before full parsing; layout/id/reset are re-checked below.
        listed = row.get("buckets") or {}
        raw = listed.get(bucket.id) if hasattr(listed, "get") else None
        if raw is None or not hasattr(raw, "get") or raw.get("reset_time") != bucket.reset_time:
            continue
        other = argon_buckets(row)
        same = getattr(other, period) if other.layout == buckets.layout else None
        if same is not None and same.id == bucket.id and same.reset_time == bucket.reset_time:
            readings.append(same.remaining_fraction)
    start = reset.timestamp() - WINDOW_SECONDS[period]
    calls = 0
    for r in attempts:
        if r.get("player", "gemini") != "gemini":
            continue
        began = _parse_iso_safe(r.get("started_at_utc"))
        if began is not None and start <= began.timestamp() < reset.timestamp():
            calls += 1
    lowest = min(readings)
    return {"method": WINDOW_ESTIMATE_VERSION, "period": period, "quota_layout": buckets.layout,
            "bucket": bucket.id, "reset_time": bucket.reset_time, "readings": len(readings),
            "min_remaining": lowest, "used_lower_bound": max(0.0, 1.0 - lowest), "gemini_calls": calls,
            "mean_per_call": (1.0 - lowest) / calls if calls and lowest < 1.0 else None,
            "includes_non_forecast_usage": True}


def estimate(attempts, snapshots, *, current, fallback, tier=1, period="weekly",
             multiplier=DEFAULT_WINDOW_MULTIPLIER, min_calls=DEFAULT_WINDOW_MIN_CALLS,
             tier2_scale=DEFAULT_TIER2_SCALE) -> tuple[float, int]:
    """Per-call admission estimate from window-level usage; never above ``fallback`` (D97).

    Returns ``(estimate, gemini_calls_used)``; ``(fallback, 0)`` when the window has too few
    calls or no observed usage. Tier 2 is scaled by the tier-2 token multiplier cap because the
    token breaker bounds a tier-2 call at that multiple of the tier-1 baseline.
    """
    usage = window_usage(attempts, snapshots, current=current, period=period)
    if usage is None or usage["gemini_calls"] < max(1, int(min_calls)) or not usage["mean_per_call"]:
        return fallback, 0
    value = usage["mean_per_call"] * float(multiplier) * (float(tier2_scale) if tier == 2 else 1.0)
    return min(float(fallback), value), usage["gemini_calls"]


def estimate_settings(policy: Mapping[str, Any]) -> dict[str, float]:
    """Window estimate knobs from quota policy, with v4 defaults for older frozen policies."""
    cfg = (policy.get("players") or {}).get("gemini") or {}
    return {"multiplier": float(cfg.get("window_estimate_multiplier", DEFAULT_WINDOW_MULTIPLIER)),
            "min_calls": int(cfg.get("window_min_calls", DEFAULT_WINDOW_MIN_CALLS)),
            "tier2_scale": float(policy.get("token_multiplier_cap", DEFAULT_TIER2_SCALE))}


def five_hour_gate(snapshot, estimate: float) -> tuple[str | None, dict]:
    bucket = argon_buckets(snapshot).five_hour
    if bucket is None:
        return None, {}
    info = {"quota_5h_remaining": bucket.remaining_fraction, "quota_5h_estimate": estimate,
            "quota_5h_reset_at": bucket.reset_time, "quota_retry_at_utc": None}
    if _parse_iso_safe(bucket.reset_time) is None:
        return UNKNOWN_LAYOUT_REASON, info
    if bucket.remaining_fraction - estimate < 0:
        info["quota_retry_at_utc"] = bucket.reset_time
        return "quota_5h_wait", info
    return None, info


MODEL_VERIFICATION_VERSION = "quota_model_verification_v3"
ADMISSION_VERSION = "quota_admission_v2"


def idle_third_party_reset(key, before, after, x, y) -> bool:
    """Recognize only full, unused 3p buckets whose future placeholder slides with now.

    Gemini's own reset window must remain identical. This exception cannot hide a reset
    crossed during the sample, a used bucket, missing timestamps, or arbitrary reset drift.
    The <=5-second offset tolerance covers second resolution and the credits query delay;
    slow/ambiguous responses simply remain undetermined.
    """
    window = {THIRD_PARTY_WEEKLY_BUCKET_ID: 7 * 86400, THIRD_PARTY_5H_BUCKET_ID: 5 * 3600}.get(key)
    t0, t1 = _parse_iso_safe(before.taken_at), _parse_iso_safe(after.taken_at)
    r0, r1 = _parse_iso_safe(x.reset_time), _parse_iso_safe(y.reset_time)
    if window is None or x.remaining_fraction != 1.0 or y.remaining_fraction != 1.0:
        return False
    if any(t is None for t in (t0, t1, r0, r1)) or not t0 <= t1 < r0 <= r1:
        return False
    return all(abs((reset - observed).total_seconds() - window) <= 5
               for reset, observed in ((r0, t0), (r1, t1)))


def shared_group_consumed(before, after) -> bool:
    """Consistent shared Gemini movement is evidence of group use, never of model identity."""
    if argon_buckets(before).layout != "ultra_shared" or argon_buckets(after).layout != "ultra_shared":
        return False
    if any((d := quota_drop(before, after, period=p)) is None or d <= QUOTA_EPSILON
           for p in ("weekly", "five_hour")):
        return False
    a, b = _coerce_snapshot(before), _coerce_snapshot(after)
    if a.buckets.keys() != b.buckets.keys():
        return False
    for key in a.buckets:
        x, y = a.buckets[key], b.buckets[key]
        if x.remaining_fraction < y.remaining_fraction - QUOTA_EPSILON:
            return False
        if x.reset_time != y.reset_time and not idle_third_party_reset(key, a, b, x, y):
            return False
        if KNOWN_BUCKET_TO_GROUP[key] != "gemini" and abs(x.remaining_fraction - y.remaining_fraction) > QUOTA_EPSILON:
            return False
    return True


def detect_agy_activity(*, ps_output: str | None = None, current_pid: int | None = None) -> tuple[dict[str, int], str | None]:
    """Classify external processes without retaining commands (which may contain prompts)."""
    try:
        if ps_output is None:
            ps_output = subprocess.run(["/bin/ps", "-axo", "pid,ppid,command"], check=True,
                                       capture_output=True, text=True, timeout=5).stdout
        processes = {}
        for line in ps_output.splitlines():
            parts = line.strip().split(None, 2)
            if not parts or parts[0] == "PID":
                continue
            if len(parts) != 3:
                raise ValueError("invalid process row")
            processes[int(parts[0])] = (int(parts[1]), parts[2])
        if not processes:
            raise ValueError("empty process table")
        excluded = {os.getpid() if current_pid is None else current_pid}
        while children := {pid for pid, (ppid, _) in processes.items() if ppid in excluded} - excluded:
            excluded.update(children)
        counts: dict[str, int] = {}
        for pid, (_, command) in processes.items():
            if pid in excluded:
                continue
            # ps does not preserve shell quoting. Only inspect the executable/script and flags,
            # never search prompts for application names.
            try:
                argv = shlex.split(command)
            except ValueError:
                argv = command.split()
            if not argv:
                continue
            executable = argv[0]
            if Path(executable).name in ("node", "nodejs", "bun") and len(argv) > 1:
                executable = argv[1]
                argv = argv[1:]
            name = Path(executable).name
            kind = None
            if name in ("agy", "agy.js"):
                kind = "headless" if any(a == "-p" or a.startswith("-p=") or a == "--prompt" or a.startswith("--prompt=") for a in argv[1:]) else "interactive"
            elif "Antigravity.app/" in executable or name == "language_server" or name.startswith("language_server_"):
                kind = "ide"
            if kind:
                counts[kind] = counts.get(kind, 0) + 1
        return counts, None
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"headless": 1}, "ps_failed"


def record_snapshot(snapshot) -> dict:
    """JSONL-safe unknown numeric values, preserving the evidence of malformed snapshots."""
    return json.loads(json.dumps(snapshot.to_dict()), parse_constant=lambda _: None)


def manual_admission(snapshot, policy: dict, *, model: str, history_root: Path, tier: int = 1) -> tuple[str | None, dict]:
    """Shared gate for acceptance/autoload; cheap legacy Flash does not spend Argon."""
    from flab.ledger import read_complete

    buckets = argon_buckets(snapshot)
    info = {"quota_layout": buckets.layout, "quota_admission_version": ADMISSION_VERSION}
    if buckets.layout == "unknown":
        return UNKNOWN_LAYOUT_REASON, info
    if not credits_ok(snapshot) or snapshot.get("stale"):
        return "quota_snapshot_untrusted", info
    cfg = policy["players"]["gemini"]
    fallback = float(cfg["tier2_per_question_cap"])
    forecasts, history = read_complete(history_root / "forecasts.jsonl"), read_complete(history_root / "quota.jsonl")
    settings = estimate_settings(policy)
    reserve = 1 - float(cfg["weekly_cap"])
    estimate_weekly, weekly_count = estimate(forecasts, history, current=snapshot, fallback=fallback, tier=tier,
                                             period="weekly", **settings)
    info.update(quota_weekly_bucket=buckets.weekly.id, argon_remaining=buckets.weekly.remaining_fraction,
                reserve=reserve, estimate=estimate_weekly, estimate_samples=weekly_count,
                estimate_method=WINDOW_ESTIMATE_VERSION)
    if (model.startswith(("gemini-4-argon-", "gemini-4-carbon-")) or buckets.layout == "ultra_shared") and buckets.weekly.remaining_fraction - estimate_weekly < reserve - QUOTA_EPSILON:
        return "quota_exhausted", info
    estimate_5h, count = estimate(forecasts, history, current=snapshot, fallback=fallback, tier=tier,
                                  period="five_hour", **settings)
    reason, short_info = five_hour_gate(snapshot, estimate_5h)
    info.update(short_info)
    if buckets.five_hour:
        info["quota_5h_estimate_samples"] = count
    return reason, info
