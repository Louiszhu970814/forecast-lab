"""Offline aggregation for PLAN §3.3, §3.4, §4.2 and §5.5 (M2-W1).

Input attempts must be in append order, with their dependency records present. This module never
calls a model, reads project data, submits predictions, or starts tier 2. Later audit flags are
additive: excluding a previously selected answer never selects a later answer from the same tier.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from flab.config import PLAYERS, load_agg_config
from flab.ids import hash_obj, new_id
from flab.jsonl import append_record
from flab.timeutil import to_iso, utc_now
import numeric as N

IMPLEMENTATION_VERSION = "aggregate_pipeline_v2"
EXCLUDED = frozenset({"leak_confirmed", "model_mismatch", "tool_scope_violation",
                      "missing_dependency", "invalid_dependency"})


def probability(value: Any, *, endpoints: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return False
    return 0 <= value <= 1 if endpoints else 0 < value < 1


def logit(p: float) -> float:
    return math.log(p) - math.log1p(-p)


def _check_config(config: dict[str, Any]) -> None:
    # Only the approved algorithm is implemented. A different rule needs its own version.
    expected = {"version": "agg_v1", "binary": {"method": "log_odds_mean", "extremizing_factor": 1.0,
                "clip": [0.02, 0.98]}, "multiple_choice": {"method": "geometric_mean", "smoothing_epsilon": 0.02},
                "weights": dict.fromkeys(PLAYERS, 1.0)}
    if any(config.get(k) != v for k, v in expected.items()):
        raise ValueError("unsupported aggregation settings: implement and version changes to agg_v1 first")


def _options(snapshot: dict[str, Any]) -> list[str]:
    qtype = snapshot.get("question_type")
    if qtype in N.TYPES:
        N.scaling(snapshot)
        return []
    if qtype == "binary":
        return []
    opts = snapshot.get("options")
    if qtype != "multiple_choice" or not isinstance(opts, list) or len(opts) < 2 \
            or not all(isinstance(o, str) and o for o in opts) or len(set(opts)) != len(opts):
        raise ValueError("expected binary or multiple_choice with distinct nonempty option labels")
    return opts


def input_error(rec: dict[str, Any], snapshot: dict[str, Any]) -> str | None:
    """Defence against incompatible snapshots and malformed records despite a stored valid label."""
    for key in ("question_id", "post_id", "tournament_id", "question_type"):
        if key not in rec or rec[key] != snapshot.get(key):
            return f"mismatched_{key}"
    if not snapshot.get("snapshot_id") or rec.get("question_snapshot_id") != snapshot["snapshot_id"]:
        return "mismatched_snapshot"
    if rec.get("tier") == 2 and not rec.get("input_attempt_ids"):
        return "missing_tier2_inputs"
    opts = _options(snapshot)
    if snapshot["question_type"] in N.TYPES:
        try:
            cdf = N.validate_cdf(rec.get("continuous_cdf"), snapshot)
            if rec.get("probability") is not None or rec.get("option_probabilities") is not None:
                return "invalid_numeric_probability_fields"
            if rec.get("cdf_version") != N.CDF_VERSION or rec.get("cdf_sha256") != hash_obj(cdf):
                return "invalid_cdf_provenance"
            if rec.get("type") == "forecast_attempt":
                if N.to_cdf(rec.get("percentiles"), snapshot) != cdf:
                    return "inconsistent_numeric_cdf"
        except ValueError:
            return "invalid_continuous_cdf"
        return None
    if not opts:
        return None if probability(rec.get("probability")) and rec.get("option_probabilities") is None else "invalid_probability"
    ops = rec.get("option_probabilities")
    if rec.get("probability") is not None or not isinstance(ops, dict) or set(ops) != set(opts) \
            or not all(probability(v) for v in ops.values()) or abs(math.fsum(ops.values()) - 1) > 1e-9:
        return "invalid_option_probabilities"
    raw = rec.get("option_probabilities_raw")
    if raw is not None:
        if not isinstance(raw, dict) or set(raw) != set(opts) or not all(probability(v) for v in raw.values()) \
                or abs(math.fsum(raw.values()) - 1) > 0.01:
            return "invalid_raw_option_probabilities"
        total = math.fsum(raw.values())
        if any(abs(raw[o] / total - ops[o]) > 1e-9 for o in opts):
            return "inconsistent_raw_option_probabilities"
    return None


def _audit_attempt_id(event: dict[str, Any]) -> str | None:
    """Legacy raw attempt audits and graph projections are supported; child audits need the graph."""
    target = event.get("target")
    if target is None:
        return event.get("attempt_id")
    if not isinstance(target, dict) or target.get("kind") != "attempt":
        raise ValueError("non-attempt audit targets require ImpactGraph.attempt_audit_view")
    aid = target.get("id")
    if event.get("attempt_id") is not None and event["attempt_id"] != aid:
        raise ValueError("conflicting audit target and attempt_id")
    return aid


def effective_flags(attempts: list[dict[str, Any]], audit_events: Iterable[dict[str, Any]] = ()) -> dict[str, set[str]]:
    """Additive flags over prev_attempt_id and input_attempt_ids, including later audit events.

    This is the in-memory dependency calculation only; appending retrospective impact records for
    previously produced aggregates/submissions belongs to the M2 audit ledger task.
    """
    by_id: dict[str, dict[str, Any]] = {}
    flags: dict[str, set[str]] = {}
    for r in attempts:
        aid = r.get("attempt_id")
        if not isinstance(aid, str) or not aid or aid in by_id:
            raise ValueError("attempt IDs must be present and unique")
        by_id[aid] = r
        flags[aid] = set(r.get("audit_flags") or [])
        if r.get("model_check") == "mismatch" or r.get("inherited_model_mismatch"):
            flags[aid].add("model_mismatch")
        if r.get("tool_violations") or r.get("error_type") == "tool_scope_violation":
            flags[aid].add("tool_scope_violation")
    for event in audit_events:
        aid = _audit_attempt_id(event)
        if aid not in by_id:
            raise ValueError(f"audit event references an unknown attempt: {aid!r}")
        for field in ("question_id", "question_snapshot_id"):
            if event.get(field) is not None and event[field] != by_id[aid].get(field):
                raise ValueError(f"audit {field} differs from its attempt")
        flags[aid].update(event.get("audit_flags") or [])
    done, visiting = set(), set()

    def visit(aid: str) -> set[str]:
        if aid in visiting:
            raise ValueError("cycle in attempt dependencies")
        if aid in done:
            return flags[aid]
        visiting.add(aid)
        rec = by_id[aid]
        inputs = rec.get("input_attempt_ids") or []
        if not isinstance(inputs, list) or not all(isinstance(a, str) for a in inputs):
            raise ValueError("input_attempt_ids must be a list of strings")
        parents = [*inputs, *([rec["prev_attempt_id"]] if rec.get("prev_attempt_id") else [])]
        for parent_id in parents:
            parent = by_id.get(parent_id)
            if parent is None:
                flags[aid].add("missing_dependency")
                continue
            if parent.get("question_id") != rec.get("question_id") \
                    or parent.get("question_snapshot_id") != rec.get("question_snapshot_id"):
                flags[aid].add("invalid_dependency")
            if parent_id in inputs and parent.get("validation_status") != "valid":
                flags[aid].add("invalid_dependency")
            flags[aid].update(visit(parent_id))
        visiting.remove(aid)
        done.add(aid)
        return flags[aid]

    for aid in by_id:
        visit(aid)
    return flags


def select_inputs(snapshot: dict[str, Any], attempts: list[dict[str, Any]], *, stage: str,
                  audit_events: Iterable[dict[str, Any]] = ()) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """First valid per player/tier; final uses eligible tier 2, otherwise eligible tier 1.

    A first valid answer later rejected for contamination or a revised snapshot does not free up
    its slot for another answer. Continuous queues explicitly set forecast_scope=snapshot after
    a revision decision. The caller must still pass the full history for dependency/audit propagation.
    """
    if stage not in ("tier1", "final"):
        raise ValueError("stage must be tier1 or final")
    _options(snapshot)
    flags = effective_flags(attempts, audit_events)
    first: dict[tuple[str, int], dict[str, Any]] = {}
    for rec in attempts:
        if rec.get("type") != "forecast_attempt" or rec.get("question_id") != snapshot["question_id"]:
            continue
        if snapshot.get("forecast_scope") == "snapshot" and rec.get("question_snapshot_id") != snapshot["snapshot_id"]:
            continue
        if rec.get("player") not in PLAYERS or type(rec.get("tier")) is not int or rec["tier"] not in (1, 2):
            raise ValueError("unknown player or tier in question history")
        if rec.get("validation_status") == "valid":
            first.setdefault((rec["player"], rec["tier"]), rec)
    selected, exclusions = [], []
    for player in PLAYERS:
        for tier in ((2, 1) if stage == "final" else (1,)):
            rec = first.get((player, tier))
            if rec is None:
                continue
            causes = sorted(flags[rec["attempt_id"]] & EXCLUDED)
            if error := input_error(rec, snapshot):
                causes.append(error)
            if causes:
                exclusions.append({"player": player, "tier": tier, "attempt_id": rec["attempt_id"], "reasons": causes})
                continue
            selected.append({**rec, "effective_audit_flags": sorted(flags[rec["attempt_id"]])})
            break
    return selected, exclusions


def combine(snapshot: dict[str, Any], selected: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    """Numerical agg_v1; selection/audit happens in select_inputs before this function."""
    _check_config(config)
    opts = _options(snapshot)
    if not selected:
        if snapshot["question_type"] in N.TYPES:
            return {"probability": None, "option_probabilities": None, "continuous_cdf": None,
                    "cdf_version": N.CDF_VERSION, "cdf_sha256": None, "before_clipping_or_smoothing": None}
        return {"probability": None, "option_probabilities": None, "before_clipping_or_smoothing": None}
    players = [r.get("player") for r in selected]
    if len(set(players)) != len(players) or any(p not in PLAYERS for p in players):
        raise ValueError("aggregation requires at most one answer per known player")
    for rec in selected:
        if error := input_error(rec, snapshot):
            raise ValueError(error)
    if snapshot["question_type"] in N.TYPES:
        mean = [math.fsum(row["continuous_cdf"][i] for row in selected) / len(selected)
                for i in range(N.scaling(snapshot).n + 1)]
        cdf = N.project_cdf(mean, snapshot)
        return {"probability": None, "option_probabilities": None, "continuous_cdf": cdf,
                "cdf_version": N.CDF_VERSION, "cdf_sha256": hash_obj(cdf),
                "before_clipping_or_smoothing": mean}
    if not opts:
        z = math.fsum(logit(r["probability"]) for r in selected) / len(selected)
        p = 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))
        lo, hi = config["binary"]["clip"]
        return {"probability": max(lo, min(hi, p)), "option_probabilities": None,
                "before_clipping_or_smoothing": p}
    logs = {o: math.fsum(math.log(r["option_probabilities"][o]) for r in selected) / len(selected) for o in opts}
    pivot = max(logs.values())
    weights = {o: math.exp(logs[o] - pivot) for o in opts}
    total = math.fsum(weights.values())
    raw = {o: weights[o] / total for o in opts}
    epsilon = config["multiple_choice"]["smoothing_epsilon"]
    return {"probability": None, "option_probabilities": {o: (1 - epsilon) * raw[o] + epsilon / len(opts) for o in opts},
            "before_clipping_or_smoothing": raw}


def build_aggregate(snapshot: dict[str, Any], attempts: list[dict[str, Any]], *, stage: str = "tier1",
                    config: dict[str, Any] | None = None, audit_events: Iterable[dict[str, Any]] = (),
                    now: datetime | None = None) -> dict[str, Any]:
    config = load_agg_config() if config is None else config
    events = list(audit_events)
    selected, excluded = select_inputs(snapshot, attempts, stage=stage, audit_events=events)
    values = combine(snapshot, selected, config)
    by_id = {r["attempt_id"]: r for r in attempts}
    lineage = {r["attempt_id"] for r in [*selected, *excluded]}
    pending = list(lineage)
    while pending:
        r = by_id.get(pending.pop(), {})
        parents = [*(r.get("input_attempt_ids") or []), *([r["prev_attempt_id"]] if r.get("prev_attempt_id") else [])]
        for aid in parents:
            if aid not in lineage:
                lineage.add(aid)
                pending.append(aid)
    relevant_events = [e for e in events if _audit_attempt_id(e) in lineage]
    return {"type": "aggregate", "record_id": new_id("agg"), "created_at_utc": to_iso(now or utc_now()),
            "mode": "local", "stage": stage, "aggregation_version": N.AGG_VERSION if snapshot["question_type"] in N.TYPES else config["version"],
            "implementation_version": IMPLEMENTATION_VERSION, "aggregation_config_sha256": hash_obj(config),
            "question_id": snapshot["question_id"], "post_id": snapshot.get("post_id"),
            "tournament_id": snapshot.get("tournament_id"), "question_snapshot_id": snapshot.get("snapshot_id"),
            "question_type": snapshot["question_type"], "options": _options(snapshot) or None,
            "status": "ready" if selected else "no_valid_inputs", **values,
            "input_attempt_ids": [r["attempt_id"] for r in selected],
            "inputs": [{"player": r["player"], "tier": r["tier"], "attempt_id": r["attempt_id"]} for r in selected],
            "missing_players": [p for p in PLAYERS if p not in {r["player"] for r in selected}],
            "excluded_inputs": excluded,
            "audit_flags": sorted({f for r in selected for f in r["effective_audit_flags"]}),
            "audit_event_ids": sorted({eid for e in relevant_events for eid in
                                       (e.get("source_audit_event_ids") or ([e["audit_event_id"]] if e.get("audit_event_id") else []))}),
            "audit_source_records": [dict(zip(("kind", "id"), ref)) for ref in sorted({(r["kind"], r["id"])
                                     for e in relevant_events for r in e.get("source_record_refs", [])})]}


def append_local_aggregate(path: Path, record: dict[str, Any]) -> None:
    """Explicit destination, append only; no automatic pipeline activation or submission."""
    if record.get("type") != "aggregate" or record.get("mode") != "local":
        raise ValueError("only local aggregate records may be appended by this module")
    append_record(path, record)
