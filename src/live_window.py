"""Finite live authorization, checked again before each network write (D117)."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from flab.config import load_quota_policy, load_run_config
from flab.ids import hash_obj
from flab.timeutil import parse_iso

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


def campaign_from(config_dir: Path):
    return json.loads((config_dir / "cycle_campaign.json").read_text())
