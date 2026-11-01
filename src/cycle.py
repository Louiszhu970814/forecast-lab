"""One bounded continuous polling/forecast/submission cycle (cycle_v2).

Usage: .venv/bin/python src/cycle.py --campaign config/cycle_campaign.json
The campaign is explicit and finite; no implicit quota increases or launchd installation.
"""
from __future__ import annotations

import argparse
import copy
import importlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import continuous as Q
import forecast as F
import scheduler as S
from flab.config import PLAYERS, load_players_enabled, load_submit_mode, load_run_config
from flab.ids import new_id
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from flab.workdir import PROJECT_ROOT

VERSION = "cycle_v2"
INTERVAL_S = 600
SAFETY_S = 30
ADMISSION_WINDOW_S = INTERVAL_S - SAFETY_S - 60
SUBMIT_TIMEOUT_S = 120
CLEANUP_RESERVE_S = 10


def timing_limits(cfg, admission_window_s=ADMISSION_WINDOW_S):
    """Bound initial -> repair -> rerun -> repair, including Gemini quota queries.

    Lines run in parallel, so use the longest single line, not their sum. Rechecks
    stop subsequent attempts at admission close; the full chain is a conservative bound.
    """
    timeouts = cfg["timeouts_s"]
    worst_job_s = 2 * (max(timeouts["tier1"], timeouts["tier2"]) + timeouts["repair"]
                       + 4 * F.QUOTA_QUERY_S)
    worker_timeout_s = max(0, admission_window_s) + worst_job_s + SUBMIT_TIMEOUT_S
    return {"admission_window_s": admission_window_s, "worst_job_s": worst_job_s,
            "submit_timeout_s": SUBMIT_TIMEOUT_S, "cleanup_reserve_s": CLEANUP_RESERVE_S,
            "worker_timeout_s": worker_timeout_s,
            "supervisor_timeout_s": worker_timeout_s + CLEANUP_RESERVE_S}


def publish(root, scheduler, snapshots, queue):
    """Offer only current ready finals; keep revision fallbacks bound to the original snapshot.

    Publication is an additional aggregate with its own ID, never a rewrite of the local final.
    M3-W1 rejects invalidated snapshots in question_queue.jsonl before submission.
    """
    with transaction(root):
        attempts, aggregates, events, _ = scheduler.state()
        committed = Q.committed_questions(root)
        offered = [a for a in aggregates if a.get("submission_eligible") is True]
        stale = [a["record_id"] for a in offered if a.get("question_snapshot_id") in queue.invalidated
                 and a["question_id"] not in committed]
        published, rejected = [], []
        for qid, snap in snapshots.items():
            if qid in committed or snap["snapshot_id"] in queue.invalidated:
                continue
            prior = next((a for a in offered if a["question_id"] == qid and
                          a.get("question_snapshot_id") == snap["snapshot_id"]), None)
            if prior:
                scheduler._comment(snap, prior, attempts, aggregates, events)
                continue
            final = next((a for a in reversed(aggregates) if a["question_id"] == qid and
                          a.get("question_snapshot_id") == snap["snapshot_id"] and
                          a.get("completion_status") == "complete_local" and a.get("status") == "ready"), None)
            if final is None:
                continue
            offer = {**final, "record_id": new_id("agg"), "source_aggregate_id": final["record_id"],
                     "submission_eligible": True, "cycle_version": VERSION,
                     "revision_fallback": qid in queue.fallbacks, "created_at_utc": to_iso(scheduler.now())}
            try:
                comment = S.C.build(snap, offer, [*attempts, *aggregates, offer], events, now=scheduler.now())
            except S.C.CommentError:
                rejected.append(final["record_id"])
                continue
            append_record(root / "aggregates.jsonl", offer)
            aggregates.append(offer)
            append_record(root / "comments.jsonl", comment)
            published.append(offer["record_id"])
        return {"status": "ok", "aggregate_ids": published, "rejected_aggregate_ids": rejected,
                "stale_aggregate_ids": stale}


def submit_cli(project: Path, timeout_s: float):
    script = project / "src" / "submit.py"
    if not script.exists():
        return {"status": "unavailable", "reason": "M3-W1 submit.py is absent"}
    if timeout_s <= 0:
        return {"status": "deferred", "reason": "cycle time limit"}
    # submit.py owns mode, identity, whitelist and STOP_SUBMIT rechecks for every API write.
    result = subprocess.run([str(project / ".venv" / "bin" / "python"), str(script), "run"],
                            cwd=project, timeout=timeout_s, check=False)
    return {"status": "ok" if result.returncode == 0 else "error", "returncode": result.returncode}


def run_cycle(root: Path, campaign: dict, poller, *, project: Path = PROJECT_ROOT,
              now=utc_now, adapter_loader=lambda p: importlib.import_module(f"adapters.{p}"),
              preflight=None, context_extra=None, submit=submit_cli, cycle_id=None,
              deadline=None, acceptance_log=None, started_at=None):
    started = started_at or now()
    deadline = deadline or started + timedelta(seconds=ADMISSION_WINDOW_S)
    limits = timing_limits(load_run_config(), (deadline - started).total_seconds())
    rec = {"type": "cycle", "version": VERSION, "cycle_id": cycle_id or new_id("cycle"),
           "started_at": to_iso(started), "deadline": to_iso(deadline),
           "admission_deadline": to_iso(deadline), "timing_limits": limits,
           "steps": {s: {"status": "pending"} for s in ("lock", "switches", "policy", "poll", "queue", "preflight", "scheduler", "publish", "submit")},
           "players": {},
           "new_question_ids": [], "revised_question_ids": [], "errors": []}
    lock = F.Lock(root / "cycle.lock")
    scheduler_lock = F.Lock(root / "scheduler.lock")
    acquired = scheduler_acquired = False
    queue = None
    step = "lock"
    def checkpoint(next_step):
        nonlocal step
        if queue is not None:
            rec["new_question_ids"], rec["revised_question_ids"] = sorted(queue.new), sorted(queue.revised)
        step = next_step
        append_record(root / "cycle_progress.jsonl", {"type": "cycle_progress", "cycle_id": rec["cycle_id"],
            "at": to_iso(now()), "active_step": step, "summary": copy.deepcopy(rec)})
    try:
        try:
            lock.__enter__()
            acquired = True
            scheduler_lock.__enter__()
            scheduler_acquired = True
        except F.PreflightError:
            rec["status"] = "overlap_skipped"
            rec["steps"]["lock"] = {"status": "busy"}
            return rec
        rec["steps"]["lock"] = {"status": "ok"}
        checkpoint("switches")
        enabled = load_players_enabled()
        rec["steps"]["switches"] = {"status": "ok", "players_enabled": enabled,
            "stop_submit": (project / "config" / "STOP_SUBMIT").exists(), "mode": load_submit_mode()["mode"]}
        checkpoint("policy")
        Q.register(root, campaign, now=now)
        rec["steps"]["policy"] = {"status": "ok"}
        runtime = {**copy.deepcopy(campaign), "questions": []}
        queue = Q.Queue(root, runtime, poller, now=now)
        checkpoint("poll")
        snapshots = queue(force=True)
        rec["steps"]["poll"] = {"status": "ok", "questions": len(snapshots)}
        rec["steps"]["queue"] = {"status": "ok", "fallbacks": sorted(queue.fallbacks)}
        scheduler = S.Scheduler(root, runtime, snapshots=queue, now=now, adapter_loader=adapter_loader,
            preflight=preflight, context_extra=context_extra, continuous=True,
            execution_deadline=deadline,
            acceptance_log=acceptance_log or F.baseline.ACCEPTANCE_LOG)
        checkpoint("preflight")
        paused = set()
        for p in PLAYERS:
            if not enabled[p]:
                rec["players"][p] = {"status": "disabled"}
                paused.add(p)
                continue
            if now() >= scheduler.execution_deadline:
                rec["players"][p] = {"status": "deferred", "reason": "cycle time limit"}
                paused.add(p)
                continue
            try:
                adapter = adapter_loader(p)
                pf = preflight(p, 1, adapter) if preflight else F.preflight(p, cfg=scheduler.cfg, adapter=adapter, continuous=True)
                rec["players"][p] = {"status": "ready" if pf.ok else "preflight_blocked", "preflight": pf.summary()}
                if not pf.ok:
                    paused.add(p)
            except Exception as exc:
                rec["players"][p] = {"status": "preflight_blocked", "reason": type(exc).__name__}
                paused.add(p)
        rec["steps"]["preflight"] = {"status": "ok", "paused_players": sorted(paused)}
        checkpoint("scheduler")
        rec["steps"]["scheduler"] = scheduler.run(lock_held=True, paused_players=paused)
        for p in rec["steps"]["scheduler"]["paused_players"]:
            if rec["players"][p]["status"] == "ready":
                rows = read_complete(root / "scheduler_jobs.jsonl")
                last = next((r for r in reversed(rows) if r.get("player") == p and r.get("run_id") == scheduler.run_id), {})
                rec["players"][p] = {"status": last.get("outcome", "paused"),
                                     "reason": last.get("reason"), "preflight": last.get("preflight")}
        checkpoint("publish")
        # Refresh once more before publishing; revisions may arrive during the final model call.
        snapshots = queue(force=True)
        snapshots.update(queue.fallbacks)
        runtime["questions"] = [{"question_id": q, "ground": s["ground"], "priority": s["priority"]}
                                for q, s in snapshots.items()]
        scheduler.finalize(snapshots, force=queue.fallbacks)
        if (project / "config" / "STOP_SUBMIT").exists():
            rec["steps"]["submit"] = {"status": "stopped", "reason": "STOP_SUBMIT"}
        else:
            rec["steps"]["publish"] = publish(root, scheduler, snapshots, queue)
            scheduler_lock.__exit__(None, None, None)
            scheduler_acquired = False
            checkpoint("submit")
            rec["steps"]["submit"] = submit(project, SUBMIT_TIMEOUT_S)
        rec["status"] = "complete"
    except Exception as exc:
        # Exception text may contain credentials supplied by a transport; log only its class.
        rec["errors"].append({"type": type(exc).__name__, "step": step})
        rec["steps"][step] = {"status": "error", "error_type": type(exc).__name__}
        rec["status"] = "error"
    finally:
        if queue is not None:
            rec["new_question_ids"], rec["revised_question_ids"] = sorted(queue.new), sorted(queue.revised)
        for result in rec["steps"].values():
            if result.get("status") == "pending":
                result["status"] = "skipped"
        rec["finished_at"] = to_iso(now())
        # A competing cycle must not wait for the active scheduler's ledger transaction.
        try:
            append_record(root / "cycles.jsonl", rec)
        finally:
            if scheduler_acquired:
                scheduler_lock.__exit__(None, None, None)
            if acquired:
                lock.__exit__(None, None, None)
    return rec


def kill_worker_tree(pid):
    """Adapters create their own sessions; stopping only the worker group would orphan them."""
    stopped = set()
    try:
        os.kill(pid, signal.SIGSTOP)
        stopped.add(pid)
    except ProcessLookupError:
        pass
    cleanup_deadline = time.monotonic() + CLEANUP_RESERVE_S - 2
    try:
        while True:
            if time.monotonic() >= cleanup_deadline:
                raise subprocess.TimeoutExpired("worker tree cleanup", CLEANUP_RESERVE_S)
            output = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid="], text=True, timeout=2)
            children = {int(p): int(parent) for p, parent in (line.split() for line in output.splitlines())}
            found = {p for p, parent in children.items() if parent in stopped} - stopped
            if not found:
                break
            for child in found:
                try:
                    os.kill(child, signal.SIGSTOP)
                except ProcessLookupError:
                    pass
            stopped.update(found)
    finally:
        for child in stopped:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def supervise(command, *, timeout_s, popen=subprocess.Popen):
    """Bound polling, preflight, models and submit together, including hung external calls."""
    process = popen(command, start_new_session=True)
    try:
        return process.wait(timeout=max(0.01, timeout_s)), False
    except subprocess.TimeoutExpired:
        # The worker budget excludes the separately reserved cleanup time.
        code = 124
        try:
            kill_worker_tree(process.pid)
        except (OSError, ValueError, subprocess.SubprocessError):
            code = 125  # Worker killed, but descendant enumeration could not be verified.
        process.wait()
        return code, True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--cycle-id", help=argparse.SUPPRESS)
    parser.add_argument("--deadline", help=argparse.SUPPRESS)
    parser.add_argument("--started-at", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = PROJECT_ROOT / "data"
    if not args.worker:
        started = utc_now()
        cycle_id = new_id("cycle")
        deadline = started + timedelta(seconds=ADMISSION_WINDOW_S)
        limits = timing_limits(load_run_config())
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--campaign", str(args.campaign.resolve()),
                   "--cycle-id", cycle_id, "--deadline", to_iso(deadline), "--started-at", to_iso(started)]
        code, timed_out = supervise(command, timeout_s=limits["worker_timeout_s"] - (utc_now() - started).total_seconds())
        if timed_out or code:
            # A crash may have happened after the completed summary was fsynced.
            if not any(r.get("cycle_id") == cycle_id for r in read_complete(root / "cycles.jsonl")):
                progress = next((r for r in reversed(read_complete(root / "cycle_progress.jsonl"))
                                 if r.get("cycle_id") == cycle_id), {})
                summary = progress.get("summary", {"type": "cycle", "version": VERSION, "cycle_id": cycle_id,
                    "started_at": to_iso(started), "deadline": to_iso(deadline), "steps": {},
                    "players": {}, "new_question_ids": [], "revised_question_ids": [], "errors": []})
                summary.update(finished_at=to_iso(utc_now()), status="timeout" if timed_out else "worker_failed")
                summary.update(admission_deadline=to_iso(deadline), timing_limits=limits)
                summary["steps"][progress.get("active_step", "worker")] = {"status": "interrupted", "returncode": code}
                summary["errors"].append({"type": "cleanup_unverified" if code == 125 else
                                           "cycle_time_limit" if timed_out else "worker_exit"})
                append_record(root / "cycles.jsonl", summary)
        return code
    from flab.envfile import get_secret
    from metaculus import MetaculusClient
    campaign = json.loads(args.campaign.read_text())
    # Lazily construct the client only after acquiring the global lock and validating policy.
    def poller():
        client = MetaculusClient(get_secret("METACULUS_TOKEN", PROJECT_ROOT / ".env"))
        return Q.Poller(client, root, campaign)()
    result = run_cycle(root, campaign, poller, cycle_id=args.cycle_id,
                       started_at=parse_iso(args.started_at) if args.started_at else None,
                       deadline=parse_iso(args.deadline) if args.deadline else None)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
