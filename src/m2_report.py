"""Append-only M2 readiness and cost reports; never print question text or predictions.

Real acceptance is deliberately separate from synthetic integration tests. Missing cost or
quota-exhaustion evidence remains missing; this command never spends quota to exhaust a line.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

import aggregate as A
import audit_ledger as L
import baseline
import comment as C
import budget as B
import forecast as F
import quota
from flab.config import PLAYERS, load_run_config, load_tournaments
from flab.ids import new_id
from flab.jsonl import append_record
from flab.ledger import transaction, read_complete
from flab.timeutil import parse_iso, to_iso, utc_now
from poll_questions import latest_snapshots

VERSION = "m2_report_v2"


def readiness(acceptance_log=baseline.ACCEPTANCE_LOG, *, versions=None):
    cfg = load_run_config()
    versions = versions if versions is not None else baseline.cli_versions()
    records = read_complete(acceptance_log)
    approved = baseline.latest_approved(acceptance_log)
    results = {}
    for player in PLAYERS:
        for tier in (1, 2):
            pf = F.Preflight(player, tier=tier, cli_version=versions.get(player))
            F.check_acceptance(pf, [r for r in records if r.get("type") == "acceptance_result" and r.get("player") == player],
                F.runtime_fingerprint(player), production=cfg["players"][player][f"tier{tier}"])
            if F.incomplete_acceptance(acceptance_log, player):
                pf.problems.append("latest isolation acceptance run is incomplete")
            results[f"{player}.tier{tier}"] = pf.summary()
    tournaments = load_tournaments()["tournaments"]
    return {"baseline_approved_id": approved.get("record_id") if approved else None,
            "acceptance_quota_error_players": sorted({r["player"] for r in records
                if r.get("type") == "acceptance_call_finished" and r.get("error_type") == "quota_exhausted"}),
            "autoload_passed": any(r.get("type") == "autoload_result" and r.get("passed") for r in records),
            "production_acceptance": results,
            "exclusion_index_mode": "resolve current main/MiniBench API metadata before each campaign; refresh all ongoing/upcoming MiniBench rounds",
            "note": "Readiness inventory only; runtime rechecks the current global baseline, catalog, credits and safety index."}


def _stats(values):
    return {"n": len(values), "total": sum(values) if values else None,
            "median": statistics.median(values) if values else None, "p90": B.quantile(values)}


def build(root: Path, campaign: dict, *, gates: dict, now=None):
    now = now or utc_now()
    ids = {q["question_id"] for q in campaign["questions"]}
    with transaction(root):
        attempts = read_complete(root / "forecasts.jsonl")
        aggregates = read_complete(root / "aggregates.jsonl")
        events = read_complete(root / "audits.jsonl")
        comments = read_complete(root / "comments.jsonl")
        quota_rows = read_complete(root / "quota.jsonl")
        jobs = read_complete(root / "scheduler_jobs.jsonl")
        safety = read_complete(root / "campaign_safety.jsonl")
        registered = read_complete(root / "campaigns.jsonl")
        snapshots = latest_snapshots(root / "questions.jsonl")
        starts = read_complete(root / "forecast_attempt_starts.jsonl")
    graph = L.ImpactGraph([*attempts, *aggregates])
    view = graph.attempt_audit_view(events)
    selected = {p: set() for p in PLAYERS}
    for qid in ids:
        if qid in snapshots:
            rows, _ = A.select_inputs(snapshots[qid], attempts, stage="tier1", audit_events=view)
            for r in rows:
                selected[r["player"]].add(qid)
    source = [r for r in attempts if r.get("question_id") in ids]
    costs, lines = {}, {}
    observer = B.Budget(root, campaign, {}, "report")
    trusted = {r["attempt_id"]: d for r in source if r.get("player") == "gemini"
               and r.get("model_check") == "verified" and (d := observer._drop(r, quota_rows)) is not None}
    layouts = {r.get("attempt_id"): quota.argon_buckets(r).layout for r in quota_rows if r.get("when") == "before"}
    for player in PLAYERS:
        own = [r for r in source if r.get("player") == player]
        lines[player] = {"selected_tier1_questions": len(selected[player]), "denominator": len(ids),
            "valid_fraction": len(selected[player]) / len(ids),
            "errors": dict(Counter(r.get("error_type") for r in own if r.get("error_type"))),
            "quota_exhaustion_observed": player in gates.get("acceptance_quota_error_players", []) or any(r.get("player") == player and r.get("error_type") == "quota_exhausted"
                                               for r in attempts)}
        for tier in (1, 2):
            tier_rows = [r for r in own if r.get("tier") == tier]
            by_question = {}
            for r in tier_rows:
                by_question.setdefault(r["question_id"], []).append(r)
            measurements = []
            for qid, rows in by_question.items():
                native = [B.tokens(r) for r in rows]
                elapsed = [(parse_iso(r["finished_at_utc"]) - parse_iso(r["started_at_utc"])).total_seconds() for r in rows]
                trusted_rows = [r for r in rows if r["attempt_id"] in trusted]
                drops = [trusted[r["attempt_id"]] for r in trusted_rows]
                by_layout = {layout: sum(trusted[r["attempt_id"]] for r in trusted_rows if layouts[r["attempt_id"]] == layout)
                             for layout in {layouts[r["attempt_id"]] for r in trusted_rows}}
                if player == "gemini":
                    native = [B.tokens(r) for r in trusted_rows]
                    elapsed = [(parse_iso(r["finished_at_utc"]) - parse_iso(r["started_at_utc"])).total_seconds() for r in trusted_rows]
                measurements.append({"question_id": qid, "attempts": len(rows),
                    "seconds": sum(elapsed) if elapsed else None, "tokens": sum(native) if native and all(n is not None for n in native) else None,
                    "argon_fraction": sum(drops) if player == "gemini" and len(by_layout) == 1 else None,
                    "quota_fraction_by_layout": by_layout,
                    "quota_layout": next(iter(by_layout)) if len(by_layout) == 1 else None,
                    "trusted_attempts": len(trusted_rows) if player == "gemini" else None,
                    "undetermined_attempts": len(rows) - len(trusted_rows) if player == "gemini" else None,
                    "tier1_success": qid in selected[player]})
            tier_layouts = {layout for r in measurements for layout in r["quota_fraction_by_layout"]}
            # The legacy aggregate field cannot add fractions with different denominators.
            if len(tier_layouts) > 1:
                for measurement in measurements:
                    measurement["argon_fraction"] = None
            costs[f"{player}.tier{tier}"] = {"attempts": len(tier_rows), "questions": measurements,
                "attempt_kinds": dict(Counter(r.get("attempt_kind") for r in tier_rows)),
                "groups": {label: {field: _stats([r[field] for r in measurements if r[field] is not None
                    and (label == "all" or r["tier1_success"] == (label == "tier1_success"))])
                    for field in ("seconds", "tokens", "argon_fraction")} for label in ("all", "tier1_success", "tier1_failure")},
                "trusted_attempts": sum(r["attempt_id"] in trusted for r in tier_rows) if player == "gemini" else None,
                "undetermined_attempts": sum(r["attempt_id"] not in trusted for r in tier_rows) if player == "gemini" else None,
                "quota_fraction_by_layout": {layout: _stats([r["quota_fraction_by_layout"][layout] for r in measurements
                    if layout in r["quota_fraction_by_layout"]]) for layout in tier_layouts},
                "missing_token_attempts": sum(B.tokens(r) is None for r in tier_rows),
                "quota_measurement": "trusted_before_after_by_layout" if player == "gemini" else "CLI has no numeric quota readout; attempt count only"}
    finals = [a for a in aggregates if a.get("scheduler_campaign_id") == campaign["campaign_id"]
              and a.get("completion_status") == "complete_local"]
    commented = {r["aggregate_id"] for r in comments if r.get("type") == "comment"}
    unresolved = [s["attempt_id"] for s in starts if s.get("question_id") in ids
                  and s["attempt_id"] not in {r["attempt_id"] for r in attempts}]
    current_finals = set()
    for final in finals:
        if final["record_id"] not in commented or final["question_id"] not in snapshots:
            continue
        try:
            C.build(snapshots[final["question_id"]], final, [*attempts, *aggregates], events, now=now)
        except C.CommentError:
            continue
        current_finals.add(final["question_id"])
    checks = {"at_least_20_questions": len(ids) >= 20,
        "registered_before_outputs": any(r.get("campaign") == campaign for r in registered),
        "all_three_tier1_at_least_90_percent": all(r["valid_fraction"] >= .9 for r in lines.values()),
        "all_questions_finalized_and_commented": ids == current_finals,
        "upgrade_and_tier2_observed": any(r.get("tier") == 2 for r in source) and any(
            a.get("escalation", {}).get("candidate") and a["question_id"] in ids for a in aggregates),
        "all_lines_and_layers_measured": all(r["attempts"] > 0 for r in costs.values()),
        "known_tokens_all_attempts": bool(source) and all(B.tokens(r) is not None for r in source),
        "trusted_gemini_costs": bool(trusted),
        "all_cli_quota_exhaustion_observed": all(r["quota_exhaustion_observed"] for r in lines.values()),
        "single_pipeline_version": bool(source) and len({r.get("pipeline_version") for r in source}) == 1,
        "no_unknown_started_attempts": not unresolved,
        "no_preflight_waivers": bool(source) and not any(r.get("preflight_waivers") for r in source),
        "safety_checks_recorded": any(r.get("campaign_id") == campaign["campaign_id"] for r in safety),
        "baseline_and_autoload_approved": bool(gates.get("baseline_approved_id")) and gates.get("autoload_passed") is True,
        "production_acceptance_current": len(gates.get("production_acceptance", {})) == 6 and all(
            r.get("ok") for r in gates.get("production_acceptance", {}).values())}
    return {"type": "m2_report", "report_id": new_id("m2report"), "version": VERSION, "generated_at": to_iso(now),
        "campaign_id": campaign["campaign_id"], "m2_complete": all(checks.values()), "checks": checks,
        "trusted_gemini_attempts": len(trusted),
        "undetermined_gemini_attempts": sum(r.get("player") == "gemini" and r["attempt_id"] not in trusted for r in source),
        "pending": [k for k, ok in checks.items() if not ok], "lines": lines, "costs": costs,
        "unresolved_attempt_ids": unresolved, "readiness": gates,
        "version_segments": dict(Counter(str((r.get("pipeline_version"), r.get("run_config_version"),
            r.get("player"), r.get("tier"), r.get("requested_model_id"), r.get("requested_effort"),
            r.get("prompt_version"))) for r in source)),
        "scope": "Operational acceptance, not accuracy scoring. No predictions, reasoning or question text are emitted.",
        "quota_limit": "Claude/GPT quota fractions remain unobservable; attempt counts and real exhaustion errors are reported separately."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path)
    args = parser.parse_args(argv)
    gates = readiness()
    if args.campaign:
        campaign = json.loads(args.campaign.read_text())
        report = build(F.DATA, campaign, gates=gates)
    else:
        report = {"type": "m2_readiness", "report_id": new_id("m2ready"), "generated_at": to_iso(utc_now()),
                  "version": VERSION, "m2_complete": False, "readiness": gates}
    destination = F.PROJECT_ROOT / "reports" / f"{report['report_id']}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    append_record(F.DATA / "m2_reports.jsonl", {"type": "m2_report_created", "report_id": report["report_id"],
        "path": str(destination), "m2_complete": report["m2_complete"], "at": to_iso(utc_now())})
    print(json.dumps({"path": str(destination), "m2_complete": report["m2_complete"], "pending": report.get("pending"),
                      "readiness": gates}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
