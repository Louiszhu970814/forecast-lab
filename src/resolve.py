"""Fetch official Metaculus resolutions and scores for submitted questions (PLAN.md §4.1, §4.4, §5.1).

Key guarantees (resolve_v2):
- Only queries questions that have a submitted forecast (`forecast_ok` or `comment_ok` in
  `data/submissions.jsonl`, or `--include-dry-run` when explicitly requested for local testing)
  AND whose `scheduled_close_time <= now` AND (`scheduled_resolve_time <= now` unless
  `--allow-before-scheduled-resolve` is explicitly passed). `--question-id` filters IDs without
  bypassing submission or timestamp requirements. Never queries open questions.
- `--dry-candidates` performs local selection only, exposes skipped probes and integrity errors,
  and exits nonzero on errors without creating an API client or writing ledger records.
- Shares `api_backoff_v3` (`data/api_cooldowns.jsonl` with `pace=True`) across processes and
  yields immediately (`deferred_busy_lock`) whenever `submit.lock` or `cycle.lock` is held
  (including before/after pacing sleeps and before each HTTP retry), keeping resolution polling
  strictly lower priority than formal submission.
- Appends new resolution events to `data/resolutions.jsonl` (append-only, never overwrites);
  skips appending when none of `api_status`, `resolution_status`, `canonical_outcome`,
  `raw_resolution`, `platform_resolve_time_utc`, or `official_score_data` changed since the
  question's latest recorded `resolution_event`.
- Tournament rankings are NOT part of resolution events. They are appended as separate
  `ranking_observation` records (at most one per tournament per run, only when the verified state
  changed), and only when the primary project leaderboard's `userEntry.user.id` equals the single
  bot account id recorded on that tournament's submitted rows. Never infers rank from other entries
  and never records a participant count.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

_SRC_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SRC_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from api_backoff import Backoff  # noqa: E402
from forecast import Lock, PreflightError, _HELD_LOCKS  # noqa: E402
from flab.envfile import get_secret  # noqa: E402
from flab.ids import hash_obj, new_id  # noqa: E402
from flab.jsonl import append_record  # noqa: E402
from flab.ledger import read_complete, transaction  # noqa: E402
from flab.timeutil import parse_iso, to_iso, utc_now  # noqa: E402
from flab.submission_identity import submission_identity, is_snapshotless_probe, submission_status  # noqa: E402
from metaculus import MetaculusClient, _default_opener  # noqa: E402
import numeric as N  # noqa: E402

VERSION = "resolve_v3"
DEFAULT_DATA_DIR = _REPO_ROOT / "data"
SUBMITTED_STATUSES = {"forecast_ok", "comment_ok"}
AMBIGUOUS_TOKENS = {"ambiguous", "ambiguity"}
ANNULLED_TOKENS = {"annulled", "canceled", "cancelled", "void"}
TAIL_TOKENS = {"below_lower_bound", "above_upper_bound"}
SCORE_KEYS = (
    "spot_peer_score",
    "peer_score",
    "baseline_score",
    "spot_baseline_score",
    "relative_legacy_score",
    "coverage",
    "weighted_coverage",
)
SLEEP_SLICE_SECONDS = 0.1
RANKING_OBSERVATION_KEYS = (
    "rank", "matched_user_id", "endpoint", "leaderboard_id", "leaderboard_name",
    "leaderboard_score_type", "leaderboard_finalized", "calculated_on",
)

FetchPost = Callable[[int], Dict[str, Any]]
FetchRanking = Callable[[int, int], Dict[str, Any]]  # (tournament_id, expected_bot_user_id) -> verified ranking or {}


def _status(row: Dict[str, Any]) -> str:
    return submission_status(row)


def _parse_iso_safe(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return parse_iso(value.strip())
    except Exception:
        return None


def _coerce_now(now: Any) -> datetime:
    if now is None:
        return utc_now()
    if isinstance(now, datetime):
        return (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    dt = _parse_iso_safe(now)
    if dt is None:
        raise ValueError(f"invalid now timestamp: {now!r}")
    return dt


class PriorityYieldError(RuntimeError):
    """Raised when a higher-priority submission or cycle lock becomes active."""

    def __init__(self, busy_lock: str) -> None:
        super().__init__(f"higher-priority lock active: {busy_lock}")
        self.busy_lock = busy_lock


def _is_lock_busy(lock_path: Path) -> bool:
    """Read-only lock probe: returns True if `lock_path` is held, without acquiring an exclusive lock on darwin.

    On macOS (XNU), `fcntl(F_GETLK)` inspects the kernel's unified `lf_advlock` vnode lock list (shared by
    both `flock` and POSIX locks) without acquiring or modifying any lock on `lock_path`, so `submit.py`
    and `continuous.py` calling `flock(LOCK_EX | LOCK_NB)` can never fail due to a race with this probe.
    """
    if lock_path.resolve() in getattr(_HELD_LOCKS, "paths", set()):
        return True
    if not lock_path.exists():
        return False
    if sys.platform == "darwin":
        try:
            with lock_path.open("r+b") as fh:
                probe = struct.pack("qqihh", 0, 0, 0, fcntl.F_WRLCK, 0)
                res = fcntl.fcntl(fh.fileno(), fcntl.F_GETLK, probe)
                _, _, _, l_type, _ = struct.unpack("qqihh", res)
                return l_type != fcntl.F_UNLCK
        except OSError:
            # Fail toward yielding: an unreadable lock state must never let resolve.py run ahead of submit/cycle.
            return True
    probe_lock = Lock(lock_path)
    try:
        probe_lock.__enter__()
    except PreflightError:
        return True
    else:
        probe_lock.__exit__(None, None, None)
        return False


def _higher_priority_busy(data_dir: Path) -> Optional[str]:
    for name in ("submit.lock", "cycle.lock"):
        if _is_lock_busy(data_dir / name):
            return name
    return None


def _raise_if_higher_priority_busy(data_dir: Path) -> None:
    if busy := _higher_priority_busy(data_dir):
        raise PriorityYieldError(busy)


def extract_official_scores(post: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Extract numeric official score fields from question.my_forecasts.score_data."""
    q = post.get("question")
    q = q if isinstance(q, dict) else {}
    mf = q.get("my_forecasts")
    sd = mf.get("score_data") if isinstance(mf, dict) else None
    if not isinstance(sd, dict):
        return {}
    out: Dict[str, Optional[float]] = {}
    for k in SCORE_KEYS:
        if k in sd:
            v = sd.get(k)
            if v is None:
                out[k] = None
            elif type(v) in (int, float) and math.isfinite(float(v)):
                out[k] = float(v)
    return out


def classify_resolution(post: Dict[str, Any], snapshot: Dict[str, Any]
                        ) -> Tuple[str, Any, Any, Optional[str]]:
    """Classify a Metaculus post into (resolution_status, canonical_outcome, raw_resolution, resolve_time_utc).

    resolution_status is one of:
      - "resolved": unambiguously resolved to a valid canonical outcome for this question_type
      - "ambiguous": resolved as ambiguous (excluded from scoring per PLAN §4.3)
      - "annulled": resolved as annulled/cancelled (excluded from scoring per PLAN §4.3)
      - "unresolved": not yet resolved or unrecognized resolution value
    """
    q = post.get("question")
    q = q if isinstance(q, dict) else {}
    raw_qtype = snapshot.get("question_type") or q.get("type")
    qtype = str(raw_qtype).strip().lower() if isinstance(raw_qtype, str) and raw_qtype.strip() else ""

    post_status = str(post.get("status") or "").strip().lower()
    q_status = str(q.get("status") or "").strip().lower()
    post_resolved_prop = post.get("resolved")
    q_resolved_prop = q.get("resolved")

    raw_res = q.get("resolution") if "resolution" in q else post.get("resolution")
    resolve_time = (
        q.get("actual_resolve_time")
        or post.get("actual_resolve_time")
        or q.get("resolution_set_time")
        or post.get("resolved_at")
    )
    resolve_time_str = resolve_time if isinstance(resolve_time, str) and _parse_iso_safe(resolve_time) else None

    if raw_res is None or (isinstance(raw_res, str) and not raw_res.strip()):
        return "unresolved", None, raw_res, resolve_time_str

    # Reject explicit false resolution flags or any non-"resolved" status string
    if post_resolved_prop is False or q_resolved_prop is False:
        return "unresolved", None, raw_res, resolve_time_str
    if (post_status and post_status != "resolved") or (q_status and q_status != "resolved"):
        return "unresolved", None, raw_res, resolve_time_str
    is_resolved_flag = bool(
        post_resolved_prop is True
        or q_resolved_prop is True
        or post_status == "resolved"
        or q_status == "resolved"
    )
    if not is_resolved_flag:
        return "unresolved", None, raw_res, resolve_time_str

    if isinstance(raw_res, str):
        norm = raw_res.strip().lower()
        if norm in AMBIGUOUS_TOKENS:
            return "ambiguous", None, raw_res, resolve_time_str
        if norm in ANNULLED_TOKENS:
            return "annulled", None, raw_res, resolve_time_str
    else:
        norm = ""

    if qtype == "binary":
        if norm in ("yes", "true", "1", "1.0") or raw_res is True or (type(raw_res) in (int, float) and raw_res == 1):
            return "resolved", 1, raw_res, resolve_time_str
        if norm in ("no", "false", "0", "0.0") or raw_res is False or (type(raw_res) in (int, float) and raw_res == 0):
            return "resolved", 0, raw_res, resolve_time_str
        return "unresolved", None, raw_res, resolve_time_str

    if qtype == "multiple_choice":
        options = snapshot.get("options") or q.get("options") or []
        if isinstance(raw_res, str) and isinstance(options, list):
            if raw_res in options:
                return "resolved", raw_res, raw_res, resolve_time_str
            # Case-insensitive fallback if unique match
            matches = [opt for opt in options if isinstance(opt, str) and opt.strip().lower() == norm]
            if len(matches) == 1:
                return "resolved", matches[0], raw_res, resolve_time_str
        return "unresolved", None, raw_res, resolve_time_str

    if qtype in N.TYPES:
        scaling = q.get("scaling") if isinstance(q.get("scaling"), dict) else {}
        lo = snapshot.get("range_min", scaling.get("range_min"))
        hi = snapshot.get("range_max", scaling.get("range_max"))
        if not N.finite(lo) or not N.finite(hi) or float(lo) >= float(hi):
            return "unresolved", None, raw_res, resolve_time_str
        raw_open_lo = snapshot.get("open_lower_bound", scaling.get("open_lower_bound", q.get("open_lower_bound")))
        raw_open_hi = snapshot.get("open_upper_bound", scaling.get("open_upper_bound", q.get("open_upper_bound")))
        # Per M5-NUMERIC-SPEC.md §2: open_lower_bound / open_upper_bound of None is treated as closed (False).
        open_lo = bool(raw_open_lo) if isinstance(raw_open_lo, bool) else False
        open_hi = bool(raw_open_hi) if isinstance(raw_open_hi, bool) else False
        if norm in TAIL_TOKENS:
            if norm == "below_lower_bound" and not open_lo:
                return "unresolved", None, raw_res, resolve_time_str
            if norm == "above_upper_bound" and not open_hi:
                return "unresolved", None, raw_res, resolve_time_str
            return "resolved", norm, raw_res, resolve_time_str
        val: Optional[float] = None
        if type(raw_res) in (int, float) and N.finite(raw_res):
            val = float(raw_res)
        elif isinstance(raw_res, str):
            try:
                parsed = float(raw_res.strip())
                if N.finite(parsed):
                    val = parsed
            except ValueError:
                val = None
        if val is not None:
            if val < float(lo):
                if not open_lo:
                    return "unresolved", None, raw_res, resolve_time_str
                return "resolved", "below_lower_bound", raw_res, resolve_time_str
            if val > float(hi):
                if not open_hi:
                    return "unresolved", None, raw_res, resolve_time_str
                return "resolved", "above_upper_bound", raw_res, resolve_time_str
            if qtype == "discrete":
                nom_min = snapshot.get("nominal_min", scaling.get("nominal_min"))
                nom_max = snapshot.get("nominal_max", scaling.get("nominal_max"))
                n_cnt = snapshot.get("inbound_outcome_count", scaling.get("inbound_outcome_count"))
                if not N.finite(nom_min) or not N.finite(nom_max) or type(n_cnt) is not int or n_cnt < 2:
                    return "unresolved", None, raw_res, resolve_time_str
                a, b = float(nom_min), float(nom_max)
                step = (b - a) / float(n_cnt - 1)
                k_float = (val - a) / step if step > 0 else -1.0
                k = int(round(k_float))
                if not (0 <= k < n_cnt) or abs(k_float - k) > 1e-4:
                    return "unresolved", None, raw_res, resolve_time_str
            return "resolved", val, raw_res, resolve_time_str
        return "unresolved", None, raw_res, resolve_time_str

    return "unresolved", None, raw_res, resolve_time_str


def _select_candidates_with_diagnostics(
    data_dir: Path,
    *,
    now_dt: datetime,
    question_ids: Optional[Iterable[int]] = None,
    include_dry_run: bool = False,
    allow_before_scheduled_resolve: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Select snapshots for submitted questions eligible for resolution polling, plus diagnostic errors."""
    questions = [
        r for r in (read_complete(data_dir / "questions.jsonl") if (data_dir / "questions.jsonl").exists() else [])
        if r.get("type") == "question_snapshot"
    ]
    submissions = read_complete(data_dir / "submissions.jsonl") if (data_dir / "submissions.jsonl").exists() else []

    allowed_statuses = set(SUBMITTED_STATUSES)
    if include_dry_run:
        allowed_statuses.add("dry_run")

    submitted_snap_by_qid: Dict[int, Optional[str]] = {}
    submitted_mode_by_qid: Dict[int, Any] = {}
    submitted_real_by_qid: Dict[int, bool] = {}
    intents_by_id: Dict[str, Dict[str, Any]] = {}
    diagnostics: List[Dict[str, Any]] = []

    for r in submissions:
        iid = r.get("intent_id")
        if _status(r) == "intent" and isinstance(iid, str):
            intents_by_id[iid] = r

    for r in submissions:
        iid = r.get("intent_id")
        st = _status(r)
        if st in allowed_statuses:
            if r.get("at") is not None:
                at_dt = _parse_iso_safe(r.get("at"))
                if at_dt is None or at_dt > now_dt:
                    continue
            base = intents_by_id.get(iid, r) if isinstance(iid, str) else r
            qid = r.get("question_id") if type(r.get("question_id")) is int else base.get("question_id")
            if type(qid) is int:
                is_real = st in SUBMITTED_STATUSES
                if submitted_real_by_qid.get(qid, False) and not is_real:
                    continue
                identity = submission_identity(r, base)
                submitted_snap_by_qid[qid] = identity["question_snapshot_id"]
                submitted_mode_by_qid[qid] = identity["mode"]
                submitted_real_by_qid[qid] = is_real

    wanted = {int(q) for q in question_ids} if question_ids is not None else None

    snapshots_by_id: Dict[str, Dict[str, Any]] = {}
    latest_by_qid: Dict[int, Dict[str, Any]] = {}
    for snap in questions:
        sid = snap.get("snapshot_id")
        if isinstance(sid, str):
            snapshots_by_id[sid] = snap
        qid = snap.get("question_id")
        if type(qid) is int:
            latest_by_qid[qid] = snap

    selected: List[Dict[str, Any]] = []
    for qid in sorted(submitted_snap_by_qid.keys()):
        if wanted is not None and qid not in wanted:
            continue

        sub_snap_id = submitted_snap_by_qid[qid]
        if is_snapshotless_probe({"question_snapshot_id": sub_snap_id, "mode": submitted_mode_by_qid.get(qid)}):
            # Only explicit local/trial probes may omit a snapshot. Missing,
            # malformed, contradictory, or live modes remain integrity errors.
            diagnostics.append({"question_id": qid, "skip_reason": "submitted_without_snapshot_id"})
            continue
        snap = snapshots_by_id.get(sub_snap_id) if isinstance(sub_snap_id, str) else None
        if snap is None or snap.get("question_id") != qid:
            diagnostics.append({
                "question_id": qid,
                "snapshot_id": sub_snap_id,
                "error_type": "MissingSubmittedSnapshot",
            })
            continue

        latest_snap = latest_by_qid.get(qid, snap)
        close_dt = _parse_iso_safe(latest_snap.get("scheduled_close_time") or snap.get("scheduled_close_time"))
        if close_dt is None or close_dt > now_dt:
            # Never poll open questions in resolve.py
            continue

        if not allow_before_scheduled_resolve:
            resolve_dt = _parse_iso_safe(latest_snap.get("scheduled_resolve_time") or snap.get("scheduled_resolve_time"))
            if resolve_dt is None or resolve_dt > now_dt:
                continue

        selected.append(snap)
    return selected, diagnostics


def candidate_questions(
    data_dir: Path,
    *,
    now_dt: datetime,
    question_ids: Optional[Iterable[int]] = None,
    include_dry_run: bool = False,
    allow_before_scheduled_resolve: bool = False,
) -> List[Dict[str, Any]]:
    """Select snapshots for submitted questions eligible for resolution polling.

    Enforces all gates independently:
    1. Question MUST have a submitted record in `submissions.jsonl` (`forecast_ok` or `comment_ok`,
       plus `dry_run` only when `include_dry_run=True`), even when `question_ids` is specified,
       and its `question_snapshot_id` MUST exist in `questions.jsonl` (never substitutes another snapshot).
    2. `scheduled_close_time` MUST be valid and `<= now_dt` (never polls open questions).
    3. Unless `allow_before_scheduled_resolve=True`, `scheduled_resolve_time` MUST be valid and `<= now_dt`.
    """
    selected, _ = _select_candidates_with_diagnostics(
        data_dir,
        now_dt=now_dt,
        question_ids=question_ids,
        include_dry_run=include_dry_run,
        allow_before_scheduled_resolve=allow_before_scheduled_resolve,
    )
    return selected


def _submitted_bot_ids_by_tournament(data_dir: Path) -> Dict[int, set]:
    """Bot account ids recorded on real submitted rows (forecast_ok / comment_ok), per tournament."""
    path = data_dir / "submissions.jsonl"
    out: Dict[int, set] = {}
    for r in (read_complete(path) if path.exists() else []):
        if _status(r) not in SUBMITTED_STATUSES:
            continue
        tid, bid = r.get("tournament_id"), r.get("bot_id")
        if type(tid) is int and type(bid) is int and bid > 0:
            out.setdefault(tid, set()).add(bid)
        elif type(tid) is int:
            out.setdefault(tid, set()).add(None)
    return out


def poll_resolutions(
    data_dir: Path = DEFAULT_DATA_DIR,
    *,
    fetch_post: Optional[FetchPost] = None,
    fetch_ranking: Optional[FetchRanking] = None,
    now: Any = None,
    question_ids: Optional[Iterable[int]] = None,
    include_dry_run: bool = False,
    allow_before_scheduled_resolve: bool = False,
) -> Dict[str, Any]:
    """Poll Metaculus API for candidate questions and append changed resolution events."""
    data_dir = Path(data_dir)
    now_dt = _coerce_now(now)

    if busy := _higher_priority_busy(data_dir):
        return {
            "version": VERSION,
            "status": "deferred_busy_lock",
            "busy_lock": busy,
            "polled": 0,
            "appended": 0,
            "events": [],
            "ranking_observations": [],
            "ranking_diagnostics": [],
            "skipped": [],
            "errors": [],
        }

    res_lock = Lock(data_dir / "resolve.lock")
    try:
        res_lock.__enter__()
    except PreflightError:
        return {
            "version": VERSION,
            "status": "busy",
            "busy_lock": "resolve.lock",
            "polled": 0,
            "appended": 0,
            "events": [],
            "ranking_observations": [],
            "ranking_diagnostics": [],
            "skipped": [],
            "errors": [],
        }

    try:
        candidates, diagnostics = _select_candidates_with_diagnostics(
            data_dir,
            now_dt=now_dt,
            question_ids=question_ids,
            include_dry_run=include_dry_run,
            allow_before_scheduled_resolve=allow_before_scheduled_resolve,
        )
        skipped = [d for d in diagnostics if "skip_reason" in d]
        errors = [d for d in diagnostics if "skip_reason" not in d]  # anything not explicitly skipped is an error
        res_path = data_dir / "resolutions.jsonl"
        with transaction(data_dir):
            existing = read_complete(res_path) if res_path.exists() else []

        latest_event_by_qid: Dict[int, Dict[str, Any]] = {}
        latest_ranking_by_tour: Dict[int, Dict[str, Any]] = {}
        for row in existing:
            qid = row.get("question_id")
            if type(qid) is int and row.get("type") == "resolution_event":
                latest_event_by_qid[qid] = row
            tid = row.get("tournament_id")
            if type(tid) is int and row.get("type") == "ranking_observation":
                latest_ranking_by_tour[tid] = row

        appended_events: List[Dict[str, Any]] = []
        appended_rankings: List[Dict[str, Any]] = []
        ranking_diagnostics: List[Dict[str, Any]] = []
        polled = 0
        ranking_tournaments: List[int] = []

        for snap in candidates:
            if busy := _higher_priority_busy(data_dir):
                return {
                    "version": VERSION,
                    "status": "deferred_busy_lock",
                    "busy_lock": busy,
                    "polled": polled,
                    "appended": len(appended_events),
                    "events": appended_events,
                    "ranking_observations": appended_rankings,
                    "ranking_diagnostics": ranking_diagnostics,
                    "skipped": skipped,
                    "errors": errors,
                }
            if fetch_post is None:
                continue

            qid = snap["question_id"]
            post_id = snap.get("post_id")
            if type(post_id) is not int or post_id <= 0:
                errors.append({"question_id": qid, "post_id": post_id, "error_type": "MissingPostId"})
                continue
            try:
                post = fetch_post(int(post_id))
            except PriorityYieldError as exc:
                return {
                    "version": VERSION,
                    "status": "deferred_busy_lock",
                    "busy_lock": exc.busy_lock,
                    "polled": polled,
                    "appended": len(appended_events),
                    "events": appended_events,
                    "ranking_observations": appended_rankings,
                    "ranking_diagnostics": ranking_diagnostics,
                    "skipped": skipped,
                    "errors": errors,
                }
            except Exception as exc:
                polled += 1
                errors.append({"question_id": qid, "post_id": post_id, "error_type": type(exc).__name__})
                continue
            polled += 1

            if not isinstance(post, dict):
                errors.append({"question_id": qid, "post_id": post_id, "error_type": "InvalidPostPayload"})
                continue

            if snap.get('post_type') == 'group_of_questions':
                from question_mapping import select_question
                try:
                    post = select_question(post, qid, allow_groups=True)
                    if post['_group_mapping']['group_id'] != snap.get('group_id'):
                        raise ValueError('group identity changed')
                except (ValueError, KeyError):
                    errors.append({'question_id':qid,'post_id':post_id,'error_type':'GroupQuestionMismatch'})
                    continue
            q_obj = post.get("question")
            if (
                (post.get("id") is not None and post.get("id") != post_id)
                or not isinstance(q_obj, dict)
                or q_obj.get("id") != qid
            ):
                errors.append({"question_id": qid, "post_id": post_id, "error_type": "PostQuestionMismatch"})
                continue

            snap_qtype = str(snap.get("question_type") or "").strip().lower()
            post_qtype = str(q_obj.get("type") or "").strip().lower()
            valid_qtypes = {"binary", "multiple_choice", *N.TYPES}
            if snap_qtype not in valid_qtypes or (post_qtype and post_qtype != snap_qtype):
                errors.append({"question_id": qid, "post_id": post_id, "error_type": "QuestionTypeMismatch"})
                continue

            res_status, canonical, raw_res, resolve_time_utc = classify_resolution(post, snap)
            score_data = extract_official_scores(post)
            prev = latest_event_by_qid.get(qid)

            # Do not pollute resolutions.jsonl if still unresolved and no question score exists yet
            if res_status == "unresolved" and not score_data and prev is None:
                continue

            tour_id = snap.get("tournament_id")
            if type(tour_id) is int and (res_status == "resolved" or score_data) and tour_id not in ranking_tournaments:
                ranking_tournaments.append(tour_id)
            api_status = str(q_obj.get("status") or post.get("status") or "").strip().lower() or None

            fingerprint_payload = {
                "api_status": api_status,
                "resolution_status": res_status,
                "canonical_outcome": canonical,
                "raw_resolution": raw_res,
                "platform_resolve_time_utc": resolve_time_utc,
                "official_score_data": score_data,
            }
            fp = hash_obj(fingerprint_payload)
            if prev is not None and prev.get("state_sha256") == fp:
                continue

            now_iso = to_iso(_coerce_now(now))
            if not score_data:
                score_first_obs_iso = None
            elif prev is not None and prev.get("official_score_data") == score_data:
                score_first_obs_iso = prev.get("official_score_first_observed_at_utc") or prev.get("observed_at_utc")
            else:
                score_first_obs_iso = now_iso

            same_resolution_outcome = (
                prev is not None
                and prev.get("resolution_status") == res_status
                and prev.get("canonical_outcome") == canonical
                and prev.get("raw_resolution") == raw_res
            )
            first_obs_iso = (
                (prev.get("first_observed_at_utc") or prev.get("observed_at_utc") or now_iso)
                if same_resolution_outcome
                else now_iso
            )

            event = {
                "type": "resolution_event",
                "version": VERSION,
                "resolution_event_id": new_id("res"),
                "question_id": qid,
                "post_id": int(post_id),
                "tournament_id": snap.get("tournament_id"),
                "venue": snap.get("venue"),
                "question_type": snap.get("question_type"),
                "snapshot_id": snap.get("snapshot_id"),
                "observed_at_utc": now_iso,
                "first_observed_at_utc": first_obs_iso,
                "source": "metaculus_api_post",
                "api_status": api_status,
                "resolution_status": res_status,
                "canonical_outcome": canonical,
                "raw_resolution": raw_res,
                "platform_resolve_time_utc": resolve_time_utc,
                "official_score_data": score_data,
                "official_score_first_observed_at_utc": score_first_obs_iso,
                "state_sha256": fp,
                "supersedes_resolution_event_id": prev.get("resolution_event_id") if prev else None,
            }
            with transaction(data_dir):
                append_record(res_path, event)
            latest_event_by_qid[qid] = event
            appended_events.append(event)

        # Tournament rankings: separate append-only ranking_observation records, at most one per tournament
        # per run, appended only when the verified state changed. Identity must be provable.
        bot_ids_by_tour = _submitted_bot_ids_by_tournament(data_dir) if ranking_tournaments else {}
        for tour_id in ranking_tournaments:
            if fetch_ranking is None:
                break
            if busy := _higher_priority_busy(data_dir):
                return {
                    "version": VERSION,
                    "status": "deferred_busy_lock",
                    "busy_lock": busy,
                    "polled": polled,
                    "appended": len(appended_events),
                    "events": appended_events,
                    "ranking_observations": appended_rankings,
                    "ranking_diagnostics": ranking_diagnostics,
                    "skipped": skipped,
                    "errors": errors,
                }
            bot_ids = bot_ids_by_tour.get(tour_id, set())
            if len(bot_ids) != 1 or None in bot_ids:
                ranking_diagnostics.append({"tournament_id": tour_id, "reason": "bot_identity_unavailable_or_ambiguous"})
                continue
            expected_user_id = next(iter(bot_ids))
            try:
                ranking = fetch_ranking(tour_id, expected_user_id) or {}
            except PriorityYieldError as exc:
                return {
                    "version": VERSION,
                    "status": "deferred_busy_lock",
                    "busy_lock": exc.busy_lock,
                    "polled": polled,
                    "appended": len(appended_events),
                    "events": appended_events,
                    "ranking_observations": appended_rankings,
                    "ranking_diagnostics": ranking_diagnostics,
                    "skipped": skipped,
                    "errors": errors,
                }
            except Exception as exc:
                if getattr(exc, "status", None) == 404:
                    ranking_diagnostics.append({"tournament_id": tour_id, "reason": "leaderboard_not_found"})
                else:
                    errors.append({"tournament_id": tour_id, "error_type": type(exc).__name__, "stage": "ranking"})
                continue
            if (
                not ranking
                or ranking.get("matched_user_id") != expected_user_id
                or type(ranking.get("rank")) is not int
                or ranking["rank"] < 1
            ):
                ranking_diagnostics.append({"tournament_id": tour_id, "reason": "no_verified_own_leaderboard_entry"})
                continue
            ranking = {k: ranking[k] for k in RANKING_OBSERVATION_KEYS if k in ranking}
            r_fp = hash_obj({"tournament_id": tour_id, "ranking": ranking})
            prev_r = latest_ranking_by_tour.get(tour_id)
            if prev_r is not None and prev_r.get("state_sha256") == r_fp:
                continue
            obs = {
                **{k: ranking[k] for k in RANKING_OBSERVATION_KEYS if k in ranking},
                "type": "ranking_observation",
                "version": VERSION,
                "ranking_observation_id": new_id("rank"),
                "tournament_id": tour_id,
                "observed_at_utc": to_iso(_coerce_now(now)),
                "source": "metaculus_api_project_leaderboard",
                "ranking_verified": True,
                "state_sha256": r_fp,
                "supersedes_ranking_observation_id": prev_r.get("ranking_observation_id") if prev_r else None,
            }
            with transaction(data_dir):
                append_record(res_path, obs)
            latest_ranking_by_tour[tour_id] = obs
            appended_rankings.append(obs)

        return {
            "version": VERSION,
            "status": "error" if errors else "ok",
            "polled": polled,
            "candidates": len(candidates),
            "appended": len(appended_events),
            "events": appended_events,
            "ranking_observations": appended_rankings,
            "ranking_diagnostics": ranking_diagnostics,
            "skipped": skipped,
            "errors": errors,
        }
    finally:
        res_lock.__exit__(None, None, None)


def _build_client(
    data_dir: Path,
    *,
    token: Optional[str] = None,
    opener: Callable[[Any, float], Any] = _default_opener,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> MetaculusClient:
    if not token:
        token = os.environ.get("METACULUS_TOKEN")
    if not token:
        token = get_secret("METACULUS_TOKEN", _REPO_ROOT / ".env")
    cooldown_path = Path(data_dir) / "api_cooldowns.jsonl"

    def priority_sleep(seconds: float) -> None:
        _raise_if_higher_priority_busy(data_dir)
        rem = max(0.0, float(seconds))
        if rem == 0.0:
            sleep_fn(0.0)
            _raise_if_higher_priority_busy(data_dir)
            return
        while rem > 0.0:
            step = min(SLEEP_SLICE_SECONDS, rem)
            sleep_fn(step)
            _raise_if_higher_priority_busy(data_dir)
            rem -= step

    def priority_opener(req: Any, timeout: float) -> Any:
        _raise_if_higher_priority_busy(data_dir)
        return opener(req, timeout)

    backoff = Backoff(cooldown_path, pace=True, sleep=priority_sleep)
    return MetaculusClient(token, opener=priority_opener, sleep=priority_sleep, backoff=backoff, manage_backoff=True)


def _build_client_fetcher(
    data_dir: Path,
    *,
    token: Optional[str] = None,
    opener: Callable[[Any, float], Any] = _default_opener,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> FetchPost:
    client = _build_client(data_dir, token=token, opener=opener, sleep_fn=sleep_fn)
    return client.get_post


def _build_client_ranking_fetcher(
    data_dir: Path,
    *,
    client: Optional[MetaculusClient] = None,
    token: Optional[str] = None,
    opener: Callable[[Any, float], Any] = _default_opener,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> FetchRanking:
    active_client = client or _build_client(data_dir, token=token, opener=opener, sleep_fn=sleep_fn)

    def fetch_ranking(tournament_id: int, expected_user_id: int) -> Dict[str, Any]:
        # Official scoring/urls.py + project_leaderboard_view: list response, primary leaderboard only,
        # userEntry is the authenticated account's own entry. We additionally require
        # userEntry.user.id == the bot account id recorded on our submissions. Never infer our rank
        # from another user's entries and never invent a participant count.
        if type(expected_user_id) is not int or expected_user_id <= 0:
            return {}
        endpoint = f"leaderboards/project/{int(tournament_id)}/"
        payload = active_client.get(endpoint, params={"primary_only": "true"})
        if not isinstance(payload, list):
            return {}
        boards = [b for b in payload if isinstance(b, dict)
                  and type(b.get("project_id")) is int
                  and b["project_id"] == tournament_id
                  and b.get("is_primary_leaderboard") is True]
        if len(boards) != 1:
            return {}
        board = boards[0]
        entry = board.get("userEntry")
        if not isinstance(entry, dict):
            return {}
        user = entry.get("user")
        if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] != expected_user_id:
            return {}
        rank = entry.get("rank")
        if type(rank) is not int or rank < 1:
            return {}
        out: Dict[str, Any] = {
            "rank": rank,
            "matched_user_id": user["id"],
            "endpoint": endpoint + "?primary_only=true",
        }
        if type(board.get("id")) is int:
            out["leaderboard_id"] = board["id"]
        for src_k, dst_k in (("name", "leaderboard_name"), ("score_type", "leaderboard_score_type")):
            if isinstance(board.get(src_k), str):
                out[dst_k] = board[src_k]
        if type(board.get("finalized")) is bool:
            out["leaderboard_finalized"] = board["finalized"]
        if isinstance(entry.get("calculated_on"), str):
            out["calculated_on"] = entry["calculated_on"]
        return out

    return fetch_ranking


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="resolve", description=f"{VERSION}: poll Metaculus resolutions and official scores")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--question-id", type=int, action="append", default=None, help="repeatable")
    ap.add_argument("--include-dry-run", action="store_true")
    ap.add_argument("--allow-before-scheduled-resolve", action="store_true")
    ap.add_argument("--dry-candidates", action="store_true", help="list eligible candidates without calling API")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    if args.dry_candidates:
        cands, diagnostics = _select_candidates_with_diagnostics(
            data_dir,
            now_dt=utc_now(),
            question_ids=args.question_id,
            include_dry_run=args.include_dry_run,
            allow_before_scheduled_resolve=args.allow_before_scheduled_resolve,
        )
        skipped = [d for d in diagnostics if "skip_reason" in d]
        errors = [d for d in diagnostics if "skip_reason" not in d]
        print(json.dumps({
            "version": VERSION,
            "status": "error" if errors else "ok",
            "errors": errors,
            "skipped": skipped,
            "candidates": [
                {
                    "question_id": c["question_id"],
                    "post_id": c.get("post_id"),
                    "question_type": c.get("question_type"),
                    "scheduled_close_time": c.get("scheduled_close_time"),
                    "scheduled_resolve_time": c.get("scheduled_resolve_time"),
                }
                for c in cands
            ],
        }, indent=2, sort_keys=True))
        return 1 if errors else 0

    client = _build_client(data_dir)
    fetch_ranking = _build_client_ranking_fetcher(data_dir, client=client)
    result = poll_resolutions(
        data_dir,
        fetch_post=client.get_post,
        fetch_ranking=fetch_ranking,
        question_ids=args.question_id,
        include_dry_run=args.include_dry_run,
        allow_before_scheduled_resolve=args.allow_before_scheduled_resolve,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    # Yielding to submit/cycle is expected and not a failure; real fetch/payload errors are.
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
