"""Offline, discrete-event coverage replay (coverage_sim_v2).

Register freezes settings AND schedule bytes as parsed JSON before any results exist.
Only coverage_sims.jsonl / coverage_sim_results.jsonl are read or appended. No model,
transport, credentials, production ledger or wall-clock sleep is used by the simulator.
See docs/coverage_sim.md for the input schema and the deliberately explicit assumptions.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import random
from collections import Counter
from datetime import timedelta
from pathlib import Path

import budget as B
import cycle as C
import forecast as F
import scheduler as S
import validate as V
from flab.config import PLAYERS, load_run_config
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from flab.workdir import PROJECT_ROOT

VERSION = "coverage_sim_v2"
SUPPORTED = ("binary", "multiple_choice")


def _number(value, name, *, minimum=0, maximum=None):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum \
            or (maximum is not None and value > maximum):
        raise ValueError(f"invalid {name}")


def _samples(value, name):
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} needs nonempty duration samples")
    for sample in value:
        _number(sample, name, minimum=.001)


def validate(settings, schedule):
    if settings.get("version") != VERSION or not isinstance(settings.get("sim_id"), str) or not settings["sim_id"]:
        raise ValueError("coverage_sim_v2 and nonempty sim_id required")
    start, end = (parse_iso(settings["observation"][k]) for k in ("start", "end"))
    if start >= end:
        raise ValueError("invalid observation window")
    denominator = settings["denominator"]
    venues, ids = denominator["venues"], denominator["question_ids"]
    if not isinstance(venues, list) or not venues or len(set(venues)) != len(venues) \
            or any(v not in ("main", "minibench") for v in venues):
        raise ValueError("explicit main/minibench venues required")
    if ids != "all" and (not isinstance(ids, list) or not ids or
                        any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != len(ids)):
        raise ValueError("question_ids must be all or distinct positive IDs")
    for field in ("seed", "repetitions", "expected_new_questions", "repair_reserve", "urgent_slack_s"):
        value = settings[field]
        if type(value) is not int or value < (1 if field == "repetitions" else 0):
            raise ValueError(f"invalid {field}")
    _number(settings["escalation_rate"], "escalation_rate", maximum=1)
    for field in ("startup_s", "poll_s"):
        _number(settings[field], field)
    for field in ("comment_s", "precheck_s", "submit_s"):
        _number(settings["submission"][field], field)
    if set(settings["submission"]) != {"comment_s", "precheck_s", "submit_s"}:
        raise ValueError("submission needs exactly comment_s, precheck_s, submit_s")
    if settings.get("submission_policy", "on_finalized") not in ("on_finalized", "cycle_end"):
        raise ValueError("unknown submission_policy")
    for interval in settings.get("submit_busy_intervals", []):
        if parse_iso(interval["start"]) >= parse_iso(interval["end"]):
            raise ValueError("invalid submit busy interval")
    if set(settings["players"]) != set(PLAYERS):
        raise ValueError("exactly three players required")
    for p in PLAYERS:
        line = settings["players"][p]
        for field in ("tier1", "tier2", "repair"):
            _samples(line[field], f"{p}.{field}")
        for field in ("format_repair_rate", "tier1_rerun_rate", "tier2_rerun_rate", "rerun_failure_rate", "repair_failure_rate"):
            _number(line[field], field, maximum=1)
        _number(line["overhead_s"], "overhead_s")
        if type(line["attempt_cap"]) is not int or line["attempt_cap"] < 0:
            raise ValueError("nonnegative attempt_cap required")
        resets = [parse_iso(t) for t in line["reset_times"]]
        if resets != sorted(set(resets)):
            raise ValueError("reset_times must be sorted and distinct")
        for stop in line["stops"]:
            if stop["reason"] not in ("quota_exhausted", "disabled", "preflight_blocked"):
                raise ValueError("unknown stop reason")
            finish = parse_iso(stop["end"]) if stop.get("end") else next(
                (r for r in resets if r > parse_iso(stop["start"])), None)
            if finish is None or finish <= parse_iso(stop["start"]):
                raise ValueError("stop needs an end or a later reset")
    if not isinstance(schedule, list):
        raise ValueError("schedule must be a JSON array")
    seen = set()
    for q in schedule:
        qid = q["question_id"]
        if type(qid) is not int or qid <= 0 or qid in seen:
            raise ValueError("duplicate or invalid question_id")
        seen.add(qid)
        if q["venue"] not in ("main", "minibench") or not isinstance(q["question_type"], str):
            raise ValueError("invalid venue/type")
        opening, closing = parse_iso(q["open_time"]), parse_iso(q["scheduled_close_time"])
        if opening >= closing:
            raise ValueError("question must close after opening")
        last = opening
        for rev in q.get("revisions", []):
            when = parse_iso(rev["time"])
            if when < last or when >= closing or type(rev["structural"]) is not bool:
                raise ValueError("invalid or unsorted revision")
            last = when
    if ids != "all" and not set(ids) <= seen:
        raise ValueError("denominator contains unknown question IDs")
    hash_obj(settings)  # Reject nonfinite values, including unrecognized metadata.
    hash_obj(schedule)


def engine_policy():
    cfg = load_run_config()
    return {"timeouts_s": cfg["timeouts_s"], "deadlines": cfg["deadlines"],
            "interval_s": C.INTERVAL_S, "timing_limits": C.timing_limits(cfg),
            "max_jobs_per_cycle": 180, "scheduler_wake_s": 60, "cycle_version": C.VERSION,
            "scheduler_version": S.VERSION, "budget_version": B.VERSION}


def register(root: Path, settings: dict, schedule: list, *, now=utc_now):
    validate(settings, schedule)
    frozen = {"settings": copy.deepcopy(settings), "schedule": copy.deepcopy(schedule), "engine": engine_policy()}
    record = {"type": "coverage_sim_registered", "version": VERSION, "sim_id": settings["sim_id"],
              "sha256": hash_obj(frozen), "settings_sha256": hash_obj(settings),
              "schedule_sha256": hash_obj(schedule), "frozen": frozen, "registered_at": to_iso(now())}
    # A separate transaction directory avoids even sharing the production ledger lock.
    with transaction(root / ".coverage_sim"):
        prior = [r for r in read_complete(root / "coverage_sims.jsonl") if r.get("sim_id") == settings["sim_id"]]
        if prior:
            for r in prior:
                _verify(r)
                if r["sha256"] != record["sha256"]:
                    raise ValueError("registered simulation cannot change")
            return prior[0]
        append_record(root / "coverage_sims.jsonl", record)
    return record


def _verify(record):
    frozen = record["frozen"]
    if record.get("version") != VERSION or record["sha256"] != hash_obj(frozen) \
            or record["settings_sha256"] != hash_obj(frozen["settings"]) \
            or record["schedule_sha256"] != hash_obj(frozen["schedule"]) \
            or record["sim_id"] != frozen["settings"]["sim_id"]:
        raise ValueError("frozen simulation hash mismatch")
    if frozen["engine"] != engine_policy():
        raise ValueError("simulation engine policy changed; register a new ID")
    validate(frozen["settings"], frozen["schedule"])


def distribution(values):
    return {"count": len(values), "mean": sum(values) / len(values) if values else None,
            **{name: B.quantile(values, q) for name, q in (("p10", .1), ("p50", .5), ("p90", .9), ("p95", .95))},
            "max": max(values) if values else None}


class WorkerTimeout(Exception):
    pass


class Simulation:
    def __init__(self, frozen, seed):
        self.settings, self.cfg = frozen["settings"], frozen["engine"]
        self.rng = random.Random(seed)
        self.start = parse_iso(self.settings["observation"]["start"])
        self.end = parse_iso(self.settings["observation"]["end"])
        self.now = self.start
        self.next_tick = self.start
        self.questions = {}
        select = self.settings["denominator"]
        for source in sorted(frozen["schedule"], key=lambda q: q["question_id"]):
            if not self.start <= parse_iso(source["open_time"]) < self.end or source["venue"] not in select["venues"] \
                    or (select["question_ids"] != "all" and source["question_id"] not in select["question_ids"]):
                continue
            q = copy.deepcopy(source)
            q.update(seen=None, generation=0, observed_revision=0, fallback=False, blocked=False,
                     sealed=False, submitted=None, reason=None, first_start=None, candidate=None,
                     used_tier2=False, revision_actions=[], reasons=set(), states={})
            self._reset_states(q)
            self.questions[q["question_id"]] = q
        self.horizon = max([self.end, *(parse_iso(q["scheduled_close_time"]) for q in self.questions.values())])
        self.attempts, self.jobs_log, self.polls, self.cycles = [], [], [], []
        self.history = []
        for p in PLAYERS:
            for tier in (1, 2):
                for duration in self.settings["players"][p][f"tier{tier}"]:
                    self.history.append({"player": p, "tier": tier, "attempt_kind": "initial",
                        "started_at_utc": to_iso(self.start - timedelta(seconds=duration)),
                        "finished_at_utc": to_iso(self.start)})
        self.used = dict.fromkeys(PLAYERS, 0)
        self.reset_index = {p: sum(parse_iso(t) <= self.start for t in self.settings["players"][p]["reset_times"]) for p in PLAYERS}
        self.active = {}
        self.last_poll = None
        self.busy = {p: [] for p in PLAYERS}
        self.waits = []
        self.line_waits = {p: [] for p in PLAYERS}
        self.cycle = None
        self.completed_during_submit = False

    def _reset_states(self, q):
        q["states"] = {(p, t): {"terminal": False, "valid": False, "kind": "initial", "base": "initial",
                                "ready": self.now, "failure": None}
                       for p in PLAYERS for t in (1, 2)}

    def deadline(self, q):
        return F.decision_deadline(q, self.cfg)

    def estimate(self, p, tier):
        return B.duration_estimate(self.history, p, tier, self.cfg)

    def _advance(self, target):
        limit = self.cycle["worker_deadline"]
        target, timeout = min(target, limit), target >= limit
        while self.next_tick < target:
            self.cycles.append({"started_at": to_iso(self.next_tick), "finished_at": to_iso(self.next_tick),
                                "status": "overlap_skipped", "duration_s": 0})
            self.next_tick += timedelta(seconds=C.INTERVAL_S)
        self.now = target
        if timeout:
            raise WorkerTimeout

    def stop_reason(self, p):
        line = self.settings["players"][p]
        for stop in line["stops"]:
            finish = parse_iso(stop["end"]) if stop.get("end") else next(
                parse_iso(t) for t in line["reset_times"] if parse_iso(t) > parse_iso(stop["start"]))
            if parse_iso(stop["start"]) <= self.now < finish:
                return stop["reason"]
        return None

    def poll(self, *, force=False):
        if not force and self.last_poll is not None and (self.now - self.last_poll).total_seconds() < 30:
            return
        self._advance(self.now + timedelta(seconds=self.settings["poll_s"]))
        self.last_poll = self.now
        self.polls.append(to_iso(self.now))
        for q in self.questions.values():
            if not parse_iso(q["open_time"]) <= self.now < parse_iso(q["scheduled_close_time"]):
                continue
            if q["seen"] is None:
                q["seen"] = self.now
                for state in q["states"].values():
                    state["ready"] = self.now
                # A revision before first discovery is already part of the initial snapshot.
                q["observed_revision"] = sum(parse_iso(r["time"]) <= self.now for r in q.get("revisions", []))
            revisions = q.get("revisions", [])
            index = sum(parse_iso(r["time"]) <= self.now for r in revisions)
            if index > q["observed_revision"]:
                changed = revisions[q["observed_revision"]:index]
                q["observed_revision"] = index
                if q["submitted"] is not None:
                    action = "already_submitted"
                elif q["question_type"] in SUPPORTED and (self.deadline(q) - self.now).total_seconds() > max(self.estimate(p, 1) for p in PLAYERS):
                    q["generation"] += 1
                    self._reset_states(q)
                    q.update(sealed=False, candidate=None, blocked=False, fallback=False, reason=None)
                    action = "requeued"
                elif q["blocked"] or any(r["structural"] for r in changed):
                    q.update(blocked=True, sealed=False, reason="blocked_revision")
                    action = "blocked_revision"
                else:
                    q["fallback"] = True
                    action = "text_fallback"
                q["revision_actions"].append({"at": to_iso(self.now), "action": action, "generation": q["generation"]})

    def _eligible(self, q):
        return q["seen"] is not None and q["question_type"] in SUPPORTED and not q["blocked"] \
            and not q["fallback"] and not q["sealed"] and q["submitted"] is None and self.now < self.deadline(q)

    def jobs(self):
        first, second = [], []
        unfinished = False
        for qid, q in self.questions.items():
            if not self._eligible(q):
                continue
            enabled = [p for p in PLAYERS if self.stop_reason(p) != "disabled"]
            done = all(q["states"][p, 1]["terminal"] for p in enabled)
            unfinished |= not done
            if done and q["candidate"] is None:
                q["candidate"] = any(q["states"][p, 1]["valid"] for p in PLAYERS) and self.rng.random() < self.settings["escalation_rate"]
                for p in PLAYERS:
                    q["states"][p, 2]["ready"] = self.now
            for p in enabled:
                if p in self.active or p in self.paused or self.stop_reason(p):
                    continue
                tier = 2 if q["states"][p, 1]["terminal"] else 1
                if tier == 2 and (not done or not q["candidate"] or q["states"][p, 2]["terminal"]):
                    continue
                job = S.Job(p, tier, qid, q, q["venue"], self.deadline(q), self.estimate(p, tier))
                (first if tier == 1 else second).append(job)
        return S.order_jobs(first if unfinished else second, now=self.now, urgent_slack_s=self.settings["urgent_slack_s"])

    def _capacity(self, p, tier):
        line = self.settings["players"][p]
        index = sum(parse_iso(t) <= self.now for t in line["reset_times"])
        if index != self.reset_index[p]:
            self.used[p], self.reset_index[p] = 0, index
        protected = 0
        if tier == 2:
            protected = sum(self._eligible(q) and not q["states"][p, 1]["terminal"] for q in self.questions.values())
            protected += self.settings["expected_new_questions"] + self.settings["repair_reserve"]
        return self.used[p] + protected + 1 <= line["attempt_cap"]

    def start_attempt(self, job, *, continuation=False):
        q, p, tier = job.snapshot, job.player, job.tier
        state = q["states"][p, tier]
        if self.now >= self.admission_end or not self._eligible(q):
            return False
        if not continuation:
            if self.cycle_jobs >= self.cfg["max_jobs_per_cycle"]:
                return False
            self.cycle_jobs += 1
        reason = self.stop_reason(p)
        if reason:
            q["reasons"].add(reason)
            self.paused.add(p)
            return False
        if tier == 2 and (self.deadline(q) - self.now).total_seconds() <= job.estimate_s:
            state.update(terminal=True, failure="insufficient_tier2_time")
            self.jobs_log.append({"question_id": q["question_id"], "player": p, "tier": tier,
                                  "at": to_iso(self.now), "outcome": "not_admitted"})
            return False
        line = self.settings["players"][p]
        # Include validation/quota overhead in the available time, conservatively.
        remaining = (self.deadline(q) - self.now).total_seconds() - line["overhead_s"]
        if remaining < F.MIN_ATTEMPT_S:
            self.paused.add(p)
            q["reasons"].add("timeout")
            return False
        if not self._capacity(p, tier):
            state.update(terminal=True, failure="budget_skipped")
            q["reasons"].add("budget_skipped")
            return False
        self.used[p] += 1
        kind = state["kind"]
        profile = "repair" if kind == "format_repair" else f"tier{tier}"
        sampled = self.rng.choice(line[profile])
        duration = min(sampled, self.cfg["timeouts_s"][profile], remaining)
        timeout = sampled > duration
        if kind != "format_repair":
            state["base"] = kind
            state["failure"] = None
            failure_rate = line[f"tier{tier}_rerun_rate"] if kind == "initial" else line["rerun_failure_rate"]
            failed = timeout or self.rng.random() < failure_rate
            repair = not timeout and self.rng.random() < line["format_repair_rate"]
        else:
            failed = self.rng.random() < line["repair_failure_rate"] or state["failure"] is not None
            repair = False
        finish = self.now + timedelta(seconds=duration + line["overhead_s"])
        attempt = {"question_id": q["question_id"], "player": p, "tier": tier, "generation": q["generation"],
                   "attempt_kind": kind, "started_at_utc": to_iso(self.now), "finished_at_utc": to_iso(finish),
                   "duration_s": duration + line["overhead_s"], "sampled_s": sampled, "urgent_override": job.urgent_override,
                   "outcome": "timeout" if timeout else "invalid_output" if failed else "format_required" if repair else "valid"}
        if tier == 1 and q["first_start"] is None:
            q["first_start"] = self.now
        if not continuation:
            wait = (self.now - state["ready"]).total_seconds()
            self.waits.append(wait)
            self.line_waits[p].append(wait)
        self.attempts.append(attempt)
        self.active[p] = {"job": job, "state": state, "attempt": attempt, "finish": finish,
                          "start": self.now, "repair": repair, "failed": failed or timeout}
        return True

    def complete(self, p):
        active = self.active.pop(p)
        job, state, attempt = active["job"], active["state"], active["attempt"]
        q, tier = job.snapshot, job.tier
        self.busy[p].append((active["start"], active["finish"]))
        self.history.append(attempt)
        if attempt["generation"] != q["generation"] or q["blocked"] or q["submitted"] is not None:
            attempt["outcome"] = "snapshot_invalidated"
            return
        if attempt["outcome"] == "valid":
            state.update(terminal=True, valid=True, failure=None)
            return
        if active["failed"]:
            state["failure"] = attempt["outcome"]
            q["reasons"].add(attempt["outcome"])
        state["ready"] = self.now
        if active["repair"]:
            state["kind"] = "format_repair"
        elif state["base"] == "initial":
            state["kind"] = "rerun"
        else:
            state["terminal"] = True
            return
        # A repair/rerun rechecks the cached queue before starting another call.
        # Completed jobs instead get exactly one forced refresh in run_cycle.
        self.poll()
        if attempt["generation"] != q["generation"] or q["blocked"]:
            return
        if not self.start_attempt(job, continuation=True) and not state["terminal"]:
            self.paused.add(p)

    def finalize(self, *, include_fallbacks=False):
        newly_ready = []
        for q in self.questions.values():
            if q["fallback"] and not include_fallbacks:
                continue
            if q["seen"] is None or q["blocked"] or q["sealed"] or q["submitted"] is not None or q["question_type"] not in SUPPORTED:
                continue
            enabled = [p for p in PLAYERS if self.stop_reason(p) != "disabled"]
            first_done = all(q["states"][p, 1]["terminal"] for p in enabled)
            if first_done and q["candidate"] is None:
                q["candidate"] = any(q["states"][p, 1]["valid"] for p in PLAYERS) and self.rng.random() < self.settings["escalation_rate"]
                for p in PLAYERS:
                    q["states"][p, 2]["ready"] = self.now
            second_done = q["candidate"] is False or all(q["states"][p, 2]["terminal"] for p in enabled)
            if self.now >= self.deadline(q) or q["fallback"] or (first_done and second_done):
                q["sealed"] = True
                q["ready"] = any(s["valid"] for s in q["states"].values())
                q["used_tier2"] = any(q["states"][p, 2]["valid"] for p in PLAYERS)
                if q["ready"]:
                    newly_ready.append(q["question_id"])
        return newly_ready

    def _submit_advance(self, target):
        # Model calls (including repair/rerun) keep running while the main thread submits.
        while self.active and min(a["finish"] for a in self.active.values()) <= target:
            self._advance(max(self.now, min(a["finish"] for a in self.active.values())))
            for p in PLAYERS:
                if p in self.active and self.active[p]["finish"] <= self.now:
                    self.complete(p)
                    if p not in self.active:
                        self.completed_during_submit = True
        self._advance(max(self.now, target))

    def publish(self, *, during_schedule=False, question_ids=()):
        started = self.now
        call = {"phase": "on_finalized" if during_schedule else "cycle_end",
                "question_ids": list(question_ids), "started_at": to_iso(started), "status": "ok"}
        self.cycle["submit_calls"].append(call)
        try:
            self.poll(force=not during_schedule)
            if not during_schedule:
                self.finalize(include_fallbacks=True)
            if any(parse_iso(i["start"]) <= self.now < parse_iso(i["end"])
                   for i in self.settings.get("submit_busy_intervals", [])):
                call["status"] = "busy"
                return
            self._submit_ready()
        finally:
            call.update(finished_at=to_iso(self.now), elapsed_s=(self.now - started).total_seconds())

    def _submit_ready(self):
        submit_end = self.now + timedelta(seconds=C.SUBMIT_TIMEOUT_S)
        for q in self.questions.values():
            if not q["sealed"] or not q.get("ready") or q["blocked"] or q["submitted"] is not None or q.get("submit_unknown"):
                continue
            deadline = V.submit_deadline(q, self.cfg["deadlines"])
            if self.now >= deadline:
                q["reason"] = "late"
                continue
            if self.now >= submit_end:
                break
            duration = sum(self.settings["submission"].values())
            finish = self.now + timedelta(seconds=duration)
            self._submit_advance(min(finish, submit_end))
            if finish > submit_end:
                q.update(submit_unknown=True, reason="timeout")
                break
            # Submission-time revision checks must not discover unrelated new questions.
            changes = [r for r in q.get("revisions", [])[q["observed_revision"]:] if parse_iso(r["time"]) <= self.now]
            if changes:
                structural = any(r["structural"] for r in changes)
                can_rerun = (self.deadline(q) - self.now).total_seconds() > max(self.estimate(p, 1) for p in PLAYERS)
                if structural or can_rerun:
                    q["reason"] = "blocked_revision" if structural else "revision_pending"
                    continue
                q["fallback"] = True
            if self.now < deadline:
                q["submitted"] = self.now
                q["reason"] = None
            else:
                q["reason"] = "late"

    def run_cycle(self):
        started = self.now
        limits = self.cfg["timing_limits"]
        self.admission_end = started + timedelta(seconds=limits["admission_window_s"])
        self.cycle = {"started_at": to_iso(started), "worker_deadline": started + timedelta(seconds=limits["worker_timeout_s"]), "status": "complete", "submit_calls": []}
        self.cycle_jobs = 0
        self.paused = set()
        try:
            self._advance(self.now + timedelta(seconds=self.settings["startup_s"]))
            self.poll(force=True)
            for p in PLAYERS:
                if self.stop_reason(p):
                    self.paused.add(p)
            while True:
                for q in self.questions.values():
                    if self._eligible(q):
                        q["reasons"].update(filter(None, (self.stop_reason(p) for p in PLAYERS)))
                if self.settings.get("submission_policy", "on_finalized") == "on_finalized":
                    self.poll(force=self.completed_during_submit)
                    self.completed_during_submit = False
                newly_ready = self.finalize()
                if newly_ready and self.settings.get("submission_policy", "on_finalized") == "on_finalized":
                    self.publish(during_schedule=True, question_ids=newly_ready)
                if self.now < self.admission_end:
                    for job in self.jobs():
                        if job.player not in self.active and job.player not in self.paused:
                            self.start_attempt(job)
                if not self.active:
                    break
                wake = max(self.now, min(a["finish"] for a in self.active.values()))
                if self.settings.get("submission_policy", "on_finalized") == "on_finalized":
                    deadlines = [self.deadline(q) for q in self.questions.values()
                                 if q["seen"] is not None and not q["sealed"] and not q["blocked"] and not q["fallback"]
                                 and q["question_type"] in SUPPORTED and q["submitted"] is None]
                    wake = min([wake, self.now + timedelta(seconds=self.cfg["scheduler_wake_s"]),
                                *(d for d in deadlines if d > self.now)])
                self._advance(wake)
                completed = [p for p in PLAYERS if p in self.active and self.active[p]["finish"] <= self.now]
                for p in completed:
                    self.complete(p)
                    if p not in self.active:
                        self.poll(force=True)
            newly_ready = self.finalize()
            if newly_ready and self.settings.get("submission_policy", "on_finalized") == "on_finalized":
                self.publish(during_schedule=True, question_ids=newly_ready)
            self.publish()
        except WorkerTimeout:
            self.cycle["status"] = "timeout"
            for p, active in self.active.items():
                self.busy[p].append((active["start"], min(active["finish"], self.now)))
                active["state"].update(terminal=True, failure="timeout")
                active["job"].snapshot["reasons"].add("timeout")
                active["attempt"].update(outcome="supervisor_timeout", finished_at_utc=to_iso(self.now))
            self.active.clear()
            # The cycle reserves cleanup time separately from the worker timeout.
            cleanup = self.now + timedelta(seconds=limits["cleanup_reserve_s"])
            while self.next_tick < cleanup:
                self.cycles.append({"started_at": to_iso(self.next_tick), "finished_at": to_iso(self.next_tick),
                                    "status": "overlap_skipped", "duration_s": 0})
                self.next_tick += timedelta(seconds=C.INTERVAL_S)
            self.now = cleanup
        self.cycle.pop("worker_deadline")
        self.cycle.update(finished_at=to_iso(self.now), duration_s=(self.now - started).total_seconds(),
                          jobs_started=self.cycle_jobs)
        self.cycles.append(self.cycle)

    def run(self):
        while self.next_tick < self.horizon:
            self.now = self.next_tick
            self.next_tick += timedelta(seconds=C.INTERVAL_S)
            self.run_cycle()
        return self.result()

    def result(self):
        outcomes = []
        for qid, q in self.questions.items():
            reason = q["reason"]
            if q["submitted"] is None:
                if q["question_type"] not in SUPPORTED:
                    reason = "unsupported_type"
                elif reason is None:
                    reason = next((r for r in ("quota_exhausted", "disabled", "preflight_blocked", "budget_skipped", "timeout", "invalid_output")
                                   if r in q["reasons"]), "timeout")
            outcomes.append({"question_id": qid, "venue": q["venue"], "question_type": q["question_type"],
                "supported": q["question_type"] in SUPPORTED, "on_time": q["submitted"] is not None,
                "submitted_at": to_iso(q["submitted"]) if q["submitted"] else None, "reason": reason,
                "snapshot_generation": q["generation"],
                "with_tier2": q["used_tier2"] and q["submitted"] is not None,
                "tier2_started": any(a["question_id"] == qid and a["tier"] == 2 for a in self.attempts),
                "seen_at": to_iso(q["seen"]) if q["seen"] else None,
                "first_tier1_at": to_iso(q["first_start"]) if q["first_start"] else None,
                "revision_fallback": q["fallback"], "revision_actions": q["revision_actions"],
                "contributing_reasons": sorted(q["reasons"])})
        supported = [r for r in outcomes if r["supported"]]
        covered = sum(r["on_time"] for r in outcomes)
        coverage = covered / len(outcomes) if outcomes else None
        supported_coverage = covered / len(supported) if supported else None
        active_cycles = [c for c in self.cycles if c["status"] != "overlap_skipped"]
        duration = (self.end - self.start).total_seconds()
        utilization = {p: sum(max(0, (min(b, self.end) - max(a, self.start)).total_seconds()) for a, b in self.busy[p]) / duration for p in PLAYERS}
        openings = Counter(parse_iso(q["open_time"]) for q in self.questions.values())
        events = sorted([(parse_iso(q[k]), delta) for q in self.questions.values()
                         for k, delta in (("open_time", 1), ("scheduled_close_time", -1))])
        concurrent = peak = 0
        for _, delta in events:
            concurrent += delta
            peak = max(peak, concurrent)
        delays = [(q["first_start"] - parse_iso(q["open_time"])).total_seconds() for q in self.questions.values() if q["first_start"] is not None]
        return {"questions": outcomes, "denominator_all": len(outcomes), "denominator_supported": len(supported),
                "covered": covered, "coverage_all": coverage, "coverage_supported": supported_coverage,
                "supported_meets_90_percent": supported_coverage >= .9 if supported else None,
                "miss_reasons": dict(Counter(r["reason"] for r in outcomes if not r["on_time"])),
                "utilization": utilization, "queue_wait_s": distribution(self.waits), "open_to_tier1_s": distribution(delays),
                "queue_wait_by_player_s": {p: distribution(self.line_waits[p]) for p in PLAYERS},
                "not_started": sum(q["first_start"] is None for q in self.questions.values()),
                "peak_simultaneous_opens": max(openings.values(), default=0), "peak_open_questions": peak,
                "cycles_over_600_ratio": sum(c["duration_s"] > 600 for c in active_cycles) / len(active_cycles) if active_cycles else 0,
                "overlap_skipped": sum(c["status"] == "overlap_skipped" for c in self.cycles),
                "cycles": sorted(self.cycles, key=lambda c: c["started_at"]), "polls": self.polls,
                "attempts": self.attempts, "job_decisions": self.jobs_log}


def simulate(record):
    """Pure deterministic replay; only run_registered appends a results record."""
    _verify(record)
    settings = record["frozen"]["settings"]
    trials = [Simulation(record["frozen"], settings["seed"] + i).run() for i in range(settings["repetitions"])]
    summary = {k: distribution([t[k] for t in trials if t[k] is not None]) for k in
               ("coverage_all", "coverage_supported", "cycles_over_600_ratio", "peak_simultaneous_opens", "peak_open_questions", "overlap_skipped")}
    summary["utilization"] = {p: distribution([t["utilization"][p] for t in trials]) for p in PLAYERS}
    summary["queue_wait_by_player_s"] = {
        p: {stat: distribution([t["queue_wait_by_player_s"][p][stat] for t in trials
                               if t["queue_wait_by_player_s"][p][stat] is not None])
            for stat in ("mean", "p50", "p90", "p95", "max")} for p in PLAYERS}
    for metric in ("queue_wait_s", "open_to_tier1_s"):
        summary[metric] = {stat: distribution([t[metric][stat] for t in trials if t[metric][stat] is not None])
                           for stat in ("mean", "p50", "p90", "p95", "max")}
    return {"type": "coverage_sim_result", "version": VERSION, "sim_id": settings["sim_id"],
            "sha256": record["sha256"], "seed": settings["seed"], "repetitions": settings["repetitions"],
            "summary": summary, "trials": trials}


def run_registered(root: Path, sim_id: str, *, now=utc_now):
    with transaction(root / ".coverage_sim"):
        records = [r for r in read_complete(root / "coverage_sims.jsonl") if r.get("sim_id") == sim_id]
        if not records:
            raise ValueError("simulation ID is not registered")
        for record in records:
            _verify(record)
        if len({r["sha256"] for r in records}) != 1:
            raise ValueError("conflicting registered simulation hashes")
    result = simulate(records[0])
    with transaction(root / ".coverage_sim"):
        append_record(root / "coverage_sim_results.jsonl", {**result, "recorded_at": to_iso(now())})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("register").add_argument("settings", type=Path)
    sub.add_parser("run").add_argument("sim_id")
    args = parser.parse_args(argv)
    if args.command == "register":
        settings = json.loads(args.settings.read_text(encoding="utf-8"))
        schedule_file = args.settings.parent / settings["schedule_file"]
        schedule = json.loads(schedule_file.read_text(encoding="utf-8"))
        result = register(args.data_dir, settings, schedule)
        output = {k: result[k] for k in ("sim_id", "sha256", "schedule_sha256")}
    else:
        result = run_registered(args.data_dir, args.sim_id)
        output = {"sim_id": result["sim_id"], "sha256": result["sha256"], "summary": result["summary"]}
    print(json.dumps(output, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
