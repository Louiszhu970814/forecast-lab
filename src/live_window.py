"""Finite live authorization, checked again before each network write (D117)."""
from __future__ import annotations

import copy
import fcntl
import json
import os
from datetime import timedelta
from pathlib import Path

from flab.config import load_quota_policy, load_run_config
from flab.ids import hash_obj, new_id
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso

VERSION = "live_window_v1"


def bounds(mode, campaign, config_dir: Path):
    """Validate the approved immutable campaign; do not register or start it."""
    window = mode.get("live_window")
    if mode.get("mode") != "live" or not isinstance(window, dict) or set(window) != {
            "version", "campaign_id", "campaign_sha256", "start", "end"}:
        raise ValueError("finite live window required")
    if window["version"] != VERSION or type(mode.get("approved_bot_id")) is not int \
            or mode["approved_bot_id"] <= 0 or "rollover" in campaign:
        raise ValueError("invalid finite live authorization")
    start, end = parse_iso(window["start"]), parse_iso(window["end"])
    if not timedelta(0) < end - start <= timedelta(days=7):
        raise ValueError("live window must be positive and at most seven days")
    if window["campaign_id"] != campaign.get("campaign_id") \
            or window["campaign_sha256"] != hash_obj(campaign) \
            or campaign.get("budget_window_start") != window["start"] \
            or campaign.get("budget_window_end") != window["end"]:
        raise ValueError("live campaign differs from approval")
    if campaign.get("run_config_sha256") != hash_obj(load_run_config(config_dir)) \
            or campaign.get("quota_policy_sha256") != hash_obj(load_quota_policy(config_dir)):
        raise ValueError("live configuration differs from approved campaign")
    return start, end


def require_active(mode, campaign, config_dir: Path, now):
    start, end = bounds(mode, campaign, config_dir)
    if not start <= now < end:
        raise ValueError("outside approved live window")
    return hash_obj(mode["live_window"])


RENEWAL_VERSION = "live_window_renewal_v1"
RENEWAL_AUTHORIZATION = "PLAN D149"


def _write(path: Path, obj) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{new_id('tmp')}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _renewed_campaign(previous_campaign: dict, end, now, config_dir: Path) -> dict:
    week = timedelta(days=7)
    start = end + ((now - end) // week) * week  # keep the weekly boundary; skipped weeks are not replayed
    campaign = {k: v for k, v in previous_campaign.items() if k != "budget_handoff"}
    campaign.update(campaign_id="auto_" + start.strftime("%Y%m%dT%H%MZ"),
                    budget_window_start=to_iso(start), budget_window_end=to_iso(start + week),
                    run_config_sha256=hash_obj(load_run_config(config_dir)),
                    quota_policy_sha256=hash_obj(load_quota_policy(config_dir)),
                    renewed_from=previous_campaign["campaign_id"],
                    renewal_authorization=RENEWAL_AUTHORIZATION)
    return campaign


def _valid_renewal_from(previous_campaign: dict, end, candidate: dict, config_dir: Path) -> bool:
    try:
        cand_start = parse_iso(candidate.get("budget_window_start", ""))
    except (TypeError, ValueError):
        return False
    return cand_start >= end and candidate == _renewed_campaign(previous_campaign, end, cand_start, config_dir)


def _verify_bot_id(mode: dict, previous_window: dict, previous_campaign: dict, config_dir: Path, schedule_log: Path | None) -> None:
    bot_id = mode.get("approved_bot_id")
    if "approved_bot_id" in previous_campaign and previous_campaign["approved_bot_id"] != bot_id:
        raise ValueError("live campaign differs from approval")
    data_dir = schedule_log.parent if schedule_log is not None else config_dir.parent / "data"
    win_sha = hash_obj(previous_window)
    for r in read_complete(data_dir / "submissions.jsonl"):
        if r.get("status") == "intent" and r.get("live_window_sha256") == win_sha and r.get("bot_id") != bot_id:
            raise ValueError("live campaign differs from approval")
    log_path = schedule_log if schedule_log is not None else data_dir / "live_schedule.jsonl"
    for r in read_complete(log_path):
        if r.get("type") == "live_window_renewed" and "approved_bot_id" in r and r["approved_bot_id"] != bot_id:
            raise ValueError("live campaign differs from approval")


def renew(config_dir: Path, now, *, schedule_log: Path | None = None):
    """D149: an ended live window is followed by the next seven-day window.

    Venues, bot account and weekly caps are copied unchanged after verifying that the
    expiring window and its pinned campaign/config hashes are still valid. Only acts in
    live mode after the approved window has ended, so local/trial modes and Louis's
    switches (STOP_SUBMIT, players_enabled) are untouched. Serialized by a file lock and
    deduplicated by campaign_id so concurrent callers or a crash between the two writes
    never emit duplicate renewal records. Returns the renewal event, or None when nothing
    was due."""
    from flab.config import load_submit_mode
    config_dir.mkdir(parents=True, exist_ok=True)
    pending_path = config_dir / ".live_window_pending.json"
    with (config_dir / ".live_window.lock").open("a") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            mode = load_submit_mode(config_dir)
            window = mode.get("live_window")
            if mode.get("mode") != "live" or not isinstance(window, dict):
                return None
            campaign = campaign_from(config_dir)
            pending = json.loads(pending_path.read_text()) if pending_path.exists() else None
            if isinstance(pending, dict) and isinstance(pending.get("previous_window"), dict) \
                    and isinstance(pending.get("previous_campaign"), dict) and isinstance(pending.get("campaign"), dict):
                # Crash recovery: on-disk files must match either the validated pre-renewal state
                # or the partially/fully written renewed state, and the expiring approval must pass bounds().
                if pending.get("approved_bot_id") != mode.get("approved_bot_id"):
                    raise ValueError("live campaign differs from approval")
                _verify_bot_id(mode, pending["previous_window"], pending["previous_campaign"], config_dir, schedule_log)
                prev_mode = {**mode, "live_window": pending["previous_window"]}
                _, end = bounds(prev_mode, pending["previous_campaign"], config_dir)
                if not _valid_renewal_from(pending["previous_campaign"], end, pending["campaign"], config_dir) \
                        or pending.get("previous_campaign_id") != pending["previous_campaign"]["campaign_id"]:
                    raise ValueError("live campaign differs from approval")
                if campaign != pending["previous_campaign"] \
                        and not _valid_renewal_from(pending["previous_campaign"], end, campaign, config_dir):
                    raise ValueError("live campaign differs from approval")
                if window != pending["previous_window"]:
                    win_start = parse_iso(window.get("start", ""))
                    if win_start < end:
                        raise ValueError("live campaign differs from approval")
                    win_campaign = _renewed_campaign(pending["previous_campaign"], end, win_start, config_dir)
                    bounds(mode, win_campaign, config_dir)
                campaign = pending["campaign"]
                previous = pending["previous_campaign_id"]
                if now < end:
                    return None
                if now >= parse_iso(campaign["budget_window_end"]):
                    campaign = _renewed_campaign(pending["previous_campaign"], end, now, config_dir)
                    _write(pending_path, {**pending, "campaign": campaign})
            else:
                _verify_bot_id(mode, window, campaign, config_dir, schedule_log)
                _, end = bounds(mode, campaign, config_dir)
                if now < end:
                    return None
                previous = campaign["campaign_id"]
                previous_campaign = copy.deepcopy(campaign)
                campaign = _renewed_campaign(previous_campaign, end, now, config_dir)
                _write(pending_path, {"previous_window": window, "previous_campaign_id": previous,
                                      "previous_campaign": previous_campaign, "campaign": campaign,
                                      "approved_bot_id": mode["approved_bot_id"]})
            _write(config_dir / "cycle_campaign.json", campaign)
            mode = {**mode, "live_window": {"version": VERSION, "campaign_id": campaign["campaign_id"],
                                            "campaign_sha256": hash_obj(campaign),
                                            "start": campaign["budget_window_start"], "end": campaign["budget_window_end"]}}
            _write(config_dir / "submit_mode.json", mode)
            event = {"type": "live_window_renewed", "version": RENEWAL_VERSION, "authorization": RENEWAL_AUTHORIZATION,
                     "at": to_iso(now), "previous_campaign_id": previous, "campaign_id": campaign["campaign_id"],
                     "approved_bot_id": mode["approved_bot_id"],
                     "start": campaign["budget_window_start"], "end": campaign["budget_window_end"],
                     "campaign_sha256": hash_obj(campaign)}
            if schedule_log is not None:
                with transaction(schedule_log.parent):
                    if any(r.get("type") == "live_window_renewed" and r.get("campaign_id") == campaign["campaign_id"]
                           for r in read_complete(schedule_log)):
                        pending_path.unlink(missing_ok=True)
                        return None
                    append_record(schedule_log, event)
            pending_path.unlink(missing_ok=True)
            return event
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def campaign_from(config_dir: Path):
    return json.loads((config_dir / "cycle_campaign.json").read_text())
