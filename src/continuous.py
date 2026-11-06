"""Append-only snapshot queue and bounded campaign registration for continuous_v11."""
from __future__ import annotations

import copy
import discovery as D
import market_policy as MP
import threading
from collections import Counter
from datetime import timedelta
from pathlib import Path

import budget as B
import budget_handoff as H
import forecast as F
from flab.config import PLAYERS, load_quota_policy, load_run_config, load_tournaments, load_submit_mode
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from m2_campaign import discover_tournaments
from poll_questions import (TournamentIndex, latest_snapshots, poll, main_site_pages,
                            snapshot_from_post, is_synced_copy, record_snapshots)
from project_policy import MEMBERSHIP_UNKNOWN, trial_membership_error
from submit import probe_reservations
from submit_contract import own_forecast_records

VERSION = "continuous_v11"
MAIN_SITE_VERSION = "main_site_trial_v1"
# Competition questions are open three hours; a new main-site pick must leave at least
# that window minus the run_config submit margins (20 + 5 minutes) before its deadline.
MIN_SELECTION_LEAD = timedelta(minutes=150)
ROLLOVER_VERSION = "weekly_rollover_v1"
import numeric as N
from question_types import is_supported, supported_types

STRUCTURAL = ("question_type", "options", "scheduled_close_time", "post_id", "tournament_id", "group_id")


def validate_main_site_policy(policy):
    if "main_site_questions" not in policy.get("venues", []):
        if "main_site_trial" in policy:
            raise ValueError("main_site_trial requires main_site_questions")
        return
    setting = policy.get("main_site_trial")
    if policy.get("version") not in ("continuous_v8", "continuous_v9", "continuous_v10", VERSION) or not isinstance(setting, dict) or \
            set(setting) != {"version", "max_questions", "max_pages"} or \
            setting["version"] != MAIN_SITE_VERSION or \
            type(setting["max_questions"]) is not int or not 1 <= setting["max_questions"] <= 6 or \
            type(setting["max_pages"]) is not int or not 1 <= setting["max_pages"] <= 5:
        raise ValueError("continuous_v8 requires bounded main_site_trial_v1 policy")


def register(root: Path, policy: dict, *, now=utc_now):
    """Freeze an explicit budget window and venue selection before polling any outputs."""
    if policy.get("version") not in ("continuous_v2", "continuous_v3", "continuous_v4", "continuous_v5", "continuous_v6", "continuous_v7", "continuous_v8", "continuous_v9", "continuous_v10", VERSION) or not isinstance(policy.get("campaign_id"), str) or not policy["campaign_id"]:
        raise ValueError("continuous_v2 through continuous_v11 campaign identity required")
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
            or any(v not in ("main", "minibench", "bot_testing_area", "metaculus_cup", "main_site_questions", "market_pulse") for v in venues):
        raise ValueError("explicit known venues required")
    validate_main_site_policy(policy)
    D.validate(policy)
    MP.validate(policy)
    if policy.get("run_config_sha256") != hash_obj(load_run_config()) or policy.get("quota_policy_sha256") != hash_obj(load_quota_policy()):
        raise ValueError("continuous policy must pin current configuration")
    with transaction(root):
        rows = read_complete(root / "continuous_campaigns.jsonl")
        if H.superseded(policy["campaign_id"], rows):
            raise ValueError("campaign has been superseded")
        source = H.source(policy, rows) if "budget_handoff" in policy else None
        allowed_overlap = H.overlap_ids(policy, rows) if source is not None else set()
        prior = [r for r in rows if r["campaign"]["campaign_id"] == policy["campaign_id"]]
        if prior:
            if any(r["sha256"] != hash_obj(policy) for r in prior):
                raise ValueError("registered continuous campaign cannot change")
            return
        if source is not None and not parse_iso(source["budget_window_start"]) <= now() < parse_iso(source["budget_window_end"]):
            raise ValueError("handoff source must still be active at first registration")
        # A differently named overlapping window must not reset an exhausted numerical cap.
        if any(start < parse_iso(r["campaign"]["budget_window_end"]) and
               end > parse_iso(r["campaign"]["budget_window_start"]) and
               r["campaign"]["campaign_id"] not in allowed_overlap
               for r in rows):
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
    if "budget_handoff" in template:
        raise ValueError("budget handoff cannot roll over")
    setting = template["rollover"]
    if template.get("version") not in ("continuous_v7", "continuous_v8", "continuous_v9", "continuous_v10", VERSION) or not isinstance(setting, dict) or \
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
    if cfg['tournaments'].get('market_pulse',{}).get('id') is not None:
        exclusions.add(cfg['tournaments']['market_pulse']['id'])
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
        self.client, self.root, self.policy, self.now = client, root, copy.deepcopy({k:v for k,v in policy.items() if k != 'questions'}), now
        self.records = []

    def __call__(self):
        D.validate(self.policy)
        if 'discovery' not in self.policy:
            return self._poll_all()
        prior = D.latest(self.root, self.policy)
        if D.due(prior, self.now()):
            at = self.now()
            self.discovery_records = []
            previously_known = set(latest_snapshots(self.root/'questions.jsonl'))
            result = self._poll_all()
            D.record(self.root, self.policy, self.discovery_records, now=at, prior=prior, previously_known=previously_known)
            return result
        return self._refresh_known(prior)

    def _refresh_known(self, state):
        from poll_questions import snapshots_from_post
        cfg = load_tournaments()
        if load_submit_mode()['mode'] != 'live':
            raise F.PreflightError('cadenced discovery requires live authorization')
        MP.validate(self.policy)
        if 'market_pulse' in self.policy['venues'] and any(cfg['tournaments'].get('market_pulse',{}).get(k)!=v
                for k,v in {'id':MP.PROJECT_ID,'slug':MP.SETTING['slug']}.items()):
            raise F.PreflightError('Market Pulse identity differs from approved policy')
        selected, posts, accepted = {}, {}, []
        seen = set()
        committed = committed_questions(self.root)
        for entry in state['known']:
            role, project, qid = entry['ground'], entry['tournament_id'], entry['question_id']
            if qid in committed or qid in seen:
                continue
            seen.add(qid)
            if role not in self.policy['venues'] or not cfg['tournaments'].get(role, {}).get('submit_whitelisted'):
                raise F.PreflightError('known venue is no longer approved')
            if entry.get('status') != 'open':
                continue
            close = entry.get('scheduled_close_time')
            if close and self.now() >= parse_iso(close):
                continue
            post_id = entry['post_id']
            if post_id not in posts:
                posts[post_id] = self.client.get_post(post_id)
            snaps = snapshots_from_post(posts[post_id], project, allow_groups=role == 'market_pulse')
            matches = [s for s in snaps if s['question_id'] == qid]
            if len(matches) != 1:
                raise F.PreflightError('known question mapping changed')
            snap = matches[0]
            if project not in snap['project_ids'] or snap['post_id'] != post_id:
                raise F.PreflightError('known project membership changed')
            snap['tournament_id'] = project
            accepted.append((snap, role))
        record_snapshots([s for s, _ in accepted], log=self.root/'questions.jsonl', now=to_iso(self.now()))
        stored = latest_snapshots(self.root/'questions.jsonl')
        for snap, role in accepted:
            if snap['status'] == 'open':
                qid = snap['question_id']
                selected[qid] = {**stored[qid], 'status': 'open', 'ground': role,
                                 'priority': role if role in ('minibench', 'market_pulse') else 'main'}
        return selected

    def _poll_all(self):
        validate_main_site_policy(self.policy)
        MP.validate(self.policy)
        cfg = load_tournaments()
        mode = load_submit_mode()["mode"]
        if mode != "live" or "minibench" in self.policy["venues"]:
            cfg = current_tournaments(self.client, configured=cfg, now=self.now)
        entries = cfg["tournaments"]
        if 'market_pulse' in self.policy['venues'] and any(entries.get('market_pulse',{}).get(k)!=v
                for k,v in {'id':MP.PROJECT_ID,'slug':MP.SETTING['slug']}.items()):
            raise F.PreflightError('Market Pulse identity differs from approved policy')
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
        accepted_ids = set()
        targets = [(v, pid) for v in sorted(venues, key=lambda v: ({"main": 0, "minibench": 1, "market_pulse": 2}.get(v, 0), v))
                   for pid in (cfg["ongoing_minibench_ids"] if mode == "live" and v == "minibench"
                               else [entries[v]["id"]])]
        for venue, project in targets:
            if venue == "main_site_questions" and mode in ("local", "trial"):
                # The CLI passes the original template, so resolve the window here
                # too. The ledger must use the week ID, never the series ID.
                campaign = active_campaign(self.root, self.policy, now=self.now)
                selected, rec = self._main_site(campaign, project, entries["bot_testing_area"]["id"],
                                                index, set(current))
                records.append(rec)
                current.update(selected)
                continue
            rec = poll(self.client, project, questions_log=self.root / "questions.jsonl",
                       poll_log=self.root / "poll_log.jsonl", exclude=index, seen_question_ids=accepted_ids,
                       trial_bot_project_id=entries["bot_testing_area"]["id"] if mode != "live" else None,
                       statuses=['open', 'upcoming'] if 'discovery' in self.policy else None,
                       allow_groups=venue == 'market_pulse')
            records.append(rec)
            if 'discovery' in self.policy:
                self.discovery_records.append((venue, rec))
            if any(r["reason"] == MEMBERSHIP_UNKNOWN for r in rec["skipped"]):
                raise F.PreflightError("polled question project membership is unverified")
            stored = latest_snapshots(self.root / "questions.jsonl")
            skipped = {r.get("question_id") for r in rec["skipped"]}
            accepted_ids.update(t["question_id"] for t in rec["timing"] if t["question_id"] not in skipped)
            for timing in rec["timing"]:
                qid = timing["question_id"]
                if qid in skipped or qid not in stored:
                    continue
                snap = stored[qid]
                if project not in snap.get("project_ids", []) or snap["tournament_id"] != project:
                    raise F.PreflightError("polled question project membership is unverified")
                if timing["status"] == "open":
                    current[qid] = {**snap, "status": "open", "ground": venue,
                                    "priority": venue if venue in ("minibench", "market_pulse") else "main"}
        self.records.extend(records)
        return current

    def _main_site(self, campaign, project, bot_project, index, seen):
        path = self.root / "main_site_trial_selections.jsonl"
        limit = campaign["main_site_trial"]["max_questions"]
        identity = campaign["campaign_id"]
        with transaction(self.root):
            before = read_complete(path)
        selected = {r["question_id"]: r for r in before
                    if r["type"] == "main_site_trial_selected" and r["campaign_id"] == identity}
        dropped = {r["question_id"] for r in before
                   if r["type"] == "main_site_trial_dropped" and r["campaign_id"] == identity}
        if len(selected) > limit:
            raise F.PreflightError("main-site selection limit exceeded")
        # Network work stays outside the ledger lock. A concurrent selection
        # invalidates this observation and is retried by the next cycle.
        refreshed = {qid: self.client.get_post(row["post_id"]) for qid, row in selected.items()
                     if qid not in dropped}
        pages = []
        if len(selected) < limit and not dropped:
            try:
                pages = list(main_site_pages(self.client, max_pages=campaign["main_site_trial"]["max_pages"],
                                             validate_next_at_limit=True))
            except SystemExit:
                raise F.PreflightError("invalid main-site pagination") from None
        with transaction(self.root):
            if read_complete(path) != before:
                raise F.PreflightError("main-site selections changed during refresh")
            observed = self.now()
            if not parse_iso(campaign["budget_window_start"]) <= observed < parse_iso(campaign["budget_window_end"]):
                raise F.PreflightError("main-site window is inactive")
            cfg = load_run_config()
            previous = {r.get("question_id") for name in
                        ("forecasts.jsonl", "forecast_attempt_starts.jsonl", "submissions.jsonl")
                        for r in read_complete(self.root / name)}
            previous.update(q["question_id"] for r in read_complete(self.root / "campaigns.jsonl")
                            for q in r.get("campaign", {}).get("questions", []))
            previous.update(qid for _, qid, _ in probe_reservations(self.root))
            previous.update(r["question_id"] for r in before)
            reasons, valid, pool = Counter(), {}, {}
            newly_dropped = {}
            for qid, post in refreshed.items():
                snap, reason = self._main_site_snapshot(post, project, bot_project, index, cfg, observed)
                if reason is None and (snap["question_id"] != qid or snap["post_id"] != selected[qid]["post_id"]):
                    reason = "selected identity changed"
                if reason:
                    newly_dropped[qid] = reason
                    reasons[reason] += 1
                else:
                    valid[qid] = snap
            for posts in pages:
                for post in posts:
                    snap, reason = self._main_site_snapshot(post, project, bot_project, index, cfg, observed)
                    if reason is None:
                        qid = snap["question_id"]
                        if qid in previous:
                            reason = "previously recorded or reserved"
                        elif qid in seen:
                            reason = "already polled through another allowed venue"
                        elif qid in pool:
                            reason = "duplicate candidate"
                        elif F.decision_deadline(snap, cfg) < observed + MIN_SELECTION_LEAD:
                            # New picks get at least a competition-length window to finish.
                            reason = "decision deadline too soon for a new selection"
                        else:
                            try:
                                if own_forecast_records(post):
                                    reason = "platform forecast already exists"
                            except ValueError:
                                pass  # Missing/unknown history still requires submit's platform check.
                    if reason:
                        reasons[reason] += 1
                    else:
                        pool[qid] = snap
            for qid, reason in newly_dropped.items():
                append_record(path, {"type": "main_site_trial_dropped", "campaign_id": identity,
                    "question_id": qid, "reason": reason, "at": to_iso(observed)})
            rec = {"type": "main_site_trial_poll", "version": MAIN_SITE_VERSION,
                   "campaign_id": identity, "tournament_id": project, "polled_at": to_iso(observed),
                   "pages": len(pages), "n_posts": sum(map(len, pages)), "reasons": dict(reasons)}
            append_record(self.root / "poll_log.jsonl", rec)
            # Unknown membership is never selected (a selected post with unknown membership is
            # dropped above). Skipping, not failing the cycle, keeps one unrecognized post in a
            # large main feed from blocking every later cycle; m2_prepare skips the same way.
            # A dropped slot is never reused. Conservatively stop all additions
            # after a drop, even if an earlier scan did not fill the window.
            additions = []
            if not dropped and not newly_dropped:
                ordered = sorted(pool.values(), key=lambda s: (F.decision_deadline(s, cfg), s["question_id"]))
                covered = {r["question_type"] for r in selected.values()}
                for kind in supported_types():
                    if len(selected) + len(additions) < limit and kind not in covered:
                        candidate = next((s for s in ordered if s["question_type"] == kind), None)
                        if candidate is not None:
                            additions.append(candidate)
                for snap in ordered:
                    if len(selected) + len(additions) >= limit:
                        break
                    if snap not in additions:
                        additions.append(snap)
            for snap in additions:
                qid = snap["question_id"]
                append_record(path, {"type": "main_site_trial_selected", "version": MAIN_SITE_VERSION,
                    "campaign_id": identity, "question_id": qid, "post_id": snap["post_id"],
                    "question_type": snap["question_type"],
                    "decision_deadline": to_iso(F.decision_deadline(snap, cfg)), "selected_at": to_iso(observed)})
                valid[qid] = snap
            # Persist reservations before snapshots; a crash between these writes
            # re-fetches the reserved post without consuming another slot.
            record_snapshots((snap for qid, snap in valid.items() if qid not in seen),
                             log=self.root / "questions.jsonl", now=to_iso(observed))
            saved = latest_snapshots(self.root / "questions.jsonl")
            return {qid: {**saved[qid], **snap, "status": "open", "ground": "main_site_questions",
                          "priority": "main"} for qid, snap in valid.items() if qid not in seen}, rec

    @staticmethod
    def _main_site_snapshot(post, project, bot_project, index, cfg, observed):
        """Validate without persisting candidate content or remote error text."""
        if not isinstance(post, dict):
            return None, "unrecognized main-site post"
        reason = trial_membership_error(post, bot_project)
        if reason:
            return None, reason
        if any(post.get(k) is not None for k in ("group_of_questions", "conditional", "notebook")):
            return None, "not a single-question post"
        snap = snapshot_from_post(post, "main_site_questions")
        if snap is None or any(type(snap[k]) is not int or snap[k] <= 0 for k in ("question_id", "post_id")):
            return None, "invalid single-question identity"
        if not is_supported(snap["question_type"]):
            return None, "unsupported_type"
        if project not in snap["project_ids"]:
            return None, "post does not belong to main site"
        reason = is_synced_copy(snap, index)
        if reason:
            return None, reason
        if snap["status"] != "open":
            return None, "question is not open"
        try:
            if not snap["open_time"] or parse_iso(snap["open_time"]) > observed:
                return None, "open time missing or in the future"
            reason = F.eligible(snap, cfg, observed)
            if reason or F.decision_deadline(snap, cfg) is None:
                return None, reason or "decision deadline unavailable"
        except (TypeError, ValueError):
            return None, "invalid question timing"
        snap["tournament_id"] = project
        return snap, None


def committed_questions(root: Path) -> set[int]:
    """Freeze questions with any intent that submit.py has not abandoned."""
    rows = read_complete(root / "submissions.jsonl")
    abandoned = {r["intent_id"] for r in rows if r.get("status") == "abandoned" and r.get("intent_id")}
    return {r["question_id"] for r in rows if r.get("status") == "intent"
            and r.get("question_id") is not None and r.get("intent_id") not in abandoned}


class Queue:
    refresh_after_job = True
    refresh_version = "queue_refresh_v2"

    def __init__(self, root: Path, campaign: dict, poller, *, now=utc_now):
        self.root, self.campaign, self.poller, self.now = root, campaign, poller, now
        self.lock = threading.RLock()
        self.cached = None
        self._refresh_generation = 0
        self.checked = None
        self.new, self.revised = set(), set()
        self.fallbacks = {}
        self.invalidated = set()

    def __call__(self, *, force=False):
        generation = self._refresh_generation
        with self.lock:
            # A caller queued behind a successful refresh shares that fresh result,
            # even when forced. Sequential force calls still perform a new refresh.
            # This avoids serial network scans starving pre-CLI quota admission.
            fresh = self.cached is not None and 0 <= (self.now() - self.checked).total_seconds() < 30
            if fresh and (not force or generation != self._refresh_generation):
                return copy.deepcopy(self.cached)
            self.cached = None  # A failed forced refresh must invalidate the older cache.
            current = self.poller()  # Never use stale cached safety data on a failed refresh.
            result = self.update(current)
            self.campaign["questions"] = [{"question_id": q, "ground": s["ground"], "priority": s["priority"]}
                                          for q, s in result.items()]
            self.cached, self.checked = result, self.now()
            self._refresh_generation += 1
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
                    can_rerun = F.eligible(snap, cfg, self.now()) is None and deadline is not None and (deadline - self.now()).total_seconds() > estimate
                    structural = old is None or any(old.get(k) != snap.get(k) for k in STRUCTURAL) or N.scaling_changed(old, snap)
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
                if (is_supported(snap.get("question_type")) and previous
                        and previous["action"] == "unsupported_type" and not changed
                        and F.eligible(snap, cfg, self.now()) is None):
                    action, bound = "queued", snap
                if not is_supported(snap.get("question_type")) and action != "already_submitted":
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
