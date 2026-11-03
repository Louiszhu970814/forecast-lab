"""Local tier-2 orchestration (M2-W3), with no CLI or automatic budget admission.

The future scheduler must supply a tier-2 preflight and an admission callback. This module
holds the shared forecast lock, freezes anonymous inputs, and rechecks audits before every
attempt. It never polls questions, enables subagents, submits forecasts or grants a budget.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import string
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import aggregate as A
import audit_ledger as L
import escalate as E
import forecast as F
import validate as V
import numeric as N
from flab.config import PLAYERS, load_submit_mode
from flab.ids import hash_obj, new_id
from flab.jsonl import append_record, read_records
from flab.ledger import transaction, read_complete
from flab.timeutil import parse_iso, to_iso

VERSION = "reconciliation_pipeline_v4"
PROMPT_VERSION = "reconcile_v1"
SUMMARY_FIELDS = ("probability", "option_probabilities", "reasoning_summary", "key_factors",
                  "base_rate", "sources", "market_refs")
QUESTION_FIELDS = ("question_id", "question_type", "title", "description", "resolution_criteria",
                   "fine_print", "options", "open_time", "scheduled_close_time", "scheduled_resolve_time")


class InputsUnavailable(ValueError):
    """A frozen input can no longer be used; keep history and finalize a provisional fallback."""


def _read(path: Path) -> list[dict[str, Any]]:
    with transaction(path.parent):
        return read_complete(path)


def state(records: list[dict[str, Any]], events: list[dict[str, Any]]) -> tuple[list, list]:
    graph = L.ImpactGraph(records)
    # Validate all source events before exposing any summary, including clean audit records.
    view = graph.attempt_audit_view(events)
    return [r for r in records if r.get("type") == "forecast_attempt"], view


def prepare(snapshot: dict[str, Any], records: list[dict[str, Any]], events: list[dict[str, Any]], *,
            player: str, cfg: dict[str, Any], now: Any, shuffle: Callable[[list], None] | None = None) -> dict[str, Any]:
    """Pure preparation apart from randomness: explicit allowlist, never complete source records.

    Metadata anonymity does not prove an author cannot be guessed from writing style or source text.
    No extra model is used to rewrite a summary or alter its meaning.
    """
    if player not in PLAYERS:
        raise ValueError("unknown player")
    attempts, view = state(records, events)
    selected, _ = A.select_inputs(snapshot, attempts, stage="tier1", audit_events=view)
    if not selected:
        raise ValueError("no eligible tier-1 summaries")
    raw = {r["attempt_id"]: r for r in attempts}
    audited = {e["audit_event_id"]: e for e in events if e.get("type") == "audit"}
    for r in selected:
        event = audited.get(r.get("audit_event_id"), {})
        if event.get("attempt_id") != r["attempt_id"] or event.get("question_id") != snapshot["question_id"]:
            raise ValueError("selected tier-1 answer has no matching completed audit")
        output = {k: r.get(k) for k in V.question_schema(snapshot)["properties"]}
        # Revalidate the original MC probabilities; stored probabilities may already be normalised.
        if r.get("option_probabilities_raw") is not None:
            output["option_probabilities"] = r["option_probabilities_raw"]
        if V.validate_output(output, snapshot, V.question_schema(snapshot)).status != V.VALID:
            raise ValueError("selected tier-1 summary is malformed")
    ordered = list(selected)
    (shuffle or random.SystemRandom().shuffle)(ordered)
    if sorted(r["attempt_id"] for r in ordered) != sorted(r["attempt_id"] for r in selected):
        raise ValueError("shuffle must preserve the selected inputs")
    order = {chr(65 + i): r["attempt_id"] for i, r in enumerate(ordered)}
    payload = {"question": {k: snapshot.get(k) for k in QUESTION_FIELDS},
               "summaries": [{"label": label, **{k: raw[aid].get(k) for k in SUMMARY_FIELDS}}
                             for label, aid in order.items()]}
    numeric = snapshot["question_type"] in N.TYPES
    if numeric:
        payload["question"]["scaling"] = N.prompt_scaling(snapshot)
        for summary, aid in zip(payload["summaries"], order.values()):
            summary["percentiles"] = raw[aid]["percentiles"]
    delimiter = new_id("reconcile_data")
    prompt_version = N.RECONCILE_VERSION if numeric else PROMPT_VERSION
    prompt = string.Template((F.PROMPTS / f"{prompt_version}.md").read_text()).substitute(
        today_utc=now.date().isoformat(), delimiter=delimiter,
        input_json=json.dumps(payload, ensure_ascii=False, allow_nan=False))
    own = next((r for r in selected if r["player"] == player), None)
    input_ids = list(order.values())
    relevant = [e for e in view if e["attempt_id"] in input_ids]
    rec = {"type": "reconciliation_inputs", "record_id": new_id("recon"), "version": VERSION,
           "created_at_utc": to_iso(now), "mode": "local", "player": player,
           "question_id": snapshot["question_id"], "question_snapshot_id": snapshot["snapshot_id"],
           "snapshot_sha256": hash_obj(snapshot), "run_config_sha256": hash_obj(cfg),
           "prompt_version": prompt_version, "prompt": prompt,
           "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
           "input_attempt_ids": input_ids, "input_order": order,
           "input_sha256": {aid: hash_obj(raw[aid]) for aid in input_ids},
           "audit_flags": sorted({f for e in relevant for f in e["audit_flags"]}),
           "audit_event_ids": sorted({eid for e in relevant for eid in e["source_audit_event_ids"]}
                                     | {raw[aid]["audit_event_id"] for aid in input_ids}),
           "tier1_reference": {k: own.get(k) for k in ("attempt_id", "probability", "option_probabilities")} if own else None}
    if numeric and own:
        rec["tier1_reference"]["percentiles"] = own["percentiles"]
    event_by_id = {e["audit_event_id"]: e for e in events}
    if any(eid not in event_by_id for eid in rec["audit_event_ids"]):
        raise ValueError("input audit provenance is incomplete")
    rec["audit_sha256"] = {eid: hash_obj(event_by_id[eid]) for eid in rec["audit_event_ids"]}
    rec["content_sha256"] = hash_obj(rec)
    return rec


def revalidate(bundle: dict[str, Any], snapshot: dict[str, Any], records: list, events: list, cfg: dict) -> dict:
    if hash_obj({k: v for k, v in bundle.items() if k != "content_sha256"}) != bundle.get("content_sha256"):
        raise ValueError("reconciliation input record changed")
    if bundle["version"] != VERSION or bundle["snapshot_sha256"] != hash_obj(snapshot) \
            or bundle["run_config_sha256"] != hash_obj(cfg):
        raise InputsUnavailable("frozen reconciliation version, snapshot or config changed")
    attempts, view = state(records, events)
    event_by_id = {e["audit_event_id"]: e for e in events}
    if any(eid not in event_by_id or hash_obj(event_by_id[eid]) != sha for eid, sha in bundle["audit_sha256"].items()):
        raise ValueError("frozen input audit is missing or changed")
    selected, _ = A.select_inputs(snapshot, attempts, stage="tier1", audit_events=view)
    eligible = {r["attempt_id"] for r in selected}
    raw = {r["attempt_id"]: r for r in attempts}
    ids = bundle["input_attempt_ids"]
    if any(aid not in raw or hash_obj(raw[aid]) != bundle["input_sha256"][aid] for aid in ids):
        raise ValueError("frozen tier-1 input is missing or changed")
    if not set(ids) <= eligible:
        raise InputsUnavailable("frozen tier-1 input is excluded")
    # Preserve the on-disk bundle; only the transient execution overlay gains later audit flags.
    fresh = copy.deepcopy(bundle)
    relevant = [e for e in view if e["attempt_id"] in ids]
    fresh["audit_flags"] = sorted({f for e in relevant for f in e["audit_flags"]})
    fresh["audit_event_ids"] = sorted(set(bundle["audit_event_ids"]) |
                                      {eid for e in relevant for eid in e["source_audit_event_ids"]})
    return fresh


def run(ctx: F.Context, snapshot: dict[str, Any], *,
        admit: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        dependency_paths: tuple[Path, ...] = (), estimated_completion_s: float | None = None) -> dict[str, Any]:
    """Execute one admitted local tier-2 line. No admission callback means no model call.

    ``ctx.preflight`` must come from forecast.preflight(..., tier=2). ``admit`` is the future
    cross-question scheduler policy, invoked again before each CLI attempt, including repairs;
    it returns allowed=true and a nonempty decision_id only after reserving budget/capacity.
    Tests inject a synthetic policy. There is no built-in production policy or CLI in W3.
    """
    root = ctx.forecasts_log.parent
    bundles_log, runs_log, aggregates_log = (root / name for name in
        ("reconciliation_inputs.jsonl", "reconciliation_runs.jsonl", "aggregates.jsonl"))
    with F.Lock(root / f"forecast.{ctx.player}.lock"):
        F.recover_interrupted(ctx)
        if (load_submit_mode().get("mode") != "local" and not ctx.continuous) or not ctx.preflight.ok \
                or ctx.preflight.tier != 2 or ctx.preflight.player != ctx.player:
            raise F.PreflightError("local mode and a passing tier-2 preflight are required")
        if ctx.cfg["players"][ctx.player]["tier2"].get("subagents_enabled") is not False:
            raise F.PreflightError("W3 does not enable subagents")
        # Validate ledgers before appending, even when an earlier process left a partial row.
        for path in (bundles_log, runs_log, aggregates_log, ctx.line_status_log, ctx.quota_log, F.starts_log(ctx.forecasts_log)):
            _read(path)
        summary = {"type": "reconciliation_run", "record_id": new_id("rr"), "version": VERSION,
                   "run_id": ctx.run_id, "player": ctx.player, "question_id": snapshot["question_id"],
                   "question_snapshot_id": snapshot["snapshot_id"], "mode": "local",
                   "started_at_utc": to_iso(ctx.now()), "preflight": ctx.preflight.summary(), "admissions": []}

        def load() -> tuple[list, list]:
            with transaction(root):
                records = [*_read(ctx.forecasts_log), *_read(aggregates_log),
                           *[r for p in dependency_paths for r in _read(p)]]
                events = _read(ctx.audits_log)
                L.append_impacts(root / "audit_impacts.jsonl", L.ImpactGraph(records), events, now=ctx.now())
                return records, events

        def gates() -> None:
            if reason := F.eligible(snapshot, ctx.cfg, ctx.now()):
                raise F.QuestionDone(reason)
            if stop := F.line_stopped(ctx.line_status_log, ctx.player, ctx.forecasts_log, now=ctx.now()):
                raise F.LineStop(stop["reason"])
            for r in _read(ctx.forecasts_log):
                if r.get("player") == ctx.player and r.get("quota_retry_at_utc") \
                        and ctx.now() < parse_iso(r["quota_retry_at_utc"]):
                    raise F.LineStop("quota_exhausted: fixed backoff still active")

        try:
            gates()
            records, events = load()
            attempts, view = state(records, events)
            past = F.history(ctx.forecasts_log, ctx.player, snapshot["question_id"], tier=2)
            if ctx.continuous:
                past = [r for r in past if r.get("question_snapshot_id") == snapshot["snapshot_id"]]
            # An excluded first valid answer never frees a slot for a different tier-2 answer.
            if any(r.get("validation_status") == V.VALID for r in past):
                selected, _ = A.select_inputs(snapshot, attempts, stage="final", audit_events=view)
                prior = next((r for r in selected if r["player"] == ctx.player and r["tier"] == 2), None)
                summary.update(outcome="already_selected" if prior else "prior_answer_excluded",
                               selected_attempt_id=prior["attempt_id"] if prior else None)
            else:
                candidate = E.evaluate(snapshot, attempts, audit_events=view, run_config=ctx.cfg, now=ctx.now())
                stable = {k: v for k, v in candidate.items() if k not in ("record_id", "created_at_utc", "escalation")}
                stable["escalation"] = {k: v for k, v in candidate["escalation"].items() if k != "evaluated_at_utc"}
                candidate["evaluation_sha256"] = hash_obj(stable)
                prior_candidate = next((r for r in reversed(_read(aggregates_log))
                    if r.get("stage") == "tier1" and r.get("question_id") == snapshot["question_id"]), None)
                if prior_candidate is None or prior_candidate.get("evaluation_sha256") != candidate["evaluation_sha256"]:
                    append_record(aggregates_log, candidate)
                else:
                    candidate = prior_candidate
                summary["tier1_aggregate_id"] = candidate["record_id"]
                if not candidate["escalation"]["candidate"]:
                    raise F.QuestionDone("not an escalation candidate")
                if admit is None:
                    raise F.QuestionDone("budget admission unavailable; W4 policy required")
                deadline = F.decision_deadline(snapshot, ctx.cfg)
                estimate = ctx.cfg["timeouts_s"]["tier2"] + F.MIN_ATTEMPT_S
                if ctx.player == "gemini":
                    estimate += 2 * F.QUOTA_QUERY_S
                if estimated_completion_s is not None:
                    import math
                    if isinstance(estimated_completion_s, bool) or not math.isfinite(estimated_completion_s) or estimated_completion_s < F.MIN_ATTEMPT_S:
                        raise ValueError("invalid scheduler completion estimate")
                    estimate = estimated_completion_s
                if deadline is None or (deadline - ctx.now()).total_seconds() <= estimate:
                    raise F.QuestionDone("insufficient time for conservative tier-2 completion estimate")
                bundles = [r for r in _read(bundles_log) if r.get("player") == ctx.player
                           and r.get("question_id") == snapshot["question_id"]
                           and (not ctx.continuous or r.get("question_snapshot_id") == snapshot["snapshot_id"])]
                if len(bundles) > 1:
                    raise ValueError("multiple frozen input bundles for one line/question")
                if past and not bundles:
                    raise ValueError("tier-2 history exists without its frozen input bundle")
                if bundles:
                    bundle = bundles[0]
                else:
                    bundle = prepare(snapshot, records, events, player=ctx.player, cfg=ctx.cfg, now=ctx.now())
                    append_record(bundles_log, bundle)
                summary["reconciliation_id"] = bundle["record_id"]
                try:
                    active = revalidate(bundle, snapshot, records, events, ctx.cfg)
                except InputsUnavailable as exc:
                    raise F.QuestionDone(f"inputs no longer eligible: {exc}") from exc

                def recheck_inputs() -> None:
                    if ctx.recheck_inputs is not None:
                        ctx.recheck_inputs()
                    gates()
                    current_records, current_events = load()
                    try:
                        updated = revalidate(bundle, snapshot, current_records, current_events, ctx.cfg)
                    except InputsUnavailable as exc:
                        raise F.QuestionDone(f"inputs no longer eligible: {exc}") from exc
                    admission_id = active.get("admission_decision_id")
                    active.clear()
                    active.update(updated)
                    if admission_id:
                        active["admission_decision_id"] = admission_id

                def before_attempt(kind: str, attempt_id: str) -> None:
                    recheck_inputs()
                    current_records, _ = load()
                    decision = admit({"player": ctx.player, "tier": 2, "question_id": snapshot["question_id"],
                                      "attempt_kind": kind,
                                      "attempt_id": attempt_id,
                                      "reconciliation_id": bundle["record_id"],
                                      "candidate": copy.deepcopy(candidate["escalation"]),
                                      "forecast_history": copy.deepcopy(current_records)})
                    if not isinstance(decision, dict) or decision.get("allowed") is not True \
                            or not isinstance(decision.get("decision_id"), str) or not decision["decision_id"].strip():
                        if isinstance(decision, dict) and decision.get("reason") == "quota_5h_wait":
                            raise F.LineStop("quota_5h_wait", quota_retry_at_utc=decision["quota_retry_at_utc"])
                        raise F.QuestionDone("budget admission denied: " + str(
                            decision.get("reason") if isinstance(decision, dict) else "invalid decision"))
                    active["admission_decision_id"] = decision["decision_id"]
                    summary["admissions"].append(decision)
                    append_record(runs_log, {"type": "reconciliation_admission", "run_id": ctx.run_id,
                                            "attempt_id": attempt_id,
                                            "reconciliation_id": bundle["record_id"], "decision": decision,
                                            "at": to_iso(ctx.now())})

                def admission_outcome(attempt_id: str, started: bool) -> None:
                    append_record(runs_log, {"type": "reconciliation_admission_outcome", "run_id": ctx.run_id,
                        "attempt_id": attempt_id, "decision_id": active["admission_decision_id"],
                        "status": "started" if started else "released", "at": to_iso(ctx.now())})
                    if ctx.admission_outcome is not None:
                        ctx.admission_outcome(attempt_id, started)

                execution = replace(ctx, tier=2, reconciliation=active, before_attempt=before_attempt,
                                    recheck_inputs=recheck_inputs, admission_outcome=admission_outcome)
                summary.update(F.forecast_question(execution, snapshot))
        except F.QuestionDone as exc:
            summary.update(outcome="stopped_waiting", reason=str(exc))
        except F.LineStop as exc:
            summary.update(outcome=exc.outcome,
                           reason=str(exc), notify_louis=exc.notify, quota_retry_at_utc=exc.quota_retry_at_utc)
            if exc.notify:
                append_record(ctx.line_status_log, {"type": "line_stop", "player": ctx.player,
                    "run_id": ctx.run_id, "at": to_iso(ctx.now()), "reason": str(exc)})
        except ValueError as exc:
            summary.update(outcome="integrity_error", reason=str(exc), finished_at_utc=to_iso(ctx.now()))
            append_record(runs_log, summary)
            raise
        # Recompute from latest audits after execution; a now-excluded tier 2 falls back to tier 1.
        records, events = load()
        attempts, view = state(records, events)
        final = A.build_aggregate(snapshot, attempts, stage="final", audit_events=view, now=ctx.now())
        # A one-line invocation cannot declare the cross-line schedule complete. Keep the calculation
        # but make it impossible to mistake `status=ready` for authorization to submit it.
        final.update(calculation_status=final["status"], status="provisional",
                     completion_status="awaiting_scheduler_finalization", submission_eligible=False,
                     reconciliation_run_id=summary["record_id"], reconciliation_id=summary.get("reconciliation_id"))
        append_record(aggregates_log, final)
        final_ids = final["input_attempt_ids"]
        if summary.get("selected_attempt_id") and summary["selected_attempt_id"] not in final_ids:
            summary.update(outcome="output_excluded", selected_attempt_id=None)
        load()  # Register the final aggregate and any newly discovered effects durably too.
        summary.update(provisional_aggregate_id=final["record_id"], finished_at_utc=to_iso(ctx.now()))
        append_record(runs_log, summary)
        return summary
