"""Append-only snapshot queue and bounded campaign registration for continuous_v7."""
from __future__ import annotations

import copy
import threading
from datetime import timedelta
from pathlib import Path

import budget as B
import forecast as F
from flab.config import PLAYERS, load_quota_policy, load_run_config, load_tournaments, load_submit_mode
from flab.ids import hash_obj, new_id
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from m2_campaign import discover_tournaments
from poll_questions import TournamentIndex, latest_snapshots, poll
from project_policy import MEMBERSHIP_UNKNOWN

VERSION = "continuous_v7"
ROLLOVER_VERSION = "weekly_rollover_v1"
STRUCTURAL = ("question_type", "options", "scheduled_close_time", "post_id", "tournament_id")


def register(root: Path, policy: dict, *, now=utc_now):
    """Freeze an explicit budget window and venue selection before polling any outputs."""
    if policy.get("version") not in ("continuous_v2", "continuous_v3", "continuous_v4", "continuous_v5", "continuous_v6", VERSION) or not isinstance(policy.get("campaign_id"), str) or not policy["campaign_id"]:
        raise ValueError("continuous_v2 through continuous_v7 campaign identity required")
    if "rollover" in policy:
        raise ValueError("resolve the rollover template before registering a window")
    start, end = parse_iso(policy["budget_window_start"]), parse_iso(policy["budget_window_end"])
    if not start <= now() < end or end - start > timedelta(days=7):
        raise ValueError("continuous budget window must be active and at most seven days")
    for p in PLAYERS:
        cap = policy.get("weekly_attempt_caps", {}).get(p)
        if type(cap) is not int or cap <= 0:
            raise ValueError("explicit positive weekly attempt caps required")
        configured = load_quota_policy()["players"][p].get("weekly_attempts_cap")
        if configured is not None and cap > configured:
            raise ValueError("continuous cap exceeds quota policy")
    for key in ("expected_new_questions", "repair_reserve", "urgent_slack_s"):
        if type(policy.get(key)) is not int or policy[key] < 0:
            raise ValueError(f"explicit nonnegative {key} required")
    venues = policy.get("venues")
    if not isinstance(venues, list) or not venues or len(set(venues)) != len(venues) \
            or any(v not in ("main", "minibench", "bot_testing_area", "metaculus_cup", "main_site_questions") for v in venues):
        raise ValueError("explicit known venues required")
    if policy.get("run_config_sha256") != hash_obj(load_run_config()) or policy.get("quota_policy_sha256") != hash_obj(load_quota_policy()):
        raise ValueError("continuous policy must pin current configuration")
    with transaction(root):
        rows = read_complete(root / "continuous_campaigns.jsonl")
        prior = [r for r in rows if r["campaign"]["campaign_id"] == policy["campaign_id"]]
        if prior:
            if any(r["sha256"] != hash_obj(policy) for r in prior):
                raise ValueError("registered continuous campaign cannot change")
            return
        # A differently named overlapping window must not reset an exhausted numerical cap.
        if any(start < parse_iso(r["campaign"]["budget_window_end"]) and
               end > parse_iso(r["campaign"]["budget_window_start"]) for r in rows):
            raise ValueError("continuous budget windows must not overlap")
        append_record(root / "continuous_campaigns.jsonl", {"type": "continuous_campaign_registered",
            "campaign": copy.deepcopy(policy), "sha256": hash_obj(policy), "at": to_iso(now())})


def active_campaign(root: Path, template: dict, *, now=utc_now) -> dict:
    """Resolve a finite, immutable weekly schedule without changing caps or history.

    An absent rollover setting preserves the original single-window contract.
    A schedule opts in to N consecutive seven-day UTC windows, all with the same
    pinned configuration, venues and caps. Only the current window is registered;
    missed windows are never run retroactively or accumulated as extra allowance.
    """
    if "rollover" not in template:
        register(root, template, now=now)
        return copy.deepcopy(template)
    setting = template["rollover"]
    if template.get("version") != VERSION or not isinstance(setting, dict) or \
            set(setting) != {"version", "windows"} or setting["version"] != ROLLOVER_VERSION or \
            type(setting["windows"]) is not int or setting["windows"] < 1:
        raise ValueError("explicit finite weekly rollover required")
    identity = template.get("campaign_id")
    if not isinstance(identity, str) or not identity or any(k in template for k in
            ("series_id", "series_sha256", "window_index")):
        raise ValueError("rollover template identity is invalid")
    anchor, first_end = parse_iso(template["budget_window_start"]), parse_iso(template["budget_window_end"])
    week = timedelta(days=7)
    if first_end - anchor != week:
        raise ValueError("rollover anchor window must be exactly seven days")
    observed = now()
    index = (observed - anchor) // week
    if not 0 <= index < setting["windows"]:
        raise ValueError("weekly rollover schedule is inactive")
    digest = hash_obj(template)
    active = copy.deepcopy(template)
    del active["rollover"]
    active.update(campaign_id=f"{identity}:week:{index}", series_id=identity,
        series_sha256=digest, window_index=index,
        budget_window_start=to_iso(anchor + index * week),
        budget_window_end=to_iso(anchor + (index + 1) * week))
    with transaction(root):
        series = read_complete(root / "continuous_series.jsonl")
        own = [r for r in series if r["template"]["campaign_id"] == identity]
        if any(r["sha256"] != digest for r in own):
            raise ValueError("registered rollover template cannot change")
        windows = read_complete(root / "continuous_campaigns.jsonl")
        previous = [r["campaign"] for r in windows if r["campaign"].get("series_id") == identity]
        if any(p["series_sha256"] != digest for p in previous):
            raise ValueError("registered rollover template cannot change")
        if any(p["window_index"] > index for p in previous):
            raise ValueError("rollover clock moved into an earlier window")
        # register() enforces current configuration, caps, and global window
        # non-overlap under this same reentrant transaction. Write the window
        # first: a crash before the template row is safely replayable by its hash.
        register(root, active, now=lambda: observed)
        if not own:
            append_record(root / "continuous_series.jsonl", {"type": "continuous_series_registered",
                "template": copy.deepcopy(template), "sha256": digest, "at": to_iso(observed)})
    return active


def current_tournaments(client, *, configured, now=utc_now):
    """Read-only official discovery shared with the future V15 Transport.

    Return the discovered configuration plus every ongoing/upcoming MiniBench ID
    (also the current alias). No static fallback on any metadata failure.
    """
    cfg = discover_tournaments(client, configured=configured)
    # Discovery resolves identity; authorization always comes from local configuration.
    cfg["tournaments"]["minibench"]["submit_whitelisted"] = configured["tournaments"]["minibench"].get("submit_whitelisted") is True
    mini = client.get("projects/minibenches/")
    if not isinstance(mini, list) or not mini or sum(
            isinstance(r, dict) and r.get("slug") == "minibench" and
            r.get("id") == cfg["tournaments"]["minibench"]["id"] for r in mini) != 1 \
            or sum(isinstance(r, dict) and r.get("slug") == "minibench" for r in mini) != 1:
        raise F.PreflightError("MiniBench index unavailable or inconsistent")
    exclusions = {cfg["tournaments"][role]["id"] for role in ("main", "minibench")}
    seen, ongoing = set(), set()
    observed_at = now()
    for r in mini:
        if not isinstance(r, dict) or type(r.get("id")) is not int or r["id"] <= 0 or \
                type(r.get("is_ongoing")) is not bool or r["id"] in seen or (not r["is_ongoing"] and not r.get("start_date")):
            raise F.PreflightError("incomplete MiniBench metadata")
        seen.add(r["id"])
        # The API can mark the next round ongoing before its start date. It must
        # remain excluded from trial, but cannot authorize live submissions yet.
        starts_in_future = bool(r.get("start_date")) and parse_iso(r["start_date"]) > observed_at
        if r["is_ongoing"] and not starts_in_future:
            ongoing.add(r["id"])
        if r["is_ongoing"] or starts_in_future:
            exclusions.add(r["id"])
    if any(type(v) is not int or v <= 0 for v in exclusions):
        raise F.PreflightError("main and MiniBench exclusion IDs required")
    return {**cfg, "excluded_project_ids": sorted(exclusions), "ongoing_minibench_ids": sorted(ongoing)}


class Poller:
    """Use the existing poller, with current tournament metadata and trial copy exclusions."""
    def __init__(self, client, root: Path, policy: dict, *, now=utc_now):
        self.client, self.root, self.policy, self.now = client, root, policy, now
        self.records = []

    def __call__(self):
        cfg = load_tournaments()
        mode = load_submit_mode()["mode"]
        if mode != "live" or "minibench" in self.policy["venues"]:
            cfg = current_tournaments(self.client, configured=cfg, now=self.now)
        entries = cfg["tournaments"]
        venues = self.policy["venues"]
        for v in venues:
            if mode == "live":
                if not entries.get(v, {}).get("submit_whitelisted"):
                    raise F.PreflightError("continuous venue is not live-whitelisted")
            elif v not in cfg["trial_ground_types"]:
                raise F.PreflightError("only trial grounds may be forecast in local/trial mode")
            if type(entries.get(v, {}).get("id")) is not int:
                raise F.PreflightError("continuous venue has no confirmed numeric ID")
        index = None
        records = []
        if mode != "live":
            exclusions = cfg["excluded_project_ids"]
            records = [poll(self.client, v, metadata_only=True, poll_log=self.root / "poll_log.jsonl")
                       for v in sorted(set(exclusions))]
            index = TournamentIndex.from_polls(records)
        current = {}
        targets = [(v, pid) for v in sorted(venues, key=lambda v: (v == "minibench", v))
                   for pid in (cfg["ongoing_minibench_ids"] if mode == "live" and v == "minibench"
                               else [entries[v]["id"]])]
        for venue, project in targets:
            rec = poll(self.client, project, questions_log=self.root / "questions.jsonl",
                       poll_log=self.root / "poll_log.jsonl", exclude=index, seen_question_ids=set(current),
                       trial_bot_project_id=entries["bot_testing_area"]["id"] if mode != "live" else None)
            records.append(rec)
            if any(r["reason"] == MEMBERSHIP_UNKNOWN for r in rec["skipped"]):
                raise F.PreflightError("polled question project membership is unverified")
            stored = latest_snapshots(self.root / "questions.jsonl")
            skipped = {r.get("question_id") for r in rec["skipped"]}
            for timing in rec["timing"]:
                qid = timing["question_id"]
                if qid in skipped or qid not in stored:
                    continue
                snap = stored[qid]
                if project not in snap.get("project_ids", []) or snap["tournament_id"] != project:
                    raise F.PreflightError("polled question project membership is unverified")
                if timing["status"] == "open":
                    current[qid] = {**snap, "status": "open", "ground": venue,
                                    "priority": "minibench" if venue == "minibench" else "main"}
        self.records.extend(records)
        return current


def committed_questions(root: Path) -> set[int]:
    """Freeze questions with any intent that submit.py has not abandoned."""
    rows = read_complete(root / "submissions.jsonl")
    abandoned = {r["intent_id"] for r in rows if r.get("status") == "abandoned" and r.get("intent_id")}
    return {r["question_id"] for r in rows if r.get("status") == "intent"
            and r.get("question_id") is not None and r.get("intent_id") not in abandoned}


class Queue:
    refresh_after_job = True

    def __init__(self, root: Path, campaign: dict, poller, *, now=utc_now):
        self.root, self.campaign, self.poller, self.now = root, campaign, poller, now
        self.lock = threading.RLock()
        self.cached = None
        self.checked = None
        self.new, self.revised = set(), set()
        self.fallbacks = {}
        self.invalidated = set()

    def __call__(self, *, force=False):
        with self.lock:
            if not force and self.cached is not None and (self.now() - self.checked).total_seconds() < 30:
                return copy.deepcopy(self.cached)
            current = self.poller()  # Never use stale cached safety data on a failed refresh.
            result = self.update(current)
            self.cached, self.checked = result, self.now()
            self.campaign["questions"] = [{"question_id": q, "ground": s["ground"], "priority": s["priority"]}
                                          for q, s in result.items()]
            return copy.deepcopy(result)

    def update(self, current):
        with transaction(self.root):
            events = read_complete(self.root / "question_queue.jsonl")
            states = {r["question_id"]: r for r in events if r["type"] == "queue_state"}
            self.invalidated = {r["old_snapshot_id"] for r in events if r["type"] == "snapshot_invalidated"}
            stored = {r["snapshot_id"]: r for r in read_complete(self.root / "questions.jsonl")}
            attempts = read_complete(self.root / "forecasts.jsonl")
            committed = committed_questions(self.root)
            submissions = read_complete(self.root / "submissions.jsonl")
            started_or_abandoned = {r.get("intent_id") for r in submissions
                                    if r.get("status") in ("request_started", "abandoned")}
            pending = [r for r in submissions if r.get("status") == "intent"
                       and r.get("intent_id") not in started_or_abandoned]
            cfg = load_run_config()
            result, self.fallbacks = {}, {}
            for qid, snap in current.items():
                previous = states.get(qid)
                old = stored.get((previous or {}).get("bound_snapshot_id"))
                if previous and previous["action"] == "already_submitted":
                    # The frozen state has no bound snapshot. Recover its last binding
                    # so abandoning the intent can still invalidate the original offer.
                    prior_bound = next((r.get("bound_snapshot_id") for r in reversed(events)
                                        if r["type"] == "queue_state" and r["question_id"] == qid
                                        and r.get("bound_snapshot_id")), None)
                    old = stored.get(prior_bound)
                changed = previous is not None and previous["observed_snapshot_id"] != snap["snapshot_id"]
                action, bound = (previous["action"], old) if previous else ("queued", snap)
                if previous is None:
                    self.new.add(qid)
                if changed:
                    self.revised.add(qid)

                def revision_action(old):
                    deadline = F.decision_deadline(snap, cfg)
                    estimate = max(B.duration_estimate(attempts, p, 1, cfg) for p in PLAYERS)
                    can_rerun = snap.get("supported") and deadline is not None and (deadline - self.now()).total_seconds() > estimate
                    structural = old is None or any(old.get(k) != snap.get(k) for k in STRUCTURAL)
                    if can_rerun:
                        return "requeued"
                    if (structural or (old is not None and old["snapshot_id"] in self.invalidated)
                          or (previous and previous["action"] == "blocked_revision")):
                        return "blocked_revision"
                    return "text_fallback"

                if qid in committed:
                    # Keep the intent frozen until submit records abandoned, but let
                    # revisions invalidate inputs whose requests have never started.
                    for intent in pending:
                        frozen = stored.get(intent.get("question_snapshot_id"))
                        if (intent.get("question_id") != qid or frozen is None
                                or frozen["snapshot_id"] == snap["snapshot_id"]
                                or frozen["snapshot_id"] in self.invalidated):
                            continue
                        reason = revision_action(frozen)
                        if reason != "text_fallback":
                            append_record(self.root / "question_queue.jsonl", {"type": "snapshot_invalidated",
                                "question_id": qid, "old_snapshot_id": frozen["snapshot_id"],
                                "new_snapshot_id": snap["snapshot_id"], "reason": reason,
                                "at": to_iso(self.now()), "version": VERSION})
                            self.invalidated.add(frozen["snapshot_id"])
                    action, bound = "already_submitted", None
                elif changed or (previous and previous["action"] == "already_submitted"):
                    action = revision_action(old)
                    bound = snap if action == "requeued" else old if action == "text_fallback" else None
                    if (action in ("requeued", "blocked_revision") and old is not None
                            and old["snapshot_id"] != snap["snapshot_id"]
                            and old["snapshot_id"] not in self.invalidated):
                        append_record(self.root / "question_queue.jsonl", {"type": "snapshot_invalidated",
                            "question_id": qid, "old_snapshot_id": old["snapshot_id"],
                            "new_snapshot_id": snap["snapshot_id"], "reason": action, "at": to_iso(self.now()), "version": VERSION})
                        self.invalidated.add(old["snapshot_id"])
                if not snap.get("supported") and action != "already_submitted":
                    action, bound = "unsupported_type", None
                if previous is None or changed or previous["action"] != action:
                    append_record(self.root / "question_queue.jsonl", {"type": "queue_state", "version": VERSION,
                        "question_id": qid, "observed_snapshot_id": snap["snapshot_id"],
                        "bound_snapshot_id": bound["snapshot_id"] if bound else None,
                        "action": action, "at": to_iso(self.now())})
                if bound is not None and action not in ("blocked_revision", "already_submitted", "unsupported_type"):
                    item = {**bound, "forecast_scope": "snapshot", "ground": snap["ground"], "priority": snap["priority"]}
                    if action == "text_fallback":
                        self.fallbacks[qid] = item
                    else:
                        result[qid] = item
            return result
