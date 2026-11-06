"""Bounded, manually selected Cup advisory research. No submission transport.

One invocation researches ONE of the two user-selected posts on three independent
official CLIs, at most once per player. Existing forecast prompts/profiles are used.
Shared metadata accounting charges the active bot budget; answers stay separate.
"""
from __future__ import annotations

import argparse
import importlib
import json
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import aggregate
import baseline
import budget
import forecast as F
import validate as V
from flab.advisory_usage import usage, guards
from flab.config import load_quota_policy, load_run_config, load_players_enabled
from flab.ids import new_id
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from flab.workdir import PROJECT_ROOT
from metaculus import MetaculusClient
from flab.envfile import get_secret
from poll_questions import (poll, TournamentIndex, snapshot_from_post,
                            is_synced_copy, record_snapshots, latest_snapshots)

VERSION = "advisory_v1"
SELECTED_POSTS = {45330: 45519, 46106: 46343}
PLAYERS = ("claude", "gemini", "gpt")


def batch_path(value: Path, project=PROJECT_ROOT):
    root = value.resolve()
    allowed = (project / "data/advisory").resolve()
    if not root.is_relative_to(allowed) or root == allowed or not root.is_dir():
        raise ValueError("advisory batch must be an existing separate data/advisory directory")
    return root


def safe_snapshot(post, index, *, now=None):
    snap = snapshot_from_post(post, "human_cup")
    if snap is None or SELECTED_POSTS.get(snap["post_id"]) != snap["question_id"] \
            or 33108 not in snap["project_ids"]:
        raise ValueError("not a user-selected single Cup question")
    if snap["question_type"] not in ("binary", "multiple_choice") or snap["status"] != "open":
        raise ValueError("unsupported or no longer open")
    if is_synced_copy(snap, index):
        raise ValueError("overlaps an open/upcoming bot tournament question")
    current = now or utc_now()
    if parse_iso(snap["scheduled_close_time"]) <= current:
        raise ValueError("question is already due")
    snap["tournament_id"] = 33108
    return snap


def fresh_inputs(client, root, post_id, campaign):
    # Metadata-only reads; also rule out an incoming MiniBench job before advisory.
    index = client.get("projects/minibenches/")
    if not isinstance(index, list) or not index or not any(r.get("slug") == "minibench" for r in index):
        raise ValueError("missing MiniBench index")
    exclusions, active = {33121}, set()
    for r in index:
        if type(r.get("id")) is not int or type(r.get("is_ongoing")) is not bool:
            raise ValueError("incomplete MiniBench index")
        future = bool(r.get("start_date")) and parse_iso(r["start_date"]) > utc_now()
        if r["is_ongoing"] or future:
            exclusions.add(r["id"])
        if r["is_ongoing"] and not future:
            active.add(r["id"])
    records = [poll(client, pid, metadata_only=True, poll_log=root / "exclusion_polls.jsonl")
               for pid in sorted(exclusions)]
    if any(t["status"] == "open" for r in records if r["tournament_id"] in active
           for t in r["timing"]):
        raise ValueError("MiniBench has open work; advisory yields")
    snap = safe_snapshot(client.get_post(post_id), TournamentIndex.from_polls(records))
    record_snapshots([snap], log=root / "questions.jsonl")
    return latest_snapshots(root / "questions.jsonl")[snap["question_id"]]


def register_admission(shared, b, root, request, protected):
    # Same transaction as the normal scheduler. No forecasts/answers enter shared ledgers.
    with transaction(shared):
        admitted, _ = usage(shared)
        mine = [r for r in admitted.values() if r.get("version") == VERSION]
        if any(r.get("question_id") == request["question_id"] and r["player"] == request["player"] for r in mine):
            raise F.QuestionDone("player/question already admitted in any batch; no rerun")
        if len(mine) >= 6 or request["question_id"] not in SELECTED_POSTS.values():
            raise F.QuestionDone("advisory session call cap or question limit")
        decision = b.reserve(request, pending_tier1=0, expected_new=0, repair_reserve=0,
                             advisory_protected=protected)
        if not decision["allowed"]:
            raise F.QuestionDone("budget refused: " + str(decision["reason"]))
        append_record(shared / "advisory_usage.jsonl", {
            "type": "advisory_admitted", "version": VERSION, "at": to_iso(utc_now()),
            "attempt_id": request["attempt_id"], "player": request["player"],
            "question_id": request["question_id"], "batch": root.name, "batch_path": str(root.resolve()),
            "campaign_id": b.campaign["campaign_id"], "protected_attempts": protected,
            "decision_id": decision["decision_id"]})
        return decision


def finish_accounting(shared, root, player, aid):
    with transaction(root):
        starts = read_complete(root / "forecast_attempt_starts.jsonl")
        forecasts = read_complete(root / "forecasts.jsonl")
    started = any(r.get("attempt_id") == aid for r in starts)
    completed = any(r.get("attempt_id") == aid for r in forecasts)
    if completed and not started:
        raise ValueError("completed advisory attempt missing its start")
    # A start without a complete outcome stays charged AND blocks the line.
    if started and not completed:
        return
    with transaction(shared):
        admitted, finished = usage(shared)
        if aid in admitted and aid not in finished:
            append_record(shared / "advisory_usage.jsonl", {
                "type": "advisory_finished", "version": VERSION, "at": to_iso(utc_now()),
                "attempt_id": aid, "player": player, "started": started})


def run_line(player, snap, root, shared, campaign, cfg, policy, pf, adapter, *, admission=None, recheck=None):
    run_id = new_id("advisory")
    b = budget.Budget(shared, campaign, policy, run_id, acceptance_log=baseline.ACCEPTANCE_LOG)
    own = []
    # Conservatively reserve both bot tiers for expected arrivals plus repairs.
    protected = 2 * campaign["expected_new_questions"] + campaign["repair_reserve"]
    def check():
        if recheck is not None:
            recheck()
        if V.hash_obj(load_run_config()) != campaign["run_config_sha256"] \
                or V.hash_obj(load_quota_policy()) != campaign["quota_policy_sha256"]:
            raise F.LineStop("fixed configuration changed")
        if not load_players_enabled().get(player) or (shared.parent / "config/STOP_SUBMIT").exists():
            raise F.LineStop("line or project stopped")
        if F.eligible(snap, cfg, utc_now()):
            raise F.QuestionDone("question is no longer eligible")
    def before(kind, aid):
        check()
        decision = (admission or register_admission)(shared, b, root, {
            "player": player, "tier": 1, "question_id": snap["question_id"],
            "attempt_kind": kind, "attempt_id": aid}, protected)
        own.append(aid)
        shared_inputs["admission_decision_id"] = decision["decision_id"]
    shared_inputs = {"advisory_batch": str(root), "advisory_version": VERSION}
    ctx = F.Context(player=player, adapter=adapter, cfg=cfg, run_id=run_id, preflight=pf,
        quota_policy=policy, forecasts_log=root / "forecasts.jsonl", audits_log=root / "audits.jsonl",
        line_status_log=shared / "line_status.jsonl", quota_log=shared / "quota.jsonl",
        raw_root=root / "raw", reconciliation=shared_inputs,
        before_attempt=before, recheck_inputs=check, admission_outcome=b.outcome, continuous=True)
    try:
        rec = F.run_attempt(ctx, snap, kind="initial", prompt=F.forecast_prompt(snap, utc_now().date().isoformat()),
                            sch=V.question_schema(snap), prev=None)
        return {"player": player, "validation_status": rec["validation_status"],
                "error_type": rec.get("error_type"), "adoptable": F.adoptable(rec)}
    except F.LineStop as exc:
        line_event(shared, player, run_id, snap["question_id"], "engine_line_stop", stop=exc.notify)
        return {"player": player, "error_type": "LineStop", "notify": exc.notify, "adoptable": False}
    except Exception as exc:
        return {"player": player, "error_type": type(exc).__name__, "adoptable": False}
    finally:
        for aid in own:
            propagate_safety(shared, root, player, aid)
            finish_accounting(shared, root, player, aid)


def line_event(shared, player, run_id, qid, reason, *, stop, aid=None):
    with transaction(shared):
        append_record(shared / "line_status.jsonl", {
            "type": "line_stop" if stop else "advisory_line_yield", "source": "advisory",
            "version": VERSION, "at": to_iso(utc_now()), "player": player,
            "run_id": run_id, "question_id": qid, "attempt_id": aid, "reason": reason})


def propagate_safety(shared, root, player, aid):
    with transaction(root):
        row = next((r for r in read_complete(root / "forecasts.jsonl") if r.get("attempt_id") == aid), None)
    if not row:
        return
    if row.get("error_type") in ("model_mismatch", "quota_exhausted") or row.get("model_check") == "mismatch":
        with transaction(shared):
            if not any(r["attempt_id"] == aid for r in guards(shared)):
                append_record(shared / "advisory_guards.jsonl", {
                    "type": "advisory_attempt_guard", "version": VERSION,
                    **{k: row.get(k) for k in ("attempt_id", "player", "question_id", "finished_at_utc",
                        "pipeline_version", "model_check", "error_type", "quota_retry_at_utc")}})
    # A single advisory exposure conservatively stops the shared line: its body stays private.
    stop = bool(row.get("safety_stop_reason") or row.get("tool_violations") or
                row.get("error_type") == "tool_scope_violation" or row.get("recovery_no_rerun") or
                "leak_confirmed" in (row.get("own_audit_flags") or row.get("audit_flags") or []))
    reason = "advisory_safety_event" if stop else row.get("error_type")
    if reason:
        # Idempotent replay before a finished marker prevents crash gaps from hiding safety events.
        with transaction(shared):
            if not any(r.get("source") == "advisory" and r.get("attempt_id") == aid and
                       r.get("reason") == reason for r in read_complete(shared / "line_status.jsonl")):
                line_event(shared, player, row.get("run_id"), row.get("question_id"), reason, stop=stop, aid=aid)


def recover_accounting(shared, root, campaign, cfg, policy, players=PLAYERS):
    """Caller holds all six production locks. No model calls or replay of forecasts."""
    admitted, finished = usage(shared)
    mine = {aid: r for aid, r in admitted.items() if r.get("batch_path") == str(root.resolve())}
    for player in players:
        ctx = F.Context(player=player, adapter=importlib.import_module("adapters." + player), cfg=cfg,
            run_id=new_id("advisory_recovery"), preflight=None, quota_policy=policy,
            forecasts_log=root / "forecasts.jsonl", audits_log=root / "audits.jsonl",
            line_status_log=shared / "line_status.jsonl", quota_log=shared / "quota.jsonl",
            raw_root=root / "raw", continuous=True)
        F.recover_interrupted(ctx)
    starts = {r["attempt_id"] for r in read_complete(root / "forecast_attempt_starts.jsonl")}
    done = {r["attempt_id"] for r in read_complete(root / "forecasts.jsonl")}
    b = budget.Budget(shared, campaign, policy, new_id("advisory_recovery"))
    closed = []
    for aid, row in mine.items():
        if aid in finished or (aid in starts and aid not in done):
            continue
        propagate_safety(shared, root, row["player"], aid)
        b.outcome(aid, aid in starts)
        finish_accounting(shared, root, row["player"], aid)
        closed.append(aid)
    return {"status": "recovered", "closed_attempt_ids": closed,
            "unresolved_attempt_ids": sorted(set(mine) - set(usage(shared)[1]))}


def lock_all(shared, stack):
    for name in ("cycle", "scheduler", "submit", "forecast.claude", "forecast.gemini", "forecast.gpt"):
        stack.enter_context(F.Lock(shared / (name + ".lock")))


def recover(root, project=PROJECT_ROOT):
    root = batch_path(root, project)
    campaign = json.loads((project / "config/cycle_campaign.json").read_text())
    with ExitStack() as locks:
        lock_all(project / "data", locks)
        return {p: recover_accounting(project / "data", root / p, campaign,
            load_run_config(), load_quota_policy(), players=(p,)) for p in PLAYERS}


def run(root, post_id, project=PROJECT_ROOT):
    root = batch_path(root, project)
    shared = project / "data"
    if post_id not in SELECTED_POSTS:
        raise ValueError("post not selected by user")
    with ExitStack() as locks:
        lock_all(shared, locks)
        campaign = json.loads((project / "config/cycle_campaign.json").read_text())
        cfg, policy = load_run_config(), load_quota_policy()
        if campaign.get("venues") != ["minibench"]:
            raise ValueError("this advisory authorization requires MiniBench-only production")
        if V.hash_obj(cfg) != campaign["run_config_sha256"] or V.hash_obj(policy) != campaign["quota_policy_sha256"]:
            raise ValueError("fixed configuration mismatch")
        if not parse_iso(campaign["budget_window_start"]) <= utc_now() < parse_iso(campaign["budget_window_end"]):
            raise ValueError("active shared budget required")
        import budget_handoff
        budget_handoff.accounting_start(shared, campaign)
        with transaction(shared):
            admitted, finished = usage(shared)
            if set(admitted) != set(finished):
                raise ValueError("unresolved advisory attempt; recover it first")
            if any(r.get("version") == VERSION and r.get("question_id") == SELECTED_POSTS[post_id]
                   for r in admitted.values()):
                raise ValueError("question already admitted; no rerun")
        if (project / "config/STOP_SUBMIT").exists():
            raise ValueError("project stopped")
        starts = read_complete(shared / "forecast_attempt_starts.jsonl")
        done = {r.get("attempt_id") for r in read_complete(shared / "forecasts.jsonl")}
        if any(r.get("attempt_id") not in done for r in starts):
            raise ValueError("unresolved bot attempt")
        adapters = {p: importlib.import_module("adapters." + p) for p in PLAYERS}
        pfs = {p: F.preflight(p, cfg=cfg, adapter=adapters[p], continuous=True) for p in PLAYERS}
        if any(not pf.ok for pf in pfs.values()):
            return {"status": "preflight_blocked", "players": {p: pf.summary() for p, pf in pfs.items()}}
        for p in PLAYERS:
            if F.line_stopped(shared / "line_status.jsonl", p, shared / "forecasts.jsonl", now=utc_now()):
                raise ValueError("production line stopped")
            if F.leak_count(shared / "forecasts.jsonl", p, utc_now(),
                    resumed_at=F.latest_resume(shared / "line_status.jsonl", p)) >= F.LEAK_STOP_COUNT:
                raise ValueError("production line leak gate")
        for row in read_complete(shared / "forecasts.jsonl"):
            if row.get("error_type") == "quota_exhausted":
                until = row.get("quota_retry_at_utc") or to_iso(
                    parse_iso(row["finished_at_utc"]) + timedelta(seconds=F.QUOTA_BACKOFF_S))
                if utc_now() < parse_iso(until):
                    raise ValueError("production quota backoff active")
        client = MetaculusClient(get_secret("METACULUS_TOKEN", project / ".env"))
        snap = fresh_inputs(client, root, post_id, campaign)
        if F.eligible(snap, cfg, utc_now()):
            raise ValueError("advisory decision deadline reached")
        V.question_schema(snap)
        for p in PLAYERS:
            (root / p).mkdir(mode=0o700, exist_ok=True)
        append_record(root / "runs.jsonl", {"type": "advisory_batch_started", "at": to_iso(utc_now()),
            "version": VERSION, "post_id": post_id, "question_id": snap["question_id"],
            "max_initial_calls": 3, "automatic_retries": 0, "automatic_submissions": 0})
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(run_line, p, snap, root / p, shared, campaign, cfg, policy, pfs[p], adapters[p]) for p in PLAYERS]
            outcomes = [f.result() for f in futures]
        result = aggregate.build_aggregate(snap,
            [r for p in PLAYERS for r in read_complete(root / p / "forecasts.jsonl")],
            audit_events=[r for p in PLAYERS for r in read_complete(root / p / "audits.jsonl")])
        append_record(root / "aggregates.jsonl", result)
        summary = {"type": "advisory_batch_finished", "at": to_iso(utc_now()), "version": VERSION,
            "post_id": post_id, "question_id": snap["question_id"], "players": outcomes,
            "aggregate_status": result["status"], "missing_players": result["missing_players"]}
        append_record(root / "runs.jsonl", summary)
        return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", type=Path, required=True)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--post", type=int, choices=SELECTED_POSTS)
    group.add_argument("--recover", action="store_true")
    args = ap.parse_args()
    print(json.dumps(recover(args.batch) if args.recover else run(args.batch, args.post), ensure_ascii=False))


if __name__ == "__main__":
    main()
