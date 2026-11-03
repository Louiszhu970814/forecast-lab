"""Durable attempt/token/Argon admission for bounded local M2 campaigns (PLAN §9).

No implicit capacity when a weekly cap, reset, cost or token baseline is unknown. A campaign
supplies explicit weekly caps and expected incoming work before viewing any forecasts.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import quota
from flab import fail_soft as FS
from flab.ids import new_id
from flab.jsonl import append_record
from flab.ledger import transaction, read_complete
from flab.timeutil import parse_iso, to_iso, utc_now

VERSION = "budget_v7"
LIFT_AUTHORIZATION = "PLAN D121"
LIFT_PLAYERS = ("claude", "gpt", "gemini")
LIFT_REASONS = frozenset({"weekly_tier2_token_breaker", "weekly_tier2_argon_breaker", "tier2_token_breaker"})


def _player_lifts(events: list[dict], player: str, now: datetime) -> list[dict]:
    # A future-dated event cannot forgive usage before its effective time.
    return [r for r in events if r["type"] == "budget_upgrade_stop_lifted"
            and r["player"] == player and parse_iso(r["at"]) <= now]


def _stop_lifted(stop: dict, lifts: list[dict]) -> bool:
    return any(r["player"] == stop["player"] and stop["at"] in r["lifted_stop_ats"]
               and parse_iso(stop["at"]) < parse_iso(r["at"]) for r in lifts)


def lift_tier2_stops(root: Path, players: list[str], authorization: str, *,
                     now: Callable[[], datetime] = utc_now) -> list[dict]:
    """Append D121 lifts for unexpired, unlifted persisted tier-2 stops.

    Validate every requested player before writing. No quota query or campaign/forecast
    read is needed; admission retains its existing Argon layout/reset checks.
    """
    if authorization != LIFT_AUTHORIZATION:
        raise ValueError(f"authorization must be {LIFT_AUTHORIZATION!r}")
    if not players or any(p not in LIFT_PLAYERS for p in players):
        raise ValueError("unknown player for tier 2 stop lift")
    with transaction(root):
        log = root / "budget_events.jsonl"
        events = read_complete(log)
        # Compare the timestamp we actually persist, including its millisecond precision.
        at = to_iso(now())
        current = parse_iso(at)
        records = []
        for player in dict.fromkeys(players):
            lifts = _player_lifts(events, player, current)
            stops = [r for r in events if r["type"] == "budget_upgrade_stop"
                     and r["player"] == player and r["reason"] in LIFT_REASONS
                     and current < parse_iso(r["until"]) and not _stop_lifted(r, lifts)]
            if not stops:
                raise ValueError(f"{player}: no active tier 2 stops to lift")
            if any(parse_iso(r["at"]) >= current for r in stops):
                raise ValueError(f"{player}: lift time must be later than every stop")
            records.append({"type": "budget_upgrade_stop_lifted", "player": player,
                            "lifted_reasons": list(dict.fromkeys(r["reason"] for r in stops)),
                            "lifted_stop_ats": list(dict.fromkeys(r["at"] for r in stops)),
                            "authorization": authorization, "at": at, "version": VERSION})
        for record in records:
            append_record(log, record)
        return records


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
        import budget_handoff as H
        return H.accounting_start(self.root, self.campaign), end

    def _data(self):
        return (read_complete(self.root / "forecasts.jsonl"), read_complete(self.root / "forecast_attempt_starts.jsonl"),
                read_complete(self.root / "quota.jsonl"), read_complete(self.log))

    def _drop(self, attempt: dict, snapshots: list[dict], *, current=None, period="weekly") -> float | None:
        return quota.attempt_drop(attempt, snapshots, current=current, period=period)

    def recover(self):
        """Called while holding scheduler.lock: old no-start reservations never reached the CLI.

        Started reservations stay charged; forecast recovery closes orphan starts
        under the line lock instead of releasing their cost.
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
            if not any(r["type"] == "budget_policy_activated" and r.get("version") == VERSION for r in events):
                activation = {"type": "budget_policy_activated", "version": VERSION, "policy_version": FS.VERSION,
                              "at": to_iso(self.now()), "scope": "new reservations only",
                              "preserve_prior_stops_lifts_and_usage": True}
                append_record(self.log, activation)
                events.append(activation)
            player, tier, qid, aid = (request[k] for k in ("player", "tier", "question_id", "attempt_id"))
            if tier not in (1, 2) or any(type(v) is not int or v < 0 for v in (pending_tier1, expected_new, repair_reserve)):
                raise ValueError("invalid tier or protected capacity")
            reservations = {r["attempt_id"]: r for r in events if r["type"] == "budget_reserved"}
            outcomes = {r["attempt_id"]: r for r in events if r["type"] == "budget_outcome"}
            if aid in reservations:
                raise ValueError("attempt already has a budget reservation")
            player_starts = [r for r in starts if r.get("player") == player]
            window_starts = [r for r in player_starts if start <= parse_iso(r["started_at_utc"]) < end]
            done = {r["attempt_id"]: r for r in forecasts}
            active = [r for r in reservations.values() if r["player"] == player
                      and r["attempt_id"] not in done
                      and outcomes.get(r["attempt_id"], {}).get("status") != "released"]
            reason = None
            if active or any(r["attempt_id"] not in done for r in player_starts):
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
            breach_until = None
            scoped = [r for r in forecasts if r.get("player") == player and r.get("tier") == 2
                      and start <= parse_iso(r["started_at_utc"]) < end]
            if tier == 2:
                lifts = _player_lifts(events, player, self.now())
                measured = [n for r in scoped if (n := tokens(r)) is not None and n > 0]
                estimate_tokens = quantile(measured[-50:]) or limit_tokens
                if limit_tokens is not None and estimate_tokens is not None:
                    estimate_tokens = min(estimate_tokens, limit_tokens)
                if limit_tokens is None:
                    reason = "tier1_token_baseline_unmeasured"
                per_question = {}
                for r in scoped:
                    if r.get("error_type") not in ("timeout", "cli_error", "interrupted"):
                        per_question.setdefault(r["question_id"], []).append(tokens(r))
                # Only new, explicitly versioned reservations create breach decisions.
                # Their saved baseline is immutable, even if later tier-1 use grows.
                assessed = {r["attempt_id"] for r in events if r["type"] == "tier2_token_assessed"}
                for r in scoped:
                    reservation = reservations.get(r["attempt_id"], {})
                    if reservation.get("version") != VERSION or r["attempt_id"] in assessed:
                        continue
                    limit = reservation.get("token_limit")
                    countable = [x for x in scoped if x["question_id"] == r["question_id"]
                                 and parse_iso(x["finished_at_utc"]) <= parse_iso(r["finished_at_utc"])
                                 and x.get("error_type") not in ("timeout", "cli_error", "interrupted")]
                    values = [tokens(x) for x in countable]
                    status = ("incomplete" if r.get("error_type") in ("timeout", "cli_error", "interrupted")
                              else "tier2_tokens_unknown" if any(v is None for v in values)
                              else "exceeded" if limit is not None and sum(values) > limit else "within")
                    event = {"type": "tier2_token_assessed", "version": VERSION,
                             "policy_version": FS.VERSION, "player": player, "question_id": r["question_id"],
                             "attempt_id": r["attempt_id"], "status": status,
                             "started_at_utc": r["started_at_utc"],
                             "token_limit": limit, "token_baseline": reservation.get("token_baseline"),
                             "at": r["finished_at_utc"]}
                    append_record(self.log, event)
                    events.append(event)
                boundaries = [parse_iso(r["at"]) for r in lifts]
                boundaries += [parse_iso(r["until"]) for r in events
                               if r["type"] == "budget_upgrade_stop" and r["player"] == player
                               and parse_iso(r["until"]) <= self.now()]
                boundary = max(boundaries, default=start - timedelta(microseconds=1))
                breaches = [r for r in events if r["type"] == "tier2_token_assessed"
                            and r["player"] == player and r["status"] == "exceeded"
                            and boundary < parse_iso(r["at"]) <= self.now()
                            and self.now() - parse_iso(r["at"]) <= timedelta(seconds=FS.WINDOW_S)]
                if len({r["question_id"] for r in breaches}) >= FS.BREACH_COUNT:
                    breach_reason = "tier2_token_breaker"
                    breach_until = max(parse_iso(r["at"]) for r in breaches) + timedelta(seconds=FS.COOLDOWN_S)
                own = per_question.get(qid, [])
                if limit_tokens is not None and (any(v is None for v in own) or sum(own) + estimate_tokens > limit_tokens):
                    reason = "question_tier2_token_capacity"
                own_decisions = [r for r in events if r["type"] == "tier2_token_assessed"
                                 and r["player"] == player and r["question_id"] == qid
                                 and start <= parse_iso(r.get("started_at_utc", r["at"])) < end]
                if any(r["status"] == "exceeded" for r in own_decisions):
                    reason = "question_tier2_token_capacity"
                if any(r["status"] == "tier2_tokens_unknown" for r in own_decisions):
                    reason = "tier2_tokens_unknown"
            argon_estimate, argon_remaining, reset = None, None, None
            quota_info = {}
            if player == "gemini":
                before = next((r for r in reversed(snapshots) if r.get("attempt_id") == aid and r.get("when") == "before"), None)
                buckets = quota.argon_buckets(before)
                bucket = buckets.weekly or {}
                quota_info = {"quota_layout": buckets.layout, "quota_weekly_bucket": bucket.get("id")}
                argon_remaining, reset = bucket.get("remaining_fraction"), bucket.get("reset_time")
                valid = buckets.layout != "unknown" and before and quota.credits_ok(before) and not before.get("stale")
                try:
                    valid = valid and 0 <= (self.now() - parse_iso(before["taken_at"])).total_seconds() <= FS.SNAPSHOT_MAX_AGE_S \
                        and self.now() < parse_iso(reset)
                except (TypeError, KeyError, ValueError):
                    valid = False
                if not valid or not isinstance(argon_remaining, (float, int)) or isinstance(argon_remaining, bool) \
                        or not math.isfinite(argon_remaining) or not 0 <= argon_remaining <= 1:
                    reason = quota.UNKNOWN_LAYOUT_REASON if buckets.layout == "unknown" else "untrusted_argon_snapshot_or_reset"
                    valid = False
                    if not isinstance(argon_remaining, (float, int)) or isinstance(argon_remaining, bool) or not math.isfinite(argon_remaining):
                        argon_remaining = None
                # D96/D97: `/usage` reads are inconsistent, so per-call before/after deltas are never
                # costs. Estimates come from window-level usage (capped at the old fallback), and
                # Gemini tier 2 is bounded by the shared token breaker above, not by quota deltas.
                # Persisted Argon stops are honoured until expiry or an explicit D107 A3 lift.
                fallback = self.policy["players"][player]["tier2_per_question_cap"]
                settings = quota.estimate_settings(self.policy)
                argon_estimate, estimate_calls = quota.estimate(forecasts, snapshots, current=before,
                    fallback=fallback, tier=tier, period="weekly", **settings)
                first_estimate, _ = quota.estimate(forecasts, snapshots, current=before,
                    fallback=fallback, tier=1, period="weekly", **settings)
                quota_info.update(argon_estimate_method=quota.WINDOW_ESTIMATE_VERSION,
                                  argon_estimate_calls=estimate_calls)
                protected_quota = min(protected * first_estimate, FS.TIER1_QUOTA_RESERVE_CAP)
                quota_info["protected_quota"] = protected_quota
                if valid and argon_remaining - argon_estimate - protected_quota < 1 - self.policy["players"][player]["weekly_cap"]:
                    reason = "argon_personal_or_tier1_reserve"
            if tier == 2:
                paused = next((r for r in reversed(events) if r["type"] == "budget_upgrade_stop"
                               and r["player"] == player and self.now() < parse_iso(r["until"])
                               and not _stop_lifted(r, lifts)
                               and (r["reason"] != "weekly_tier2_argon_breaker" or
                                    (r.get("quota_layout", "legacy_argon") == buckets.layout and r["until"] == reset))), None)
                if paused:
                    reason = paused["reason"]
                elif breach_reason:
                    if self.now() < breach_until:
                        reason = breach_reason
                    append_record(self.log, {"type": "budget_upgrade_stop", "player": player,
                        "reason": breach_reason, "until": to_iso(breach_until),
                        "at": to_iso(self.now()), "version": VERSION, **quota_info})
            if player == "gemini" and valid:
                fallback = self.policy["players"][player]["tier2_per_question_cap"]
                estimate_5h, samples_5h = quota.estimate(forecasts, snapshots, current=before,
                    fallback=fallback, tier=tier, period="five_hour", **quota.estimate_settings(self.policy))
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
            rows = read_complete(self.log)
            if any(r["type"] == "budget_outcome" and r["attempt_id"] == attempt_id for r in rows):
                return
            reservation = next((r for r in reversed(rows)
                                if r["type"] == "budget_reserved" and r["attempt_id"] == attempt_id), None)
            if reservation is None:
                raise ValueError("outcome has no reservation")
            append_record(self.log, {"type": "budget_outcome", "attempt_id": attempt_id,
                "decision_id": reservation["decision_id"], "status": "started" if started else "released",
                "at": to_iso(self.now()), "version": VERSION})


def main(argv=None):
    parser = argparse.ArgumentParser(description="Append an authorized tier 2 breaker lift.")
    commands = parser.add_subparsers(dest="command", required=True)
    lift = commands.add_parser("lift-tier2-stops")
    lift.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "data",
                      help="ledger directory containing budget_events.jsonl")
    lift.add_argument("--player", action="append", choices=LIFT_PLAYERS, required=True)
    lift.add_argument("--authorization", required=True)
    args = parser.parse_args(argv)
    try:
        records = lift_tier2_stops(args.root, args.player, args.authorization, now=utc_now)
    except (ValueError, OSError):
        # Ledger parse errors can embed row contents; never print those or other data.
        parser.exit(1, "tier 2 lift refused: check authorization, active stops and ledger timestamps\n")
    for record in records:
        print(json.dumps({key: record[key] for key in
                          ("player", "lifted_reasons", "lifted_stop_ats", "at")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
