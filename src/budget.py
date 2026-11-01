"""Durable attempt/token/Argon admission for bounded local M2 campaigns (PLAN §9).

No implicit capacity when a weekly cap, reset, cost or token baseline is unknown. A campaign
supplies explicit weekly caps and expected incoming work before viewing any forecasts.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import quota
from flab.ids import new_id
from flab.jsonl import append_record
from flab.ledger import transaction, read_complete
from flab.timeutil import parse_iso, to_iso, utc_now

VERSION = "budget_v2"


def quantile(values: list[float], q=.9) -> float | None:
    return sorted(values)[max(0, math.ceil(q * len(values)) - 1)] if values else None


def tokens(record: dict) -> int | None:
    usage = record.get("usage") or {}
    vals = [usage.get("input_tokens"), usage.get("output_tokens")]
    if any(type(v) is not int or v < 0 for v in vals):
        return None
    return sum(vals)  # Do not add cached/reasoning detail counters a second time.


def duration_estimate(records: list[dict], player: str, tier: int, cfg: dict) -> float:
    values = []
    for r in records:
        if r.get("player") != player or r.get("tier") != tier or r.get("attempt_kind") == "format_repair":
            continue
        try:
            duration = (parse_iso(r["finished_at_utc"]) - parse_iso(r["started_at_utc"])).total_seconds()
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(duration) and duration > 0:
            values.append(duration)
    # Full cap while unmeasured; validation and next before/after quota queries are explicit overhead.
    return (quantile(values[-50:]) or cfg["timeouts_s"][f"tier{tier}"]) + 60 + (60 if player == "gemini" else 0)


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


@dataclass
class Budget:
    root: Path
    campaign: dict
    policy: dict
    run_id: str
    now: Callable[[], datetime] = utc_now
    acceptance_log: Path | None = None

    @property
    def log(self):
        return self.root / "budget_events.jsonl"

    def _window(self):
        start, end = parse_iso(self.campaign["budget_window_start"]), parse_iso(self.campaign["budget_window_end"])
        if not start <= self.now() < end:
            raise ValueError("campaign budget window is inactive")
        return start, end

    def _data(self):
        return (read_complete(self.root / "forecasts.jsonl"), read_complete(self.root / "forecast_attempt_starts.jsonl"),
                read_complete(self.root / "quota.jsonl"), read_complete(self.log))

    def _drop(self, attempt: dict, snapshots: list[dict], *, current=None, period="weekly") -> float | None:
        return quota.attempt_drop(attempt, snapshots, current=current, period=period)

    def recover(self):
        """Called while holding scheduler.lock: old no-start reservations never reached the CLI.

        Started but missing outcomes are retained and block the line; they are not released.
        """
        with transaction(self.root):
            _, starts, _, events = self._data()
            started = {r["attempt_id"] for r in starts}
            finished = {r["attempt_id"] for r in events if r["type"] == "budget_outcome"}
            for r in events:
                if r["type"] == "budget_reserved" and r["run_id"] != self.run_id \
                        and r["attempt_id"] not in started | finished:
                    append_record(self.log, {"type": "budget_outcome", "attempt_id": r["attempt_id"],
                        "decision_id": r["decision_id"], "status": "released", "reason": "prior scheduler ended before CLI start",
                        "at": to_iso(self.now()), "version": VERSION})

    def reserve(self, request: dict, *, pending_tier1: int, expected_new: int, repair_reserve: int) -> dict:
        with transaction(self.root):
            start, end = self._window()
            forecasts, starts, snapshots, events = self._data()
            player, tier, qid, aid = (request[k] for k in ("player", "tier", "question_id", "attempt_id"))
            if tier not in (1, 2) or any(type(v) is not int or v < 0 for v in (pending_tier1, expected_new, repair_reserve)):
                raise ValueError("invalid tier or protected capacity")
            reservations = {r["attempt_id"]: r for r in events if r["type"] == "budget_reserved"}
            outcomes = {r["attempt_id"]: r for r in events if r["type"] == "budget_outcome"}
            if aid in reservations:
                raise ValueError("attempt already has a budget reservation")
            window_starts = [r for r in starts if r.get("player") == player and start <= parse_iso(r["started_at_utc"]) < end]
            done = {r["attempt_id"]: r for r in forecasts}
            active = [r for r in reservations.values() if r["player"] == player
                      and start <= parse_iso(r["at"]) < end and r["attempt_id"] not in done
                      and outcomes.get(r["attempt_id"], {}).get("status") != "released"]
            reason = None
            if active or any(r["attempt_id"] not in done for r in window_starts):
                reason = "unresolved_or_running_attempt"
            acceptance = read_complete(self.acceptance_log) if self.acceptance_log else []
            calls = [r for r in acceptance if r.get("type") == "acceptance_call_started" and r.get("player") == player]
            journaled_runs = {r["run_id"] for r in calls}
            acceptance_count = len({r["attempt_id"] for r in calls if start <= parse_iso(r["started_at"]) < end})
            acceptance_count += sum(r.get("model_call_count", 3) for r in acceptance
                if r.get("type") == "acceptance_result" and r.get("player") == player
                and r.get("run_id") not in journaled_runs and start <= parse_iso(r["finished_at"]) < end)
            cap = self.campaign["weekly_attempt_caps"].get(player)
            used_ids = {r["attempt_id"] for r in window_starts}
            used_ids.update(r["attempt_id"] for r in forecasts if r.get("player") == player
                and start <= parse_iso(r.get("started_at_utc") or r["finished_at_utc"]) < end)
            used = len(used_ids) + acceptance_count
            protected = pending_tier1 + expected_new + repair_reserve if tier == 2 else 0
            if type(cap) is not int or cap <= 0:
                reason = "weekly_attempt_cap_unset"
            elif used + protected + 1 > cap:
                reason = "weekly_attempt_capacity_reserved_or_exhausted"
            source = [r for r in forecasts if r.get("player") == player and r.get("tier") == 1
                      and r.get("attempt_kind") != "format_repair" and r.get("model_check") != "mismatch"]
            baselines = [n for r in source if (n := tokens(r)) is not None and n > 0]
            base_tokens = statistics.median(baselines) if baselines else None
            limit_tokens = base_tokens * self.policy["token_multiplier_cap"] if base_tokens else None
            estimate_tokens = None
            breach_reason = None
            scoped = [r for r in forecasts if r.get("player") == player and r.get("tier") == 2
                      and start <= parse_iso(r["started_at_utc"]) < end]
            if tier == 2:
                measured = [n for r in scoped if (n := tokens(r)) is not None and n > 0]
                estimate_tokens = quantile(measured[-50:]) or limit_tokens
                if limit_tokens is None:
                    reason = "tier1_token_baseline_unmeasured"
                per_question = {}
                for r in scoped:
                    per_question.setdefault(r["question_id"], []).append(tokens(r))
                if any(any(v is None for v in vals) or (limit_tokens is not None and sum(vals) > limit_tokens)
                       for vals in per_question.values()):
                    breach_reason = "weekly_tier2_token_breaker"
                own = per_question.get(qid, [])
                if limit_tokens is not None and (any(v is None for v in own) or sum(own) + estimate_tokens > limit_tokens):
                    reason = "question_tier2_token_capacity"
            argon_estimate, argon_remaining, reset = None, None, None
            quota_info = {}
            if player == "gemini":
                before = next((r for r in reversed(snapshots) if r.get("attempt_id") == aid and r.get("when") == "before"), None)
                buckets = quota.argon_buckets(before)
                bucket = buckets.weekly or {}
                quota_info = {"quota_layout": buckets.layout, "quota_weekly_bucket": bucket.get("id")}
                argon_remaining, reset = bucket.get("remaining_fraction"), bucket.get("reset_time")
                valid = buckets.layout != "unknown" and before and quota.credits_ok(before) and not before.get("stale") and not before.get("concurrent_agy_detected")
                try:
                    valid = valid and 0 <= (self.now() - parse_iso(before["taken_at"])).total_seconds() <= 90 \
                        and self.now() < parse_iso(reset)
                except (TypeError, KeyError, ValueError):
                    valid = False
                if not valid or not isinstance(argon_remaining, (float, int)) or isinstance(argon_remaining, bool) \
                        or not math.isfinite(argon_remaining) or not 0 <= argon_remaining <= 1:
                    reason = quota.UNKNOWN_LAYOUT_REASON if buckets.layout == "unknown" else "untrusted_argon_snapshot_or_reset"
                    valid = False
                    if not isinstance(argon_remaining, (float, int)) or isinstance(argon_remaining, bool) or not math.isfinite(argon_remaining):
                        argon_remaining = None
                samples = [drop for r in forecasts if r.get("player") == player and r.get("tier") == tier
                           and r.get("model_check") == "verified" and (drop := self._drop(r, snapshots, current=before)) is not None and drop > 0]
                argon_estimate = quantile(samples[-50:]) or self.policy["players"][player]["tier2_per_question_cap"]
                first_samples = [drop for r in source if r.get("model_check") == "verified"
                                 and (drop := self._drop(r, snapshots, current=before)) is not None and drop > 0]
                first_estimate = quantile(first_samples[-50:]) or self.policy["players"][player]["tier2_per_question_cap"]
                if valid and argon_remaining - argon_estimate - protected * first_estimate < 1 - self.policy["players"][player]["weekly_cap"]:
                    reason = "argon_personal_or_tier1_reserve"
                if tier == 2:
                    costs = {}
                    for r in scoped:
                        # Only the current Argon reset window belongs to this weekly breaker.
                        pair = next((s for s in snapshots if s.get("attempt_id") == r["attempt_id"] and s.get("when") == "before"), {})
                        old_buckets = quota.argon_buckets(pair)
                        old_reset = old_buckets.weekly.reset_time if old_buckets.weekly else None
                        if old_buckets.layout == buckets.layout and old_reset == reset:
                            costs.setdefault(r["question_id"], []).append(self._drop(r, snapshots, current=before))
                    qcap = self.policy["players"][player]["tier2_per_question_cap"]
                    if any(any(v is None for v in vals) or sum(vals) > qcap for vals in costs.values()):
                        breach_reason = "weekly_tier2_argon_breaker"
                    own = costs.get(qid, [])
                    if any(v is None for v in own) or sum(own) + argon_estimate > qcap + 1e-12:
                        reason = "question_tier2_argon_capacity"
            if tier == 2:
                paused = next((r for r in reversed(events) if r["type"] == "budget_upgrade_stop"
                               and r["player"] == player and self.now() < parse_iso(r["until"])
                               and (r["reason"] != "weekly_tier2_argon_breaker" or
                                    (r.get("quota_layout", "legacy_argon") == buckets.layout and r["until"] == reset))), None)
                if paused:
                    reason = paused["reason"]
                elif breach_reason:
                    reason = breach_reason
                    append_record(self.log, {"type": "budget_upgrade_stop", "player": player,
                        "reason": reason, "until": reset if player == "gemini" and valid else to_iso(end),
                        "at": to_iso(self.now()), "version": VERSION, **quota_info})
            if player == "gemini" and valid:
                fallback = self.policy["players"][player]["tier2_per_question_cap"]
                estimate_5h, samples_5h = quota.estimate(forecasts, snapshots, current=before,
                    fallback=fallback, tier=tier, period="five_hour")
                wait_reason, info = quota.five_hour_gate(before, estimate_5h)
                quota_info.update(info)
                if buckets.five_hour:
                    quota_info["quota_5h_estimate_samples"] = samples_5h
                if reason is None and wait_reason:
                    reason = wait_reason
            if player == "gemini" and buckets.layout == "unknown":
                reason = quota.UNKNOWN_LAYOUT_REASON
            decision = {"allowed": reason is None, "decision_id": new_id("budget"), "reason": reason,
                        "version": VERSION, "player": player, "tier": tier, "question_id": qid, "attempt_id": aid,
                        "weekly_attempts_used": used, "weekly_attempt_cap": cap, "protected_attempts": protected,
                        "token_baseline": base_tokens, "token_limit": limit_tokens, "token_estimate": estimate_tokens,
                        "argon_estimate": argon_estimate, "argon_remaining": argon_remaining, "argon_reset_at": reset, **quota_info}
            append_record(self.log, {"type": "budget_reserved" if reason is None else "budget_denied",
                                    **decision, "run_id": self.run_id, "at": to_iso(self.now()),
                                    "campaign_id": self.campaign["campaign_id"]})
            return decision

    def outcome(self, attempt_id: str, started: bool):
        with transaction(self.root):
            reservation = next((r for r in reversed(read_complete(self.log))
                                if r["type"] == "budget_reserved" and r["attempt_id"] == attempt_id), None)
            if reservation is None:
                raise ValueError("outcome has no reservation")
            append_record(self.log, {"type": "budget_outcome", "attempt_id": attempt_id,
                "decision_id": reservation["decision_id"], "status": "started" if started else "released",
                "at": to_iso(self.now()), "version": VERSION})
