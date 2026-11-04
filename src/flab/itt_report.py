"""Pure D128 reporting over selected roots and already validated score rows.

No file or network I/O. The legacy scoreboard remains a separate historical view.
Any cohort membership/outcome gap blocks full-population effects and efficiency;
paired observations are still explicitly descriptive. Different configurations and
question types never share an effect estimate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import math
from typing import Any, Callable, Iterable

from flab.submission_identity import (
    is_snapshotless_probe, submission_identity, submission_status,
)
from flab.timeutil import parse_iso, to_iso

VERSION = "live_itt_report_v1"
METRICS = ("log_score", "log_score_clamped", "brier_score")


def _dt(value: Any):
    try:
        return parse_iso(value) if isinstance(value, str) and value.strip() else None
    except (ValueError, TypeError, OverflowError):
        return None


def _time_state(row: dict, cutoff: datetime, keys: tuple[str, ...]) -> str:
    dates = [row[k] for k in keys if row.get(k) is not None]
    parsed = [_dt(v) for v in dates]
    if not dates or any(v is None for v in parsed):
        return "invalid"
    return "future" if any(v > cutoff for v in parsed) else "observed"


def _retained(rows, cutoff, keys):
    # Only provably future evidence can be omitted. Undated/corrupt rows remain
    # available for episode attribution and explicit completeness diagnostics.
    return [(r, state) for r in rows if (state := _time_state(r, cutoff, keys)) != "future"]


def _finite(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _unique(rows: Iterable[dict], key: str):
    indexed, conflicts = {}, set()
    for row in rows:
        value = row.get(key)
        if not isinstance(value, str) or not value:
            continue
        if value in indexed and indexed[value] != row:
            conflicts.add(value)
        indexed[value] = row
    return indexed, conflicts


def build_live_itt(
    cohort: dict, *, scored_questions: list[dict], submissions: list[dict],
    forecasts: list[dict], aggregates: list[dict] = (), resolutions: list[dict] = (),
    subagents: list[dict] = (), as_of: str, n_boot: int,
    build_draws: Callable, ci_from_draws: Callable, conclusion: Callable,
) -> dict:
    """Bind root episodes to confirmed submissions and validated paired scores.

    ``scored_questions`` must be the existing scorer's as-of/audit-validated rows,
    augmented with ``tier1_aggregate_id``. Roots must come from itt_cohort.
    Bootstrap/conclusion callbacks are the existing score.py functions.
    """
    cutoff = _dt(as_of)
    if cutoff is None or type(n_boot) is not int or n_boot < 1:
        raise ValueError("valid as_of and positive bootstrap iteration count required")
    timed_subrows = _retained(submissions, cutoff, ("at", "created_at_utc"))
    subrows = [r for r, _ in timed_subrows]
    intents, conflicting_intents = _unique(
        [r for r in subrows if submission_status(r) == "intent"], "intent_id")
    accepted = []
    for row, time_state in timed_subrows:
        if submission_status(row) not in {"forecast_ok", "comment_ok"}:
            continue
        base = intents.get(row.get("intent_id"), {})
        identity = submission_identity(row, base)
        if is_snapshotless_probe(identity):
            continue
        merged = {**base, **row, **identity}
        # Missing completion metadata may inherit the immutable intent. Explicit
        # contradictory metadata is never silently replaced by the intent.
        for key in ("aggregate_id", "question_id", "bot_id"):
            if merged.get(key) is None and key in base:
                merged[key] = base[key]
        merged["_conflict"] = row.get("intent_id") in conflicting_intents or any(
            base.get(k) is not None and row.get(k) is not None and base[k] != row[k]
            for k in ("aggregate_id", "question_id", "bot_id"))
        merged["_invalid_time"] = time_state == "invalid" or bool(base) and _time_state(
            base, cutoff, ("at", "created_at_utc")) == "invalid"
        merged["_links"] = [(r.get("question_id"), r.get("aggregate_id")) for r in (base, row, merged)]
        accepted.append(merged)
    latest_res, invalid_resolution_qids = {}, set()
    for row in resolutions:
        if row.get("type") != "resolution_event":
            continue
        if _time_state(row, cutoff, ("observed_at_utc",)) == "invalid":
            invalid_resolution_qids.add(row.get("question_id"))
        observed = _dt(row.get("observed_at_utc"))
        if observed and observed <= cutoff:
            qid = row.get("question_id")
            if qid not in latest_res or observed >= latest_res[qid][0]:
                latest_res[qid] = (observed, row)
    scored, duplicate_scores = {}, set()
    for row in scored_questions:
        key = (row.get("question_id"), row.get("snapshot_id"), row.get("submitted_aggregate_id"))
        if key in scored:
            duplicate_scores.add(key)
        scored[key] = row
    timed_attempts = _retained(forecasts, cutoff, ("started_at_utc", "finished_at_utc", "created_at_utc"))
    timed_children = _retained(subagents, cutoff, ("started_at_utc", "finished_at_utc", "created_at_utc", "at", "observed_at_utc"))
    timed_aggregates = _retained(aggregates, cutoff, ("created_at_utc", "at"))
    attempts, conflicting_attempts = _unique((r for r, _ in timed_attempts), "attempt_id")
    children, conflicting_children = _unique((r for r, _ in timed_children), "subagent_id")
    _, conflicting_aggregates = _unique((r for r, _ in timed_aggregates), "record_id")
    invalid_attempts = {r.get("attempt_id") for r, state in timed_attempts if state == "invalid"}
    invalid_aggregates = {r.get("record_id") for r, state in timed_aggregates if state == "invalid"}
    included = [e for e in cohort.get("episodes", []) if e.get("status") == "included"]
    unknown = [e for e in cohort.get("episodes", []) if e.get("status") == "unknown"]
    repeated_questions = {q for q, n in Counter(e.get("question_id") for e in included).items() if n > 1}
    reports, observations, all_t2_ids = [], {}, set()
    global_gaps = Counter()
    if cohort.get("decision_capture_complete") is not True:
        global_gaps["decision_capture_incomplete"] = 1
    if unknown:
        global_gaps["unknown_cohort_membership"] = len(unknown)
    for ep in included:
        qid, sid = ep.get("question_id"), ep.get("snapshot_id")
        ep_id = ep["episode_id"]
        agg_ids = set(ep.get("aggregate_ids", []))
        t2_ids = set(ep.get("tier2_attempt_ids", []))
        all_t2_ids.update(t2_ids)
        matches = [r for r in accepted if any(q == qid and a in agg_ids for q, a in r["_links"])]
        actual_ids = {r.get("aggregate_id") for r in matches}
        bot_id = cohort.get("bot_id", 311763)
        is_submitted = bool(matches)
        gaps = []
        row = None
        related_attempts = t2_ids | set(ep.get("tier1_attempt_ids", []))
        related_aggregates = agg_ids | set(ep.get("related_tier1_aggregate_ids", []))
        if related_attempts & invalid_attempts:
            gaps.append("invalid_attempt_time")
        if related_aggregates & invalid_aggregates:
            gaps.append("invalid_aggregate_time")
        if related_aggregates & conflicting_aggregates:
            gaps.append("conflicting_aggregate_record")
        if any(state == "invalid" and r.get("parent_attempt_id") in related_attempts for r, state in timed_children):
            gaps.append("invalid_subagent_time")
        if qid in invalid_resolution_qids:
            gaps.append("invalid_resolution_time")
        if any(r["_invalid_time"] for r in matches):
            gaps.append("invalid_submission_time")
        outcome_status = (latest_res.get(qid, (None, {}))[1]).get("resolution_status")
        if not matches:
            gaps.append("unsubmitted_escalation")
        elif any(r["_conflict"] or r.get("question_snapshot_id") != sid or
                 type(r.get("bot_id")) is not int or r["bot_id"] != bot_id for r in matches):
            gaps.append("submission_identity_conflict")
        elif len(actual_ids) != 1:
            gaps.append("multiple_submitted_aggregates")
        elif outcome_status in ("ambiguous", "annulled"):
            gaps.append("excluded_resolution_" + outcome_status)
        elif outcome_status != "resolved":
            gaps.append("not_yet_evaluable")
        else:
            key = (qid, sid, next(iter(actual_ids)))
            row = scored.get(key)
            aliases = set(ep.get("tier1_aggregate_ids", [ep.get("root_aggregate_id")]))
            if row is None or key in duplicate_scores:
                gaps.append("missing_validated_pair")
            elif row.get("tier1_aggregate_id") not in aliases:
                gaps.append("baseline_root_mismatch")
            elif any(not isinstance(row.get("scores", {}).get(series), dict) or any(
                not _finite(row["scores"][series].get(metric)) for metric in METRICS)
                for series in ("final_aggregate", "tier1_aggregate")):
                gaps.append("missing_validated_pair")
        if qid in repeated_questions:
            gaps.append("multiple_episode_question")
        segment = row.get("config_segment") if row else None
        qtype = row.get("question_type") if row else None
        if not isinstance(segment, str) or not segment or "unknown" in segment.split("|") \
                or qtype not in {"binary", "multiple_choice", "numeric", "discrete"}:
            segment = None
        if segment is None:
            # An unresolved episode cannot be silently removed from every segment's denominator.
            gaps.append("unsegmented_episode")
        global_gaps.update(gaps)
        if not gaps:
            observations[ep_id] = row
        reports.append({"episode_id": ep_id, "question_id": qid, "snapshot_id": sid,
                        "root_aggregate_id": ep.get("root_aggregate_id"),
                        "submitted": is_submitted, "paired": not gaps,
                        "submission_time_known": bool(matches) and not any(r["_invalid_time"] for r in matches),
                        "config_segment": segment, "question_type": qtype,
                        "gap_reasons": gaps, "tier2_attempt_ids": sorted(t2_ids)})

    def resources(ids):
        ids = set(ids)
        missing = ids - attempts.keys()
        rows = [attempts[i] for i in sorted(ids & attempts.keys())]
        costs, cost_fields = [], []
        for r in rows:
            field = next((k for k in ("budget_cost", "quota_weight")
                          if _finite(r.get(k)) and r[k] >= 0), None)
            costs.append(r[field] if field else None)
            if field:
                cost_fields.append(field)
        child_ids = {cid for cid, r in children.items() if r.get("parent_attempt_id") in ids}
        invalid_children = sum(state == "invalid" for r, state in timed_children if r.get("parent_attempt_id") in ids)
        unidentified_children = sum(not isinstance(r.get("subagent_id"), str) or not r["subagent_id"]
                                   for r, _ in timed_children if r.get("parent_attempt_id") in ids)
        # Existing ledgers do not state whether child cost is included in the
        # parent. Count children, but never sum both and silently double count.
        missing_children = sum(max(0, r.get("subagent_count", 0) - sum(
            children[cid].get("parent_attempt_id") == r["attempt_id"] for cid in child_ids))
            for r in rows if type(r.get("subagent_count")) is int and r["subagent_count"] > 0)
        missing_n = len(missing) + sum(c is None for c in costs)
        conflicts = (ids & conflicting_attempts) | (child_ids & conflicting_children)
        invalid_times = len(ids & invalid_attempts) + invalid_children
        mixed_cost_fields = len(set(cost_fields)) > 1
        complete = not (missing_n or child_ids or conflicts or missing_children or invalid_times or
                        unidentified_children or mixed_cost_fields)
        return {"scope": "recorded_linked_attempts_only", "tier2_attempt_count": len(ids), "tier2_subagent_count": len(child_ids),
                "missing_attempt_ids": sorted(missing), "budget_cost_missing_count": missing_n,
                "conflicting_cost_record_count": len(conflicts), "missing_subagent_record_count": missing_children,
                "invalid_time_record_count": invalid_times, "unidentified_subagent_record_count": unidentified_children,
                "cost_fields": sorted(set(cost_fields)), "mixed_cost_fields": mixed_cost_fields,
                "subagent_cost_accounting": "not_separable_from_parent" if child_ids else "no_subagents",
                "budget_cost_complete": complete,
                "budget_units": math.fsum(costs) if complete else None}

    groups = defaultdict(list)
    for item in reports:
        if item["config_segment"] is not None:
            groups[(item["config_segment"], item["question_type"])].append(item)
    by_segment = []
    for (segment, qtype), items in sorted(groups.items()):
        pairs = [observations[i["episode_id"]] for i in items if i["episode_id"] in observations]
        clusters = [r.get("event_cluster_id") or r["question_id"] for r in pairs]
        draws_q, draws_c = build_draws(len(pairs), clusters, n_boot=n_boot,
                                      seed_prefix=f"{cohort.get('cohort_id')}:{as_of}:{segment}:{qtype}")
        metrics = {}
        for metric in METRICS:
            values = {i: r["scores"]["final_aggregate"][metric] - r["scores"]["tier1_aggregate"][metric]
                      for i, r in enumerate(pairs)}
            ci_q = ci_from_draws(values, draws_q, distinct_units=len(pairs))
            ci_c = ci_from_draws(values, draws_c, distinct_units=len(set(clusters)))
            cq, cc = list(ci_q) if ci_q else None, list(ci_c) if ci_c else None
            disc = conclusion(len(pairs), cq, cc, higher_is_better=metric != "brier_score")
            if global_gaps:
                disc = {**disc, "tier": "descriptive_only", "superiority_assessment_eligible": False, "can_claim_superiority": False,
                        "superiority_claim": "none_incomplete_live_itt", "itt_incomplete_reasons": dict(global_gaps)}
            metrics[metric + "_diff"] = {"mean": math.fsum(values.values()) / len(values) if values else None,
                                        "ci95_question": cq, "ci95_cluster": cc,
                                        "conclusion_discipline": disc}
        resource = resources(i for item in items for i in item["tier2_attempt_ids"])
        total = math.fsum(r["scores"]["final_aggregate"]["log_score"] - r["scores"]["tier1_aggregate"]["log_score"] for r in pairs)
        complete = bool(items) and not global_gaps
        by_segment.append({"config_segment": segment, "question_type": qtype,
                           "estimate_scope": "observed_pair_subset_descriptive_only" if global_gaps else "complete_registered_itt",
                           "episode_count": len(items), "paired_question_count": len(pairs),
                           "pairwise": metrics, "resources": resource,
                           "evaluatable_subset_log_score_gain_nats": total if pairs else None,
                           "total_log_score_gain_nats": total if complete else None,
                           "log_score_gain_per_tier2_attempt": total / resource["tier2_attempt_count"]
                           if complete and resource["tier2_attempt_count"] else None,
                           "log_score_gain_per_budget_unit": total / resource["budget_units"]
                           if complete and resource["budget_units"] else None})
    return {"version": VERSION, "cohort": cohort, "as_of_utc": to_iso(cutoff),
            "bootstrap_iterations": n_boot, "effect_scope": "registered_escalated_questions_only",
            "included_episode_count": len(included), "included_question_count": len({e.get("question_id") for e in included}),
            "unknown_episode_count": len(unknown), "excluded_episode_count": sum(e.get("status") == "excluded" for e in cohort.get("episodes", [])),
            "submitted_episode_count": sum(i["submitted"] for i in reports),
            "submission_time_unknown_episode_count": sum(i["submitted"] and not i["submission_time_known"] for i in reports),
            "unsubmitted_episode_count": sum(not i["submitted"] for i in reports),
            "paired_question_count": len(observations),
            "complete_itt_coverage": bool(included) and not global_gaps,
            "itt_gap_reasons": dict(global_gaps), "resources": resources(all_t2_ids),
            "unknown_resources": resources(i for ep in unknown for i in ep.get("tier2_attempt_ids", [])),
            "pooling_prohibited": True, "by_config_and_type": by_segment, "episodes": reports}
