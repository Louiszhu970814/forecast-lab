"""One bounded continuous polling/forecast/submission cycle (cycle_v8).

Usage: .venv/bin/python src/cycle.py --campaign config/cycle_campaign.json
The campaign is explicit and finite; no implicit quota increases or launchd installation.
"""
from __future__ import annotations

import argparse
import copy
import importlib
import json
import math
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
from submit import BUSY_EXIT_CODE
from flab.config import PLAYERS, load_players_enabled, load_submit_mode, load_run_config
from flab.ids import new_id
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from flab.workdir import PROJECT_ROOT

VERSION = "cycle_v9"
INTERVAL_S = 600
SAFETY_S = 30
ADMISSION_WINDOW_S = INTERVAL_S - SAFETY_S - 60
SUBMIT_TIMEOUT_S = 300
CLEANUP_RESERVE_S = 10


def timing_limits(cfg, admission_window_s=ADMISSION_WINDOW_S):
    """Bound initial -> repair -> rerun -> repair, including Gemini quota queries.

    Lines run in parallel, so use the longest single line, not their sum. Rechecks
    stop subsequent attempts at admission close; the full chain is a conservative bound.
    """
    timeouts = cfg["timeouts_s"]
    worst_job_s = 2 * (max(timeouts["tier1"], timeouts["tier2"]) + timeouts["repair"]
                       + 4 * F.QUOTA_QUERY_S)
    # Drain existing jobs, then retain enough time to reach a launchd handoff tick.
    # Exit 30 seconds before that tick without another network/submission tail.
    handoff_tick_s = math.ceil((max(0, admission_window_s) + worst_job_s +
                                2 * SUBMIT_TIMEOUT_S + SAFETY_S) / INTERVAL_S) * INTERVAL_S
    handoff_s = handoff_tick_s - SAFETY_S
    worker_timeout_s = handoff_tick_s + SUBMIT_TIMEOUT_S
    return {"admission_window_s": admission_window_s, "worst_job_s": worst_job_s,
            "handoff_s": handoff_s, "submit_timeout_s": SUBMIT_TIMEOUT_S, "cleanup_reserve_s": CLEANUP_RESERVE_S,
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
            scoped = {**snap, "forecast_scope": "snapshot"} if scheduler.continuous else snap
            final = next((a for a in reversed(aggregates) if a["question_id"] == qid and
                          a.get("question_snapshot_id") == snap["snapshot_id"] and
                          a.get("completion_status") == "complete_local" and
                          not a.get("submission_eligible")), None)
            prior = next((a for a in reversed(offered) if a["question_id"] == qid and
                          a.get("question_snapshot_id") == snap["snapshot_id"]), None)
            if prior is not None:
                if prior.get("fallback") == "salvaged_inputs":
                    try:
                        if final is not None and prior.get("source_aggregate_id") != final["record_id"]:
                            raise S.C.CommentError("fallback offer superseded by newer final aggregate")
                        S.C.build(scoped, prior, [*attempts, *aggregates], events, now=scheduler.now())
                    except S.C.CommentError:
                        rejected.append(prior["record_id"])
                        prior = None
                if prior is not None:
                    scheduler._comment(scoped, prior, attempts, aggregates, events)
                    continue
            if final is None or final.get("status") != "ready":
                continue
            offer = {**final, "record_id": new_id("agg"), "source_aggregate_id": final["record_id"],
                     "submission_eligible": True, "cycle_version": VERSION,
                     "revision_fallback": qid in queue.fallbacks, "created_at_utc": to_iso(scheduler.now())}
            try:
                comment = S.C.build(scoped, offer, [*attempts, *aggregates, offer], events, now=scheduler.now())
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
                            cwd=project, timeout=timeout_s, check=False, capture_output=True, text=True)
    base = {"returncode": result.returncode}
    if result.returncode == BUSY_EXIT_CODE:
        return {"status": "busy", **base}
    if result.returncode:
        return {"status": "error", **base}
    try:
        counts = json.loads(result.stdout)
        statuses = counts["statuses"]
        if set(counts) != {"events", "statuses"} or type(counts["events"]) is not int or counts["events"] < 0 or \
                not isinstance(statuses, dict) or any(k not in SUBMISSION_STATES or type(v) is not int or v < 0
                                                     for k, v in statuses.items()) or \
                sum(statuses.values()) != counts["events"]:
            raise ValueError("invalid submission counts")
    except (ValueError, TypeError, KeyError):
        return {"status": "error", "reason": "invalid submission counts", **base}
    return {"status": "attention" if any(statuses.get(s, 0) for s in ATTENTION_STATES) else "ok",
            **base, **counts}


ATTENTION_STATES = {"rejected_precheck", "unknown", "confirmed_failed", "forecast_failed", "comment_failed", "abandoned"}
SUBMISSION_STATES = ATTENTION_STATES | {"intent", "dry_run", "request_started", "forecast_ok", "comment_ok"}


def run_cycle(root: Path, campaign: dict, poller, *, project: Path = PROJECT_ROOT,
              now=utc_now, adapter_loader=lambda p: importlib.import_module(f"adapters.{p}"),
              preflight=None, context_extra=None, submit=submit_cli, cycle_id=None,
              deadline=None, acceptance_log=None, started_at=None, sleep=None):
    started = started_at or now()
    deadline = deadline or started + timedelta(seconds=ADMISSION_WINDOW_S)
    limits = timing_limits(load_run_config(), (deadline - started).total_seconds())
    rec = {"type": "cycle", "version": VERSION, "queue_refresh_version": Q.Queue.refresh_version,
           "cycle_id": cycle_id or new_id("cycle"),
           "started_at": to_iso(started), "deadline": to_iso(deadline),
           "admission_deadline": to_iso(deadline), "timing_limits": limits,
           "steps": {s: {"status": "pending"} for s in ("lock", "switches", "policy", "poll", "queue", "preflight", "scheduler", "publish", "submit")},
           "players": {},
           "new_question_ids": [], "revised_question_ids": [], "errors": []}
    rec["steps"]["on_finalized"] = {"status": "ok", "count": 0, "calls": []}
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
        if rec["steps"]["switches"]["mode"] == "live":
            import live_window as W
            W.require_active(load_submit_mode(), campaign, project / "config", now())
        campaign = Q.active_campaign(root, campaign, now=now)
        deadline = min(deadline, parse_iso(campaign["budget_window_end"]))
        rec["deadline"] = rec["admission_deadline"] = to_iso(deadline)
        rec["steps"]["policy"] = {"status": "ok", "campaign_id": campaign["campaign_id"],
            "budget_window_start": campaign["budget_window_start"], "budget_window_end": campaign["budget_window_end"]}
        runtime = {**copy.deepcopy(campaign), "questions": []}
        queue = Q.Queue(root, runtime, poller, now=now)
        checkpoint("poll")
        snapshots = queue(force=True)
        rec["steps"]["poll"] = {"status": "ok", "questions": len(snapshots)}
        rec["steps"]["queue"] = {"status": "ok", "fallbacks": sorted(queue.fallbacks)}
        def on_finalized(question_ids):
            previous_step = step
            calls = rec["steps"]["on_finalized"]
            call = {"question_ids": sorted(question_ids), "started_at": to_iso(now()), "status": "pending"}
            calls["calls"].append(call)
            calls["count"] += 1
            checkpoint("on_finalized")
            try:
                if (project / "config" / "STOP_SUBMIT").exists():
                    call.update(status="stopped", submit={"status": "stopped", "reason": "STOP_SUBMIT"})
                else:
                    current = queue()
                    current.update(queue.fallbacks)
                    call["publish"] = publish(root, scheduler, current, queue)
                    checkpoint("on_finalized")
                    call["submit"] = submit(project, SUBMIT_TIMEOUT_S)
                    call["status"] = call["submit"]["status"]
            except Exception as exc:
                call.update(status="error", error_type=type(exc).__name__)
                rec["errors"].append({"type": type(exc).__name__, "step": "on_finalized"})
            finally:
                call["finished_at"] = to_iso(now())
                call["elapsed_s"] = (parse_iso(call["finished_at"]) - parse_iso(call["started_at"])).total_seconds()
                checkpoint(previous_step)

        scheduler = S.Scheduler(root, runtime, snapshots=queue, now=now, adapter_loader=adapter_loader,
            preflight=preflight, context_extra=context_extra, continuous=True,
            execution_deadline=deadline, on_finalized=on_finalized, sleep=sleep,
            handoff_deadline=started + timedelta(seconds=limits["handoff_s"]),
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
        if scheduler.handoff:
            rec["steps"]["publish"] = {"status": "deferred", "reason": "launchd handoff"}
            rec["steps"]["submit"] = {"status": "deferred", "reason": "launchd handoff"}
            rec["status"] = "complete"
            return rec
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
        if rec.get("status") == "complete" and (rec["steps"]["submit"].get("status") == "attention" or
                any(c.get("status") == "attention" for c in rec["steps"]["on_finalized"]["calls"])):
            rec["status"] = "attention"
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
    parser.add_argument("--heartbeat", action="store_true", help="skip idle ticks without discovery or known work")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--cycle-id", help=argparse.SUPPRESS)
    parser.add_argument("--deadline", help=argparse.SUPPRESS)
    parser.add_argument("--started-at", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = PROJECT_ROOT / "data"
    if not args.worker:
        started = utc_now()
        live_end = None
        if load_submit_mode()["mode"] == "live":
            import live_window as W
            try:
                # D149: an ended window is followed by the next one instead of stopping all lines.
                if args.campaign.resolve() == (PROJECT_ROOT / "config" / "cycle_campaign.json").resolve():
                    renewed = W.renew(PROJECT_ROOT / "config", started, schedule_log=root / "live_schedule.jsonl")
                    if renewed:
                        print(json.dumps(renewed))
                policy = json.loads(args.campaign.read_text())
                live_start, live_end = W.bounds(load_submit_mode(), policy, PROJECT_ROOT / "config")
                state = ("waiting" if started < live_start else "expired" if started >= live_end
                         else "draining" if (live_end - started).total_seconds() <= CLEANUP_RESERVE_S else "active")
                event = {"type": "live_schedule", "version": VERSION, "at": to_iso(started),
                         "status": state, "campaign_id": policy["campaign_id"],
                         "start": to_iso(live_start), "end": to_iso(live_end)}
            except (OSError, ValueError, TypeError, KeyError) as exc:
                event = {"type": "live_schedule", "version": VERSION, "at": to_iso(started),
                         "status": "blocked", "error_type": type(exc).__name__}
                append_record(root / "live_schedule.jsonl", event)
                print(json.dumps(event))
                return 1
            if state != "active":
                append_record(root / "live_schedule.jsonl", event)
                print(json.dumps(event))
                return 0
        if args.heartbeat:
            import discovery
            policy = json.loads(args.campaign.read_text())
            if not discovery.needs_cycle(root, policy, started):
                return 3  # wrapper skips maintenance on this cheap no-op heartbeat
        if live_end is not None:
            append_record(root / "live_schedule.jsonl", event)
        cycle_id = new_id("cycle")
        deadline = started + timedelta(seconds=ADMISSION_WINDOW_S)
        if live_end is not None:
            deadline = min(deadline, live_end - timedelta(seconds=CLEANUP_RESERVE_S))
        limits = timing_limits(load_run_config())
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--campaign", str(args.campaign.resolve()),
                   "--cycle-id", cycle_id, "--deadline", to_iso(deadline), "--started-at", to_iso(started)]
        timeout_s = limits["worker_timeout_s"] - (utc_now() - started).total_seconds()
        if live_end is not None:
            timeout_s = min(timeout_s, (live_end - utc_now()).total_seconds() - CLEANUP_RESERVE_S)
        code, timed_out = supervise(command, timeout_s=timeout_s)
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
