"""Bounded cross-question scheduler for fixed M2 and continuous M3 campaigns.

One scheduler instance, at most one CLI per line; no submission or launchd installation.
Fixed campaigns remain local-only; cycle.py owns continuous registration and publication.
A missing approval/cap never becomes a waiver.
"""
from __future__ import annotations

import argparse
import copy
import importlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
from datetime import datetime, timedelta
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable

import aggregate as A
import audit_ledger as L
import baseline
import budget as B
import comment as C
import escalate as E
import forecast as F
import numeric as N
import reconcile as R
from flab.config import PLAYERS, load_players_enabled, load_quota_policy, load_run_config, load_submit_mode
from flab.ids import hash_obj, new_id
from flab.jsonl import append_record
from flab.ledger import transaction, read_complete
from flab.timeutil import parse_iso, to_iso, utc_now
from poll_questions import latest_snapshots
from flab import fail_soft as FS

VERSION = "scheduler_v8"
QUOTA_WAITS = {"quota_5h_wait", "quota_weekly_wait"}
IDLE_LOOKAHEAD_S = 780  # next 600-second tick + bounded submission tail + startup allowance
TEMPORARY_BUDGET_REASONS = {
    "weekly_attempt_capacity_reserved_or_exhausted", "argon_personal_or_tier1_reserve",
    "untrusted_argon_snapshot_or_reset", "quota_layout_unknown",
    "unresolved_or_running_attempt", "tier1_token_baseline_unmeasured",
    "tier2_token_breaker", "weekly_tier2_token_breaker", "weekly_tier2_argon_breaker",
    "quota_5h_wait",
}
SNAPSHOT_WAIT_REASONS = QUOTA_WAITS | {"argon_personal_or_tier1_reserve"}
BREAKER_WAIT_REASONS = {"tier2_token_breaker", "weekly_tier2_token_breaker",
                        "weekly_tier2_argon_breaker"}


def idle_delay(now, deadlines, handoff_deadline=None):
    """Pure timing rule, also used by the coverage simulator; never busy-wait."""
    upcoming = [d for d in deadlines if d is not None and d > now]
    if not upcoming or min(upcoming) > now + timedelta(seconds=IDLE_LOOKAHEAD_S):
        return None
    if handoff_deadline is not None and now >= handoff_deadline:
        return None
    targets = [now + timedelta(seconds=60), min(upcoming)]
    if handoff_deadline is not None:
        targets.append(handoff_deadline)
    return max(0.0, (min(targets) - now).total_seconds())


TERMINAL = {"selected", "already_selected", "prior_answer_excluded", "output_excluded", "attempts_exhausted",
            "schema_failed", "semantic_failed", "parse_failed", "invalid_output", "not_candidate", "not_admitted", "budget_skipped"}


def register(root: Path, campaign: dict, *, now=None) -> dict:
    """Freeze question selection, windows and numerical caps; never load or show model answers."""
    if campaign.get("version") != "m2_campaign_v1" or not isinstance(campaign.get("campaign_id"), str):
        raise ValueError("invalid campaign identity")
    questions = campaign.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("campaign needs questions")
    ids = []
    for q in questions:
        if type(q.get("question_id")) is not int or q["question_id"] <= 0 \
                or q.get("ground") not in ("bot_testing_area", "metaculus_cup", "main_site_questions") \
                or q.get("priority") not in ("main", "minibench"):
            raise ValueError("invalid test question or queue priority")
        ids.append(q["question_id"])
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate campaign question")
    if parse_iso(campaign["budget_window_start"]) >= parse_iso(campaign["budget_window_end"]):
        raise ValueError("invalid campaign budget window")
    for p in PLAYERS:
        if type(campaign.get("weekly_attempt_caps", {}).get(p)) is not int or campaign["weekly_attempt_caps"][p] <= 0:
            raise ValueError("explicit positive weekly caps are required, including prior attempts and acceptance")
    for field in ("expected_new_questions", "repair_reserve", "urgent_slack_s"):
        if type(campaign.get(field)) is not int or campaign[field] < 0:
            raise ValueError(f"explicit nonnegative {field} required")
    if campaign.get("run_config_sha256") != hash_obj(load_run_config()) \
            or campaign.get("quota_policy_sha256") != hash_obj(load_quota_policy()):
        raise ValueError("campaign must pin the current run config and quota policy")
    record = {"type": "campaign_registered", "campaign": copy.deepcopy(campaign), "sha256": hash_obj(campaign),
              "registered_at": to_iso(now or utc_now())}
    with transaction(root):
        rows = [r for r in read_complete(root / "campaigns.jsonl")
                if r.get("campaign", {}).get("campaign_id") == campaign["campaign_id"]]
        if rows:
            if any(r.get("sha256") != record["sha256"] for r in rows):
                raise ValueError("registered campaign cannot be changed after seeing outputs")
            return rows[0]
        if any(r.get("question_id") in ids for r in read_complete(root / "forecasts.jsonl")):
            raise ValueError("new campaign selection must precede forecasts of its questions")
        append_record(root / "campaigns.jsonl", record)
    return record


@dataclass(frozen=True)
class Job:
    player: str
    tier: int
    question_id: int
    snapshot: dict
    priority: str
    deadline: datetime
    estimate_s: float
    urgent_override: bool = False


def order_jobs(jobs: list[Job], *, now: datetime, urgent_slack_s: int) -> list[Job]:
    def key(job):
        rank = (job.tier - 1) * 2 + (job.priority == "minibench")
        return rank, job.deadline, job.question_id, job.player
    ordered = sorted(jobs, key=key)
    main = [j for j in jobs if j.tier == 1 and j.priority == "main"]
    urgent = [j for j in jobs if j.tier == 1 and j.priority == "minibench"
              and (j.deadline - now).total_seconds() <= j.estimate_s + urgent_slack_s
              and all((m.deadline - now).total_seconds() > 2 * m.estimate_s + urgent_slack_s for m in main)]
    if main and urgent:
        from dataclasses import replace
        ids = {(j.player, j.question_id, j.tier) for j in urgent}
        ordered = [replace(j, urgent_override=True) for j in sorted(urgent, key=key)] + [
            j for j in ordered if (j.player, j.question_id, j.tier) not in ids]
    return ordered


class Scheduler:
    def __init__(self, root: Path, campaign: dict, *,
                 snapshots: Callable[[], dict[int, dict]] | None = None,
                 adapter_loader: Callable[[str], Any] = lambda p: importlib.import_module(f"adapters.{p}"),
                 preflight: Callable[[str, int, Any], F.Preflight] | None = None,
                 context_extra: Callable[[str], dict] | None = None,
                 now: Callable[[], datetime] = utc_now,
                 acceptance_log: Path = baseline.ACCEPTANCE_LOG,
                 continuous: bool = False, execution_deadline: datetime | None = None,
                 on_finalized: Callable[[list[int]], None] | None = None,
                 handoff_deadline: datetime | None = None, sleep=None):
        if snapshots is None:
            from m2_campaign import SafeSnapshots
            from metaculus import MetaculusClient
            from flab.envfile import get_secret
            from flab.workdir import PROJECT_ROOT
            snapshots = SafeSnapshots(MetaculusClient(get_secret("METACULUS_TOKEN", PROJECT_ROOT / ".env")), root, campaign)
        self.continuous, self.execution_deadline = continuous, execution_deadline
        self.on_finalized = on_finalized
        self.handoff_deadline, self.sleep = handoff_deadline, sleep or time.sleep
        self.handoff = False
        self.snapshot_ids = {}
        self.root, self.campaign, self.provider, self.now = root, campaign, snapshots, now
        self.cfg, self.policy = load_run_config(), load_quota_policy()
        self.loader, self.pf, self.extra = adapter_loader, preflight, context_extra or (lambda _: {})
        self.run_id = new_id("scheduler")
        self.budget = B.Budget(root, campaign, self.policy, self.run_id, now, acceptance_log)
        self.jobs_log = root / "scheduler_jobs.jsonl"
        self._wait_snapshot_checked = False

    def _recover(self, player):
        ctx = F.Context(player=player, adapter=None, cfg=self.cfg, run_id=self.run_id,
            preflight=F.Preflight(player), now=self.now, forecasts_log=self.root / "forecasts.jsonl",
            audits_log=self.root / "audits.jsonl", raw_root=self.root / "raw",
            line_status_log=self.root / "line_status.jsonl", quota_log=self.root / "quota.jsonl",
            **self.extra(player))
        F.recover_interrupted(ctx)

    def _reevaluate_waits(self, rows):
        """New-policy waits can expire or be superseded by a valid fresh observation."""
        waits = [r for r in rows if r.get("version") == VERSION and
                 r.get("outcome") in QUOTA_WAITS | {"budget_wait"}]
        released = {r["wait_id"] for r in rows if r.get("type") == "scheduler_wait_reevaluated"}
        pending = [r for r in waits if r["record_id"] not in released]
        def budget_reason(row):
            return row.get("budget_reason") or str(row.get("reason") or "").removeprefix(
                "budget_skipped: ").removeprefix("budget admission denied: ")

        def snapshot_wait(row):
            return row.get("outcome") in QUOTA_WAITS or budget_reason(row) in SNAPSHOT_WAIT_REASONS

        if any(r["player"] == "gemini" and snapshot_wait(r) for r in pending) and not self._wait_snapshot_checked:
            self._wait_snapshot_checked = True
            try:
                observed = self.extra("gemini").get("quota_snapshot", F.quota.snapshot)(timeout_s=F.QUOTA_QUERY_S)
                with transaction(self.root):
                    append_record(self.root / "quota.jsonl", {"type": "quota_snapshot", "when": "reevaluate",
                        "player": "gemini", **F.quota.record_snapshot(observed)})
            except (OSError, ValueError, RuntimeError):
                pass
        snapshots = read_complete(self.root / "quota.jsonl")
        for row in pending:
            retry = row.get("quota_retry_at_utc") or row.get("reevaluate_after")
            reason, snapshot_id = None, None
            cause = budget_reason(row)
            if cause == "weekly_attempt_capacity_reserved_or_exhausted":
                retry = row.get("budget_window_end") or self.campaign["budget_window_end"]
            elif cause in BREAKER_WAIT_REASONS:
                stops = [r for r in read_complete(self.budget.log)
                         if r.get("type") == "budget_upgrade_stop" and r.get("player") == row["player"]
                         and r.get("reason") == cause]
                retry = stops[-1]["until"] if stops else retry
            elif cause == "tier1_token_baseline_unmeasured":
                retry = None
                waited_at = parse_iso(row["finished_at"])
                window_start = parse_iso(self.campaign["budget_window_start"])
                window_end = parse_iso(self.campaign["budget_window_end"])
                observed_at = self.now()
                if any(r.get("player") == row["player"] and r.get("tier") == 1
                       and r.get("attempt_kind") != "format_repair" and r.get("model_check") != "mismatch"
                       # Release only on a new completed measurement in this window.
                       and r.get("finished_at_utc")
                       and window_start <= (finished := parse_iso(r["finished_at_utc"])) < window_end
                       and waited_at < finished <= observed_at
                       and (B.tokens(r) or 0) > 0 for r in read_complete(self.root / "forecasts.jsonl")):
                    reason = "token_baseline_measured"
            if retry and self.now() >= parse_iso(retry):
                reason = "wait_expired"
            if row["player"] == "gemini" and snapshot_wait(row):
                for snap in reversed(snapshots):
                    if not snap.get("taken_at"):
                        continue
                    if parse_iso(snap["taken_at"]) <= parse_iso(row["finished_at"]):
                        continue
                    if not F.quota.fresh(snap, self.now()):
                        continue
                    estimate, _ = F.argon_estimate(self.root / "quota.jsonl", self.policy,
                        self.root / "forecasts.jsonl", tier=row["tier"], current=snap)
                    protected_quota = 0
                    if row["tier"] == 2:
                        first_estimate, _ = F.argon_estimate(self.root / "quota.jsonl", self.policy,
                            self.root / "forecasts.jsonl", tier=1, current=snap)
                        protected = (self._protected("gemini") + self.campaign["expected_new_questions"]
                                     + self.campaign["repair_reserve"])
                        protected_quota = min(protected * first_estimate, FS.TIER1_QUOTA_RESERVE_CAP)
                    buckets = F.quota.argon_buckets(snap)
                    estimate_5h, _ = F.argon_estimate(self.root / "quota.jsonl", self.policy,
                        self.root / "forecasts.jsonl", tier=row["tier"], current=snap, period="five_hour")
                    short, _ = F.quota.five_hour_gate(snap, estimate_5h)
                    if buckets.weekly.remaining_fraction - estimate - protected_quota >= 1 - self.policy["players"]["gemini"]["weekly_cap"] and not short:
                        reason, snapshot_id = "new_valid_snapshot", snap.get("snapshot_id")
                        break
            if reason:
                with transaction(self.root):
                    append_record(self.jobs_log, {"type": "scheduler_wait_reevaluated", "version": VERSION,
                        "wait_id": row["record_id"], "player": row["player"], "question_id": row["question_id"],
                        "reason": reason, "snapshot_id": snapshot_id, "at": to_iso(self.now())})
                released.add(row["record_id"])
        return released

    def state(self):
        with transaction(self.root):
            attempts = read_complete(self.root / "forecasts.jsonl")
            aggregates = read_complete(self.root / "aggregates.jsonl")
            events = read_complete(self.root / "audits.jsonl")
            graph = L.ImpactGraph([*attempts, *aggregates])
            L.append_impacts(self.root / "audit_impacts.jsonl", graph, events, now=self.now())
            return attempts, aggregates, events, graph.attempt_audit_view(events)

    def _terminal(self, qid, player, tier, attempts, rows):
        if self.continuous:
            sid = self.snapshot_ids.get(qid)
            attempts = [r for r in attempts if r.get("question_snapshot_id") == sid]
            rows = [r for r in rows if r.get("question_snapshot_id") == sid]
        if any(r["question_id"] == qid and r["player"] == player and r["tier"] == tier
               and F.adoptable(r) for r in attempts):
            return True
        latest = next((r for r in reversed(rows) if r.get("type") == "scheduler_job_finished"
                       and (r["question_id"], r["player"], r["tier"]) == (qid, player, tier)), {})
        return latest.get("outcome") in TERMINAL

    def jobs(self, snapshots: dict[int, dict], *, paused: set[str], active: set[str]) -> list[Job]:
        self.snapshot_ids = {qid: s["snapshot_id"] for qid, s in snapshots.items()}
        attempts, aggregates, events, view = self.state()
        with transaction(self.root):
            rows = read_complete(self.jobs_log)
        released = self._reevaluate_waits(rows)
        waiting = {r["player"] for r in rows if r.get("outcome") in QUOTA_WAITS
                   and r.get("record_id") not in released
                   and r.get("quota_retry_at_utc") and self.now() < parse_iso(r["quota_retry_at_utc"])}
        budget_waits = {(r["question_id"], r["player"], r["tier"]) for r in rows
                        if r.get("outcome") == "budget_wait" and r.get("record_id") not in released}
        if self.continuous:
            waiting.update(r["player"] for r in attempts if r.get("quota_retry_at_utc")
                           and self.now() < parse_iso(r["quota_retry_at_utc"]))
            waiting.update(p for p in PLAYERS if F.line_stopped(self.root / "line_status.jsonl", p,
                                                              self.root / "forecasts.jsonl", now=self.now()))
        enabled = load_players_enabled()
        sealed = {a["question_id"] for a in aggregates if (self.continuous or a.get("scheduler_campaign_id") == self.campaign["campaign_id"])
                  and a.get("completion_status") == "complete_local"
                  and (not self.continuous or a.get("question_snapshot_id") == self.snapshot_ids.get(a["question_id"]))}
        first, second = [], []
        unfinished_first = False
        for entry in self.campaign["questions"]:
            qid, snap = entry["question_id"], snapshots.get(entry["question_id"])
            if qid in sealed or snap is None or F.eligible(snap, self.cfg, self.now()):
                continue
            deadline = F.decision_deadline(snap, self.cfg)
            if deadline is None:
                continue
            tier1_done = all(not enabled[p] or
                             (self.continuous and (p in paused or p in waiting)) or
                             (qid, p, 1) in budget_waits or
                             self._terminal(qid, p, 1, attempts, rows) for p in PLAYERS)
            unfinished_first |= not tier1_done
            candidate = E.evaluate(snap, attempts, audit_events=view, now=self.now()) if tier1_done else None
            for p in PLAYERS:
                if not enabled[p] or p in paused or p in active or p in waiting:
                    continue
                tier = 1 if not self._terminal(qid, p, 1, attempts, rows) else 2
                if (qid, p, tier) in budget_waits:
                    continue
                if tier == 2 and (not candidate or not candidate["escalation"]["candidate"]
                                  or self._terminal(qid, p, 2, attempts, rows)):
                    continue
                estimate = B.duration_estimate(attempts, p, tier, self.cfg)
                job = Job(p, tier, qid, snap, entry["priority"], deadline, estimate)
                (first if tier == 1 else second).append(job)
        # Running first-layer work still has precedence; continuous paused lines do not block upgrades.
        return order_jobs(first if unfinished_first else second, now=self.now(), urgent_slack_s=self.campaign["urgent_slack_s"])

    def _protected(self, player):
        attempts, _, _, _ = self.state()
        rows = read_complete(self.jobs_log)
        return sum(not self._terminal(q["question_id"], player, 1, attempts, rows) for q in self.campaign["questions"])

    def execute(self, job: Job) -> dict:
        self._recover(job.player)
        try:
            adapter = self.loader(job.player)
            pf = self.pf(job.player, job.tier, adapter) if self.pf else F.preflight(job.player, cfg=self.cfg, tier=job.tier, adapter=adapter, continuous=self.continuous)
        except Exception as exc:
            pf = F.Preflight(job.player, tier=job.tier, problems=["preflight error: " + type(exc).__name__])
        result = {"type": "scheduler_job_finished", "run_id": self.run_id, "version": VERSION,
                  "campaign_id": self.campaign["campaign_id"], "question_id": job.question_id,
                  "question_snapshot_id": job.snapshot["snapshot_id"], "player": job.player, "tier": job.tier,
                  "urgent_override": job.urgent_override, "estimated_completion_s": job.estimate_s,
                  "started_at": to_iso(self.now())}
        if not pf.ok:
            result.update(outcome="preflight_blocked", preflight=pf.summary())
        else:
            shared = {"scheduler_run_id": self.run_id}
            def admit(request):
                return self.budget.reserve(request, pending_tier1=self._protected(job.player),
                    expected_new=self.campaign["expected_new_questions"], repair_reserve=self.campaign["repair_reserve"])
            def recheck():
                if self.execution_deadline is not None and self.now() >= self.execution_deadline:
                    raise F.QuestionDone("cycle time limit")
                if hash_obj(load_run_config()) != self.campaign["run_config_sha256"] or hash_obj(load_quota_policy()) != self.campaign["quota_policy_sha256"]:
                    raise F.LineStop("campaign configuration changed during execution")
                latest = self.provider().get(job.question_id)
                if latest is None or hash_obj(latest) != hash_obj(job.snapshot):
                    raise F.QuestionDone("question snapshot revised; no stale-input call")
                if reason := F.eligible(latest, self.cfg, self.now()):
                    raise F.QuestionDone(reason)
                if not load_players_enabled().get(job.player):
                    raise F.LineStop("line disabled")
            def before(kind, attempt_id):
                recheck()
                decision = admit({"player": job.player, "tier": 1, "question_id": job.question_id,
                                  "attempt_kind": kind, "attempt_id": attempt_id})
                if not decision["allowed"]:
                    if decision.get("reason") == "quota_5h_wait":
                        raise F.LineStop("quota_5h_wait", quota_retry_at_utc=decision["quota_retry_at_utc"])
                    raise F.QuestionDone("budget_skipped: " + decision["reason"])
                shared["admission_decision_id"] = decision["decision_id"]
            ctx = F.Context(player=job.player, adapter=adapter, cfg=self.cfg, run_id=self.run_id, preflight=pf,
                tier=job.tier, now=self.now, forecasts_log=self.root / "forecasts.jsonl", audits_log=self.root / "audits.jsonl",
                line_status_log=self.root / "line_status.jsonl", quota_log=self.root / "quota.jsonl", raw_root=self.root / "raw",
                reconciliation=shared, before_attempt=before, recheck_inputs=recheck,
                admission_outcome=self.budget.outcome, continuous=self.continuous, **self.extra(job.player))
            try:
                if job.tier == 2:
                    output = R.run(ctx, job.snapshot, admit=admit, estimated_completion_s=job.estimate_s)
                    # A denied upgrade is a recorded decision for this campaign, not a reason to
                    # consume repeated prompt permutations while waiting for more budget.
                    if output.get("outcome") == "stopped_waiting" and output.get("reason") != "cycle time limit":
                        output = {**output, "outcome": "not_admitted"}
                else:
                    with F.Lock(self.root / f"forecast.{job.player}.lock"):
                        if stop := F.line_stopped(ctx.line_status_log, job.player, ctx.forecasts_log, now=self.now()):
                            raise F.LineStop(stop["reason"])
                        for r in read_complete(ctx.forecasts_log):
                            if r.get("player") == job.player and r.get("quota_retry_at_utc") and self.now() < parse_iso(r["quota_retry_at_utc"]):
                                raise F.LineStop("quota_exhausted: fixed backoff active")
                        if reason := F.eligible(job.snapshot, self.cfg, self.now()):
                            raise F.QuestionDone(reason)
                        output = F.forecast_question(ctx, job.snapshot)
                result.update(output)
                if job.tier == 1 and output.get("outcome") == "stopped_waiting" and output.get("reason") in (
                    "budget_skipped: weekly_attempt_capacity_reserved_or_exhausted",
                    "budget_skipped: argon_personal_or_tier1_reserve"):
                    result["outcome"] = "budget_skipped"
                if output.get("outcome") in ("timeout", "cli_error", "tool_denied", "incomplete"):
                    past = F.history(ctx.forecasts_log, job.player, job.question_id, job.tier)
                    if any(r.get("attempt_kind") == "rerun" and (not self.continuous or
                           r.get("question_snapshot_id") == job.snapshot["snapshot_id"]) for r in past):
                        result.update(outcome="attempts_exhausted", last_error=output["outcome"])
            except F.QuestionDone as exc:
                result.update(outcome="stopped_waiting", reason=str(exc))
            except F.LineStop as exc:
                result.update(outcome=exc.outcome,
                              reason=str(exc), notify_louis=exc.notify, quota_retry_at_utc=exc.quota_retry_at_utc)
                if exc.notify:
                    with transaction(self.root):
                        append_record(ctx.line_status_log, {"type": "line_stop", "player": job.player,
                            "run_id": self.run_id, "at": to_iso(self.now()), "reason": str(exc)})
        # Reconciliation returns its own typed record; keep the scheduler event identity so
        # terminal denials without a forecast are visible on restart as well as in this run.
        result.update(type="scheduler_job_finished", version=VERSION, run_id=self.run_id,
                      campaign_id=self.campaign["campaign_id"], player=job.player, tier=job.tier,
                      question_id=job.question_id, question_snapshot_id=job.snapshot["snapshot_id"])
        reason = str(result.get("reason") or "")
        decisions = result.get("admissions") or []
        budget_reason = (decisions[-1].get("reason") if decisions else None) or reason.removeprefix(
            "budget_skipped: ").removeprefix("budget admission denied: ")
        if result.get("outcome") in ("not_admitted", "budget_skipped", "stopped_waiting") \
                and budget_reason in TEMPORARY_BUDGET_REASONS:
            retry = to_iso(self.now() + timedelta(seconds=FS.TEMPORARY_RECHECK_S))
            if budget_reason == "weekly_attempt_capacity_reserved_or_exhausted":
                retry = self.campaign["budget_window_end"]
            elif budget_reason in BREAKER_WAIT_REASONS:
                stops = [r for r in read_complete(self.budget.log)
                         if r.get("type") == "budget_upgrade_stop" and r.get("player") == job.player
                         and r.get("reason") == budget_reason]
                if stops:
                    retry = stops[-1]["until"]
            result.update(outcome="budget_wait", budget_reason=budget_reason, reevaluate_after=retry,
                          budget_window_end=self.campaign["budget_window_end"])
        result["record_id"] = new_id("job")
        result["finished_at"] = to_iso(self.now())
        with transaction(self.root):
            append_record(self.jobs_log, result)
        return result

    def finalize(self, snapshots, *, force=()):
        newly_ready = []
        self.snapshot_ids = {qid: s["snapshot_id"] for qid, s in snapshots.items()}
        with transaction(self.root):
            attempts, aggregates, events, view = self.state()
            jobs = read_complete(self.jobs_log)
            enabled = load_players_enabled()
            for entry in self.campaign["questions"]:
                qid = entry["question_id"]
                snap = snapshots.get(qid)
                # Finalization must observe the same dynamic numeric gate as job
                # admission, including older snapshots without scaling metadata.
                # An expired but otherwise supported snapshot may still finalize.
                if snap is not None and snap.get("question_type") in N.TYPES and \
                        F.eligible(snap, self.cfg, self.now()) in ("unsupported_type", "unsupported_scaling"):
                    continue
                existing = next((a for a in aggregates if (self.continuous or a.get("scheduler_campaign_id") == self.campaign["campaign_id"])
                                 and a["question_id"] == qid and a.get("completion_status") == "complete_local"
                                 and (not self.continuous or a.get("question_snapshot_id") == self.snapshot_ids.get(qid))), None)
                if existing is not None:
                    # Crash after aggregate append must not permanently lose its comment.
                    snap = snapshots.get(qid)
                    if snap is not None:
                        if self._comment(snap, existing, attempts, aggregates, events):
                            newly_ready.append(qid)
                    continue
                snap = snapshots.get(qid)
                if snap is None:
                    continue
                deadline = F.decision_deadline(snap, self.cfg)
                expired = qid in force or (deadline is not None and self.now() >= deadline)
                first_done = all(not enabled[p] or self._terminal(qid, p, 1, attempts, jobs) for p in PLAYERS)
                candidate = E.evaluate(snap, attempts, audit_events=view, now=self.now())
                second_done = not candidate["escalation"]["candidate"] or all(
                    not enabled[p] or self._terminal(qid, p, 2, attempts, jobs) for p in PLAYERS)
                if not expired and not (first_done and second_done):
                    continue
                final = A.build_aggregate(snap, attempts, stage="final", audit_events=view, now=self.now())
                final.update(completion_status="complete_local", scheduler_campaign_id=self.campaign["campaign_id"],
                             scheduler_version=VERSION, completion_reason="decision_deadline" if expired else "all_lines_accounted_for",
                             submission_eligible=False)
                append_record(self.root / "aggregates.jsonl", final)
                aggregates.append(final)
                if self._comment(snap, final, attempts, aggregates, events):
                    newly_ready.append(qid)
            L.append_impacts(self.root / "audit_impacts.jsonl", L.ImpactGraph([*attempts, *aggregates]), events, now=self.now())
        if self.continuous and newly_ready and self.on_finalized is not None:
            self.on_finalized(newly_ready)
        return newly_ready

    def _comment(self, snap, final, attempts, aggregates, events):
        if final["status"] != "ready":
            return
        existing = [r for r in read_complete(self.root / "comments.jsonl") if r.get("aggregate_id") == final["record_id"]]
        if any(r["type"] == "comment" for r in existing):
            return
        try:
            comment = C.build(snap, final, [*attempts, *aggregates], events, now=self.now())
        except C.CommentError as exc:
            if any(r.get("error") == str(exc) for r in existing):
                return
            comment = {"type": "comment_failed", "aggregate_id": final["record_id"], "question_id": final["question_id"],
                       "error": str(exc), "at": to_iso(self.now())}
        append_record(self.root / "comments.jsonl", comment)
        return comment["type"] == "comment"

    def _pending_deadlines(self, snapshots):
        with transaction(self.root):
            sealed = {a.get("question_snapshot_id") for a in read_complete(self.root / "aggregates.jsonl")
                      if (self.continuous or a.get("scheduler_campaign_id") == self.campaign["campaign_id"])
                      and a.get("completion_status") == "complete_local"}
        return [F.decision_deadline(s, self.cfg) for s in snapshots.values()
                if s["snapshot_id"] not in sealed]

    def _wait_timeout(self, snapshots):
        if not self.continuous:
            return None
        deadlines = self._pending_deadlines(snapshots)
        return min([60.0, *(max(0.0, (d - self.now()).total_seconds()) for d in deadlines if d is not None)])

    def run(self, *, max_jobs=180, lock_held=False, paused_players=()) -> dict:
        if not self.continuous and load_submit_mode().get("mode") != "local":
            raise F.PreflightError("M2 scheduler only runs in local mode")
        if type(max_jobs) is not int or max_jobs <= 0:
            raise ValueError("max_jobs must be a positive integer")
        with nullcontext() if lock_held else F.Lock(self.root / "scheduler.lock"):
            if not self.continuous:
                register(self.root, self.campaign, now=self.now())
            self.budget.recover()
            for player in PLAYERS:
                self._recover(player)
            paused, active, results = set(paused_players), {}, []
            started = to_iso(self.now())
            with ThreadPoolExecutor(max_workers=3) as pool:
                while True:
                    if self.continuous and not active and self.handoff_deadline is not None and self.now() >= self.handoff_deadline:
                        self.handoff = True
                        break
                    snapshots = self.provider()
                    if self.continuous:
                        self.finalize(snapshots)
                    if (self.execution_deadline is None or self.now() < self.execution_deadline) and len(results) + len(active) < max_jobs:
                        jobs = self.jobs(snapshots, paused=paused, active={j.player for j in active.values()})
                        for job in jobs:
                            if job.player in {j.player for j in active.values()} or len(results) + len(active) >= max_jobs:
                                continue
                            if self.execution_deadline is not None and self.now() >= self.execution_deadline:
                                break
                            active[pool.submit(self.execute, job)] = job
                    if not active:
                        if self.continuous:
                            deadlines = self._pending_deadlines(snapshots)
                            if self.handoff_deadline is not None and self.now() >= self.handoff_deadline:
                                self.handoff = True
                                break
                            delay = idle_delay(self.now(), deadlines, self.handoff_deadline)
                            if delay is not None:
                                self.sleep(delay)
                                continue
                        break
                    completed, _ = wait(active, timeout=self._wait_timeout(snapshots), return_when=FIRST_COMPLETED)
                    for future in completed:
                        job = active.pop(future)
                        try:
                            result = future.result()
                        except Exception as exc:
                            if not self.continuous:
                                raise
                            result = {"type": "scheduler_job_finished", "version": VERSION, "run_id": self.run_id,
                                "campaign_id": self.campaign["campaign_id"], "player": job.player, "tier": job.tier,
                                "question_id": job.question_id, "question_snapshot_id": job.snapshot["snapshot_id"],
                                "outcome": "line_error", "reason": type(exc).__name__, "finished_at": to_iso(self.now())}
                            with transaction(self.root):
                                append_record(self.jobs_log, result)
                        results.append(result)
                        if result.get("outcome") not in TERMINAL:
                            paused.add(job.player)
                        # A production provider refreshes the live exclusion index after each item.
                        from m2_campaign import SafeSnapshots
                        if isinstance(self.provider, SafeSnapshots) or getattr(self.provider, "refresh_after_job", False):
                            self.provider(force=True)
                        else:
                            self.provider()
                    self.finalize(self.provider())
            if not self.handoff:
                self.finalize(self.provider())
            summary = {"type": "scheduler_run", "run_id": self.run_id, "version": VERSION, "mode": load_submit_mode()["mode"] if self.continuous else "local",
                       "continuous": self.continuous, "handoff": self.handoff,
                       "campaign_id": self.campaign["campaign_id"], "started_at": started, "finished_at": to_iso(self.now()),
                       "jobs_completed": len(results), "paused_players": sorted(paused), "max_jobs": max_jobs}
            append_record(self.root / "scheduler_runs.jsonl", summary)
            return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True, help="pre-registered bounded M2 selection and caps")
    parser.add_argument("--max-jobs", type=int, default=180)
    args = parser.parse_args(argv)
    campaign = json.loads(args.campaign.read_text())
    from m2_campaign import SafeSnapshots
    from metaculus import MetaculusClient
    from flab.envfile import get_secret
    from flab.workdir import PROJECT_ROOT
    provider = SafeSnapshots(MetaculusClient(get_secret("METACULUS_TOKEN", PROJECT_ROOT / ".env")), F.DATA, campaign)
    result = Scheduler(F.DATA, campaign, snapshots=provider).run(max_jobs=args.max_jobs)
    print(json.dumps(result, ensure_ascii=False))  # Never print predictions or question text.
    return 0 if not result["paused_players"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
