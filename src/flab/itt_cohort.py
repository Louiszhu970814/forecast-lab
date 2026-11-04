"""D128 preregistered, metadata-only live escalation cohort selection.

Pure/read-only. A submission or a completed Tier 2 is never an inclusion condition.
Input rows may be full ledger rows: only whitelisted metadata enters evidence hashes.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from flab.ids import hash_obj
from flab.timeutil import parse_iso, to_iso

VERSION = "live_itt_cohort_v1"
COHORT_ID = "d118_live_itt_v1"
CAMPAIGN_ID = "d118_minibench_20261103T0400Z"
BOT_ID = 311763
START = "2026-11-03T04:12:32.156Z"
END = "2026-11-10T04:00:00.000Z"
CAMPAIGN_SHA256 = "6239cd390b401376660f386e82dd9b1d3b0c7da080010d9730137938db7adc3d"
LEDGER_FILES = {"questions": "questions.jsonl", "forecasts": "forecasts.jsonl",
                "aggregates": "aggregates.jsonl", "budget_events": "budget_events.jsonl",
                "scheduler_runs": "scheduler_runs.jsonl", "reconciliation_runs": "reconciliation_runs.jsonl",
                "scheduler_jobs": "scheduler_jobs.jsonl",
                "campaigns": "continuous_campaigns.jsonl", "live_authorizations": "live_authorizations.jsonl"}
_COMMON = ("type", "record_id", "attempt_id", "prev_attempt_id", "question_id", "question_snapshot_id",
           "snapshot_id", "tier", "stage", "input_attempt_ids", "run_id", "campaign_id", "scheduler_campaign_id",
           "tier1_aggregate_id", "selected_attempt_id", "reconciliation_id", "reconciliation_run_id",
           "started_at_utc", "finished_at_utc", "created_at_utc", "started_at", "finished_at", "at",
           "recorded_at", "seen_at", "t0", "mode", "continuous", "tournament_id", "venue", "ground", "priority",
           "bot_id", "campaign_sha256", "sha256", "evaluation_sha256", "authorization_id", "project_ids",
           "aggregation_version", "aggregation_config_sha256", "implementation_version")


def _text(value):
    return value if isinstance(value, str) and value.strip() else None


def _dt(value):
    if not isinstance(value, str):
        return None
    try:
        return parse_iso(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _meta(row):
    out = {k: row[k] for k in _COMMON if k in row}
    if isinstance(row.get("escalation"), dict):
        out["escalation"] = {k: row["escalation"][k] for k in
                             ("candidate", "escalate", "evaluated_at_utc", "version", "run_config_version")
                             if k in row["escalation"]}
    if isinstance(row.get("campaign"), dict):
        # Campaign policies have no forecast contents; only identity/bounds are exposed.
        c = row["campaign"]
        out["campaign"] = {k: c.get(k) for k in ("campaign_id", "venues", "budget_window_start", "budget_window_end")}
        out["_computed_campaign_sha256"] = hash_obj(c)
    return out


def _row_time(row):
    keys = ("created_at_utc", "finished_at_utc", "finished_at", "at", "recorded_at", "seen_at", "t0", "started_at_utc", "started_at")
    vals = [row[k] for k in keys if row.get(k) is not None]
    parsed = [_dt(v) for v in vals]
    if not vals or any(v is None for v in parsed):
        return None
    return max(parsed)


def select_cohort(*, questions: Iterable[dict], forecasts: Iterable[dict], aggregates: Iterable[dict],
                  budget_events: Iterable[dict] = (), scheduler_runs: Iterable[dict] = (),
                  reconciliation_runs: Iterable[dict] = (), campaigns: Iterable[dict] = (),
                  live_authorizations: Iterable[dict] = (), scheduler_jobs: Iterable[dict] = (),
                  as_of: Any = None) -> dict:
    """Return all candidate/root episodes, classified included/excluded/unknown.

    Known future rows are omitted; undated/malformed rows are kept and cannot prove
    eligibility. ``aggregate_ids`` never contains a final with ambiguous ancestry.
    No probabilities, outcomes, reasoning, or submission statuses are inspected.
    Current runtime logs cannot prove capture of every first candidate decision;
    even an included observed episode must not support complete-population claims.
    """
    cutoff = as_of if isinstance(as_of, datetime) else _dt(as_of)
    if as_of is not None and cutoff is None:
        raise ValueError("invalid cohort as_of")
    if cutoff is not None and cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    sources = {}
    supplied = dict(questions=questions, forecasts=forecasts, aggregates=aggregates, budget_events=budget_events,
                    scheduler_runs=scheduler_runs, reconciliation_runs=reconciliation_runs,
                    campaigns=campaigns, live_authorizations=live_authorizations, scheduler_jobs=scheduler_jobs)
    for name, rows in supplied.items():
        clean = [_meta(r) for r in rows if isinstance(r, dict)]
        sources[name] = [r for r in clean if cutoff is None or _row_time(r) is None or _row_time(r) <= cutoff]
    fs, aggs = sources["forecasts"], sources["aggregates"]

    def index(rows, key):
        groups = {}
        for r in rows:
            if _text(r.get(key)):
                groups.setdefault(r[key], []).append(r)
        # Identical repeated metadata is harmless, conflicting identities fail closed.
        return {k: v[0] for k, v in groups.items()}, {k for k, v in groups.items() if len({hash_obj(r) for r in v}) > 1}

    by_att, bad_att = index(fs, "attempt_id")
    by_snap, bad_snap = index(sources["questions"], "snapshot_id")
    _, bad_agg = index(aggs, "record_id")
    by_run, bad_run = index(sources["scheduler_runs"], "run_id")

    def ancestry(ids, qid, sid):
        leaves, all_ids, active, done = set(), set(), set(), set()
        errors = set()
        def visit(aid):
            if aid in active:
                errors.add("cyclic_attempt_ancestry"); return
            if aid in done:
                return
            f = by_att.get(aid)
            if f is None or aid in bad_att or f.get("question_id") != qid or f.get("question_snapshot_id") != sid:
                errors.add("missing_or_conflicting_attempt_ancestry"); return
            if f.get("tier") not in (1, 2) or type(f.get("tier")) is not int:
                errors.add("invalid_attempt_tier"); return
            if _row_time(f) is None:
                errors.add("invalid_attempt_time")
            active.add(aid); all_ids.add(aid)
            parents = f.get("input_attempt_ids") or []
            if not isinstance(parents, list) or not all(_text(p) for p in parents):
                errors.add("invalid_attempt_ancestry"); parents = []
            prev = f.get("prev_attempt_id")
            if prev is not None and not _text(prev):
                errors.add("invalid_attempt_ancestry")
            if _text(prev):
                parents = [*parents, prev]
            if f.get("tier") == 1 and not parents:
                leaves.add(aid)
            elif f.get("tier") == 2 and not parents:
                errors.add("missing_tier2_root")
            for parent in parents:
                visit(parent)
            active.remove(aid); done.add(aid)
        if not isinstance(ids, list) or not ids or not all(_text(i) for i in ids):
            errors.add("missing_input_attempt_ids")
        else:
            for aid in ids: visit(aid)
        return leaves, all_ids, errors

    candidates = []
    for i, a in enumerate(aggs):
        esc = a.get("escalation") or {}
        if a.get("stage") != "tier1" or not (esc.get("candidate") is True or esc.get("escalate") is True):
            continue
        qid, sid = a.get("question_id"), a.get("question_snapshot_id")
        leaves, ids, errors = ancestry(a.get("input_attempt_ids"), qid, sid)
        if type(qid) is not int or not _text(sid): errors.add("invalid_episode_identity")
        if not _text(a.get("record_id")) or a.get("record_id") in bad_agg: errors.add("invalid_root_aggregate_id")
        direct_inputs = a.get("input_attempt_ids")
        if isinstance(direct_inputs, list) and all(_text(aid) for aid in direct_inputs) \
                and any(by_att.get(aid, {}).get("tier") != 1 for aid in direct_inputs):
            errors.add("non_tier1_candidate_input")
        decision = _dt(a.get("created_at_utc"))
        evaluated = _dt(esc.get("evaluated_at_utc"))
        if decision is None or (esc.get("evaluated_at_utc") is not None and evaluated != decision):
            errors.add("invalid_decision_time")
        candidates.append({"row": a, "leaves": leaves, "ids": ids, "errors": errors, "time": decision,
                           "fallback_id": f"missing_root:{i}"})

    # Shared originating T1 IDs mean the same source chain, including repairs or
    # additional surviving inputs. Unrelated runs on the same question stay apart.
    groups = []
    for c in candidates:
        matching = [g for g in groups if any(
            x["row"].get("question_id") == c["row"].get("question_id")
            and x["row"].get("question_snapshot_id") == c["row"].get("question_snapshot_id")
            and bool(x["leaves"] & c["leaves"]) for x in g)]
        merged = [c]
        for g in matching:
            merged.extend(g); groups.remove(g)
        groups.append(merged)
    episodes = []
    start, end = parse_iso(START), parse_iso(END)
    for group in groups:
        group.sort(key=lambda c: (c["time"] or datetime.min.replace(tzinfo=timezone.utc), str(c["row"].get("record_id"))))
        root = group[0]; a = root["row"]; qid, sid = a.get("question_id"), a.get("question_snapshot_id")
        errors = set().union(*(c["errors"] for c in group))
        root_id = _text(a.get("record_id")) or root["fallback_id"]
        aliases = sorted({_text(c["row"].get("record_id")) for c in group} - {None})
        def equivalent_baseline(other):
            if other.get("record_id") == root_id:
                return True
            keys = ("aggregation_version", "aggregation_config_sha256")
            return all(_text(a.get(k)) and a.get(k) == other.get(k) for k in keys) \
                and a.get("implementation_version") == other.get("implementation_version") \
                and isinstance(a.get("input_attempt_ids"), list) \
                and isinstance(other.get("input_attempt_ids"), list) \
                and all(_text(x) for x in a["input_attempt_ids"] + other["input_attempt_ids"]) \
                and sorted(a["input_attempt_ids"]) == sorted(other["input_attempt_ids"])
        baseline_aliases = sorted({_text(c["row"].get("record_id")) for c in group
                                   if equivalent_baseline(c["row"])} - {None})
        leaves = set().union(*(c["leaves"] for c in group))
        ids = set().union(*(c["ids"] for c in group))
        ep = {"episode_id": hash_obj([qid, sid, root_id]), "root_aggregate_id": root_id,
              "question_id": qid, "snapshot_id": sid, "decision_at_utc": to_iso(root["time"]) if root["time"] else None,
              "tier1_aggregate_ids": baseline_aliases, "related_tier1_aggregate_ids": aliases,
              "tier1_attempt_ids": sorted(ids), "tier2_attempt_ids": [], "scheduler_job_ids": [],
              "reconciliation_run_ids": [],
              "aggregate_ids": aliases[:], "_leaves": leaves, "_errors": errors, "_root": a,
              "evidence_run_ids": [], "status": "unknown", "reasons": []}
        episodes.append(ep)

    def matches(qid, sid, leaves):
        return [e for e in episodes if e["question_id"] == qid and e["snapshot_id"] == sid
                and leaves and leaves <= e["_leaves"]]

    assigned = set()
    for f in fs:
        if f.get("tier") != 2 or not _text(f.get("attempt_id")): continue
        leaves, ids, errors = ancestry([f["attempt_id"]], f.get("question_id"), f.get("question_snapshot_id"))
        found = matches(f.get("question_id"), f.get("question_snapshot_id"), leaves)
        if len(found) == 1:
            ep = found[0]; ep["tier2_attempt_ids"].append(f["attempt_id"]); ep["_errors"].update(errors)
            assigned.add(f["attempt_id"])
        else:
            for ep in found: ep["_errors"].add("ambiguous_tier2_root")
    def provenance(row):
        tokens = set()
        for key, kind in (("attempt_id", "attempt"), ("prev_attempt_id", "attempt"),
                          ("selected_attempt_id", "attempt"), ("reconciliation_id", "bundle"),
                          ("reconciliation_run_id", "reconciliation_run"), ("tier1_aggregate_id", "root")):
            if _text(row.get(key)):
                tokens.add((kind, row[key]))
        if row.get("type") == "reconciliation_run" and _text(row.get("record_id")):
            tokens.add(("reconciliation_run", row["record_id"]))
        if row.get("tier") == 2 and _text(row.get("attempt_id")):
            leaves, _, _ = ancestry([row["attempt_id"]], row.get("question_id"), row.get("question_snapshot_id"))
            tokens.update(("leaf", aid) for aid in leaves)
        if not tokens and row.get("type") == "scheduler_job_finished" and _text(row.get("run_id")):
            # Only groups rootless job diagnostics; it does not prove a root.
            tokens.add(("rootless_job_run", row["run_id"]))
        return tokens

    # Orphan or ambiguous T2 cannot disappear just because no candidate survived.
    for aid in sorted({f["attempt_id"] for f in fs if f.get("tier") == 2 and _text(f.get("attempt_id"))} - assigned):
        f = by_att[aid]
        episodes.append({"episode_id": hash_obj(["orphan", aid]), "root_aggregate_id": None,
                         "question_id": f.get("question_id"), "snapshot_id": f.get("question_snapshot_id"),
                         "decision_at_utc": None, "tier1_aggregate_ids": [], "related_tier1_aggregate_ids": [],
                         "tier1_attempt_ids": [], "scheduler_job_ids": [], "reconciliation_run_ids": [],
                         "tier2_attempt_ids": [aid], "aggregate_ids": [], "evidence_run_ids": [],
                         "_leaves": set(), "_errors": {"missing_or_ambiguous_root"}, "_root": {},
                         "_provenance": provenance(f),
                         "status": "unknown", "reasons": []})

    # Preflight-blocked Tier 2 jobs may never persist the candidate aggregate.
    # Do not infer a root from question/snapshot alone: repeated evaluations can
    # have different source chains. Preserve a grouped unknown diagnostic instead.
    for source, row_type, id_key, missing_reason, ambiguous_reason in (
        ("scheduler_jobs", "scheduler_job_finished", "scheduler_job_ids",
         "scheduler_tier2_job_without_root", "ambiguous_scheduler_job_root"),
        ("reconciliation_runs", "reconciliation_run", "reconciliation_run_ids",
         "reconciliation_run_without_root", "ambiguous_reconciliation_root"),
    ):
        for i, row in enumerate(sources[source]):
            if row.get("type") != row_type or (source == "scheduler_jobs" and row.get("tier") != 2):
                continue
            rid = _text(row.get("record_id")) or f"missing_{source}_id:{i}"
            found = [e for e in episodes if row.get("tier1_aggregate_id") in e["related_tier1_aggregate_ids"]
                     and row.get("question_id") == e["question_id"]
                     and row.get("question_snapshot_id") == e["snapshot_id"]]
            if len(found) == 1:
                found[0][id_key].append(rid)
                continue
            for ep in found:
                ep["_errors"].add(ambiguous_reason)
            diagnostic = {"episode_id": hash_obj([source, rid]), "root_aggregate_id": None,
                "question_id": row.get("question_id"), "snapshot_id": row.get("question_snapshot_id"),
                "decision_at_utc": None, "tier1_aggregate_ids": [], "related_tier1_aggregate_ids": [],
                "tier1_attempt_ids": [], "tier2_attempt_ids": [], "aggregate_ids": [],
                "scheduler_job_ids": [], "reconciliation_run_ids": [], "evidence_run_ids": [],
                "_leaves": set(), "_errors": {ambiguous_reason if found else missing_reason}, "_root": {},
                "_provenance": provenance(row), "status": "unknown", "reasons": []}
            diagnostic[id_key].append(rid)
            episodes.append(diagnostic)

    # A job, reconciliation summary and orphan attempt can describe the same
    # missing root. Merge only explicit shared provenance, never question alone.
    diagnostic_groups = []
    for ep in [e for e in episodes if e["root_aggregate_id"] is None]:
        matching = [g for g in diagnostic_groups if any(
            e["question_id"] == ep["question_id"] and e["snapshot_id"] == ep["snapshot_id"]
            and e.get("_provenance", set()) & ep.get("_provenance", set()) for e in g)]
        combined = [ep]
        for group in matching:
            combined.extend(group); diagnostic_groups.remove(group)
        diagnostic_groups.append(combined)
    episodes = [e for e in episodes if e["root_aggregate_id"] is not None]
    for group in diagnostic_groups:
        ep = group[0]
        ep["episode_id"] = hash_obj(["unknown_provenance", sorted(e["episode_id"] for e in group)])
        for other in group[1:]:
            ep["_errors"].update(other["_errors"])
            for key in ("tier2_attempt_ids", "scheduler_job_ids", "reconciliation_run_ids"):
                ep[key].extend(other[key])
        episodes.append(ep)

    for a in aggs:
        if a.get("stage") != "final" or not _text(a.get("record_id")): continue
        leaves, _, errors = ancestry(a.get("input_attempt_ids"), a.get("question_id"), a.get("question_snapshot_id"))
        found = matches(a.get("question_id"), a.get("question_snapshot_id"), leaves)
        if len(found) == 1 and not errors and a["record_id"] not in bad_agg:
            ep = found[0]
            if _dt(a.get("created_at_utc")) is not None and ep["decision_at_utc"] is not None \
                    and _dt(a["created_at_utc"]) >= _dt(ep["decision_at_utc"]):
                ep["aggregate_ids"].append(a["record_id"])
        elif len(found) > 1:
            for ep in found: ep["_errors"].add("ambiguous_final_aggregate")

    for ep in episodes:
        errors = ep["_errors"]; decision = _dt(ep["decision_at_utc"])
        snap = by_snap.get(ep["snapshot_id"]) if _text(ep["snapshot_id"]) else None
        if snap is None or ep["snapshot_id"] in bad_snap or snap.get("question_id") != ep["question_id"]:
            errors.add("missing_or_conflicting_snapshot")
        elif _row_time(snap) is None:
            errors.add("invalid_snapshot_time")
        elif decision is not None and _row_time(snap) > decision:
            errors.add("snapshot_after_recorded_decision")
        # Explicitly outside the frozen time window is excluded even if no live
        # evidence exists. Missing/contradictory root/time cannot prove exclusion.
        if errors:
            ep["reasons"] = sorted(errors)
        elif not start <= decision < end:
            ep.update(status="excluded", reasons=["before_live_start" if decision < start else "after_cohort_end"])
        else:
            for aid in ep["tier1_attempt_ids"]:
                attempt = by_att.get(aid, {})
                began = _dt(attempt.get("started_at_utc", attempt.get("started_at"))) or _row_time(attempt)
                if began is not None and began < start:
                    errors.add("ancestry_predates_live_start")
            # Ground is a runtime overlay, venue can be a dynamic tournament slug.
            # A registered MiniBench-only campaign plus a live scheduler is the
            # source of venue authorization; a contradictory explicit ground fails.
            ground = snap.get("ground")
            if ground is not None and ground != "minibench": errors.add("conflicting_venue")
            if type(snap.get("tournament_id")) is not int or snap["tournament_id"] <= 0:
                errors.add("missing_tournament_identity")
            if "project_ids" in snap and (not isinstance(snap["project_ids"], list)
                                           or snap.get("tournament_id") not in snap["project_ids"]):
                errors.add("conflicting_tournament_membership")
            decision_links = [r for r in sources["reconciliation_runs"]
                              if r.get("type") == "reconciliation_run"
                              and r.get("tier1_aggregate_id") in ep["related_tier1_aggregate_ids"]
                              and r.get("question_id") == ep["question_id"]
                              and r.get("question_snapshot_id") == ep["snapshot_id"]]
            decision_links.extend(r for r in sources["scheduler_jobs"]
                                  if r.get("record_id") in ep["scheduler_job_ids"])
            for link in decision_links:
                if link.get("type") == "scheduler_job_finished" and link.get("campaign_id") != CAMPAIGN_ID:
                    errors.add("conflicting_job_campaign")
                if link.get("type") == "reconciliation_run" and link.get("campaign_id") not in (None, CAMPAIGN_ID):
                    errors.add("conflicting_reconciliation_campaign")
            runs = {r["run_id"] for r in decision_links if _text(r.get("run_id"))}
            decision_runs = set()
            for r in decision_links:
                lo = _dt(r.get("started_at_utc", r.get("started_at")))
                hi = _dt(r.get("finished_at_utc", r.get("finished_at")))
                if lo is None or hi is None or hi < lo or not _text(r.get("run_id")):
                    errors.add("invalid_live_decision_linkage")
                elif lo <= decision <= hi:
                    decision_runs.add(r["run_id"])
            # Budget/attempt linkage provides evidence even for interrupted recon
            # summaries; it cannot replace a missing live scheduler record.
            for aid in ep["tier1_attempt_ids"] + ep["tier2_attempt_ids"]:
                f = by_att.get(aid, {})
                if _text(f.get("run_id")): runs.add(f["run_id"])
                runs.update(b["run_id"] for b in sources["budget_events"]
                            if b.get("attempt_id") == aid and b.get("question_id") == ep["question_id"]
                            and _text(b.get("run_id")))
            runs.discard(None); ep["evidence_run_ids"] = sorted(runs)
            live_runs = []
            for rid in runs:
                r = by_run.get(rid)
                if r is None:
                    errors.add("unresolved_evidence_run")
                    continue
                if rid in bad_run:
                    errors.add("conflicting_run_metadata")
                    continue
                lo = _dt(r.get("started_at_utc", r.get("started_at")))
                hi = _dt(r.get("finished_at_utc", r.get("finished_at")))
                if _row_time(r) is None or lo is None or hi is None or hi < lo:
                    errors.add("undated_evidence_run")
                    continue
                if r.get("campaign_id") != CAMPAIGN_ID or r.get("mode") != "live" or r.get("continuous") is not True:
                    errors.add("conflicting_campaign_or_mode")
                else:
                    live_runs.append(r)
            # Root's decision must fall in a linked live run. The T1 runs alone
            # cannot prove that a later offline re-evaluation happened live.
            if not any(r.get("run_id") in decision_runs
                       and _dt(r.get("started_at_utc", r.get("started_at"))) <= decision
                       <= _dt(r.get("finished_at_utc", r.get("finished_at"))) for r in live_runs):
                errors.add("missing_live_decision_run")
            for b in sources["budget_events"]:
                if b.get("attempt_id") in ep["tier1_attempt_ids"] + ep["tier2_attempt_ids"] \
                        and b.get("campaign_id") not in (None, CAMPAIGN_ID): errors.add("conflicting_budget_campaign")
            regs = [r for r in sources["campaigns"] if (r.get("campaign") or {}).get("campaign_id") == CAMPAIGN_ID]
            auths = [r for r in sources["live_authorizations"] if (r.get("campaign") or {}).get("campaign_id") == CAMPAIGN_ID]
            valid_reg = lambda r: r.get("sha256") == r.get("_computed_campaign_sha256") == CAMPAIGN_SHA256 \
                and _row_time(r) is not None and _row_time(r) <= decision and r["campaign"].get("venues") == ["minibench"]
            valid_auth = lambda r: r.get("bot_id") == BOT_ID and type(r.get("bot_id")) is int \
                and r.get("campaign_sha256") == r.get("_computed_campaign_sha256") == CAMPAIGN_SHA256 \
                and _row_time(r) is not None and _row_time(r) <= decision
            if not regs or not all(valid_reg(r) for r in regs): errors.add("missing_or_conflicting_campaign_registration")
            if not auths or not all(valid_auth(r) for r in auths): errors.add("missing_or_conflicting_bot_authorization")
            ep.update(status="unknown" if errors else "included", reasons=sorted(errors))
        ep["needs_complete_capture"] = True
        ep["decision_time_basis"] = "first_recorded_candidate_aggregate" if ep["root_aggregate_id"] else "unknown"
        for key in ("tier1_attempt_ids", "tier2_attempt_ids", "aggregate_ids", "scheduler_job_ids", "reconciliation_run_ids"):
            ep[key] = sorted(set(ep[key]))
        for key in ("_leaves", "_errors", "_root", "_provenance"): ep.pop(key, None)
    episodes.sort(key=lambda e: (str(e["question_id"]), str(e["snapshot_id"]), str(e["root_aggregate_id"])))
    counts = Counter(e["status"] for e in episodes)
    return {"version": VERSION, "cohort_id": COHORT_ID, "campaign_id": CAMPAIGN_ID, "bot_id": BOT_ID,
            "start_utc": START, "end_utc": END, "as_of_utc": to_iso(cutoff) if cutoff else None,
            "source_sha256": hash_obj(sources), "source_hash_scope": "whitelisted_metadata_only",
            "counts": {k: counts[k] for k in ("included", "excluded", "unknown")}, "episodes": episodes,
            "included_episode_ids": [e["episode_id"] for e in episodes if e["status"] == "included"],
            "unknown_episode_ids": [e["episode_id"] for e in episodes if e["status"] == "unknown"],
            "observed_membership_complete": counts["unknown"] == 0,
            "decision_capture_complete": False,
            "capture_limitations": ["first_candidate_decisions_not_durably_logged"],
            "complete_membership": False}
