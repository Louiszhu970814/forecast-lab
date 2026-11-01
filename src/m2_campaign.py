"""Fresh read-only test-ground snapshots for a frozen local M2 campaign.

The CLI requires current numeric main/MiniBench IDs in tournament config. Never infer safety
from a question's user-supplied ground label or from an old question snapshot alone.
"""
from __future__ import annotations

import copy
import threading
import time
from pathlib import Path

import forecast as F
from flab.config import load_tournaments
from flab.jsonl import append_record
from flab.ledger import transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from poll_questions import (TournamentIndex, is_synced_copy, latest_snapshots, poll,
                            record_snapshots, snapshot_from_post)

VERSION = "m2_safety_v1"


def discover_tournaments(client, *, configured=None):
    """Resolve approved trial-ground *types* to current API metadata, without editing the
    Louis-approved tournament/submit whitelist config. Returned data is read-only runtime state.
    """
    cfg = copy.deepcopy(configured if configured is not None else load_tournaments())
    mini = client.get("projects/tournaments/minibench/")
    cups = client.get("projects/tournaments/")
    main_site = client.get("projects/site_main/")
    if not isinstance(cups, list):
        raise F.PreflightError("unrecognized tournament metadata")
    active = [r for r in cups if r.get("is_ongoing") is True and r.get("name", "").startswith("Metaculus Cup ")]
    if not active:
        raise F.PreflightError("no current Metaculus Cup was found")
    cup = max(active, key=lambda r: parse_iso(r["start_date"]))
    if mini.get("slug") != "minibench" or main_site.get("type") != "site_main":
        raise F.PreflightError("official project metadata did not match the requested ground")
    for name, rec in (("minibench", mini), ("metaculus_cup", cup), ("main_site_questions", main_site)):
        if type(rec.get("id")) is not int or rec["id"] <= 0:
            raise F.PreflightError("missing official project ID")
        cfg["tournaments"][name] = {"id": rec["id"], "slug": rec.get("slug"), "submit_whitelisted": False}
    return cfg


class SafeSnapshots:
    def __init__(self, client, root: Path, campaign: dict, *, tournaments=None, monotonic=time.monotonic):
        self.client, self.root, self.campaign = client, root, campaign
        self.tournaments = tournaments if tournaments is not None else discover_tournaments(client)
        self.clock = monotonic
        self.lock = threading.RLock()
        self.cached, self.checked = {}, None

    def __call__(self, *, force=False):
        with self.lock:
            if not force and self.checked is not None and self.clock() - self.checked < 30:
                return copy.deepcopy(self.cached)
            configured = self.tournaments.get("tournaments", {})
            exclusions = [configured.get(role, {}).get("id") for role in ("main", "minibench")]
            if any(type(v) is not int or v <= 0 for v in exclusions):
                raise F.PreflightError("M2 safety needs confirmed current numeric main and MiniBench IDs")
            # The `minibench` alias can already point to the next round while the previous
            # round is still ongoing. Resolve every ongoing/upcoming round from project metadata.
            mini = self.client.get("projects/minibenches/")
            if not isinstance(mini, list) or not mini or not any(r.get("slug") == "minibench" for r in mini):
                raise F.PreflightError("MiniBench project index is unavailable or unrecognized")
            for r in mini:
                if type(r.get("id")) is not int or not isinstance(r.get("is_ongoing"), bool):
                    raise F.PreflightError("MiniBench project index has incomplete metadata")
                if r["is_ongoing"] or (r.get("start_date") and parse_iso(r["start_date"]) > utc_now()):
                    exclusions.append(r["id"])
            exclusions = sorted(set(exclusions))
            polls = [poll(self.client, venue, metadata_only=True, poll_log=self.root / "poll_log.jsonl")
                     for venue in exclusions]
            index = TournamentIndex.from_polls(polls)
            current, skipped = [], []
            for entry in self.campaign["questions"]:
                if type(entry.get("post_id")) is not int or entry["post_id"] <= 0:
                    raise F.PreflightError("each M2 question needs its independently checked post ID")
                post = self.client.get_post(entry["post_id"])
                snap = snapshot_from_post(post, entry["ground"])
                if snap is None or snap["question_id"] != entry["question_id"] or snap["post_id"] != entry["post_id"]:
                    raise F.PreflightError("M2 post/question ID mapping changed")
                ground = entry["ground"]
                project = configured.get(ground, {}).get("id")
                if type(project) is not int or project <= 0 or project not in snap["project_ids"]:
                    raise F.PreflightError("M2 test-ground project membership has not been verified")
                snap["tournament_id"] = project
                reason = is_synced_copy(snap, index)
                if reason or not snap["supported"] or snap["status"] != "open":
                    skipped.append({"question_id": snap["question_id"], "reason": reason or "closed_or_unsupported"})
                else:
                    current.append(snap)
            with transaction(self.root):
                record_snapshots(current, log=self.root / "questions.jsonl")
                stored = latest_snapshots(self.root / "questions.jsonl")
                result = {s["question_id"]: {**stored[s["question_id"]], **s} for s in current}
                append_record(self.root / "campaign_safety.jsonl", {"type": "campaign_safety_check",
                    "version": VERSION, "campaign_id": self.campaign["campaign_id"], "checked_at": to_iso(utc_now()),
                    "exclusion_poll_ids": [r["record_id"] for r in polls], "excluded_project_ids": exclusions,
                    "safe_snapshot_ids": {str(qid): s["snapshot_id"] for qid, s in result.items()}, "skipped": skipped})
            self.cached, self.checked = result, self.clock()
            return copy.deepcopy(result)
