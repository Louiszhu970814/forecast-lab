"""Offline tier-2 candidate detection, not permission to start research (PLAN §3.4, §9)."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import urlsplit

from aggregate import build_aggregate, logit, probability, select_inputs
from flab.config import load_run_config
from flab.ids import hash_obj
from flab.timeutil import parse_iso, to_iso, utc_now
import numeric as N

VERSION = "escalation_v2"


def _market_error(market: Any, options: list[str], now: datetime) -> str | None:
    if not isinstance(market, dict) or market.get("match") != "exact":
        return "not_exact"
    try:
        url = urlsplit(market.get("url", ""))
        if url.scheme not in ("http", "https") or not url.hostname:
            return "invalid_url"
        if parse_iso(market.get("retrieved_at_utc")) > now:
            return "future_timestamp"
    except (TypeError, ValueError):
        return "invalid_timestamp_or_url"
    if not options:
        return None if probability(market.get("probability"), endpoints=True) else "invalid_market_probability"
    ops = market.get("option_probabilities")
    if market.get("complete") is not True or not isinstance(ops, dict) or set(ops) != set(options):
        return "incomplete_option_mapping"
    if not all(probability(v, endpoints=True) for v in ops.values()) or abs(math.fsum(ops.values()) - 1) > 0.01:
        return "invalid_market_distribution"
    return None


def evaluate(snapshot: dict[str, Any], attempts: list[dict[str, Any]], *,
             audit_events: Iterable[dict[str, Any]] = (), run_config: dict[str, Any] | None = None,
             agg_config: dict[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Tier-1 aggregation plus every applicable escalation trigger and its observed strength.

    Exact option labels, complete=true, valid probabilities and a timestamp are machine-checkable
    requirements; equivalence of market resolution conditions remains the model's reported claim.
    Budget admission, duration estimates and launching tier 2 are the scheduler's responsibility.
    """
    now = now or utc_now()
    config = load_run_config() if run_config is None else run_config
    policy = config["escalation"]
    expected = {"binary_log_odds_diff": 1.0, "binary_clip": [0.02, 0.98],
                "multiple_choice_prob_diff": 0.25, "market_logit_diff": 0.7,
                "market_price_epsilon": 0.005, "flags": ["resolution_ambiguity", "evidence_conflict"]}
    # v3 changes only deadline headroom; v4 changes only Gemini tier1/tier2 models;
    # v5 changes only forecast timeouts (D152); candidate thresholds are identical.
    if policy != expected or config.get("version") not in {"run_config_v2", "run_config_v3", "run_config_v4", "run_config_v5"}:
        raise ValueError("unsupported escalation settings: implement and version changes first")
    events = list(audit_events)
    selected, _ = select_inputs(snapshot, attempts, stage="tier1", audit_events=events)
    agg = build_aggregate(snapshot, attempts, config=agg_config, audit_events=events, now=now)
    triggers, rejected_markets = [], []
    options = agg["options"] or []
    numeric = snapshot["question_type"] in N.TYPES
    if numeric:
        triggers.extend(N.escalation_triggers(selected, snapshot))
    if len(selected) >= 2 and not numeric:
        if not options:
            lo, hi = policy["binary_clip"]
            values = [logit(min(hi, max(lo, r["probability"]))) for r in selected]
            strength, threshold, option = max(values) - min(values), policy["binary_log_odds_diff"], None
        else:
            # D53: original model values before validation normalisation; no clipping or smoothing.
            probs = [r.get("option_probabilities_raw") or r["option_probabilities"] for r in selected]
            gaps = {o: max(p[o] for p in probs) - min(p[o] for p in probs) for o in options}
            option = max(gaps, key=gaps.get)
            strength, threshold = gaps[option], policy["multiple_choice_prob_diff"]
        if strength >= threshold:
            triggers.append({"type": "model_disagreement", "strength": strength, "threshold": threshold,
                             "option": option, "attempt_ids": [r["attempt_id"] for r in selected]})
    epsilon = policy["market_price_epsilon"]
    for rec in selected:
        for flag in rec.get("escalation_flags") or []:
            if flag in policy["flags"]:
                triggers.append({"type": flag, "attempt_id": rec["attempt_id"], "strength": None, "threshold": None})
        for index, market in enumerate([] if numeric else rec.get("market_refs") or []):
            if reason := _market_error(market, options, now):
                rejected_markets.append({"attempt_id": rec["attempt_id"], "market_index": index, "reason": reason})
                continue
            prices = market["option_probabilities"] if options else {"Yes": market["probability"]}
            estimates = agg["option_probabilities"] if options else {"Yes": agg["probability"]}
            # Only the exact endpoints are replaced (§3.4); retain near-endpoint prices as reported.
            used = {o: epsilon if p == 0 else 1 - epsilon if p == 1 else p for o, p in prices.items()}
            gaps = {o: abs(logit(estimates[o]) - logit(used[o])) for o in used}
            option = max(gaps, key=gaps.get)
            strength = gaps[option]
            if strength >= policy["market_logit_diff"]:
                triggers.append({"type": "market_disagreement", "attempt_id": rec["attempt_id"],
                                 "market_index": index, "market_url": market["url"],
                                 "option": option if options else None, "strength": strength,
                                 "threshold": policy["market_logit_diff"], "market_prices_raw": prices,
                                 "market_prices_used": used})
    agg["escalation"] = {"version": VERSION, "run_config_version": config["version"],
                         "policy_sha256": hash_obj(policy), "evaluated_at_utc": to_iso(now),
                         "candidate": bool(triggers), "triggers": triggers, "rejected_markets": rejected_markets,
                         "admission_status": "pending_budget_and_deadline" if triggers else "not_requested"}
    if numeric:
        numeric_policy = {"iqr_multiplier": N.IQR_MULTIPLIER, "floor_u": N.ESCALATION_FLOOR_U,
                          "flags": policy["flags"], "market_disagreement": False}
        agg["escalation"].update(version=N.ESCALATION_VERSION, policy_sha256=hash_obj(numeric_policy),
                                 numeric_policy=numeric_policy)
    return agg
