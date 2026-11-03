"""Read-only post-close diagnostic health check for Forecast Lab (PLAN.md D126 / D45).

Strictly read-only: never writes, appends, or modifies anything in data/ or config/.
Forecast content (probabilities, CDFs, option probabilities, base rates, market refs,
reasoning) is unmasked ONLY when all three review gates pass:

  Gate 1  gate1_platform_closed      fresh API post is closed/resolved and past close + N min
  Gate 2  gate2_lifecycle_converged  local lifecycle (attempts, aggregates, submissions) settled
  Gate 3  gate3_not_queued           question is not (re)queued and snapshot not invalidated
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

VERSION = "post_close_audit_v1"

_SRC_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SRC_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

try:  # lock-free reader; never use transaction() here
    from flab.ledger import read_complete as _read_complete  # type: ignore
except Exception:  # pragma: no cover - fallback keeps the tool usable but strict
    _read_complete = None

DEFAULT_DATA_DIR = _REPO_ROOT / "data"

REQUIRED_LEDGERS = (
    "questions.jsonl",
    "forecasts.jsonl",
    "aggregates.jsonl",
    "submissions.jsonl",
    "question_queue.jsonl",
)
OPTIONAL_LEDGERS = ("forecast_attempt_starts.jsonl", "submission_readbacks.jsonl")

CLOSED_STATUSES = {"closed", "resolved"}
TERMINAL_INTENT_STATUSES = {"comment_ok", "dry_run", "forecast_failed", "comment_failed"}
OUTPUT_INTENT_STATUSES = {"comment_ok", "dry_run"}
SUBMITTED_STATUSES = {"forecast_ok", "comment_ok"}
NO_INTENT_OK_STATUSES = {"rejected_precheck", "dry_run"}
UNRESOLVED_REQUEST_STATUSES = {"request_started", "unknown"}
READBACK_OK_OUTCOMES = {"found", "not_accepted"}
QUEUE_DONE_ACTIONS = {"already_submitted", "unsupported_type", "skipped", "dropped", "closed"}
QUEUE_ACTIVE_ACTIONS = {"queued", "requeued"}
CLOSE_TIME_KEYS = ("actual_close_time", "scheduled_close_time")
HIGH_DISPERSION_THRESHOLD = 0.30

FetchPost = Callable[[Any], Any]


# --------------------------------------------------------------------------- utilities
def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _same_id(a: Any, b: Any) -> bool:
    return a is not None and b is not None and str(a) == str(b)


def _nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _status(row: Dict[str, Any]) -> str:
    v = row.get("status") or row.get("state")
    return v.strip().lower() if isinstance(v, str) else ""


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string to an aware UTC datetime; None if invalid."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _coerce_now(now: Any) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    parsed = _parse_iso(now)
    if parsed is None:
        raise ValueError(f"invalid 'now' value: {now!r}")
    return parsed


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


# --------------------------------------------------------------------------- ledger reading
def _strict_scan(path: Path) -> Optional[str]:
    """Independent read-only integrity scan. Returns an error string or None."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    return f"malformed_line:{lineno}"
                if not isinstance(obj, dict):
                    return f"non_object_line:{lineno}"
    except (OSError, UnicodeDecodeError) as exc:
        return f"unreadable:{type(exc).__name__}"
    return None


def _unpack_read_complete(result: Any) -> Tuple[Optional[List[Any]], bool]:
    """Normalise read_complete's return value -> (rows, complete)."""
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], bool):
        return (list(result[0]) if result[0] is not None else None), result[1]
    if isinstance(result, dict) and "rows" in result:
        complete = bool(result.get("complete", not result.get("truncated", False)))
        return list(result.get("rows") or []), complete
    if isinstance(result, (list, tuple)):
        return list(result), True
    return None, False


def _read_ledger(path: Path, *, required: bool) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    if not path.exists():
        return [], ("missing" if required else None)
    scan_err = _strict_scan(path)
    if scan_err:
        return [], scan_err
    if _read_complete is not None:
        try:
            rows, complete = _unpack_read_complete(_read_complete(path))
        except Exception as exc:
            return [], f"malformed:{type(exc).__name__}"
        if rows is None or not complete:
            return [], "incomplete"
    else:
        rows = []
        with path.open("r", encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    if any(not isinstance(r, dict) for r in rows):
        return [], "non_object_row"
    return rows, None


def _load_ledgers(data_dir: Path) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, str]]:
    ledgers: Dict[str, List[Dict[str, Any]]] = {}
    errors: Dict[str, str] = {}
    for name in REQUIRED_LEDGERS:
        rows, err = _read_ledger(data_dir / name, required=True)
        ledgers[name] = rows
        if err:
            errors[name] = err
    for name in OPTIONAL_LEDGERS:
        rows, err = _read_ledger(data_dir / name, required=False)
        ledgers[name] = rows
        if err:
            errors[name] = err
    return ledgers, errors


def _stat_snapshot(data_dir: Path) -> Dict[str, Tuple[int, int]]:
    snap: Dict[str, Tuple[int, int]] = {}
    try:
        for p in sorted(data_dir.glob("*.jsonl")):
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            snap[p.name] = (st.st_mtime_ns, st.st_size)
    except OSError:
        pass
    return snap


def _rows_for(rows: Iterable[Dict[str, Any]], qid: Any) -> List[Dict[str, Any]]:
    return [r for r in rows if _same_id(r.get("question_id"), qid)]


def _latest_snapshot(questions: List[Dict[str, Any]], qid: Any) -> Optional[Dict[str, Any]]:
    rows = _rows_for(questions, qid)
    return rows[-1] if rows else None


# --------------------------------------------------------------------------- gate 1
def _gate1(post: Any, snapshot: Optional[Dict[str, Any]], now_dt: datetime,
           min_closed_minutes: float, fetch_error: Optional[str]) -> Tuple[bool, List[str], Dict[str, Any]]:
    info: Dict[str, Any] = {"post_status": None, "question_status": None, "effective_close_time": None}
    if fetch_error:
        return False, [fetch_error], info
    if post is None:
        return False, ["offline_or_no_post"], info
    if not isinstance(post, dict):
        return False, ["post_not_dict"], info

    reasons: List[str] = []
    q = post.get("question")
    q = q if isinstance(q, dict) else {}

    statuses = []
    for label, raw in (("post_status", post.get("status")), ("question_status", q.get("status"))):
        if raw is None:
            continue
        norm = raw.strip().lower() if isinstance(raw, str) else None
        info[label] = norm if norm is not None else repr(raw)
        statuses.append(norm)
    if not statuses:
        reasons.append("no_platform_status")
    elif any(s not in CLOSED_STATUSES for s in statuses):
        reasons.append("platform_not_closed")

    candidates: List[Any] = []
    for src in (post, q):
        for key in CLOSE_TIME_KEYS:
            if key in src and src.get(key) is not None:
                candidates.append(src.get(key))
    if snapshot and snapshot.get("scheduled_close_time") is not None:
        candidates.append(snapshot.get("scheduled_close_time"))

    parsed: List[datetime] = []
    malformed = False
    for c in candidates:
        dt = _parse_iso(c)
        if dt is None:
            malformed = True
        else:
            parsed.append(dt)
    if malformed:
        reasons.append("malformed_close_time")
    elif not parsed:
        reasons.append("no_close_time")
    else:
        effective = max(parsed)
        info["effective_close_time"] = effective.isoformat()
        if effective > now_dt - timedelta(minutes=min_closed_minutes):
            reasons.append("close_window_not_elapsed")
    return (not reasons), reasons, info


# --------------------------------------------------------------------------- gate 2
def _reconcile_submissions(subs: List[Dict[str, Any]], readbacks: List[Dict[str, Any]]
                           ) -> Tuple[List[str], Dict[str, List[Dict[str, Any]]]]:
    reasons: List[str] = []
    by_intent: Dict[str, List[Dict[str, Any]]] = {}
    for row in subs:
        iid = row.get("intent_id")
        if not _nonempty_str(iid):
            if _status(row) not in NO_INTENT_OK_STATUSES:
                reasons.append("missing_intent_id")
            continue
        by_intent.setdefault(iid, []).append(row)

    pending = unresolved = False
    for iid, rows in by_intent.items():
        statuses = {_status(r) for r in rows}
        if "abandoned" in statuses and "request_started" not in statuses:
            continue  # converged
        if not statuses & TERMINAL_INTENT_STATUSES:
            pending = True
        for op in ("forecast", "comment"):
            op_rows = [r for r in rows if r.get("operation") == op]
            if not op_rows:
                continue
            last = op_rows[-1]
            if _status(last) not in UNRESOLVED_REQUEST_STATUSES:
                continue
            rid = last.get("request_id")
            if not _nonempty_str(rid):
                unresolved = True
                continue
            matched = any(
                rb.get("intent_id") == iid and rb.get("request_id") == rid
                and rb.get("operation") == op
                and _s(rb.get("outcome")).lower() in READBACK_OK_OUTCOMES
                for rb in readbacks
            )
            if not matched:
                unresolved = True
    if pending:
        reasons.append("pending_submission_intents")
    if unresolved:
        reasons.append("unresolved_or_unknown_requests")
    return sorted(set(reasons)), by_intent


def _gate2(qid: Any, ledgers: Dict[str, List[Dict[str, Any]]], errors: Dict[str, str],
           changed: List[str]) -> Tuple[bool, List[str], Dict[str, List[Dict[str, Any]]]]:
    reasons: List[str] = []
    if changed:
        reasons.append("concurrent_ledger_change")
    if errors:
        reasons.append("ledger_incomplete_or_missing")
        return False, reasons, {}

    forecasts = _rows_for(ledgers["forecasts.jsonl"], qid)
    aggregates = _rows_for(ledgers["aggregates.jsonl"], qid)
    subs = _rows_for(ledgers["submissions.jsonl"], qid)
    readbacks = ledgers["submission_readbacks.jsonl"]

    sub_reasons, by_intent = _reconcile_submissions(subs, readbacks)
    reasons.extend(sub_reasons)

    has_valid = any(r.get("validation_status") == "valid" for r in forecasts)
    has_output_intent = any(_status(r) in OUTPUT_INTENT_STATUSES and _nonempty_str(r.get("intent_id")) for r in subs)
    if not (has_valid or aggregates or has_output_intent):
        reasons.append("no_forecast_output")

    starts = _rows_for(ledgers["forecast_attempt_starts.jsonl"], qid)
    starts += [r for r in forecasts if r.get("type") == "forecast_attempt_started"]
    finished = {r.get("attempt_id") for r in forecasts
                if r.get("type") == "forecast_attempt" and _nonempty_str(r.get("attempt_id"))}
    if any(not _nonempty_str(s.get("attempt_id")) or s.get("attempt_id") not in finished for s in starts):
        reasons.append("unfinished_attempts")

    if aggregates and aggregates[-1].get("completion_status") == "awaiting_scheduler_finalization":
        reasons.append("awaiting_scheduler_finalization")

    return (not reasons), reasons, by_intent


# --------------------------------------------------------------------------- gate 3
def _gate3(qid: Any, ledgers: Dict[str, List[Dict[str, Any]]], errors: Dict[str, str],
           snapshot: Optional[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    if "question_queue.jsonl" in errors:
        return False, ["queue_ledger_unavailable"]
    queue = ledgers["question_queue.jsonl"]
    reasons: List[str] = []

    q_rows = [r for r in _rows_for(queue, qid) if r.get("type") != "snapshot_invalidated"]
    state_rows = [r for r in q_rows if r.get("type") == "queue_state"] or q_rows
    if state_rows:
        action = state_rows[-1].get("action")
        action = action.strip().lower() if isinstance(action, str) else None
        if action in QUEUE_ACTIVE_ACTIONS:
            reasons.append(f"queue_active:{action}")
        elif action not in QUEUE_DONE_ACTIONS:
            reasons.append("queue_action_missing_or_unrecognized")

    snap_id = snapshot.get("snapshot_id") if snapshot else None
    if snap_id is not None and any(
        r.get("type") == "snapshot_invalidated" and _same_id(r.get("old_snapshot_id"), snap_id)
        for r in queue
    ):
        reasons.append("snapshot_invalidated")
    return (not reasons), reasons


# --------------------------------------------------------------------------- diagnostics
def _tier(row: Dict[str, Any]) -> int:
    for key in ("tier", "stage", "round"):
        v = row.get(key)
        if v is None:
            continue
        s = str(v).strip().lower()
        if s in ("2", "tier2", "tier_2", "t2", "revision", "revised"):
            return 2
        if s in ("1", "tier1", "tier_1", "t1", "initial"):
            return 1
    return 1


def _player(row: Dict[str, Any]) -> Optional[str]:
    for key in ("player", "player_id", "model"):
        if _nonempty_str(row.get(key)):
            return row[key]
    return None


def _as_list(v: Any) -> List[Any]:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _tier_view(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "attempt_id": row.get("attempt_id"),
        "probability": row.get("probability"),
        "option_probabilities": row.get("option_probabilities"),
        "percentiles": row.get("percentiles"),
        "cdf": row.get("continuous_cdf") if row.get("continuous_cdf") is not None else row.get("cdf"),
        "base_rate": row.get("base_rate"),
        "market_refs": row.get("market_refs"),
        "changed_from_tier1": row.get("changed_from_tier1"),
        "change_reason": row.get("change_reason"),
        "reasoning_summary": row.get("reasoning_summary") or row.get("summary"),
        "key_factors": _as_list(row.get("key_factors")),
        "audit_flags": _as_list(row.get("audit_flags")),
    }


def _spread(values: List[Optional[float]]) -> Optional[float]:
    nums = [v for v in values if v is not None]
    return round(max(nums) - min(nums), 6) if len(nums) >= 2 else None


def _build_diagnostics(qid: Any, ledgers: Dict[str, List[Dict[str, Any]]], snapshot: Optional[Dict[str, Any]],
                       by_intent: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    snap_id = snapshot.get("snapshot_id") if snapshot else None

    def bound(r: Dict[str, Any]) -> bool:
        r_snap = r.get("question_snapshot_id") if r.get("question_snapshot_id") is not None else r.get("snapshot_id")
        return snap_id is None or r_snap is None or _same_id(r_snap, snap_id)

    forecasts = [r for r in _rows_for(ledgers["forecasts.jsonl"], qid)
                 if r.get("validation_status") == "valid" and bound(r)]
    aggregates = [r for r in _rows_for(ledgers["aggregates.jsonl"], qid) if bound(r)]
    latest_agg = aggregates[-1] if aggregates else None

    per_player: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for r in forecasts:
        p = _player(r)
        if p is not None:
            per_player.setdefault(p, {})[_tier(r)] = r  # last row wins

    players: Dict[str, Any] = {}
    warnings: List[str] = []
    t1_vals: List[Optional[float]] = []
    t2_vals: List[Optional[float]] = []
    for p in sorted(per_player):
        t1, t2 = per_player[p].get(1), per_player[p].get(2)
        v1, v2 = _tier_view(t1), _tier_view(t2)
        if v2 is not None and v2["changed_from_tier1"] is None and t1 is not None:
            a, b = _num(t1.get("probability")), _num(t2.get("probability"))
            if a is not None and b is not None:
                v2["changed_from_tier1"] = abs(a - b) > 1e-9
        players[p] = {"tier1": v1, "tier2": v2}
        has_any_t2 = any(per_player[op].get(2) is not None for op in per_player)
        expect_t2 = has_any_t2 or bool(latest_agg and latest_agg.get("expected_players"))
        if t1 is None or (t2 is None and expect_t2):
            warnings.append(f"missing_player:{p}")
        def _val(row):
            if not row:
                return None
            prob = _num(row.get("probability"))
            if prob is not None:
                return prob
            pct = row.get("percentiles")
            return _num(pct.get("p50")) if isinstance(pct, dict) else None
        t1_vals.append(_val(t1))
        t2_vals.append(_val(t2))
        for view in (v1, v2):
            for flag in (view or {}).get("audit_flags", []):
                warnings.append(f"audit_flag:{flag}")
        final = t2 or t1
        if final is not None and final.get("base_rate") is None and not final.get("market_refs"):
            warnings.append(f"no_market_or_base_rate:{p}")

    if latest_agg is not None:
        for p in _as_list(latest_agg.get("expected_players") or latest_agg.get("players")):
            if isinstance(p, str) and p not in per_player:
                warnings.append(f"missing_player:{p}")
        for p in _as_list(latest_agg.get("missing_players")):
            if isinstance(p, str):
                warnings.append(f"missing_player:{p}")

    tier1_spread, tier2_spread = _spread(t1_vals), _spread(t2_vals)
    for sp in (tier2_spread if tier2_spread is not None else tier1_spread,):
        if sp is not None and sp > HIGH_DISPERSION_THRESHOLD:
            warnings.append(f"high_dispersion:{sp:.3f}")

    intents = []
    submitted = False
    for iid, rows in by_intent.items():
        statuses = [_status(r) for r in rows]
        if set(statuses) & SUBMITTED_STATUSES:
            submitted = True
        intents.append({
            "intent_id": iid,
            "final_status": statuses[-1] if statuses else None,
            "statuses": statuses,
            "operations": sorted({_s(r.get("operation")) for r in rows if r.get("operation")}),
            "aggregate_record_id": next(((r.get("aggregate_record_id") or r.get("aggregate_id")) for r in reversed(rows)
                                         if (r.get("aggregate_record_id") or r.get("aggregate_id"))), None),
        })
    if latest_agg is not None and not submitted:
        warnings.append("unsubmitted_final_aggregate")

    return {
        "snapshot_id": snap_id,
        "aggregate": None if latest_agg is None else {
            "record_id": latest_agg.get("record_id"),
            "stage": latest_agg.get("stage"),
            "status": latest_agg.get("status") or latest_agg.get("completion_status"),
            "probability": latest_agg.get("probability"),
            "option_probabilities": latest_agg.get("option_probabilities"),
            "input_attempt_ids": _as_list(latest_agg.get("input_attempt_ids")),
        },
        "players": players,
        "tier1_spread": tier1_spread,
        "tier2_spread": tier2_spread,
        "submission_summary": {"intent_count": len(intents), "submitted": submitted, "intents": intents},
        "health_warnings": sorted(set(warnings)),
    }


# --------------------------------------------------------------------------- shared evaluator
def _evaluate_question(qid: Any, *, data_dir: Path, fetch_post: Optional[FetchPost],
                       now_dt: datetime, min_closed_minutes: float) -> Dict[str, Any]:
    data_dir = Path(data_dir)
    # Pre-read only to locate post_id; authoritative reads happen AFTER fetch_post.
    pre_questions, _ = _read_ledger(data_dir / "questions.jsonl", required=True)
    pre_snap = _latest_snapshot(pre_questions, qid)
    post_id = (pre_snap or {}).get("post_id") or qid

    before = _stat_snapshot(data_dir)
    post, fetch_error = None, None
    if fetch_post is not None:
        try:
            post = fetch_post(post_id)
        except Exception as exc:
            fetch_error = f"fetch_error:{type(exc).__name__}"
    after = _stat_snapshot(data_dir)
    changed = sorted(n for n in set(before) | set(after) if before.get(n) != after.get(n))

    ledgers, errors = _load_ledgers(data_dir)
    snapshot = _latest_snapshot(ledgers["questions.jsonl"], qid)

    g1, r1, g1_info = _gate1(post, snapshot, now_dt, min_closed_minutes, fetch_error)
    g2, r2, by_intent = _gate2(qid, ledgers, errors, changed)
    g3, r3 = _gate3(qid, ledgers, errors, snapshot)
    eligible = bool(g1 and g2 and g3)

    result: Dict[str, Any] = {
        "question_id": qid,
        "post_id": post_id,
        "snapshot_id": (snapshot or {}).get("snapshot_id"),
        "title": (snapshot or {}).get("title"),
        "review_eligible": eligible,
        "masked": not eligible,
        "gates": {
            "gate1_platform_closed": g1,
            "gate2_lifecycle_converged": g2,
            "gate3_not_queued": g3,
        },
        "reasons": {
            "gate1_platform_closed": r1,
            "gate2_lifecycle_converged": r2,
            "gate3_not_queued": r3,
        },
        "platform": g1_info,
        "ledger_errors": dict(errors),
        "concurrent_changes": changed,
        "diagnostics": None,
    }
    if eligible:
        result["diagnostics"] = _build_diagnostics(qid, ledgers, snapshot, by_intent)
    return result


# --------------------------------------------------------------------------- public API
def review_eligible(question_id: Any, *, data_dir: Any = DEFAULT_DATA_DIR,
                    fetch_post: Optional[FetchPost] = None, now: Any = None,
                    min_closed_minutes: float = 15) -> Dict[str, Any]:
    if min_closed_minutes < 0:
        raise ValueError("min_closed_minutes must be >= 0")
    return _evaluate_question(question_id, data_dir=Path(data_dir), fetch_post=fetch_post,
                              now_dt=_coerce_now(now), min_closed_minutes=min_closed_minutes)


def build_post_close_report(*, data_dir: Any = DEFAULT_DATA_DIR, fetch_post: Optional[FetchPost] = None,
                            now: Any = None, min_closed_minutes: float = 15,
                            question_ids: Optional[Iterable[Any]] = None,
                            only_eligible: bool = False) -> Dict[str, Any]:
    if min_closed_minutes < 0:
        raise ValueError("min_closed_minutes must be >= 0")
    data_dir = Path(data_dir)
    now_dt = _coerce_now(now)

    questions, q_err = _read_ledger(data_dir / "questions.jsonl", required=True)
    known: List[Any] = []
    seen = set()
    for r in questions:
        qid = r.get("question_id")
        if qid is not None and str(qid) not in seen:
            seen.add(str(qid))
            known.append(qid)
    if question_ids is not None:
        wanted = [q for q in question_ids]
        by_str = {str(q): q for q in known}
        targets = [by_str.get(str(q), q) for q in wanted]
    else:
        targets = known

    records = []
    for qid in targets:
        rec = _evaluate_question(qid, data_dir=data_dir, fetch_post=fetch_post,
                                 now_dt=now_dt, min_closed_minutes=min_closed_minutes)
        if only_eligible and not rec["review_eligible"]:
            continue
        records.append(rec)

    evaluated = len(targets)
    eligible_n = sum(1 for r in records if r["review_eligible"])
    return {
        "version": VERSION,
        "generated_at": now_dt.isoformat(),
        "data_dir": str(data_dir),
        "live_api": fetch_post is not None,
        "min_closed_minutes": min_closed_minutes,
        "only_eligible": only_eligible,
        "questions_ledger_error": q_err,
        "counts": {"evaluated": evaluated, "eligible": eligible_n,
                   "masked": evaluated - eligible_n if not only_eligible else None},
        "questions": records,
    }


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    if isinstance(v, (list, dict)):
        return json.dumps(v, sort_keys=True, ensure_ascii=False)
    return str(v).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: Dict[str, Any]) -> str:
    out: List[str] = [
        f"# Post-close audit ({report.get('version', VERSION)})",
        "",
        f"- Generated: {report.get('generated_at')}",
        f"- Live API: {report.get('live_api')}  |  min closed minutes: {report.get('min_closed_minutes')}",
        f"- Evaluated: {report.get('counts', {}).get('evaluated')}  |  "
        f"Eligible: {report.get('counts', {}).get('eligible')}",
    ]
    if report.get("questions_ledger_error"):
        out.append(f"- **questions.jsonl error:** {report['questions_ledger_error']}")
    out += ["", "| question_id | eligible | G1 | G2 | G3 | reasons |", "|---|---|---|---|---|---|"]
    for rec in report.get("questions", []):
        g = rec["gates"]
        reasons = [x for rs in rec["reasons"].values() for x in rs]
        out.append(f"| {_fmt(rec['question_id'])} | {rec['review_eligible']} | "
                   f"{g['gate1_platform_closed']} | {g['gate2_lifecycle_converged']} | "
                   f"{g['gate3_not_queued']} | {_fmt(', '.join(reasons) or '')} |")

    for rec in report.get("questions", []):
        if rec.get("masked") or not rec.get("diagnostics"):
            continue
        d = rec["diagnostics"]
        out += ["", f"## Question {rec['question_id']} — {_fmt(rec.get('title'))}",
                f"- Snapshot: `{_fmt(d['snapshot_id'])}`"]
        agg = d.get("aggregate")
        if agg:
            out.append(f"- Aggregate `{_fmt(agg['record_id'])}` stage={_fmt(agg['stage'])} "
                       f"status={_fmt(agg['status'])} p={_fmt(agg['probability'])} "
                       f"options={_fmt(agg['option_probabilities'])} inputs={_fmt(agg['input_attempt_ids'])}")
        out.append(f"- Tier1 spread: {_fmt(d['tier1_spread'])}  |  Tier2 spread: {_fmt(d['tier2_spread'])}")
        ss = d["submission_summary"]
        out.append(f"- Submissions: {ss['intent_count']} intent(s), submitted={ss['submitted']}")
        out += ["", "| player | T1 p | T2 p | changed | change_reason | base_rate | market_refs | flags |",
                "|---|---|---|---|---|---|---|---|"]
        for p, tiers in d["players"].items():
            t1, t2 = tiers.get("tier1") or {}, tiers.get("tier2") or {}
            fin = t2 or t1
            flags = sorted(set(t1.get("audit_flags", []) + t2.get("audit_flags", [])))
            out.append(f"| {_fmt(p)} | {_fmt(t1.get('probability'))} | {_fmt(t2.get('probability'))} | "
                       f"{_fmt(t2.get('changed_from_tier1'))} | {_fmt(t2.get('change_reason'))} | "
                       f"{_fmt(fin.get('base_rate'))} | {_fmt(fin.get('market_refs'))} | {_fmt(flags or None)} |")
        for p, tiers in d["players"].items():
            fin = tiers.get("tier2") or tiers.get("tier1") or {}
            if fin.get("reasoning_summary") or fin.get("key_factors"):
                out.append(f"- **{_fmt(p)}**: {_fmt(fin.get('reasoning_summary'))} "
                           f"(key factors: {_fmt(fin.get('key_factors') or None)})")
        if d["health_warnings"]:
            out += ["", "**Health warnings:** " + ", ".join(f"`{w}`" for w in d["health_warnings"])]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- CLI
def _build_live_fetch(data_dir: Optional[Path] = None) -> FetchPost:
    token = os.environ.get("METACULUS_TOKEN")
    if not token and data_dir is not None and Path(data_dir).resolve() == DEFAULT_DATA_DIR.resolve():
        try:
            from flab.envfile import get_secret
            token = get_secret("METACULUS_TOKEN", _REPO_ROOT / ".env")
        except Exception:
            token = None
    if not token:
        raise RuntimeError("METACULUS_TOKEN is not set (required for --live-api)")
    client_cls = None
    for mod in ("flab.metaculus", "metaculus", "flab.api", "flab.client"):
        try:
            client_cls = getattr(__import__(mod, fromlist=["MetaculusClient"]), "MetaculusClient")
            break
        except (ImportError, AttributeError):
            continue
    if client_cls is None:
        raise RuntimeError("MetaculusClient not importable")
    try:
        from api_backoff import Backoff
        client = client_cls(token, backoff=Backoff(), manage_backoff=False)
    except TypeError:
        client = client_cls(token, manage_backoff=False)  # never writes data/api_cooldowns.jsonl
    fn = getattr(client, "fetch_post", None) or getattr(client, "get_post", None)
    if fn is None:
        raise RuntimeError("MetaculusClient has no fetch_post/get_post")
    return fn


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="post_close_audit", description=f"{VERSION}: read-only post-close audit")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--live-api", action="store_true", help="fetch fresh post status from Metaculus")
    ap.add_argument("--min-closed-minutes", type=float, default=15)
    ap.add_argument("--question-id", action="append", default=None, help="repeatable")
    ap.add_argument("--only-eligible", action="store_true")
    ap.add_argument("--format", choices=("json", "markdown"), default="json")
    args = ap.parse_args(argv)

    if args.min_closed_minutes < 0:
        print("error: --min-closed-minutes must be >= 0", file=sys.stderr)
        return 2
    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        print(f"error: data dir not found: {data_dir}", file=sys.stderr)
        return 2
    fetch = None
    if args.live_api:
        try:
            fetch = _build_live_fetch(data_dir)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    report = build_post_close_report(data_dir=data_dir, fetch_post=fetch,
                                     min_closed_minutes=args.min_closed_minutes,
                                     question_ids=args.question_id, only_eligible=args.only_eligible)
    if args.format == "markdown":
        sys.stdout.write(render_markdown(report))
    else:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
