"""D133: one registered, bounded closed FutureEval practice batch. GET only.

prepare freezes the complete accessible population before any model output; run
executes one selected question; recover never retries; report is metadata-only.
Neither forecasts nor resolutions are copied into the formal scoring population.
"""
from __future__ import annotations

import argparse
import importlib
import json
import threading
import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from pathlib import Path

import aggregate
import baseline
import budget
import forecast as F
import post_close_audit as A
import validate as V
from advise import lock_all
from flab.config import load_players_enabled, load_quota_policy, load_run_config
from flab.envfile import get_secret
from flab.external_usage import guards, practice_usage, usage
from flab.ids import hash_obj, new_id
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from flab.workdir import PROJECT_ROOT
from metaculus import MetaculusClient, MetaculusError
from poll_questions import TournamentIndex, is_synced_copy, poll, snapshot_from_post

VERSION = "closed_practice_v1"
PLAYERS = ("claude", "gemini", "gpt")
PROJECT_ID = 33121
LIMIT = 9
LIFECYCLE = (*A.REQUIRED_LEDGERS, "forecast_attempt_starts.jsonl",
             "reconciliation_inputs.jsonl", "reconciliation_runs.jsonl",
             "submission_readbacks.jsonl", "budget_events.jsonl", "advisory_usage.jsonl")
LOCK_BUDGET_S = 720


@contextmanager
def practice_locks(root, shared, phase):
    with ExitStack() as stack:
        lock_all(shared, stack)
        started = utc_now()
        append_record(root / "lock_events.jsonl", {"type": "practice_lock_acquired", "phase": phase,
                      "at": to_iso(started), "admission_budget_s": LOCK_BUDGET_S})
        try:
            yield started
        finally:
            stack.close()
            append_record(root / "lock_events.jsonl", {"type": "practice_lock_released", "phase": phase,
                          "at": to_iso(utc_now()), "elapsed_s": (utc_now() - started).total_seconds()})


def batch_path(value, project=PROJECT_ROOT):
    root = Path(value).resolve()
    if root.parent != (project / "data/practice").resolve() or not root.is_dir():
        raise ValueError("batch must be a direct, existing data/practice child")
    return root


def client_for(project):
    return MetaculusClient(get_secret("METACULUS_TOKEN", project / ".env"), max_retries=0)


def lifecycle_evidence(shared, qid, post_id=None, batch=None, budget_run_ids=None):
    """Read a consistent view while sibling practice lines admit/finish."""
    with transaction(shared):
        return _lifecycle_evidence(shared, qid, post_id, batch, budget_run_ids or set())


def _lifecycle_evidence(shared, qid, post_id, batch, budget_run_ids):
    evidence = {}
    practice, _ = practice_usage(shared)
    if any(r["question_id"] == qid and r["batch"] != batch for r in practice.values()):
        raise ValueError("question has prior practice lifecycle")
    own_ids = {aid for aid, r in practice.items() if r["batch"] == batch and r["question_id"] == qid}
    for name in LIFECYCLE:
        path = shared / name
        if not path.exists():
            raise ValueError("lifecycle ledger missing: " + name)
        rows = read_complete(path)
        if any((str(r.get("question_id")) == str(qid) or
                (post_id is not None and str(r.get("post_id")) == str(post_id)))
               and not (name == "budget_events.jsonl" and (r.get("attempt_id") in own_ids or r.get("run_id") in budget_run_ids)) for r in rows):
            raise ValueError("question has formal lifecycle; use post_close_audit")
        evidence[name] = hash_obj(rows)
    return evidence


def fresh_index(client, root, *, now=None, horizon_s=1200):
    now = now or utc_now()
    rounds = client.get("projects/minibenches/")
    if not isinstance(rounds, list) or not rounds or not any(r.get("slug") == "minibench" for r in rounds):
        raise ValueError("missing MiniBench discovery")
    ids, active = {PROJECT_ID}, set()
    for r in rounds:
        if type(r.get("id")) is not int or type(r.get("is_ongoing")) is not bool or not r.get("start_date"):
            raise ValueError("incomplete MiniBench discovery")
        starts = parse_iso(r["start_date"])
        if r["is_ongoing"] or starts > now:
            ids.add(r["id"])
            active.add(r["id"])
    polls = [poll(client, pid, metadata_only=True, poll_log=root / "exclusion_polls.jsonl") for pid in sorted(ids)]
    for r in polls:
        for t in r["timing"]:
            if t["status"] not in ("open", "upcoming") or not t.get("open_time"):
                raise ValueError("uncertain open/upcoming exclusion coverage")
            if r["tournament_id"] in active and (t["status"] == "open" or
                    parse_iso(t["open_time"]) <= now + timedelta(seconds=max(1200, horizon_s))):
                raise ValueError("formal_work_priority")
    return TournamentIndex.from_polls(polls)


def closed_snapshot(post, index, *, now=None):
    now = now or utc_now()
    if not isinstance(post, dict) or not isinstance(post.get("question"), dict):
        raise ValueError("single-question data unavailable")
    q = post["question"]
    snap = snapshot_from_post(post, "closed_practice")
    if snap is None or PROJECT_ID not in snap["project_ids"]:
        raise ValueError("not current-season FutureEval")
    ok, reasons, _ = A._gate1(post, snap, now, 15, None)
    if not ok:
        raise ValueError("platform_gate:" + ",".join(reasons))
    if q.get("status") not in A.CLOSED_STATUSES:
        raise ValueError("question status unknown")
    if not snap.get("supported"):
        raise ValueError("unsupported_type")
    for k in ("title", "description", "resolution_criteria"):
        if not isinstance(snap.get(k), str) or not snap[k].strip():
            raise ValueError("data_permission_or_missing_" + k)
    # The closed target naturally belongs to 33121. Other active memberships,
    # exact post/question IDs and suspected title copies still exclude it.
    other = TournamentIndex(index.project_ids - {PROJECT_ID}, index.question_ids,
                            index.post_ids, index.title_keys)
    if is_synced_copy(snap, other):
        raise ValueError("open_or_upcoming_synced_copy")
    V.question_schema(snap)
    snap.update(tournament_id=PROJECT_ID, purpose="closed_practice",
                snapshot_id="practice_" + hash_obj(snap))
    return snap


def remote_unforecasted(post):
    """Only existence metadata; never expose historical probabilities to a model."""
    q = post["question"]
    if "my_forecasts" not in q:
        raise ValueError("remote forecast history unavailable")
    value = q["my_forecasts"]
    if not isinstance(value, dict) or "history" not in value or "latest" not in value \
            or value["history"] != [] or value["latest"] is not None \
            or value.get("score_data") not in (None, {}):
        raise ValueError("remote prior forecast exists or is uncertain; use post_close_audit")


def frozen_versions(cfg):
    base = Path(__file__).resolve().parent.parent
    paths = ["src/practice.py", "src/flab/practice_contract.py", "src/flab/external_usage.py",
             "src/forecast.py", "src/budget.py", "src/aggregate.py", "src/validate.py", "src/numeric.py"]
    paths += [str(p.relative_to(base)) for p in (base / "prompts").glob("*") if p.is_file()]
    return {"pipeline": F.PIPELINE_VERSION, "budget": budget.VERSION, "practice": VERSION,
            "models": cfg.get("players"), "files": {p: hashlib.sha256((base / p).read_bytes()).hexdigest() for p in paths}}


def timing_classification(post):
    """No inference of event time from a platform close or scheduled resolution."""
    q = post["question"]
    known = q.get("resolution") is not None or q.get("actual_resolve_time") is not None
    return {"group": "retrospective_diagnostic" if known else "outcome_timing_unknown_diagnostic",
            "event_time": None, "event_time_evidence": None,
            "scheduled_resolve_time": q.get("scheduled_resolve_time"),
            "actual_resolve_time": q.get("actual_resolve_time"),
            "resolution_present": q.get("resolution") is not None,
            "clean_evaluation_eligible": False}


def campaign_settings(project):
    c = json.loads((project / "config/cycle_campaign.json").read_text())
    cfg, policy = load_run_config(), load_quota_policy()
    if c.get("venues") != ["minibench"] or hash_obj(cfg) != c["run_config_sha256"] \
            or hash_obj(policy) != c["quota_policy_sha256"]:
        raise ValueError("fixed MiniBench campaign/configuration mismatch")
    if not parse_iso(c["budget_window_start"]) <= utc_now() < parse_iso(c["budget_window_end"]):
        raise ValueError("shared campaign inactive")
    import budget_handoff
    budget_handoff.accounting_start(project / "data", c)
    if (project / "config/STOP_SUBMIT").exists():
        raise ValueError("project stopped")
    return c, cfg, policy


def prepare(root, project=PROJECT_ROOT, *, client=None):
    root = batch_path(root, project)
    shared = project / "data"
    with practice_locks(root, shared, "prepare") as lock_started:
        c, cfg, policy = campaign_settings(project)
        shared_ready(shared)
        if read_complete(shared / "practice_batches.jsonl") or (root / "manifest.jsonl").exists():
            raise ValueError("D133 batch already frozen; no replacement batch")
        client = client or client_for(project)
        index = fresh_index(client, root)
        # Never fall back to public scraping or another account for masked fields.
        population, usable = [], []
        for post in client.list_posts(PROJECT_ID, statuses=["closed", "resolved"], forecast_type=None):
            if (utc_now() - lock_started).total_seconds() >= LOCK_BUDGET_S - 60:
                raise F.QuestionDone("inventory time budget exhausted; no batch registered")
            q = post.get("question") or {}
            meta = {"post_id": post.get("id"), "question_id": q.get("id"), "status": q.get("status"),
                    "question_type": q.get("type"), "scheduled_close_time": q.get("scheduled_close_time"),
                    "scheduled_resolve_time": q.get("scheduled_resolve_time"), "excluded": None}
            try:
                snap = closed_snapshot(post, index)
                proof = lifecycle_evidence(shared, snap["question_id"], snap["post_id"])
                # Detail GET is mandatory for an actually readable candidate.
                detail = client.get_post(snap["post_id"])
                detailed = closed_snapshot(detail, index)
                if (detailed["post_id"], detailed["question_id"]) != (snap["post_id"], snap["question_id"]):
                    raise ValueError("detail identity changed")
                snap = detailed
                remote_unforecasted(detail)
                entry = {"question_id": snap["question_id"], "post_id": snap["post_id"],
                         "snapshot": snap, "lifecycle_evidence": proof, **timing_classification(detail)}
                usable.append(entry)
            except (ValueError, MetaculusError) as exc:
                meta["excluded"] = str(exc)
            population.append(meta)
        if (utc_now() - lock_started).total_seconds() >= LOCK_BUDGET_S - 60:
            raise F.QuestionDone("inventory time budget exhausted; no batch registered")
        # No outcomes, probabilities or confidence determine ordering. Reserve
        # the most recent close-time group, with no content sent to a model.
        usable.sort(key=lambda r: (parse_iso(r["snapshot"]["scheduled_close_time"]), r["question_id"]))
        latest_close = parse_iso(usable[-1]["snapshot"]["scheduled_close_time"]) if usable else None
        distinct_times = {parse_iso(r["snapshot"]["scheduled_close_time"]) for r in usable}
        holdout = [r for r in usable if parse_iso(r["snapshot"]["scheduled_close_time"]) == latest_close] if len(distinct_times) > 1 else []
        development = [r for r in usable if r not in holdout]
        development.sort(key=lambda r: (r["resolution_present"], parse_iso(r["snapshot"]["scheduled_close_time"]), r["question_id"]))
        selected = development[:3]
        at = to_iso(utc_now())
        manifest = {"type": "practice_manifest" if selected else "practice_inventory", "version": VERSION, "at": at, "batch": root.name,
                    "campaign_id": c["campaign_id"], "campaign_sha256": hash_obj(c),
                    "expires_at": c["budget_window_end"], "run_config_sha256": hash_obj(cfg),
                    "quota_policy_sha256": hash_obj(policy), "max_admissions": LIMIT,
                    "versions": frozen_versions(cfg),
                    "max_questions": 3, "automatic_retries": 0, "submissions": 0,
                    "selection_rule": "latest distinct close-time group held out; otherwise diagnostic only, future independent group required; unresolved first, close time then question ID",
                    "validation_status": "time_group_reserved" if holdout else "independent_future_group_required",
                    "validation_rule": "No parameter tuning from this batch; preregister unused later time/event group before validation.",
                    "comparison": "engineering diagnostics only; no clean backtest or official Peer Score",
                    "population": population, "selected": selected,
                    "holdout": [{k: r[k] for k in ("post_id", "question_id")} for r in holdout],
                    "remaining_unused": [r["question_id"] for r in usable if r not in selected and r not in holdout]}
        with transaction(shared):
            append_record(root / "manifest.jsonl", manifest)
            if selected:
                append_record(shared / "practice_batches.jsonl", {"type": "practice_batch_registered", "version": VERSION,
                    "at": at, "batch": root.name, "manifest_sha256": hash_obj(manifest),
                    "selected_question_ids": [r["question_id"] for r in selected], "max_admissions": LIMIT})
        return {"status": "registered" if selected else "inventory_only", "candidates": len(population), "selected": len(selected),
                "holdout": len(holdout), "manifest_sha256": hash_obj(manifest), "model_calls": 0}


def manifest_for(root, shared):
    rows = read_complete(root / "manifest.jsonl")
    reg = read_complete(shared / "practice_batches.jsonl")
    if len(rows) == 1 and rows[0].get("type") == "practice_inventory" and rows[0].get("selected") == []:
        return rows[0]
    if len(rows) != 1 or len(reg) != 1 or reg[0].get("batch") != root.name \
            or reg[0].get("manifest_sha256") != hash_obj(rows[0]):
        raise ValueError("unregistered or modified practice batch")
    return rows[0]


def register_admission(shared, b, line_root, request, manifest, decision_id):
    with transaction(shared):
        admitted, _ = practice_usage(shared)
        selected = {r["question_id"] for r in manifest["selected"]}
        if request["question_id"] not in selected or len(selected) > 3 or len(admitted) >= LIMIT \
                or request["tier"] != 1 or request["attempt_kind"] != "initial":
            raise F.QuestionDone("D133 question/call contract")
        if any(r["question_id"] == request["question_id"] and r["player"] == request["player"] for r in admitted.values()):
            raise F.QuestionDone("practice duplicate across batches; no rerun")
        protected = 2 * b.campaign["expected_new_questions"] + b.campaign["repair_reserve"]
        # The question-level start is already durable. Even if this metadata
        # append fails, the question cannot be restarted to fill missing lines.
        aid = request["attempt_id"]
        append_record(shared / "practice_usage.jsonl", {
            "type": "practice_admitted", "version": VERSION, "at": to_iso(b.now()),
            "attempt_id": aid, "player": request["player"], "question_id": request["question_id"],
            "batch": manifest["batch"], "batch_path": str(line_root.resolve()),
            "campaign_id": b.campaign["campaign_id"], "protected_attempts": protected,
            "decision_id": decision_id})
        return protected


def propagate_safety(shared, line_root, player, aid):
    row = next((r for r in read_complete(line_root / "forecasts.jsonl") if r.get("attempt_id") == aid), None)
    if not row:
        return
    with transaction(shared):
        own_flags = row.get("own_audit_flags", row.get("audit_flags")) or []
        if row.get("error_type") in ("model_mismatch", "quota_exhausted") or row.get("model_check") == "mismatch" \
                or "leak_confirmed" in own_flags:
            if not any(r["attempt_id"] == aid for r in guards(shared)):
                append_record(shared / "practice_guards.jsonl", {
                    "type": "practice_attempt_guard", "version": VERSION,
                    "own_audit_flags": [f for f in own_flags if f == "leak_confirmed"],
                    **{k: row.get(k) for k in ("attempt_id", "player", "question_id", "finished_at_utc",
                        "pipeline_version", "model_check", "error_type", "quota_retry_at_utc")}})
        stop = bool(row.get("safety_stop_reason") or row.get("tool_violations") or row.get("recovery_no_rerun")
                    or row.get("error_type") == "tool_scope_violation")
        if stop and not any(r.get("source") == "practice" and r.get("attempt_id") == aid
                            for r in read_complete(shared / "line_status.jsonl")):
            append_record(shared / "line_status.jsonl", {"type": "line_stop", "source": "practice",
                "at": to_iso(utc_now()), "player": player, "attempt_id": aid, "reason": "practice_safety_event"})


def finish_accounting(shared, line_root, player, aid, b):
    starts = {r["attempt_id"] for r in read_complete(line_root / "forecast_attempt_starts.jsonl")}
    done = {r["attempt_id"] for r in read_complete(line_root / "forecasts.jsonl")}
    if aid in done and aid not in starts:
        raise ValueError("practice completion without start")
    if aid in starts and aid not in done:
        return  # unknown stays charged and blocks the shared line
    propagate_safety(shared, line_root, player, aid)
    with transaction(shared):
        a, f = practice_usage(shared)
        reserved = any(r.get("type") == "budget_reserved" and r.get("attempt_id") == aid for r in read_complete(b.log))
        if aid not in a:
            if aid in starts:
                raise ValueError("practice start lacks admission; preserve charge and stop")
            if reserved:
                b.outcome(aid, False)
            return
        if aid in f:
            return
        if reserved:
            b.outcome(aid, aid in starts)
        append_record(shared / "practice_usage.jsonl", {"type": "practice_finished", "version": VERSION,
            "at": to_iso(b.now()), "attempt_id": aid, "player": player, "started": aid in starts})


def run_line(player, entry, root, shared, c, cfg, policy, pf, adapter, manifest, guard, clock, budget_run_ids=None):
    line_root = root / str(entry["question_id"]) / player
    line_root.mkdir(parents=True, exist_ok=True)
    b = budget.Budget(shared, c, policy, new_id("practice"), now=utc_now, acceptance_log=baseline.ACCEPTANCE_LOG)
    if budget_run_ids is not None:
        with transaction(shared):
            budget_run_ids.add(b.run_id)  # include denied/reserved before admission metadata
    own = []
    inputs = {"practice_batch": root.name, "practice_version": VERSION}
    def before(kind, aid):
        guard()
        line_ready(shared, player)
        # Budget.reserve precedes the metadata admission in the same transaction.
        # Its unknown reservation is retained if metadata writing fails.
        with transaction(shared):
            existing, _ = practice_usage(shared)
            if len(existing) >= LIMIT or any(r["player"] == player and r["question_id"] == entry["question_id"]
                                            for r in existing.values()):
                raise F.QuestionDone("practice cap or duplicate")
            request = {"player": player, "tier": 1, "question_id": entry["question_id"],
                       "attempt_id": aid, "attempt_kind": kind}
            protected = 2 * c["expected_new_questions"] + c["repair_reserve"]
            decision = b.reserve(request, pending_tier1=0, expected_new=0, repair_reserve=0,
                                 advisory_protected=protected)
            if not decision["allowed"]:
                raise F.QuestionDone("budget refused:" + decision["reason"])
            own.append(aid)
            register_admission(shared, b, line_root, request, manifest, decision["decision_id"])
            inputs["admission_decision_id"] = decision["decision_id"]
    def recheck():
        guard()
        line_ready(shared, player)
    ctx = F.Context(player=player, adapter=adapter, cfg=cfg, run_id=b.run_id, preflight=pf, now=utc_now,
        quota_policy=policy, forecasts_log=line_root / "forecasts.jsonl", audits_log=line_root / "audits.jsonl",
        line_status_log=shared / "line_status.jsonl", quota_log=shared / "quota.jsonl",
        raw_root=line_root / "raw", before_attempt=before, recheck_inputs=recheck,
        admission_outcome=b.outcome, continuous=True, reconciliation=inputs, closed_practice=clock)
    try:
        rec = F.run_attempt(ctx, entry["snapshot"], kind="initial", prev=None,
            prompt=F.forecast_prompt(entry["snapshot"], utc_now().date().isoformat()),
            sch=V.question_schema(entry["snapshot"]))
        return {"player": player, "validation_status": rec["validation_status"], "adoptable": F.adoptable(rec)}
    except F.LineStop as exc:
        done = {r.get("attempt_id") for r in read_complete(line_root / "forecasts.jsonl")}
        if exc.notify and not any(aid in done for aid in own):
            with transaction(shared):
                append_record(shared / "line_status.jsonl", {"type": "line_stop", "source": "practice",
                    "at": to_iso(utc_now()), "player": player, "attempt_id": own[-1] if own else None, "reason": str(exc)})
        return {"player": player, "adoptable": False, "error_type": "LineStop",
                "reason": str(exc), "quota_retry_at_utc": exc.quota_retry_at_utc}
    except Exception as exc:
        return {"player": player, "adoptable": False, "error_type": type(exc).__name__, "reason": str(exc)}
    finally:
        for aid in own:
            finish_accounting(shared, line_root, player, aid, b)


def line_ready(shared, player):
    if not load_players_enabled().get(player) or F.line_stopped(shared / "line_status.jsonl", player,
            shared / "forecasts.jsonl", now=utc_now()):
        raise ValueError("shared line stopped or cooling down")
    if F.leak_count(shared / "forecasts.jsonl", player, utc_now(),
                   resumed_at=F.latest_resume(shared / "line_status.jsonl", player)) >= F.LEAK_STOP_COUNT:
        raise ValueError("shared leak stop")
    for r in read_complete(shared / "forecasts.jsonl"):
        if r.get("player") == player and r.get("error_type") == "quota_exhausted":
            until = r.get("quota_retry_at_utc") or to_iso(parse_iso(r["finished_at_utc"]) + timedelta(seconds=F.QUOTA_BACKOFF_S))
            if utc_now() < parse_iso(until):
                raise ValueError("formal quota backoff")


def shared_ready(shared):
    admitted, finished = usage(shared)
    if set(admitted) != set(finished):
        raise ValueError("unknown external attempt; recover first")
    starts = read_complete(shared / "forecast_attempt_starts.jsonl")
    done = {r.get("attempt_id") for r in read_complete(shared / "forecasts.jsonl")}
    if any(r.get("attempt_id") not in done for r in starts):
        raise ValueError("unknown formal attempt")
    reasons, _ = A._reconcile_submissions(read_complete(shared / "submissions.jsonl"),
                                        read_complete(shared / "submission_readbacks.jsonl"))
    if reasons:
        raise ValueError("formal submission not converged")
    for p in PLAYERS:
        line_ready(shared, p)


def run(root, qid, project=PROJECT_ROOT, *, client=None):
    root = batch_path(root, project)
    shared = project / "data"
    with practice_locks(root, shared, "run") as lock_started:
        manifest = manifest_for(root, shared)
        c, cfg, policy = campaign_settings(project)
        if hash_obj(c) != manifest["campaign_sha256"]:
            raise ValueError("campaign changed")
        if frozen_versions(cfg) != manifest["versions"]:
            raise ValueError("practice source or models changed after registration")
        entry = next((r for r in manifest["selected"] if r["question_id"] == qid), None)
        if entry is None:
            raise ValueError("not a selected practice question")
        if any(r.get("question_id") == qid for r in read_complete(root / "runs.jsonl")):
            raise ValueError("question was already attempted; never restart or fill missing lines")
        shared_ready(shared)
        client = client or client_for(project)
        cutoff = min(lock_started + timedelta(seconds=LOCK_BUDGET_S), parse_iso(manifest["expires_at"]))
        check_lock = threading.Lock()
        last = [None]
        budget_run_ids = set()
        def guard():
            with check_lock:
                campaign_settings(project)
                if utc_now() >= cutoff - timedelta(seconds=60):
                    raise F.QuestionDone("practice total lock admission budget exhausted")
                lifecycle_evidence(shared, qid, entry["post_id"], root.name, budget_run_ids)
                if last[0] is None or (utc_now() - last[0]).total_seconds() >= 20:
                    index = fresh_index(client, root, horizon_s=(cutoff - utc_now()).total_seconds() + 120)
                    post = client.get_post(entry["post_id"])
                    fresh = closed_snapshot(post, index)
                    remote_unforecasted(post)
                    if any(fresh[k] != entry["snapshot"][k] for k in ("post_id", "question_id", "content_hash", "scheduled_close_time")):
                        raise F.QuestionDone("closed question changed or reopened")
                    append_record(root / "observations.jsonl", {"type": "timing_observation", "at": to_iso(utc_now()),
                        "question_id": qid, **timing_classification(post)})
                    last[0] = utc_now()
        guard()
        adapters = {p: importlib.import_module("adapters." + p) for p in PLAYERS}
        pfs = {p: F.preflight(p, cfg=cfg, adapter=adapters[p], continuous=True) for p in PLAYERS}
        if any(not pf.ok or pf.waivers for pf in pfs.values()):
            return {"status": "preflight_blocked", "players": {p: pf.summary() for p, pf in pfs.items()}}
        shared_ready(shared)
        last[0] = None  # preflight may consume most of the lock budget
        guard()
        started = utc_now()
        clock = {"batch": root.name, "batch_path": str(root), "started_at": to_iso(lock_started),
                 "cutoff": to_iso(cutoff)}
        append_record(root / "runs.jsonl", {"type": "practice_question_started", "at": to_iso(started),
                      "question_id": qid, "version": VERSION, "clock": clock})
        with ThreadPoolExecutor(max_workers=3) as pool:
            tasks = [pool.submit(run_line, p, entry, root, shared, c, cfg, policy, pfs[p], adapters[p], manifest, guard, clock, budget_run_ids)
                     for p in PLAYERS]
            outcomes = [t.result() for t in tasks]
        rows = [r for p in PLAYERS for r in read_complete(root / str(qid) / p / "forecasts.jsonl")]
        audits = [r for p in PLAYERS for r in read_complete(root / str(qid) / p / "audits.jsonl")]
        agg = aggregate.build_aggregate(entry["snapshot"], rows, audit_events=audits)
        agg.update(purpose="closed_practice", submission_eligible=False)
        append_record(root / "aggregates.jsonl", agg)
        summary = {"type": "practice_question_finished", "at": to_iso(utc_now()), "question_id": qid,
                   "version": VERSION, "players": outcomes, "aggregate_status": agg["status"],
                   "missing_players": agg["missing_players"], "clean_evaluation_eligible": False}
        append_record(root / "runs.jsonl", summary)
        return summary


def recover(root, project=PROJECT_ROOT):
    root = batch_path(root, project)
    shared = project / "data"
    with practice_locks(root, shared, "recover"):
        manifest = manifest_for(root, shared)
        c = json.loads((project / "config/cycle_campaign.json").read_text())
        cfg, policy = load_run_config(), load_quota_policy()
        a, f = practice_usage(shared)
        for aid, row in a.items():
            if aid in f or row["batch"] != root.name:
                continue
            line_root = Path(row["batch_path"]).resolve()
            if line_root != root / str(row["question_id"]) / row["player"]:
                raise ValueError("invalid recovery path")
            ctx = F.Context(player=row["player"], adapter=importlib.import_module("adapters." + row["player"]),
                cfg=cfg, run_id=new_id("practice_recovery"), preflight=None, quota_policy=policy,
                forecasts_log=line_root / "forecasts.jsonl", audits_log=line_root / "audits.jsonl",
                line_status_log=shared / "line_status.jsonl", quota_log=shared / "quota.jsonl",
                raw_root=line_root / "raw", continuous=True)
            F.recover_interrupted(ctx)
            b = budget.Budget(shared, c, policy, new_id("practice_recovery"))
            finish_accounting(shared, line_root, row["player"], aid, b)
        a, f = practice_usage(shared)
        return {"status": "recovered", "pending": sorted(set(a) - set(f)), "model_calls": 0}


def score(root, project=PROJECT_ROOT, *, client=None):
    """Score frozen outputs locally. Does not take or compete for formal locks."""
    from resolve import classify_resolution
    from score import score_prediction
    import numeric as N
    root = batch_path(root, project)
    shared = project / "data"
    with F.Lock(root / "score.lock"):
        manifest = manifest_for(root, shared)
        if not manifest["selected"]:
            return {"status": "no_selected_questions", "scored": 0}
        client = client or client_for(project)
        results = []
        frozen = read_complete(root / "aggregates.jsonl")
        for entry in manifest["selected"]:
            qid, snap = entry["question_id"], entry["snapshot"]
            r = {"type": "practice_local_scores", "at": to_iso(utc_now()), "question_id": qid,
                 "snapshot_id": snap["snapshot_id"], "question_type": snap["question_type"],
                 "source": "official_get_post", "group": entry["group"],
                 "clean_evaluation_eligible": False, "scores": [], "missing_players": [],
                 "official_peer_score": None}
            try:
                runs = [x for x in read_complete(root / "runs.jsonl") if x.get("question_id") == qid]
                if any(x["type"] == "practice_question_started" for x in runs) and not any(
                        x["type"] == "practice_question_finished" for x in runs):
                    r["resolution_status"] = "run_in_progress"
                    append_record(root / "scores.jsonl", r)
                    results.append({"question_id": qid, "resolution_status": "run_in_progress", "scored_outputs": 0})
                    continue
                post = client.get_post(entry["post_id"])
                fresh = snapshot_from_post(post, "closed_practice")
                if fresh is None or any(fresh.get(k) != snap.get(k) for k in
                        ("question_id", "post_id", "question_type", "scheduled_close_time", "options")):
                    raise ValueError("scoring identity/type/options/close changed")
                if snap["question_type"] in N.TYPES and N.scaling_changed(snap, fresh):
                    raise ValueError("scoring scale changed")
                if not A._gate1(post, snap, utc_now(), 15, None)[0]:
                    raise ValueError("scoring closure no longer established")
                state, outcome, raw, resolved_at = classify_resolution(post, snap)
                r.update(resolution_status=state, canonical_outcome=outcome,
                         platform_resolve_time=resolved_at,
                         text_changed_since_prediction=fresh.get("content_hash") != snap["content_hash"])
                forecasts = [x for p in PLAYERS for x in read_complete(root / str(qid) / p / "forecasts.jsonl")]
                audits = [x for p in PLAYERS for x in read_complete(root / str(qid) / p / "audits.jsonl")]
                inputs, excluded = aggregate.select_inputs(snap, forecasts, stage="tier1", audit_events=audits)
                r["missing_players"] = [p for p in PLAYERS if p not in {x["player"] for x in inputs}]
                aggs = [x for x in frozen if x.get("question_id") == qid]
                if len(aggs) > 1:
                    raise ValueError("multiple frozen aggregates")
                if state == "resolved":
                    if aggs and aggs[0]["status"] == "ready":
                        agg = aggs[0]
                        if not set(agg["input_attempt_ids"]) <= {x["attempt_id"] for x in inputs}:
                            r["aggregate_excluded"] = "input no longer eligible after audit"
                        else:
                            inputs.append(agg)
                    for x in inputs:
                        r["scores"].append({"source_id": x.get("attempt_id") or x["record_id"],
                            "player": x.get("player", "aggregate"), **score_prediction(x, snap, outcome)})
            except (ValueError, MetaculusError) as exc:
                r.update(resolution_status="unavailable", error=str(exc), scores=[])
            append_record(root / "scores.jsonl", r)
            results.append({"question_id": qid, "resolution_status": r["resolution_status"],
                            "scored_outputs": len(r["scores"])})
        return {"status": "local_diagnostics_only", "questions": results}


def report(root, project=PROJECT_ROOT):
    """Safe for shared progress: counts only. Private artifacts retain full evidence."""
    root = batch_path(root, project)
    manifest = manifest_for(root, project / "data")
    a, f = practice_usage(project / "data")
    a = {k: v for k, v in a.items() if v["batch"] == root.name}
    failures = {}
    for r in manifest["population"]:
        if r["excluded"]:
            failures[r["excluded"]] = failures.get(r["excluded"], 0) + 1
    rows = [r for q in manifest["selected"] for p in PLAYERS
            for r in read_complete(root / str(q["question_id"]) / p / "forecasts.jsonl")]
    audits = [r for q in manifest["selected"] for p in PLAYERS
              for r in read_complete(root / str(q["question_id"]) / p / "audits.jsonl")]
    flags = {}
    for r in rows:
        for flag in r.get("own_audit_flags", r.get("audit_flags")) or []:
            flags[flag] = flags.get(flag, 0) + 1
    adopted = [x for q in manifest["selected"] for x in aggregate.select_inputs(
        q["snapshot"], rows, stage="tier1", audit_events=audits)[0]]
    latest_scores = {}
    for r in read_complete(root / "scores.jsonl"):
        latest_scores[r["question_id"]] = r
    score_groups = {}
    for r in latest_scores.values():
        key = r["group"] + "/" + r["question_type"]
        group = score_groups.setdefault(key, {"questions": 0, "scored_outputs": 0, "resolution_states": {}})
        group["questions"] += 1
        group["scored_outputs"] += len(r["scores"])
        state = r["resolution_status"]
        group["resolution_states"][state] = group["resolution_states"].get(state, 0) + 1
    by_player = {}
    for player in PLAYERS:
        pr = [r for r in rows if r.get("player") == player]
        ar = [r for r in adopted if r.get("player") == player]
        pa = {k for k, v in a.items() if v["player"] == player}
        errors = {}
        for r in read_complete(root / "runs.jsonl"):
            for out in r.get("players", []):
                if out["player"] == player and out.get("error_type"):
                    key = out["error_type"]
                    errors[key] = errors.get(key, 0) + 1
        by_player[player] = {"outputs": len(pr), "valid": sum(r.get("validation_status") == "valid" for r in pr),
            "adoptable": len(ar), "missing_usable": len(manifest["selected"]) - len(ar),
            "admitted": len(pa), "pending": len(pa - set(f)), "failure_types": errors,
            "cost_unknown": sum(budget.tokens(r) is None for r in pr) + len(pa - set(f)),
            "known_tokens": sum(budget.tokens(r) or 0 for r in pr),
            "token_coverage": {"observed": sum(budget.tokens(r) is not None for r in pr), "outputs": len(pr)}}
    summary = {"type": "practice_report", "at": to_iso(utc_now()), "version": VERSION, "batch": root.name,
               "candidates": len(manifest["population"]), "exclusions": failures, "by_player": by_player,
               "selected": len(manifest["selected"]), "holdout": len(manifest["holdout"]),
               "admitted": len(a), "started": sum(f.get(k, {}).get("started") is True for k in a),
               "pending": len(set(a) - set(f)), "valid": sum(r.get("validation_status") == "valid" for r in rows),
               "adoptable": len(adopted), "failed_outputs": sum(r.get("validation_status") != "valid" for r in rows),
               "missing_outputs": len(manifest["selected"]) * 3 - len(rows),
               "missing_usable_outputs": len(manifest["selected"]) * 3 - len(adopted),
               "audit_flags": flags, "audits": len(audits),
               "durations_s": [(parse_iso(r["finished_at_utc"]) - parse_iso(r["started_at_utc"])).total_seconds()
                               if r.get("started_at_utc") and r.get("finished_at_utc") else None for r in rows],
               "cost_unknown": sum(budget.tokens(r) is None for r in rows) + len(set(a) - set(f)),
               "usage_observed": sum(budget.tokens(r) is not None for r in rows),
               "clean_evaluation_eligible": False, "local_score_groups": score_groups, "official_scores": None,
               "research_quality": "Manual source/date/units/criteria review still required; valid is not factually verified.",
               "score_limitation": "No resolution access or pristine event-time evidence is inferred from closed status."}
    append_record(root / "reports.jsonl", summary)
    target = project / "reports/practice" / (root.name + "_" + new_id("report") + ".md")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x") as stream:
        stream.write("# D133 closed practice\n\n```json\n" + json.dumps(summary, ensure_ascii=False, indent=2) + "\n```\n")
        stream.write("\nPrivate diagnostic score details by group/type (latest observation per question):\n\n```json\n" +
                     json.dumps(list(latest_scores.values()), ensure_ascii=False, indent=2) + "\n```\n")
    return {**summary, "report_path": str(target)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "recover", "score", "report"))
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--question", type=int)
    args = parser.parse_args()
    if args.action == "run" and args.question is None:
        parser.error("run requires --question")
    result = run(args.batch, args.question) if args.action == "run" else globals()[args.action](args.batch)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
