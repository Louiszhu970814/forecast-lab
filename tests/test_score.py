"""Unit tests for src/score.py (score_v1 / cdf_score_v1, PLAN.md §4.1–§4.4)."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from flab.jsonl import append_record  # noqa: E402
from flab.ledger import read_complete  # noqa: E402
import numeric as N  # noqa: E402
import resolve as R  # noqa: E402
import score as SC  # noqa: E402


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for r in rows:
        append_record(path, r)


def _freeze_fixture_intents(data_dir: Path) -> None:
    """Give legacy synthetic fixtures the production submit contract before scoring.

    Production intents always carry the frozen `forecast_payload` built by submit.forecast_payload()
    from a contract-complete aggregate (C2). Legacy fixtures only wrote status rows, so this helper
    (a) fills aggregate identity fields from the snapshot where absent and (b) (re)writes one
    fixture intent per submitted intent_id with the real payload. Aggregates that the real
    submit contract rejects get no intent, exactly as in production.
    """
    import submit as SUB

    data_dir = Path(data_dir)
    sub_p, agg_p, q_p = (data_dir / n for n in ("submissions.jsonl", "aggregates.jsonl", "questions.jsonl"))
    if not (sub_p.exists() and agg_p.exists() and q_p.exists()):
        return
    snaps = {r.get("snapshot_id"): r for r in read_complete(q_p) if r.get("type") == "question_snapshot"}
    aggs = read_complete(agg_p)
    subs = [r for r in read_complete(sub_p) if not r.get("_fixture_intent")]
    wanted = {r.get("aggregate_id") for r in subs if r.get("status") in ("forecast_ok", "comment_ok", "dry_run")}
    for a in aggs:
        snap = snaps.get(a.get("question_snapshot_id"))
        if a.get("record_id") in wanted and snap is not None:
            for k in ("post_id", "tournament_id", "question_type", "options"):
                a.setdefault(k, snap.get(k))
    agg_by_id = {a.get("record_id"): a for a in aggs}
    have_payload = {r.get("intent_id") for r in subs if r.get("status") == "intent" and "forecast_payload" in r}
    extra: List[Dict[str, Any]] = []
    seen: set = set()
    for r in subs:
        iid = r.get("intent_id")
        if r.get("status") not in ("forecast_ok", "comment_ok", "dry_run") or iid in have_payload or iid in seen:
            continue
        a = agg_by_id.get(r.get("aggregate_id"))
        snap = snaps.get(r.get("question_snapshot_id"))
        if a is None or snap is None:
            continue
        try:
            payload = SUB.forecast_payload(snap, a)
        except Exception:
            continue
        seen.add(iid)
        extra.append({"type": "submission", "status": "intent", "_fixture_intent": True, "intent_id": iid,
                      "question_id": r.get("question_id"), "question_snapshot_id": r.get("question_snapshot_id"),
                      "aggregate_id": r.get("aggregate_id"), "snapshot": snap, "forecast_payload": payload,
                      "at": r.get("at")})
    agg_p.write_text("".join(json.dumps(a) + "\n" for a in aggs))
    sub_p.write_text("".join(json.dumps(x) + "\n" for x in extra + subs))


@pytest.fixture(autouse=True)
def _production_submit_contract(monkeypatch: pytest.MonkeyPatch):
    real = SC.evaluate_scoreboard

    def wrapped(data_dir: Path = SC.DEFAULT_DATA_DIR, **kw: Any):
        _freeze_fixture_intents(Path(data_dir))
        return real(data_dir, **kw)

    monkeypatch.setattr(SC, "evaluate_scoreboard", wrapped)
    yield


def _numeric_snapshot(
    qid: int = 201,
    *,
    qtype: str = "numeric",
    lo: float = 0.0,
    hi: float = 100.0,
    n: int = 20,
    open_lo: bool = False,
    open_hi: bool = False,
) -> Dict[str, Any]:
    grid = [lo + (hi - lo) * (i / n) for i in range(n + 1)]
    snap: Dict[str, Any] = {
        "type": "question_snapshot",
        "snapshot_id": f"snap_{qid}",
        "question_id": qid,
        "post_id": 9000 + qid,
        "tournament_id": 33135,
        "venue": "minibench",
        "question_type": qtype,
        "range_min": lo,
        "range_max": hi,
        "zero_point": None,
        "open_lower_bound": open_lo,
        "open_upper_bound": open_hi,
        "inbound_outcome_count": n,
        "unit": "units",
        "continuous_range": grid,
    }
    if qtype == "discrete":
        # Discrete: n outcomes from nominal_min=0 to nominal_max=n-1, step=1, range=[-0.5, n-0.5]
        a, b = 0.0, float(n - 1)
        r_lo, r_hi = -0.5, float(n) - 0.5
        snap.update({
            "nominal_min": a,
            "nominal_max": b,
            "range_min": r_lo,
            "range_max": r_hi,
            "continuous_range": [r_lo + (r_hi - r_lo) * (i / n) for i in range(n + 1)],
        })
    return snap


# --------------------------------------------------------------------------- core formulas
def test_score_binary_exact_and_clamped():
    # Extreme forecast p = 0.998, outcome = 0 (wrong)
    res = SC.score_binary(0.998, 0)
    assert res["log_score"] == pytest.approx(math.log(0.002))
    assert res["log_score_clamped"] == pytest.approx(math.log(0.01))
    assert res["brier_score"] == pytest.approx(0.998 ** 2)

    # Normal forecast p = 0.75, outcome = 1 (correct)
    res2 = SC.score_binary(0.75, 1)
    assert res2["log_score"] == pytest.approx(math.log(0.75))
    assert res2["log_score_clamped"] == pytest.approx(math.log(0.75))
    assert res2["brier_score"] == pytest.approx(0.25 ** 2)


def test_score_multiple_choice_exact_and_multiclass_brier():
    options = ["A", "B", "C"]
    probs = {"A": 0.995, "B": 0.003, "C": 0.002}
    res = SC.score_multiple_choice(probs, options, "C")
    assert res["log_score"] == pytest.approx(math.log(0.002))
    assert res["log_score_clamped"] == pytest.approx(math.log(0.01))
    expected_brier = (0.995 - 0.0) ** 2 + (0.003 - 0.0) ** 2 + (0.002 - 1.0) ** 2
    assert res["brier_score"] == pytest.approx(expected_brier)


def test_score_multiple_choice_rejects_unnormalized_or_duplicate_options():
    with pytest.raises(ValueError, match="must sum to 1.0"):
        SC.score_multiple_choice({"A": 0.9, "B": 0.9}, ["A", "B"], "A")

    with pytest.raises(ValueError, match="distinct"):
        SC.score_multiple_choice({"A": 0.5, "B": 0.5}, ["A", "A"], "A")

    with pytest.raises(ValueError, match="match options"):
        SC.score_multiple_choice({"A": 0.5, "B": 0.3, "C": 0.2}, ["A", "B"], "A")


def test_score_cdf_numeric_uniform_exact_crps_and_concentrated():
    snap = _numeric_snapshot(qtype="numeric", lo=0.0, hi=100.0, n=20, open_lo=False, open_hi=False)
    uniform_cdf = [i / 20.0 for i in range(21)]
    s_uni = SC.score_cdf(uniform_cdf, snap, 50.0)
    # Uniform density on unit scale is (1/20) * 20 = 1.0 -> log(1.0) == 0.0
    assert s_uni["log_score"] == pytest.approx(0.0, abs=1e-9)
    assert s_uni["log_score_clamped"] == pytest.approx(0.0, abs=1e-9)
    # Exact piecewise-linear CRPS of uniform unit distribution at u*=0.5 is 1/12
    assert s_uni["brier_score"] == pytest.approx(1.0 / 12.0, abs=1e-12)

    # Concentrated CDF around 50.0 should beat uniform on both log_score and CRPS (brier_score)
    concentrated = N.to_cdf(
        {"p05": 40.0, "p10": 43.0, "p25": 47.0, "p50": 50.0, "p75": 53.0, "p90": 57.0, "p95": 60.0},
        snap,
    )
    s_conc = SC.score_cdf(concentrated, snap, 50.0)
    assert s_conc["log_score"] > s_uni["log_score"]
    assert s_conc["brier_score"] < s_uni["brier_score"]


def test_score_cdf_open_bound_tails_and_proper_tail_crps_incentives():
    snap_open = _numeric_snapshot(qtype="numeric", lo=0.0, hi=100.0, n=20, open_lo=True, open_hi=True)
    # Check GPT's analytical tail-indicator case on _continuous_unit_crps:
    # For above_upper_bound, H(u) == 0 everywhere on [0, 1], so F(u) = c * u has exact integral c^2 / 3.
    cdf_c01 = [0.01 * (i / 20.0) for i in range(21)]
    cdf_c10 = [0.10 * (i / 20.0) for i in range(21)]
    loss_99_tail = SC._continuous_unit_crps(cdf_c01, 20, None, "above_upper_bound")
    loss_90_tail = SC._continuous_unit_crps(cdf_c10, 20, None, "above_upper_bound")
    assert loss_99_tail == pytest.approx((0.01 ** 2) / 3.0, rel=1e-12)
    assert loss_90_tail == pytest.approx((0.10 ** 2) / 3.0, rel=1e-12)
    assert loss_99_tail < loss_90_tail

    # Now check full score_cdf with two valid open-bound CDFs: one shifted toward upper tail, one centered
    cdf_center = N.to_cdf(
        {"p05": 10.0, "p10": 20.0, "p25": 35.0, "p50": 50.0, "p75": 65.0, "p90": 80.0, "p95": 90.0},
        snap_open,
    )
    cdf_high = N.to_cdf(
        {"p05": 60.0, "p10": 70.0, "p25": 82.0, "p50": 92.0, "p75": 98.0, "p90": 105.0, "p95": 115.0},
        snap_open,
    )
    s_center_hi = SC.score_cdf(cdf_center, snap_open, "above_upper_bound")
    s_high_hi = SC.score_cdf(cdf_high, snap_open, "above_upper_bound")
    assert s_high_hi["log_score"] == pytest.approx(math.log(1.0 - cdf_high[-1]))
    assert s_high_hi["log_score"] > s_center_hi["log_score"]
    # Higher mass on the upper tail (smaller F(u) across [0, 1]) MUST yield lower CRPS loss
    assert s_high_hi["brier_score"] < s_center_hi["brier_score"]

    # Symmetrically for below_lower_bound
    cdf_low = N.to_cdf(
        {"p05": -15.0, "p10": -5.0, "p25": 2.0, "p50": 8.0, "p75": 18.0, "p90": 30.0, "p95": 40.0},
        snap_open,
    )
    s_center_lo = SC.score_cdf(cdf_center, snap_open, "below_lower_bound")
    s_low_lo = SC.score_cdf(cdf_low, snap_open, "below_lower_bound")
    assert s_low_lo["log_score"] == pytest.approx(math.log(cdf_low[0]))
    assert s_low_lo["log_score"] > s_center_lo["log_score"]
    assert s_low_lo["brier_score"] < s_center_lo["brier_score"]

    # Closed bounds must reject tail outcomes
    snap_closed = _numeric_snapshot(qtype="numeric", lo=0.0, hi=100.0, n=20, open_lo=False, open_hi=False)
    uniform_cdf = [i / 20.0 for i in range(21)]
    with pytest.raises(ValueError, match="closed lower bound"):
        SC.score_cdf(uniform_cdf, snap_closed, "below_lower_bound")
    with pytest.raises(ValueError, match="closed upper bound"):
        SC.score_cdf(uniform_cdf, snap_closed, "above_upper_bound")


def test_score_cdf_discrete():
    snap = _numeric_snapshot(qid=202, qtype="discrete", n=10, open_lo=True, open_hi=True)
    cdf = N.to_cdf(
        {"p05": 2.0, "p10": 3.0, "p25": 4.0, "p50": 5.0, "p75": 6.0, "p90": 7.0, "p95": 8.0},
        snap,
    )
    res = SC.score_cdf(cdf, snap, 5.0)
    expected_mass = cdf[6] - cdf[5]
    assert res["log_score"] == pytest.approx(math.log(expected_mass))

    # Discrete diagnostic clamp MUST apply [0.01, 0.99] on mass directly (not continuous [0.01/n, 0.99])
    cdf_tiny = [0.002 if i == 0 else 0.002 + (0.996 * i / 10.0) for i in range(11)]
    res_tiny = SC.score_cdf(cdf_tiny, snap, "below_lower_bound")
    assert res_tiny["log_score"] == pytest.approx(math.log(0.002))
    assert res_tiny["log_score_clamped"] == pytest.approx(math.log(0.01))

    # Discrete upper tail RPS: H_i = 0 for all i=0..n
    res_hi = SC.score_cdf(cdf, snap, "above_upper_bound")
    assert res_hi["log_score"] == pytest.approx(math.log(1.0 - cdf[-1]))
    assert res_hi["brier_score"] == pytest.approx(sum(v * v for v in cdf) / 11.0)

    with pytest.raises(ValueError, match="does not align"):
        SC.score_cdf(cdf, snap, 5.3)


# --------------------------------------------------------------------------- evaluate_scoreboard & provenance
def test_evaluate_scoreboard_end_to_end_and_provenance_binding(tmp_path: Path):
    data_dir = tmp_path / "data"
    ts_snap = "2026-11-03T08:00:00Z"
    ts_t1 = "2026-11-03T08:30:00Z"
    ts_t2 = "2026-11-03T09:00:00Z"
    ts_agg_t1 = "2026-11-03T08:40:00Z"
    ts_agg_fin = "2026-11-03T09:10:00Z"
    ts_sub = "2026-11-03T10:00:00Z"

    # Q101: escalated binary question (resolved Yes=1, with later resolution revision after as_of)
    # Q102: non-escalated binary question (resolved No=0)
    # Q103: ambiguous question (must be excluded from scoring)
    # Q104: annulled question (must be excluded from scoring)
    # Q105: escalated question that failed to submit (must be counted in D52 escalation denominator!)
    _write_jsonl(data_dir / "questions.jsonl", [
        {
            "type": "question_snapshot",
            "snapshot_id": "s101",
            "question_id": 101,
            "post_id": 9101,
            "tournament_id": 33135,
            "venue": "minibench",
            "question_type": "binary",
            "title": "Q101",
            "status": "resolved",
            "seen_at": ts_snap,
        },
        {
            "type": "question_snapshot",
            "snapshot_id": "s102",
            "question_id": 102,
            "post_id": 9102,
            "tournament_id": 33135,
            "venue": "minibench",
            "question_type": "binary",
            "title": "Q102",
            "status": "resolved",
            "seen_at": ts_snap,
        },
        {
            "type": "question_snapshot",
            "snapshot_id": "s103",
            "question_id": 103,
            "post_id": 9103,
            "tournament_id": 33135,
            "venue": "minibench",
            "question_type": "binary",
            "title": "Q103",
            "status": "resolved",
            "seen_at": ts_snap,
        },
        {
            "type": "question_snapshot",
            "snapshot_id": "s104",
            "question_id": 104,
            "post_id": 9104,
            "tournament_id": 33135,
            "venue": "minibench",
            "question_type": "binary",
            "title": "Q104",
            "status": "resolved",
            "seen_at": ts_snap,
        },
        {
            "type": "question_snapshot",
            "snapshot_id": "s105",
            "question_id": 105,
            "post_id": 9105,
            "tournament_id": 33135,
            "venue": "minibench",
            "question_type": "binary",
            "title": "Q105",
            "status": "resolved",
            "seen_at": ts_snap,
        },
        # Undated snapshot: must be rejected by cutoff filter
        {
            "type": "question_snapshot",
            "snapshot_id": "s_undated",
            "question_id": 999,
            "post_id": 9999,
            "question_type": "binary",
        },
    ])

    _write_jsonl(data_dir / "forecasts.jsonl", [
        # Q101 Tier 1 & Tier 2 (escalated, bound to agg101_fin)
        {"type": "forecast_attempt", "attempt_id": "101_c1", "question_id": 101, "question_snapshot_id": "s101",
         "player": "claude", "tier": 1, "validation_status": "valid", "probability": 0.70,
         "run_config_version": "run_config_v3", "prompt_version": "forecast_v2", "finished_at_utc": ts_t1},
        {"type": "forecast_attempt", "attempt_id": "101_g1", "question_id": 101, "question_snapshot_id": "s101",
         "player": "gemini", "tier": 1, "validation_status": "valid", "probability": 0.80,
         "run_config_version": "run_config_v3", "prompt_version": "forecast_v2", "finished_at_utc": ts_t1},
        {"type": "forecast_attempt", "attempt_id": "101_p1", "question_id": 101, "question_snapshot_id": "s101",
         "player": "gpt", "tier": 1, "validation_status": "valid", "probability": 0.60,
         "run_config_version": "run_config_v3", "prompt_version": "forecast_v2", "finished_at_utc": ts_t1},
        {"type": "forecast_attempt", "attempt_id": "101_c2", "question_id": 101, "question_snapshot_id": "s101",
         "player": "claude", "tier": 2, "validation_status": "valid", "probability": 0.85,
         "input_attempt_ids": ["101_c1", "101_g1", "101_p1"],
         "run_config_version": "run_config_v3", "prompt_version": "reconcile_v1", "finished_at_utc": ts_t2},
        {"type": "forecast_attempt", "attempt_id": "101_g2", "question_id": 101, "question_snapshot_id": "s101",
         "player": "gemini", "tier": 2, "validation_status": "valid", "probability": 0.82,
         "input_attempt_ids": ["101_c1", "101_g1", "101_p1"],
         "run_config_version": "run_config_v3", "prompt_version": "reconcile_v1", "finished_at_utc": ts_t2},
        {"type": "forecast_attempt", "attempt_id": "101_p2", "question_id": 101, "question_snapshot_id": "s101",
         "player": "gpt", "tier": 2, "validation_status": "valid", "probability": 0.78,
         "input_attempt_ids": ["101_c1", "101_g1", "101_p1"],
         "run_config_version": "run_config_v3", "prompt_version": "reconcile_v1", "finished_at_utc": ts_t2},
        # Unrelated later unsubmitted rerun on s101 with different config & probability: MUST NOT pollute Q101!
        {"type": "forecast_attempt", "attempt_id": "101_c1_unrelated", "question_id": 101, "question_snapshot_id": "s101",
         "player": "claude", "tier": 1, "validation_status": "valid", "probability": 0.05,
         "run_config_version": "run_config_v99", "prompt_version": "forecast_v99", "finished_at_utc": ts_t2},
        # Q102 Tier 1 only (not escalated), plus a leak_confirmed attempt that was excluded from agg102_fin
        {"type": "forecast_attempt", "attempt_id": "102_c1", "question_id": 102, "question_snapshot_id": "s102",
         "player": "claude", "tier": 1, "validation_status": "valid", "probability": 0.20,
         "run_config_version": "run_config_v3", "prompt_version": "forecast_v2", "finished_at_utc": ts_t1},
        {"type": "forecast_attempt", "attempt_id": "102_g1", "question_id": 102, "question_snapshot_id": "s102",
         "player": "gemini", "tier": 1, "validation_status": "valid", "probability": 0.25,
         "run_config_version": "run_config_v3", "prompt_version": "forecast_v2", "finished_at_utc": ts_t1},
        {"type": "forecast_attempt", "attempt_id": "102_p1_leak", "question_id": 102, "question_snapshot_id": "s102",
         "player": "gpt", "tier": 1, "validation_status": "valid", "probability": 0.01,
         "audit_flags": ["leak_confirmed"],
         "run_config_version": "run_config_v3", "prompt_version": "forecast_v2", "finished_at_utc": ts_t1},
        # Q105 failed Tier 2 attempt on an escalated question that never submitted (counted in D52 resource usage)
        {"type": "forecast_attempt", "attempt_id": "105_c2_fail", "question_id": 105, "question_snapshot_id": "s105",
         "player": "claude", "tier": 2, "validation_status": "invalid_output", "probability": None,
         "run_config_version": "run_config_v3", "prompt_version": "reconcile_v1", "finished_at_utc": ts_t2},
    ])

    _write_jsonl(data_dir / "aggregates.jsonl", [
        {"type": "aggregate", "record_id": "agg101_t1", "question_id": 101, "question_snapshot_id": "s101",
         "stage": "tier1", "status": "ready", "probability": 0.70, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["101_c1", "101_g1", "101_p1"],
         "escalation": {"escalate": True}, "created_at_utc": ts_agg_t1},
        {"type": "aggregate", "record_id": "agg101_fin", "question_id": 101, "question_snapshot_id": "s101",
         "stage": "final", "status": "ready", "probability": 0.82, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["101_c2", "101_g2", "101_p2"], "created_at_utc": ts_agg_fin},
        # Unrelated later tier1 aggregate on s101 (or partial subset): must not replace agg101_t1
        {"type": "aggregate", "record_id": "agg101_t1_subset", "question_id": 101, "question_snapshot_id": "s101",
         "stage": "tier1", "status": "ready", "probability": 0.05, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["101_c1", "101_g1"], "created_at_utc": ts_agg_t1},
        {"type": "aggregate", "record_id": "agg102_fin", "question_id": 102, "question_snapshot_id": "s102",
         "stage": "final", "status": "ready", "probability": 0.22, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["102_c1", "102_g1"], "created_at_utc": ts_agg_fin},
        {"type": "aggregate", "record_id": "agg103_fin", "question_id": 103, "question_snapshot_id": "s103",
         "stage": "final", "status": "ready", "probability": 0.50, "aggregation_version": "agg_v1",
         "input_attempt_ids": [], "created_at_utc": ts_agg_fin},
        {"type": "aggregate", "record_id": "agg104_fin", "question_id": 104, "question_snapshot_id": "s104",
         "stage": "final", "status": "ready", "probability": 0.50, "aggregation_version": "agg_v1",
         "input_attempt_ids": [], "created_at_utc": ts_agg_fin},
        {"type": "aggregate", "record_id": "agg105_t1", "question_id": 105, "question_snapshot_id": "s105",
         "stage": "tier1", "status": "ready", "probability": 0.50, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["101_c1"], "escalation": {"escalate": True}, "created_at_utc": ts_agg_t1},
    ])

    _write_jsonl(data_dir / "submissions.jsonl", [
        {"question_id": 101, "question_snapshot_id": "s101", "intent_id": "i101", "aggregate_id": "agg101_fin",
         "status": "comment_ok", "at": ts_sub},
        {"question_id": 102, "question_snapshot_id": "s102", "intent_id": "i102", "aggregate_id": "agg102_fin",
         "status": "comment_ok", "at": ts_sub},
        {"question_id": 103, "question_snapshot_id": "s103", "intent_id": "i103", "aggregate_id": "agg103_fin",
         "status": "comment_ok", "at": ts_sub},
        {"question_id": 104, "question_snapshot_id": "s104", "intent_id": "i104", "aggregate_id": "agg104_fin",
         "status": "comment_ok", "at": ts_sub},
    ])

    _write_jsonl(data_dir / "resolutions.jsonl", [
        {"type": "resolution_event", "resolution_event_id": "r101_v1", "question_id": 101, "snapshot_id": "s101",
         "observed_at_utc": "2026-11-06T12:00:00Z", "resolution_status": "resolved", "canonical_outcome": 1,
         "official_score_data": {"spot_peer_score": 12.5},
         "official_ranking_data": {"leaderboard_rank": 3, "total_participants": 40, "ranking_source": "tournament_leaderboard"}},
        # Future revision after as_of="2026-11-06T13:00:00Z": must be ignored at 13:00Z
        {"type": "resolution_event", "resolution_event_id": "r101_v2", "question_id": 101, "snapshot_id": "s101",
         "observed_at_utc": "2026-11-07T12:00:00Z", "resolution_status": "resolved", "canonical_outcome": 0,
         "official_score_data": {"spot_peer_score": -30.0}},
        {"type": "resolution_event", "resolution_event_id": "r102", "question_id": 102, "snapshot_id": "s102",
         "observed_at_utc": "2026-11-06T12:00:00Z", "resolution_status": "resolved", "canonical_outcome": 0,
         "official_score_data": {"spot_peer_score": 8.5}},
        {"type": "resolution_event", "resolution_event_id": "r103", "question_id": 103, "snapshot_id": "s103",
         "observed_at_utc": "2026-11-06T12:00:00Z", "resolution_status": "ambiguous", "canonical_outcome": None},
        {"type": "resolution_event", "resolution_event_id": "r104", "question_id": 104, "snapshot_id": "s104",
         "observed_at_utc": "2026-11-06T12:00:00Z", "resolution_status": "annulled", "canonical_outcome": None},
    ])

    board = SC.evaluate_scoreboard(data_dir, as_of="2026-11-06T13:00:00Z", n_boot=100)
    assert board["submitted_questions_count"] == 4
    assert board["scorable_resolved_count"] == 2
    assert board["excluded_counts"] == {"ambiguous": 1, "annulled": 1, "unresolved": 0, "no_resolution_event": 0}
    assert board["rejected_cutoff_counts"]["questions"] == 1
    assert board["rejected_cutoff_counts"]["resolutions"] == 1
    assert board["conclusion_discipline"]["tier"] == "descriptive_only"
    assert board["conclusion_discipline"]["can_claim_superiority"] is False

    # Verify Q101 provenance: claude Tier 1 used 0.70 (101_c1), NOT 0.05 (101_c1_unrelated),
    # tier1_aggregate used 0.70 (agg101_t1), NOT 0.05 (agg101_t1_subset),
    # and config_segment includes reconcile_v1, NOT run_config_v99!
    q101 = next(q for q in board["questions"] if q["question_id"] == 101)
    assert q101["config_segment"] == "run_config_v3|agg_v1|forecast_v2|reconcile_v1"
    assert q101["scores"]["player_tier1:claude"]["log_score"] == pytest.approx(math.log(0.70))
    assert q101["scores"]["tier1_aggregate"]["log_score"] == pytest.approx(math.log(0.70))

    # Official score & ranking summary
    assert board["official_scores"]["spot_peer_score_count"] == 2
    assert board["official_scores"]["spot_peer_score_sum"] == pytest.approx(21.0)
    # B3/B4: rankings embedded in resolution_event rows (legacy shape) are ignored; there is no global
    # "latest" ranking, only verified per-tournament ranking_observation records.
    assert "official_ranking" not in board["official_scores"]
    assert board["official_scores"]["official_rankings_by_tournament"] == {}

    # Verify D52 escalation accounting includes Q105 (unsubmitted escalated question + failed Tier 2 attempt)
    # while matching ITT efficiency denominator to ITT-evaluatable episodes (Q101's 3 attempts)
    d52 = board["overall"]["tier2_escalation_analysis"]
    assert d52["escalated_questions_total"] == 2  # Q101 + Q105
    assert d52["escalated_submitted_count"] == 1
    assert d52["escalated_unsubmitted_count"] == 1
    assert d52["incomplete_questions"] == 1  # Q105
    assert d52["incomplete_rate"] == pytest.approx(0.5)
    assert d52["extra_tier2_attempts_used"] == 4  # 3 valid on Q101 + 1 failed on Q105 (all-escalation cost)
    assert d52["itt_tier2_attempts"] == 3  # Q101 is ITT-evaluatable
    # B1: unsubmitted Q105 has no outcome source at all -> permanent ITT gap, not "not yet evaluable"
    assert d52["not_yet_evaluable_count"] == 0
    assert d52["missing_outcome_count"] == 1
    assert d52["missing_outcome_question_ids"] == [105]
    assert d52["missing_outcome_reasons"]["105"] == "unsubmitted_escalation_no_outcome_source"
    assert d52["tier2_failed_or_tainted_attempts"] == 1
    # B5: Q105's tier1 aggregate cites another question's attempt and 105_c2_fail has no parent link,
    # so Q105 is unsegmentable and the overall D52 view refuses to pool gains (no guessed attribution).
    assert board["overall"]["unsegmentable_question_ids"] == [105]
    assert d52["pooling_prohibited"] is True
    assert d52["intent_to_treat_log_score_diff"] is None
    assert d52["log_score_gain_per_tier2_attempt"] is None

    # Check player_tier1:gpt excluded the leak_confirmed attempt on Q102 -> scored_count=1, missing_count=1
    gpt_t1 = board["overall"]["series"]["player_tier1:gpt"]
    assert gpt_t1["eligible_questions"] == 2
    assert gpt_t1["scored_count"] == 1
    assert gpt_t1["missing_count"] == 1

    # Check player_tier2 is restricted to escalated questions only (eligible_questions == 1, not 2!)
    for p in ("claude", "gemini", "gpt"):
        t2_s = board["overall"]["series"][f"player_tier2:{p}"]
        assert t2_s["eligible_questions"] == 1
        assert t2_s["scored_count"] == 1

    # Render markdown and verify --write-report (and verify refusing to overwrite the same report path)
    report_path = tmp_path / "reports" / "scoreboard_v1.md"
    rc = SC.main([
        "--data-dir", str(data_dir),
        "--as-of", "2026-11-06T13:00:00Z",
        "--bootstrap-iterations", "50",
        "--format", "markdown",
        "--write-report",
        "--report-path", str(report_path),
    ])
    assert rc == 0
    assert report_path.exists()
    md_text = report_path.read_text(encoding="utf-8")
    assert "descriptive_only" in md_text
    assert "final_aggregate" in md_text
    assert "### Segment `run_config_v3|agg_v1|forecast_v2|reconcile_v1`" in md_text
    assert "Tier 2 Escalation Accounting (PLAN §4.3 / D52)" in md_text

    # Attempting to overwrite the existing report_path must raise FileExistsError (PLAN §4.4)
    with pytest.raises(FileExistsError, match="refusing to overwrite existing report"):
        SC.main([
            "--data-dir", str(data_dir),
            "--as-of", "2026-11-06T13:00:00Z",
            "--write-report",
            "--report-path", str(report_path),
        ])


def test_missing_or_invalid_provenance_disqualifies_final_aggregate_and_enforces_non_pooling(tmp_path: Path):
    data_dir = tmp_path / "data"
    ts = "2026-11-03T08:00:00Z"
    _write_jsonl(data_dir / "questions.jsonl", [
        {"type": "question_snapshot", "snapshot_id": "s1", "question_id": 1, "post_id": 1,
         "question_type": "binary", "status": "resolved", "seen_at": ts},
        {"type": "question_snapshot", "snapshot_id": "s2", "question_id": 2, "post_id": 2,
         "question_type": "binary", "status": "resolved", "seen_at": ts},
        {"type": "question_snapshot", "snapshot_id": "s3", "question_id": 3, "post_id": 3,
         "question_type": "binary", "status": "resolved", "seen_at": ts},
    ])
    _write_jsonl(data_dir / "forecasts.jsonl", [
        # Q2 uses reconcile_v1
        {"type": "forecast_attempt", "attempt_id": "2_c1", "question_id": 2, "question_snapshot_id": "s2",
         "player": "claude", "tier": 1, "validation_status": "valid", "probability": 0.60,
         "run_config_version": "run_config_v3", "prompt_version": "forecast_v2", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "2_c2", "question_id": 2, "question_snapshot_id": "s2",
         "player": "claude", "tier": 2, "validation_status": "valid", "probability": 0.70,
         "input_attempt_ids": ["2_c1"],
         "run_config_version": "run_config_v3", "prompt_version": "reconcile_v1", "finished_at_utc": ts},
        # Q3 uses reconcile_v2 -> must form a distinct config_segment and trigger non-pooling in overall!
        {"type": "forecast_attempt", "attempt_id": "3_c1", "question_id": 3, "question_snapshot_id": "s3",
         "player": "claude", "tier": 1, "validation_status": "valid", "probability": 0.65,
         "run_config_version": "run_config_v3", "prompt_version": "forecast_v2", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "3_c2", "question_id": 3, "question_snapshot_id": "s3",
         "player": "claude", "tier": 2, "validation_status": "valid", "probability": 0.75,
         "input_attempt_ids": ["3_c1"],
         "run_config_version": "run_config_v3", "prompt_version": "reconcile_v2", "finished_at_utc": ts},
    ])
    _write_jsonl(data_dir / "aggregates.jsonl", [
        # Q1 references a missing attempt "1_missing_att" -> must disqualify agg1!
        {"type": "aggregate", "record_id": "agg1", "question_id": 1, "question_snapshot_id": "s1",
         "stage": "final", "status": "ready", "probability": 0.90, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["1_missing_att"], "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg2_t1", "question_id": 2, "question_snapshot_id": "s2",
         "stage": "tier1", "status": "ready", "probability": 0.60, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["2_c1"], "escalation": {"escalate": True}, "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg2_fin", "question_id": 2, "question_snapshot_id": "s2",
         "stage": "final", "status": "ready", "probability": 0.70, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["2_c2"], "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg3_t1", "question_id": 3, "question_snapshot_id": "s3",
         "stage": "tier1", "status": "ready", "probability": 0.65, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["3_c1"], "escalation": {"escalate": True}, "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg3_fin", "question_id": 3, "question_snapshot_id": "s3",
         "stage": "final", "status": "ready", "probability": 0.75, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["3_c2"], "created_at_utc": ts},
    ])
    _write_jsonl(data_dir / "submissions.jsonl", [
        {"question_id": 1, "question_snapshot_id": "s1", "intent_id": "i1", "aggregate_id": "agg1",
         "status": "comment_ok", "at": ts},
        {"question_id": 2, "question_snapshot_id": "s2", "intent_id": "i2", "aggregate_id": "agg2_fin",
         "status": "comment_ok", "at": ts},
        {"question_id": 3, "question_snapshot_id": "s3", "intent_id": "i3", "aggregate_id": "agg3_fin",
         "status": "comment_ok", "at": ts},
    ])
    _write_jsonl(data_dir / "resolutions.jsonl", [
        {"type": "resolution_event", "resolution_event_id": "r1", "question_id": 1, "snapshot_id": "s1",
         "observed_at_utc": "2026-11-06T12:00:00Z", "resolution_status": "resolved", "canonical_outcome": 1},
        {"type": "resolution_event", "resolution_event_id": "r2", "question_id": 2, "snapshot_id": "s2",
         "observed_at_utc": "2026-11-06T12:00:00Z", "resolution_status": "resolved", "canonical_outcome": 1},
        {"type": "resolution_event", "resolution_event_id": "r3", "question_id": 3, "snapshot_id": "s3",
         "observed_at_utc": "2026-11-06T12:00:00Z", "resolution_status": "resolved", "canonical_outcome": 1},
    ])
    # Multi-segment cycle spanning Q2 (reconcile_v1) and Q3 (reconcile_v2), with explicit questions_skipped_budget=[2]:
    # skip must be attributed specifically to reconcile_v1 (and not lost as unattributed)!
    _write_jsonl(data_dir / "cycles.jsonl", [
        {"type": "cycle_summary", "cycle_id": "cyc_multi", "finished_at_utc": ts,
         "skipped_budget": 1, "questions_skipped_budget": [2], "question_ids": [2, 3]},
    ])

    board = SC.evaluate_scoreboard(data_dir, as_of="2026-11-06T13:00:00Z", n_boot=20)
    q1 = next(q for q in board["questions"] if q["question_id"] == 1)
    assert q1["scores"]["final_aggregate"] is None
    assert q1["scoring_errors"]["final_aggregate"] == "missing_input_attempt:1_missing_att"

    # Q2 and Q3 have different Tier 2 reconciliation prompt versions -> distinct segments & non-pooling in overall!
    assert "run_config_v3|agg_v1|forecast_v2|reconcile_v1" in board["by_config_segment"]
    assert "run_config_v3|agg_v1|forecast_v2|reconcile_v2" in board["by_config_segment"]
    assert board["overall"]["pooling_prohibited"] is True
    assert board["overall"]["series"] is None
    assert board["overall"]["pairwise"] is None
    overall_d52 = board["overall"]["tier2_escalation_analysis"]
    assert overall_d52["pooling_prohibited"] is True
    assert overall_d52["total_log_score_gain_nats"] is None
    assert overall_d52["observed_pair_log_score_diff"] is None
    assert overall_d52["intent_to_treat_log_score_diff"] is None
    # Meanwhile each individual segment DOES have its own pooled D52 analysis and accurate explicit budget-skip attribution
    seg_v1_d52 = board["by_config_segment"]["run_config_v3|agg_v1|forecast_v2|reconcile_v1"]["tier2_escalation_analysis"]
    seg_v2_d52 = board["by_config_segment"]["run_config_v3|agg_v1|forecast_v2|reconcile_v2"]["tier2_escalation_analysis"]
    assert seg_v1_d52["pooling_prohibited"] is False
    assert seg_v1_d52["total_log_score_gain_nats"] == pytest.approx(math.log(0.70) - math.log(0.60))
    assert seg_v1_d52["missed_question_opportunity_cost"]["budget_skipped_questions_in_cycles"] == 1
    assert seg_v1_d52["missed_question_opportunity_cost"]["unattributed_cycle_budget_skips"] == 0
    assert seg_v2_d52["missed_question_opportunity_cost"]["budget_skipped_questions_in_cycles"] == 0


def test_round3_regressions_mc_signature_ancestors_itt_and_cutoff(tmp_path: Path):
    data_dir = tmp_path / "data"
    ts = "2026-11-03T08:00:00Z"
    ts_late = "2026-11-07T08:00:00Z"
    _write_jsonl(data_dir / "questions.jsonl", [
        # Q10: multiple_choice with duplicate identical tier1 aggregate records (must NOT raise unhashable dict TypeError)
        {"type": "question_snapshot", "snapshot_id": "s10", "question_id": 10, "post_id": 10,
         "tournament_id": 33135, "question_type": "multiple_choice", "options": ["A", "B"],
         "status": "resolved", "seen_at": ts},
        # Q11: multiple_choice with conflicting tier1 aggregate records -> must reject tier1_aggregate as ambiguous
        {"type": "question_snapshot", "snapshot_id": "s11", "question_id": 11, "post_id": 11,
         "tournament_id": 33135, "question_type": "multiple_choice", "options": ["A", "B"],
         "status": "resolved", "seen_at": ts},
        # Q12: Tier 2 attempt whose prev_attempt_id is tainted/invalid -> must disqualify final_aggregate
        {"type": "question_snapshot", "snapshot_id": "s12", "question_id": 12, "post_id": 12,
         "tournament_id": 33135, "question_type": "binary", "status": "resolved", "seen_at": ts},
        # Q13: Escalated question that resolved (Yes=1) with valid Tier 1 baseline, but Tier 2 failed to submit -> ITT gain = 0.0!
        {"type": "question_snapshot", "snapshot_id": "s13", "question_id": 13, "post_id": 13,
         "tournament_id": 99999, "question_type": "binary", "status": "resolved", "seen_at": ts},
    ])
    _write_jsonl(data_dir / "forecasts.jsonl", [
        # Q10
        {"type": "forecast_attempt", "attempt_id": "10_c1", "question_id": 10, "question_snapshot_id": "s10",
         "player": "claude", "tier": 1, "validation_status": "valid", "option_probabilities": {"A": 0.6, "B": 0.4},
         "run_config_version": "rc1", "prompt_version": "p1", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "10_c2", "question_id": 10, "question_snapshot_id": "s10",
         "player": "claude", "tier": 2, "validation_status": "valid", "option_probabilities": {"A": 0.8, "B": 0.2},
         "input_attempt_ids": ["10_c1"],
         "run_config_version": "rc1", "prompt_version": "r1", "finished_at_utc": ts},
        # Q11
        {"type": "forecast_attempt", "attempt_id": "11_c1", "question_id": 11, "question_snapshot_id": "s11",
         "player": "claude", "tier": 1, "validation_status": "valid", "option_probabilities": {"A": 0.6, "B": 0.4},
         "run_config_version": "rc1", "prompt_version": "p1", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "11_c2", "question_id": 11, "question_snapshot_id": "s11",
         "player": "claude", "tier": 2, "validation_status": "valid", "option_probabilities": {"A": 0.8, "B": 0.2},
         "input_attempt_ids": ["11_c1"],
         "run_config_version": "rc1", "prompt_version": "r1", "finished_at_utc": ts},
        # Q12: 12_c2 has prev_attempt_id="12_c2_bad" which is invalid_output
        {"type": "forecast_attempt", "attempt_id": "12_c1", "question_id": 12, "question_snapshot_id": "s12",
         "player": "claude", "tier": 1, "validation_status": "valid", "probability": 0.6,
         "run_config_version": "rc1", "prompt_version": "p1", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "12_c2_bad", "question_id": 12, "question_snapshot_id": "s12",
         "player": "claude", "tier": 2, "validation_status": "invalid_output", "probability": None,
         "run_config_version": "rc1", "prompt_version": "r1", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "12_c2", "question_id": 12, "question_snapshot_id": "s12",
         "player": "claude", "tier": 2, "validation_status": "valid", "probability": 0.75,
         "input_attempt_ids": ["12_c1"], "prev_attempt_id": "12_c2_bad",
         "run_config_version": "rc1", "prompt_version": "r1", "finished_at_utc": ts},
        # Q13: Escalated question with valid Tier 1 baseline, failed Tier 2 attempt, unsubmitted
        {"type": "forecast_attempt", "attempt_id": "13_c1", "question_id": 13, "question_snapshot_id": "s13",
         "player": "claude", "tier": 1, "validation_status": "valid", "probability": 0.7,
         "run_config_version": "rc1", "prompt_version": "p1", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "13_c2_fail", "question_id": 13, "question_snapshot_id": "s13",
         "player": "claude", "tier": 2, "validation_status": "invalid_output", "probability": None,
         "input_attempt_ids": ["13_c1"],
         "run_config_version": "rc1", "prompt_version": "r1", "finished_at_utc": ts},
    ])
    _write_jsonl(data_dir / "aggregates.jsonl", [
        # Q10 duplicate identical tier1 aggregates
        {"type": "aggregate", "record_id": "agg10_t1_a", "question_id": 10, "question_snapshot_id": "s10",
         "stage": "tier1", "status": "ready", "option_probabilities": {"A": 0.6, "B": 0.4},
         "aggregation_version": "agg_v1", "input_attempt_ids": ["10_c1"],
         "escalation": {"escalate": True}, "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg10_t1_b", "question_id": 10, "question_snapshot_id": "s10",
         "stage": "tier1", "status": "ready", "option_probabilities": {"A": 0.6, "B": 0.4},
         "aggregation_version": "agg_v1", "input_attempt_ids": ["10_c1"],
         "escalation": {"escalate": True}, "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg10_fin", "question_id": 10, "question_snapshot_id": "s10",
         "stage": "final", "status": "ready", "option_probabilities": {"A": 0.8, "B": 0.2},
         "aggregation_version": "agg_v1", "input_attempt_ids": ["10_c2"], "created_at_utc": ts},
        # Q11 conflicting tier1 aggregates
        {"type": "aggregate", "record_id": "agg11_t1_a", "question_id": 11, "question_snapshot_id": "s11",
         "stage": "tier1", "status": "ready", "option_probabilities": {"A": 0.6, "B": 0.4},
         "aggregation_version": "agg_v1", "input_attempt_ids": ["11_c1"],
         "escalation": {"escalate": True}, "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg11_t1_b", "question_id": 11, "question_snapshot_id": "s11",
         "stage": "tier1", "status": "ready", "option_probabilities": {"A": 0.5, "B": 0.5},
         "aggregation_version": "agg_v1", "input_attempt_ids": ["11_c1"],
         "escalation": {"escalate": True}, "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg11_fin", "question_id": 11, "question_snapshot_id": "s11",
         "stage": "final", "status": "ready", "option_probabilities": {"A": 0.8, "B": 0.2},
         "aggregation_version": "agg_v1", "input_attempt_ids": ["11_c2"], "created_at_utc": ts},
        # Q12
        {"type": "aggregate", "record_id": "agg12_t1", "question_id": 12, "question_snapshot_id": "s12",
         "stage": "tier1", "status": "ready", "probability": 0.6,
         "aggregation_version": "agg_v1", "input_attempt_ids": ["12_c1"],
         "escalation": {"escalate": True}, "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg12_fin", "question_id": 12, "question_snapshot_id": "s12",
         "stage": "final", "status": "ready", "probability": 0.75,
         "aggregation_version": "agg_v1", "input_attempt_ids": ["12_c2"], "created_at_utc": ts},
        # Q13 (tier1 aggregate only; Tier 2 failed so never submitted)
        {"type": "aggregate", "record_id": "agg13_t1", "question_id": 13, "question_snapshot_id": "s13",
         "stage": "tier1", "status": "ready", "probability": 0.7,
         "aggregation_version": "agg_v1", "input_attempt_ids": ["13_c1"],
         "escalation": {"escalate": True}, "created_at_utc": ts},
    ])
    _write_jsonl(data_dir / "submissions.jsonl", [
        {"question_id": 10, "question_snapshot_id": "s10", "intent_id": "i10", "aggregate_id": "agg10_fin",
         "status": "comment_ok", "at": ts},
        {"question_id": 11, "question_snapshot_id": "s11", "intent_id": "i11", "aggregate_id": "agg11_fin",
         "status": "comment_ok", "at": ts},
        {"question_id": 12, "question_snapshot_id": "s12", "intent_id": "i12", "aggregate_id": "agg12_fin",
         "status": "comment_ok", "at": ts},
    ])
    _write_jsonl(data_dir / "resolutions.jsonl", [
        {"type": "resolution_event", "resolution_event_id": "r10", "question_id": 10, "snapshot_id": "s10",
         "tournament_id": 33135, "observed_at_utc": "2026-11-06T12:00:00Z", "resolution_status": "resolved",
         "canonical_outcome": "A",
         "official_ranking_data": {"leaderboard_rank": 9, "ranking_observed_at_utc": "2026-11-06T10:00:00Z"}},
        {"type": "resolution_event", "resolution_event_id": "r11", "question_id": 11, "snapshot_id": "s11",
         "tournament_id": 33135, "observed_at_utc": "2026-11-06T12:00:00Z", "resolution_status": "resolved",
         "canonical_outcome": "A",
         "official_ranking_data": {"leaderboard_rank": 2, "ranking_observed_at_utc": "2026-11-06T12:00:00Z"}},
        {"type": "resolution_event", "resolution_event_id": "r12", "question_id": 12, "snapshot_id": "s12",
         "tournament_id": 33135, "observed_at_utc": "2026-11-06T12:00:00Z", "resolution_status": "resolved",
         "canonical_outcome": 1},
        {"type": "resolution_event", "resolution_event_id": "r13", "question_id": 13, "snapshot_id": "s13",
         "tournament_id": 99999, "observed_at_utc": "2026-11-06T12:00:00Z", "resolution_status": "resolved",
         "canonical_outcome": 1,
         "official_ranking_data": {"leaderboard_rank": 1, "ranking_observed_at_utc": "2026-11-06T11:00:00Z"}},
    ])
    # Future, conflicting-timestamp, malformed-timestamp, and undated audit_impact / subagent records
    # must all be rejected by _record_before_as_of even when analysis_at is omitted!
    _write_jsonl(data_dir / "audit_impacts.jsonl", [
        # 1) Single future timestamp (> as_of)
        {"type": "audit_impact", "attempt_id": "10_c1", "flag": "leak_confirmed", "created_at_utc": ts_late},
        # 2) Conflicting timestamps: audited_at <= as_of, but recorded_at_utc > as_of -> MUST be rejected!
        {"type": "audit_impact", "attempt_id": "10_c1", "flag": "leak_confirmed",
         "audited_at": ts, "recorded_at_utc": ts_late},
        # 3) Malformed timestamp string -> MUST be rejected and counted in audits_undated_or_malformed!
        {"type": "audit_impact", "attempt_id": "10_c1", "flag": "leak_confirmed",
         "audited_at": ts, "created_at_utc": "not-an-iso-timestamp"},
        # 4) Undated record -> MUST be rejected and counted in audits_undated_or_malformed!
        {"type": "audit_impact", "attempt_id": "10_c1", "flag": "leak_confirmed"},
    ])
    _write_jsonl(data_dir / "subagents.jsonl", [
        # 1) Future subagent with taint flag
        {"type": "subagent_invocation", "subagent_id": "sub_late", "parent_attempt_id": "10_c2",
         "result_delivered": True, "audit_flags": ["leak_confirmed"], "finished_at_utc": ts_late},
        # 2) Conflicting timestamps on subagent: started_at_utc <= as_of, finished_at_utc > as_of -> MUST be rejected!
        {"type": "subagent_invocation", "subagent_id": "sub_conflict", "parent_attempt_id": "10_c2",
         "result_delivered": True, "audit_flags": ["leak_confirmed"],
         "started_at_utc": ts, "finished_at_utc": ts_late},
        # 3) Malformed timestamp on subagent -> MUST be rejected!
        {"type": "subagent_invocation", "subagent_id": "sub_malformed", "parent_attempt_id": "10_c2",
         "result_delivered": True, "audit_flags": ["leak_confirmed"],
         "started_at_utc": "bad-ts"},
        # 4) Undated subagent -> MUST be rejected!
        {"type": "subagent_invocation", "subagent_id": "sub_undated", "parent_attempt_id": "10_c2",
         "result_delivered": True, "audit_flags": ["leak_confirmed"]},
    ])
    _write_jsonl(data_dir / "cycles.jsonl", [
        {"type": "cycle_summary", "cycle_id": "cyc1", "finished_at_utc": ts,
         "tier2_reconciliation": {"skipped_budget": 1, "questions_skipped_budget": 1, "question_ids": [10, 13]}},
    ])

    board = SC.evaluate_scoreboard(data_dir, as_of="2026-11-06T13:00:00Z", n_boot=20)
    assert board["rejected_cutoff_counts"]["audits"] == 4
    assert board["rejected_cutoff_counts"]["audits_undated_or_malformed"] == 2
    assert board["rejected_cutoff_counts"]["subagents"] == 4
    assert board["rejected_cutoff_counts"]["subagents_undated_or_malformed"] == 2

    q10 = next(q for q in board["questions"] if q["question_id"] == 10)
    assert q10["scores"]["tier1_aggregate"]["log_score"] == pytest.approx(math.log(0.6))
    assert q10["scores"]["final_aggregate"]["log_score"] == pytest.approx(math.log(0.8))

    q11 = next(q for q in board["questions"] if q["question_id"] == 11)
    assert q11["scores"]["tier1_aggregate"] is None
    assert q11["scoring_errors"]["tier1_aggregate"] == "ambiguous_tier1_aggregate_candidates"

    q12 = next(q for q in board["questions"] if q["question_id"] == 12)
    assert q12["escalated"] is True
    assert q12["scores"]["final_aggregate"] is None
    assert q12["scoring_errors"]["final_aggregate"] == "invalid_or_unusable_prev_attempt:12_c2_bad"

    # B4: rankings embedded in resolution events are never read (see test_ranking_observation_* for the new path)
    assert board["official_scores"]["official_rankings_by_tournament"] == {}

    # Segment-level D52 accounting:
    # - Q10: observed pair (gain = log(0.8) - log(0.6))
    # - Q11: submitted with ambiguous tier1_aggregate -> missing outcome
    # - Q12: submitted with tainted final_aggregate -> missing outcome (MUST NOT be imputed 0.0!)
    # - Q13: unsubmitted escalated question -> permanent ITT gap (B1): never imputed 0.0
    seg_d52 = board["by_config_segment"]["rc1|agg_v1|p1|r1"]["tier2_escalation_analysis"]
    assert seg_d52["escalated_questions_total"] == 4
    assert seg_d52["paired_count"] == 1  # Only Q10 has both valid final_aggregate and valid tier1_aggregate
    assert seg_d52["itt_evaluatable_count"] == 1  # Q10 only
    assert seg_d52["itt_fallback_zero_count"] == 0
    assert seg_d52["missing_outcome_count"] == 3  # Q11, Q12 and unsubmitted Q13
    assert seg_d52["missing_outcome_question_ids"] == [11, 12, 13]
    assert seg_d52["missing_outcome_reasons"]["13"] == "unsubmitted_escalation_no_outcome_source"
    assert seg_d52["observed_total_log_score_gain_nats"] == pytest.approx(math.log(0.8) - math.log(0.6))
    # B1-R: with ITT gaps, total gain and efficiency are withheld; only the labeled subset sum is shown
    assert seg_d52["total_log_score_gain_nats"] is None
    assert seg_d52["evaluatable_subset_log_score_gain_nats"] == pytest.approx(math.log(0.8) - math.log(0.6))
    assert seg_d52["tier2_efficiency_status"] == "incomplete_itt"
    assert seg_d52["itt_tier2_attempts"] == 1
    assert seg_d52["log_score_gain_per_tier2_attempt"] is None
    assert seg_d52["observed_pair_log_score_diff"]["mean"] == pytest.approx(math.log(0.8) - math.log(0.6))
    assert seg_d52["intent_to_treat_log_score_diff"]["mean"] == pytest.approx(math.log(0.8) - math.log(0.6))
    itt_cd = seg_d52["intent_to_treat_log_score_diff"]["conclusion_discipline"]
    assert itt_cd["superiority_claim"] == "none_incomplete_itt_outcomes"
    assert itt_cd["itt_incomplete_reasons"]["escalated_unsubmitted"] == 1
    assert seg_d52["intent_to_treat_log_score_diff"]["complete_itt_coverage"] is False
    # Synthetic attempts carry no budget_cost: never defaulted to 1 (PLAN §4.5)
    assert seg_d52["log_score_gain_per_budget_unit"] is None
    assert seg_d52["itt_tier2_budget_cost_missing_count"] == 1
    assert seg_d52["missed_question_opportunity_cost"]["budget_skipped_questions_in_cycles"] == 1


def test_round4_regressions_dag_lineage_itt_baseline_segment_d52_and_cycle_skips(tmp_path: Path):
    data_dir = tmp_path / "data"
    ts = "2026-11-03T08:00:00Z"
    ts_mid = "2026-11-06T12:30:00Z"

    _write_jsonl(data_dir / "questions.jsonl", [
        # Q20:
        # 1) Claude Tier 2 has prev_attempt_id="20_c1" AND input_attempt_ids=["20_c1", "20_g1"] (shared DAG node)
        # 2) Gemini Tier 1 ("20_g1") has valid retry history prev_attempt_id="20_g1_retry0" (same player gemini)
        # 3) A second escalation run on Q20 shares run_config_version="rc1" and prompt_version="p1"
        #    but uses reconciliation prompt "r2" ("rc1|agg_v1|p1|r2") -> must NOT merge into "r1+r2"!
        # 4) A third escalation run on Q20 uses "rc_v99|agg_v1|p_v99|r_v99"
        {"type": "question_snapshot", "snapshot_id": "s20", "question_id": 20, "post_id": 20,
         "tournament_id": 33135, "question_type": "binary", "status": "resolved", "seen_at": ts},
        # Q21: Unsubmitted escalated question whose Tier 1 baseline input ("21_c1") has a broken prev_attempt_id
        # -> must be rejected from ITT fallback (missing_valid_tier1_baseline, NOT imputed 0.0!)
        {"type": "question_snapshot", "snapshot_id": "s21", "question_id": 21, "post_id": 21,
         "tournament_id": 33135, "question_type": "binary", "status": "resolved", "seen_at": ts},
    ])
    _write_jsonl(data_dir / "forecasts.jsonl", [
        {"type": "forecast_attempt", "attempt_id": "20_c1", "question_id": 20, "question_snapshot_id": "s20",
         "player": "claude", "tier": 1, "validation_status": "valid", "probability": 0.60,
         "run_config_version": "rc1", "prompt_version": "p1", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "20_g1_retry0", "question_id": 20, "question_snapshot_id": "s20",
         "player": "gemini", "tier": 1, "validation_status": "valid", "probability": 0.55,
         "run_config_version": "rc1", "prompt_version": "p1", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "20_g1", "question_id": 20, "question_snapshot_id": "s20",
         "player": "gemini", "tier": 1, "validation_status": "valid", "probability": 0.64,
         "prev_attempt_id": "20_g1_retry0",
         "run_config_version": "rc1", "prompt_version": "p1", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "20_c2", "question_id": 20, "question_snapshot_id": "s20",
         "player": "claude", "tier": 2, "validation_status": "valid", "probability": 0.80,
         "prev_attempt_id": "20_c1", "input_attempt_ids": ["20_c1", "20_g1"],
         "budget_cost": 0.0,
         "run_config_version": "rc1", "prompt_version": "r1", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "20_g2", "question_id": 20, "question_snapshot_id": "s20",
         "player": "gemini", "tier": 2, "validation_status": "valid", "probability": 0.84,
         "prev_attempt_id": "20_g1", "input_attempt_ids": ["20_c1", "20_g1"],
         "budget_cost": 1.0,
         "run_config_version": "rc1", "prompt_version": "r1", "finished_at_utc": ts},
        # Second escalation run on Q20 with SAME run_config_version="rc1" and tier1 prompt "p1",
        # but different reconciliation prompt "r2" ("rc1|agg_v1|p1|r2"):
        # MUST NOT merge into "r1+r2" or duplicate attempts across r1 and r2!
        {"type": "forecast_attempt", "attempt_id": "20_c1_r2", "question_id": 20, "question_snapshot_id": "s20",
         "player": "claude", "tier": 1, "validation_status": "valid", "probability": 0.61,
         "run_config_version": "rc1", "prompt_version": "p1", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "20_c2_r2", "question_id": 20, "question_snapshot_id": "s20",
         "player": "claude", "tier": 2, "validation_status": "invalid_output", "probability": None,
         "input_attempt_ids": ["20_c1_r2"], "budget_cost": 0.5,
         "run_config_version": "rc1", "prompt_version": "r2", "finished_at_utc": ts},
        # Third escalation run on Q20 under configuration B ("rc_v99|agg_v1|p_v99|r_v99"):
        # must be tracked in its own segment B without contaminating configuration A ("rc1|agg_v1|p1|r1")!
        {"type": "forecast_attempt", "attempt_id": "20_c1_v99", "question_id": 20, "question_snapshot_id": "s20",
         "player": "claude", "tier": 1, "validation_status": "valid", "probability": 0.58,
         "run_config_version": "rc_v99", "prompt_version": "p_v99", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "20_c2_v99", "question_id": 20, "question_snapshot_id": "s20",
         "player": "claude", "tier": 2, "validation_status": "invalid_output", "probability": None,
         "input_attempt_ids": ["20_c1_v99"], "budget_cost": 1.5,
         "run_config_version": "rc_v99", "prompt_version": "r_v99", "finished_at_utc": ts},
        # Q21 attempts: 21_c1 references missing prev_attempt_id
        {"type": "forecast_attempt", "attempt_id": "21_c1", "question_id": 21, "question_snapshot_id": "s21",
         "player": "claude", "tier": 1, "validation_status": "valid", "probability": 0.70,
         "prev_attempt_id": "21_c1_missing_prev",
         "run_config_version": "rc1", "prompt_version": "p1", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "21_c2_fail", "question_id": 21, "question_snapshot_id": "s21",
         "player": "claude", "tier": 2, "validation_status": "invalid_output", "probability": None,
         "run_config_version": "rc1", "prompt_version": "r1", "finished_at_utc": ts},
    ])
    _write_jsonl(data_dir / "aggregates.jsonl", [
        {"type": "aggregate", "record_id": "agg20_t1", "question_id": 20, "question_snapshot_id": "s20",
         "stage": "tier1", "status": "ready", "probability": 0.62, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["20_c1", "20_g1"], "escalation": {"escalate": True}, "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg20_fin", "question_id": 20, "question_snapshot_id": "s20",
         "stage": "final", "status": "ready", "probability": 0.82, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["20_c2", "20_g2"], "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg20_t1_r2", "question_id": 20, "question_snapshot_id": "s20",
         "stage": "tier1", "status": "ready", "probability": 0.61, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["20_c1_r2"], "escalation": {"escalate": True, "run_config_version": "rc1"},
         "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg20_t1_v99", "question_id": 20, "question_snapshot_id": "s20",
         "stage": "tier1", "status": "ready", "probability": 0.58, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["20_c1_v99"], "escalation": {"escalate": True, "run_config_version": "rc_v99"},
         "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg21_t1_bad", "question_id": 21, "question_snapshot_id": "s21",
         "stage": "tier1", "status": "ready", "probability": 0.70, "aggregation_version": "agg_v1",
         "input_attempt_ids": ["21_c1"], "escalation": {"escalate": True}, "created_at_utc": ts},
    ])
    _write_jsonl(data_dir / "submissions.jsonl", [
        {"question_id": 20, "question_snapshot_id": "s20", "intent_id": "i20", "aggregate_id": "agg20_fin",
         "status": "comment_ok", "at": ts},
    ])
    # Pre-review draft shape (ranking embedded in resolution events); score_v1 must ignore it (B4).
    q21_resolver_ranking = {"rank": 1, "total_participants": 40, "question_rank": 1,
                            "ranking_source": "question.my_forecasts.score_data",
                            "ranking_observed_at_utc": "2026-11-06T12:00:00Z"}

    _write_jsonl(data_dir / "resolutions.jsonl", [
        # Equal-timestamp tie-break test: r20_v2 appears BEFORE r20_v1 in file order,
        # but has higher resolution_event_id ("r20_v2" > "r20_v1") at equal ranking_observed_at_utc="10:00:00Z"
        # -> r20_v2 (rank 4) MUST win over r20_v1 (rank 7) per (ranking_observed_at_utc, resolution_event_id, qid)!
        {"type": "resolution_event", "resolution_event_id": "r20_v2", "question_id": 20, "snapshot_id": "s20",
         "tournament_id": 33135, "observed_at_utc": "2026-11-06T10:00:00Z", "resolution_status": "resolved",
         "canonical_outcome": 1,
         "official_ranking_data": {"leaderboard_rank": 4, "tournament_rank": 4, "total_participants": 50,
                                   "ranking_source": "tournament_leaderboard",
                                   "ranking_observed_at_utc": "2026-11-06T10:00:00Z"}},
        {"type": "resolution_event", "resolution_event_id": "r20_v1", "question_id": 20, "snapshot_id": "s20",
         "tournament_id": 33135, "observed_at_utc": "2026-11-06T10:00:00Z", "resolution_status": "resolved",
         "canonical_outcome": 1,
         "official_ranking_data": {"leaderboard_rank": 7, "tournament_rank": 7, "total_participants": 50,
                                   "ranking_source": "tournament_leaderboard",
                                   "ranking_observed_at_utc": "2026-11-06T10:00:00Z"}},
        # Superseding Q20 resolution event at 11:00Z where ranking fetch failed (empty official_ranking_data)
        {"type": "resolution_event", "resolution_event_id": "r20", "question_id": 20, "snapshot_id": "s20",
         "tournament_id": 33135, "observed_at_utc": "2026-11-06T11:00:00Z", "resolution_status": "resolved",
         "canonical_outcome": 1, "official_ranking_data": {}},
        # Later Q21 resolution event at 12:00Z with resolver's actual question-only ranking output
        # (including unprefixed total_participants=40 + question_total_participants=40):
        # MUST NOT overwrite tournament 33135 leaderboard_rank=4 / total_participants=50!
        {"type": "resolution_event", "resolution_event_id": "r21", "question_id": 21, "snapshot_id": "s21",
         "tournament_id": 33135, "observed_at_utc": "2026-11-06T12:00:00Z", "resolution_status": "resolved",
         "canonical_outcome": 1,
         "official_ranking_data": q21_resolver_ranking},
    ])
    # Audit created at 12:30Z (after as_of=12:00Z, before analysis_at=13:00Z)
    _write_jsonl(data_dir / "audit_impacts.jsonl", [
        {"type": "audit_impact", "attempt_id": "20_g2", "flag": "leak_confirmed", "created_at_utc": ts_mid},
    ])
    # Cycles testing explicit configuration skip attribution & ambiguous skip preservation:
    # 1) cyc_r1: tier2_reconciliation with prompt_version="r1", skipped_budget=2, question_ids=[20, 21] -> attributed to rc1|agg_v1|p1|r1
    # 2) cyc_v99: explicit run_config_version="rc_v99" with questions_skipped_budget=[20] -> attributed to rc_v99|agg_v1|p_v99|r_v99 (NOT Q20's submitted rc1 segment!)
    # 3) cyc_ambig: run_config_version="rc1" (no r1/r2 prompt_version) with questions_skipped_budget=[20] -> ambiguous between r1 and r2 -> unattributed_cycle_budget_skips=1
    _write_jsonl(data_dir / "cycles.jsonl", [
        {"type": "cycle_summary", "cycle_id": "cyc_r1", "finished_at_utc": ts,
         "skipped_budget": 0,
         "tier2_reconciliation": {"prompt_version": "r1", "skipped_budget": 2, "question_ids": [20, 21]}},
        {"type": "cycle_summary", "cycle_id": "cyc_v99", "finished_at_utc": ts,
         "run_config_version": "rc_v99", "questions_skipped_budget": [20]},
        {"type": "cycle_summary", "cycle_id": "cyc_ambig", "finished_at_utc": ts,
         "run_config_version": "rc1", "questions_skipped_budget": [20]},
    ])

    # 1) When analysis_at is omitted (defaults to as_of=12:00Z), the 12:30Z audit is filtered out,
    #    and Q20's shared DAG node + Gemini retry lineage succeed!
    b_as_of = SC.evaluate_scoreboard(data_dir, as_of="2026-11-06T12:00:00Z", n_boot=20)
    q20 = next(q for q in b_as_of["questions"] if q["question_id"] == 20)
    assert q20["scores"]["final_aggregate"]["log_score"] == pytest.approx(math.log(0.82))
    assert q20["scores"]["tier1_aggregate"]["log_score"] == pytest.approx(math.log(0.62))
    assert q20["scores"]["player_tier1:gemini"]["log_score"] == pytest.approx(math.log(0.64))

    # B4: embedded legacy rankings are ignored entirely; no question-level ranking output exists
    assert b_as_of["official_scores"]["official_rankings_by_tournament"] == {}
    assert "official_question_rankings" not in b_as_of["official_scores"]

    # All three configuration segments exist separately (no synthetic "r1+r2" segment!)
    assert sorted(b_as_of["by_config_segment"].keys()) == [
        "rc1|agg_v1|p1|r1",
        "rc1|agg_v1|p1|r2",
        "rc_v99|agg_v1|p_v99|r_v99",
    ]

    # B5 strict provenance: 21_c2_fail has no Tier 1 parent link, so it is never attributed to a segment
    # and Q21's escalation episode (reconcile version unknown) is unsegmentable instead of guessed into A.
    assert b_as_of["overall"]["unsegmentable_question_ids"] == [21]

    seg_d52 = b_as_of["by_config_segment"]["rc1|agg_v1|p1|r1"]["tier2_escalation_analysis"]
    # Segment A ("rc1|agg_v1|p1|r1"): only Q20's provenance-linked 20_c2, 20_g2; budget = 0.0 + 1.0
    assert seg_d52["escalated_questions_total"] == 1
    assert seg_d52["escalated_submitted_count"] == 1
    assert seg_d52["escalated_unsubmitted_count"] == 0
    assert seg_d52["extra_tier2_attempts_used"] == 2
    assert seg_d52["tier2_failed_or_tainted_attempts"] == 0
    assert seg_d52["tier2_budget_units_consumed"] == pytest.approx(1.0)
    assert seg_d52["itt_tier2_attempts"] == 2
    assert seg_d52["itt_tier2_budget_units"] == pytest.approx(1.0)
    assert seg_d52["missed_question_opportunity_cost"]["budget_skipped_questions_in_cycles"] == 2
    assert seg_d52["missed_question_opportunity_cost"]["unattributed_cycle_budget_skips"] == 1
    assert seg_d52["missed_question_opportunity_cost"]["out_of_segment_tier2_attempts"] == 2  # 20_c2_r2 and 20_c2_v99
    assert seg_d52["missed_question_opportunity_cost"]["unattributed_tier2_attempts"] == 0
    assert seg_d52["itt_fallback_zero_count"] == 0
    assert seg_d52["missing_outcome_count"] == 0
    assert seg_d52["intent_to_treat_log_score_diff"]["complete_itt_coverage"] is True

    # Segment R2 ("rc1|agg_v1|p1|r2") preserves Q20's same-rc1, different-reconcile-prompt run separately!
    # Its Tier 2 run produced no submission -> permanent ITT gap (B1), never a 0.0 imputation.
    seg_r2_d52 = b_as_of["by_config_segment"]["rc1|agg_v1|p1|r2"]["tier2_escalation_analysis"]
    assert seg_r2_d52["escalated_questions_total"] == 1
    assert seg_r2_d52["escalated_submitted_count"] == 0
    assert seg_r2_d52["escalated_unsubmitted_count"] == 1
    assert seg_r2_d52["extra_tier2_attempts_used"] == 1
    assert seg_r2_d52["tier2_budget_units_consumed"] == pytest.approx(0.5)
    assert seg_r2_d52["itt_evaluatable_count"] == 0
    assert seg_r2_d52["itt_fallback_zero_count"] == 0
    assert seg_r2_d52["missing_outcome_reasons"] == {"20": "unsubmitted_escalation_no_outcome_source"}
    assert seg_r2_d52["intent_to_treat_log_score_diff"] is None
    assert seg_r2_d52["missed_question_opportunity_cost"]["budget_skipped_questions_in_cycles"] == 0

    # Segment B ("rc_v99|agg_v1|p_v99|r_v99") preserves Q20's third escalation run and gets cyc_v99's explicit skip!
    seg_b = b_as_of["by_config_segment"]["rc_v99|agg_v1|p_v99|r_v99"]
    assert seg_b["resolved_count"] == 0
    seg_b_d52 = seg_b["tier2_escalation_analysis"]
    assert seg_b_d52["escalated_questions_total"] == 1
    assert seg_b_d52["escalated_submitted_count"] == 0
    assert seg_b_d52["escalated_unsubmitted_count"] == 1
    assert seg_b_d52["extra_tier2_attempts_used"] == 1
    assert seg_b_d52["tier2_failed_or_tainted_attempts"] == 1
    assert seg_b_d52["tier2_budget_units_consumed"] == pytest.approx(1.5)
    assert seg_b_d52["itt_evaluatable_count"] == 0
    assert seg_b_d52["itt_fallback_zero_count"] == 0
    assert seg_b_d52["missing_outcome_count"] == 1
    assert seg_b_d52["missed_question_opportunity_cost"]["budget_skipped_questions_in_cycles"] == 1

    # Overall D52 accounting across segments prohibits pooling while preserving combined attempt totals;
    # 21_c2_fail carries no budget_cost, so the combined budget total is incomplete (None), never defaulted.
    overall_d52 = b_as_of["overall"]["tier2_escalation_analysis"]
    assert overall_d52["pooling_prohibited"] is True
    assert overall_d52["escalated_questions_total"] == 2
    assert overall_d52["extra_tier2_attempts_used"] == 5
    assert overall_d52["tier2_failed_or_tainted_attempts"] == 3
    assert overall_d52["tier2_budget_units_consumed"] is None
    assert overall_d52["tier2_budget_cost_missing_count"] == 1
    assert overall_d52["itt_evaluatable_count"] == 1
    assert overall_d52["itt_fallback_zero_count"] == 0
    assert overall_d52["missing_outcome_count"] == 3
    assert overall_d52["missing_outcome_reasons"]["21"] == "unsubmitted_escalation_no_outcome_source"
    assert overall_d52["missed_question_opportunity_cost"]["budget_skipped_questions_in_cycles"] == 4
    assert overall_d52["missed_question_opportunity_cost"]["unattributed_cycle_budget_skips"] == 1

    # 2) Positive control: when analysis_at=13:00Z (> 12:30Z), the 12:30Z audit IS included and taints 20_g2 -> disqualifies agg20_fin,
    #    AND Q20's submitted segment identity remains strictly "rc1|agg_v1|p1|r1" (never merged with r2 or rc_v99)!
    b_later_analysis = SC.evaluate_scoreboard(
        data_dir, as_of="2026-11-06T12:00:00Z", analysis_at="2026-11-06T13:00:00Z", n_boot=20
    )
    q20_tainted = next(q for q in b_later_analysis["questions"] if q["question_id"] == 20)
    assert q20_tainted["scores"]["final_aggregate"] is None
    assert q20_tainted["scoring_errors"]["final_aggregate"] == "tainted_submitted_aggregate"
    assert q20_tainted["config_segment"] == "rc1|agg_v1|p1|r1"
    assert sorted(b_later_analysis["by_config_segment"].keys()) == [
        "rc1|agg_v1|p1|r1",
        "rc1|agg_v1|p1|r2",
        "rc_v99|agg_v1|p_v99|r_v99",
    ]
    assert b_later_analysis["by_config_segment"]["rc1|agg_v1|p1|r1"]["tier2_escalation_analysis"]["extra_tier2_attempts_used"] == 2
    assert b_later_analysis["by_config_segment"]["rc1|agg_v1|p1|r2"]["tier2_escalation_analysis"]["extra_tier2_attempts_used"] == 1
    assert b_later_analysis["by_config_segment"]["rc_v99|agg_v1|p_v99|r_v99"]["tier2_escalation_analysis"]["extra_tier2_attempts_used"] == 1

    # 3) Reject analysis_at < as_of
    with pytest.raises(ValueError, match="analysis_at .* cannot be earlier than as_of"):
        SC.evaluate_scoreboard(data_dir, as_of="2026-11-06T12:00:00Z", analysis_at="2026-11-06T11:00:00Z")



def test_conclusion_discipline_and_pairwise_ci_enforcement():
    assert SC.conclusion_discipline(0)["tier"] == "descriptive_only"
    assert SC.conclusion_discipline(29)["tier"] == "descriptive_only"
    assert SC.conclusion_discipline(30)["tier"] == "preliminary_with_ci"
    assert SC.conclusion_discipline(99)["tier"] == "preliminary_with_ci"
    assert SC.conclusion_discipline(100)["tier"] == "effect_size_with_ci"
    assert SC.conclusion_discipline(100)["superiority_assessment_eligible"] is True
    # Sample count alone never claims superiority without checking paired CI!
    assert SC.conclusion_discipline(100)["can_claim_superiority"] is False

    # Missing cluster CI must fail closed (never substitute question CI)
    d_no_cluster = SC.pairwise_conclusion_discipline(120, [0.02, 0.08], None, higher_is_better=True)
    assert d_no_cluster["can_claim_superiority"] is False
    assert d_no_cluster["superiority_claim"] == "none_missing_ci"

    # 100 paired samples with identical forecasts -> CI [0, 0] covers 0 -> cannot claim superiority
    d_zero = SC.pairwise_conclusion_discipline(100, [0.0, 0.0], [0.0, 0.0], higher_is_better=True)
    assert d_zero["superiority_assessment_eligible"] is True
    assert d_zero["can_claim_superiority"] is False
    assert d_zero["superiority_claim"] == "inconclusive_ci_covers_zero"

    # < 100 paired samples even with non-zero CI -> cannot claim superiority
    d_small = SC.pairwise_conclusion_discipline(45, [0.05, 0.15], [0.04, 0.16], higher_is_better=True)
    assert d_small["can_claim_superiority"] is False
    assert d_small["superiority_claim"] == "none_insufficient_sample"

    # >= 100 paired samples: log_score (higher_is_better=True) vs brier_score (higher_is_better=False)
    d_log_win = SC.pairwise_conclusion_discipline(120, [0.02, 0.08], [0.01, 0.09], higher_is_better=True)
    assert d_log_win["can_claim_superiority"] is True
    assert d_log_win["superiority_claim"] == "final_aggregate_better"

    d_brier_win = SC.pairwise_conclusion_discipline(120, [-0.05, -0.01], [-0.06, -0.005], higher_is_better=False)
    assert d_brier_win["can_claim_superiority"] is True
    assert d_brier_win["superiority_claim"] == "final_aggregate_better"

    d_brier_loss = SC.pairwise_conclusion_discipline(120, [0.01, 0.05], [0.005, 0.06], higher_is_better=False)
    assert d_brier_loss["can_claim_superiority"] is True
    assert d_brier_loss["superiority_claim"] == "comparator_better"


def test_validate_report_path_blocks_protected_dirs_and_symlinks(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    # Reject non-.md path
    with pytest.raises(ValueError, match="must end with .md"):
        SC.validate_report_path(data_dir / "submissions.jsonl", data_dir=data_dir)

    # Reject .md inside data_dir or repo config/data/src/tests
    with pytest.raises(ValueError, match="protected directory"):
        SC.validate_report_path(data_dir / "report.md", data_dir=data_dir)
    with pytest.raises(ValueError, match="protected directory"):
        SC.validate_report_path(_REPO_ROOT / "config" / "evil.md", data_dir=data_dir)
    with pytest.raises(ValueError, match="protected directory"):
        SC.validate_report_path(_REPO_ROOT / "data" / "evil.md", data_dir=data_dir)
    with pytest.raises(ValueError, match="protected file"):
        SC.validate_report_path(_REPO_ROOT / "PLAN.md", data_dir=data_dir)

    # Reject symlink alias pointing into protected directory
    alias_dir = tmp_path / "reports_alias"
    alias_dir.symlink_to(data_dir)
    with pytest.raises(ValueError, match="protected directory"):
        SC.validate_report_path(alias_dir / "scoreboard.md", data_dir=data_dir)

    # Reject --report-path without --write-report
    with pytest.raises(ValueError, match="--report-path requires --write-report"):
        SC.main(["--data-dir", str(data_dir), "--report-path", str(tmp_path / "reports" / "unused.md")])


def test_dry_run_labeling_and_end_to_end_numeric_discrete_scoreboard(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    ts = "2026-11-03T08:00:00Z"
    ts_later = "2026-11-03T09:00:00Z"

    # Q30: log-scaled numeric question (zero_point = 0.0, range [10.0, 1000.0]) with duplicate identical Tier 1 aggregates
    log_r = math.log((1000.0 - 0.0) / (10.0 - 0.0))
    n_num = 10
    grid_num = [10.0 + 10.0 * math.expm1(log_r * (i / n_num)) for i in range(n_num + 1)]
    snap_num = {
        "type": "question_snapshot",
        "snapshot_id": "s30",
        "question_id": 30,
        "post_id": 30,
        "tournament_id": 33135,
        "question_type": "numeric",
        "range_min": 10.0,
        "range_max": 1000.0,
        "zero_point": 0.0,
        "open_lower_bound": False,
        "open_upper_bound": False,
        "inbound_outcome_count": n_num,
        "unit": "units",
        "continuous_range": grid_num,
        "status": "resolved",
        "seen_at": ts,
    }
    pct_num = {"p05": 15.0, "p10": 25.0, "p25": 50.0, "p50": 100.0, "p75": 250.0, "p90": 500.0, "p95": 750.0}
    cdf_num = N.to_cdf(pct_num, snap_num)
    sha_num = SC.hash_obj(cdf_num)

    # Q31: discrete question (nominal [0.0, 3.0], N=4) submitted via dry_run only
    snap_disc = _numeric_snapshot(qid=31, qtype="discrete", n=4)
    snap_disc.update({
        "snapshot_id": "s31",
        "question_id": 31,
        "post_id": 31,
        "tournament_id": 33135,
        "status": "resolved",
        "seen_at": ts,
    })
    pct_disc = {"p05": 0.1, "p10": 0.3, "p25": 0.8, "p50": 1.5, "p75": 2.2, "p90": 2.7, "p95": 2.9}
    cdf_disc = N.to_cdf(pct_disc, snap_disc)
    sha_disc = SC.hash_obj(cdf_disc)

    _write_jsonl(data_dir / "questions.jsonl", [snap_num, snap_disc])
    _write_jsonl(data_dir / "forecasts.jsonl", [
        {"type": "forecast_attempt", "attempt_id": "30_c1", "question_id": 30, "question_snapshot_id": "s30",
         "player": "claude", "tier": 1, "validation_status": "valid",
         "percentiles": pct_num, "continuous_cdf": cdf_num, "cdf_version": N.CDF_VERSION, "cdf_sha256": sha_num,
         "run_config_version": "rc1", "prompt_version": "p_num", "finished_at_utc": ts},
        {"type": "forecast_attempt", "attempt_id": "31_c1", "question_id": 31, "question_snapshot_id": "s31",
         "player": "claude", "tier": 1, "validation_status": "valid",
         "percentiles": pct_disc, "continuous_cdf": cdf_disc, "cdf_version": N.CDF_VERSION, "cdf_sha256": sha_disc,
         "run_config_version": "rc1", "prompt_version": "p_num", "finished_at_utc": ts},
    ])
    _write_jsonl(data_dir / "aggregates.jsonl", [
        # Duplicate identical numeric tier1 aggregates on Q30 (tests _prediction_signature on list/cdf fields)
        {"type": "aggregate", "record_id": "agg30_t1_a", "question_id": 30, "question_snapshot_id": "s30",
         "stage": "tier1", "status": "ready", "aggregation_version": "agg_v1",
         "continuous_cdf": cdf_num, "cdf_version": N.CDF_VERSION, "cdf_sha256": sha_num,
         "input_attempt_ids": ["30_c1"], "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg30_t1_b", "question_id": 30, "question_snapshot_id": "s30",
         "stage": "tier1", "status": "ready", "aggregation_version": "agg_v1",
         "continuous_cdf": cdf_num, "cdf_version": N.CDF_VERSION, "cdf_sha256": sha_num,
         "input_attempt_ids": ["30_c1"], "created_at_utc": ts},
        {"type": "aggregate", "record_id": "agg30_fin", "question_id": 30, "question_snapshot_id": "s30",
         "stage": "final", "status": "ready", "aggregation_version": "agg_v1",
         "continuous_cdf": cdf_num, "cdf_version": N.CDF_VERSION, "cdf_sha256": sha_num,
         "input_attempt_ids": ["30_c1"], "created_at_utc": ts},
        # Bad dry_run aggregate on Q30 with wrong cdf_sha256 (must NOT supersede live agg30_fin!)
        {"type": "aggregate", "record_id": "agg30_dry_bad", "question_id": 30, "question_snapshot_id": "s30",
         "stage": "final", "status": "ready", "aggregation_version": "agg_v1",
         "continuous_cdf": cdf_num, "cdf_version": N.CDF_VERSION, "cdf_sha256": "0" * 64,
         "input_attempt_ids": ["30_c1"], "created_at_utc": ts_later},
        # Q31 discrete aggregate
        {"type": "aggregate", "record_id": "agg31_fin", "question_id": 31, "question_snapshot_id": "s31",
         "stage": "final", "status": "ready", "aggregation_version": "agg_v1",
         "continuous_cdf": cdf_disc, "cdf_version": N.CDF_VERSION, "cdf_sha256": sha_disc,
         "input_attempt_ids": ["31_c1"], "created_at_utc": ts},
    ])
    _write_jsonl(data_dir / "submissions.jsonl", [
        {"question_id": 30, "question_snapshot_id": "s30", "intent_id": "i30_live", "aggregate_id": "agg30_fin",
         "status": "comment_ok", "at": ts},
        # Later dry_run row on Q30: must NEVER supersede the earlier live comment_ok row!
        {"question_id": 30, "question_snapshot_id": "s30", "intent_id": "i30_dry", "aggregate_id": "agg30_dry_bad",
         "status": "dry_run", "at": ts_later},
        # Q31 only has a dry_run row
        {"question_id": 31, "question_snapshot_id": "s31", "intent_id": "i31_dry", "aggregate_id": "agg31_fin",
         "status": "dry_run", "at": ts},
    ])
    _write_jsonl(data_dir / "resolutions.jsonl", [
        {"type": "resolution_event", "resolution_event_id": "r30", "question_id": 30, "snapshot_id": "s30",
         "observed_at_utc": "2026-11-06T12:00:00Z", "resolution_status": "resolved", "canonical_outcome": 100.0},
        {"type": "resolution_event", "resolution_event_id": "r31", "question_id": 31, "snapshot_id": "s31",
         "observed_at_utc": "2026-11-06T12:00:00Z", "resolution_status": "resolved", "canonical_outcome": 2.0},
    ])

    b_live = SC.evaluate_scoreboard(data_dir, as_of="2026-11-06T13:00:00Z", include_dry_run=False, n_boot=20)
    assert b_live["include_dry_run"] is False
    assert b_live["scorable_resolved_count"] == 1
    assert b_live["questions"][0]["question_id"] == 30
    assert b_live["questions"][0]["submission_status"] == "comment_ok"
    assert b_live["questions"][0]["scores"]["final_aggregate"]["log_score"] is not None

    b_dry = SC.evaluate_scoreboard(data_dir, as_of="2026-11-06T13:00:00Z", include_dry_run=True, n_boot=20)
    assert b_dry["include_dry_run"] is True
    assert b_dry["scorable_resolved_count"] == 2
    q30_dry = next(q for q in b_dry["questions"] if q["question_id"] == 30)
    q31_dry = next(q for q in b_dry["questions"] if q["question_id"] == 31)
    # Q30 kept live comment_ok (agg30_fin) instead of later dry_run (agg30_dry_bad)!
    assert q30_dry["submission_status"] == "comment_ok"
    assert q30_dry["submitted_aggregate_id"] == "agg30_fin"
    assert q30_dry["scores"]["final_aggregate"]["log_score"] is not None
    assert q31_dry["submission_status"] == "dry_run"
    assert q31_dry["scores"]["final_aggregate"]["log_score"] is not None

    md_dry = SC.render_scoreboard_markdown(b_dry)
    assert "WARNING (DRY-RUN INCLUDED)" in md_dry

    # Verify auto-generated report filename collision retry in write_report_exclusive
    monkeypatch.setattr(SC, "DEFAULT_REPORTS_DIR", tmp_path / "auto_reports")
    p1 = SC.write_report_exclusive(None, b_dry, md_dry, data_dir=data_dir)
    p2 = SC.write_report_exclusive(None, b_dry, md_dry, data_dir=data_dir)
    assert p1.exists() and p2.exists() and p1 != p2
    assert "_dryrun_" in p1.name
    assert p2.name.endswith("_2.md")



# --------------------------------------------------------------------------- M4 r2 regressions (B1/B2/B3/B4/B5/C2)
_REAL_EVALUATE = SC.evaluate_scoreboard  # captured before the autouse fixture wraps it
_TS = "2026-11-03T08:00:00Z"
_TS_RES = "2026-11-06T12:00:00Z"
_AS_OF = "2026-11-06T13:00:00Z"
_BOT = 77


def _mini_question(
    qid: int,
    *,
    p_t1: float = 0.6,
    p_t2: Optional[float] = None,
    submitted: bool = True,
    outcome: Any = 1,
    qtype: str = "binary",
    options: Optional[List[str]] = None,
    mc_final: Optional[Dict[str, float]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """One question in the production record shapes (attempt -> tier1 agg -> [tier2] -> final -> intent)."""
    import submit as SUB

    snap = {"type": "question_snapshot", "snapshot_id": f"s{qid}", "question_id": qid, "post_id": 9000 + qid,
            "tournament_id": 33135, "venue": "minibench", "question_type": qtype, "options": options,
            "seen_at": _TS}
    base = {"question_id": qid, "question_snapshot_id": f"s{qid}", "post_id": 9000 + qid,
            "tournament_id": 33135, "question_type": qtype}

    def pred(p: float) -> Dict[str, Any]:
        if qtype == "multiple_choice":
            return {"option_probabilities": dict(mc_final or {}), "options": options}
        return {"probability": p}

    t1 = [{"type": "forecast_attempt", "attempt_id": f"{qid}_{pl}1", **base, "player": pl, "tier": 1,
           "validation_status": "valid", **pred(p_t1), "run_config_version": "rc1", "prompt_version": "p1",
           "finished_at_utc": _TS} for pl in ("claude", "gemini", "gpt")]
    escalated = p_t2 is not None
    t1_agg = {"type": "aggregate", "record_id": f"a{qid}_t1", **base, "stage": "tier1", "status": "ready",
              **pred(p_t1), "aggregation_version": "agg_v1", "input_attempt_ids": [a["attempt_id"] for a in t1],
              "created_at_utc": _TS,
              **({"escalation": {"candidate": True, "admission_status": "pending_budget_and_deadline",
                                 "run_config_version": "rc1"}} if escalated else {})}
    t2 = [{"type": "forecast_attempt", "attempt_id": f"{qid}_{pl}2", **base, "player": pl, "tier": 2,
           "validation_status": "valid", **pred(p_t2), "input_attempt_ids": [a["attempt_id"] for a in t1],
           "run_config_version": "rc1", "prompt_version": "r1", "finished_at_utc": _TS}
          for pl in ("claude", "gemini", "gpt")] if escalated else []
    final_inputs = [a["attempt_id"] for a in (t2 or t1)]
    final = {"type": "aggregate", "record_id": f"a{qid}_fin", **base, "stage": "final", "status": "ready",
             **pred(p_t2 if escalated else p_t1), "aggregation_version": "agg_v1",
             "input_attempt_ids": final_inputs, "created_at_utc": _TS}
    subs: List[Dict[str, Any]] = []
    res: List[Dict[str, Any]] = []
    if submitted:
        subs = [
            {"type": "submission", "status": "intent", "intent_id": f"i{qid}", "aggregate_id": final["record_id"],
             "question_id": qid, "question_snapshot_id": f"s{qid}", "tournament_id": 33135, "bot_id": _BOT,
             "snapshot": snap, "forecast_payload": SUB.forecast_payload(snap, final), "at": _TS},
            {"type": "submission", "status": "forecast_ok", "intent_id": f"i{qid}", "aggregate_id": final["record_id"],
             "question_id": qid, "question_snapshot_id": f"s{qid}", "tournament_id": 33135, "bot_id": _BOT, "at": _TS},
        ]
        res = [{"type": "resolution_event", "resolution_event_id": f"r{qid}", "question_id": qid,
                "observed_at_utc": _TS_RES, "resolution_status": "resolved", "canonical_outcome": outcome}]
    return {"questions.jsonl": [snap], "forecasts.jsonl": t1 + t2,
            "aggregates.jsonl": [t1_agg] + ([final] if (submitted or not escalated) else []),
            "submissions.jsonl": subs, "resolutions.jsonl": res}


def _write_mini(data_dir: Path, *parts: Dict[str, List[Dict[str, Any]]], extra_res: Optional[List[Dict[str, Any]]] = None) -> None:
    for name in ("questions.jsonl", "forecasts.jsonl", "aggregates.jsonl", "submissions.jsonl", "resolutions.jsonl"):
        rows = [r for p in parts for r in p[name]] + (list(extra_res or []) if name == "resolutions.jsonl" else [])
        _write_jsonl(data_dir / name, rows)


def test_b1_unsubmitted_escalation_blocks_itt_superiority_and_budget_cost_is_never_defaulted(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_mini(data_dir, _mini_question(1, p_t1=0.6, p_t2=0.8), _mini_question(2, p_t1=0.6, p_t2=0.7, submitted=False))
    board = _REAL_EVALUATE(data_dir, as_of=_AS_OF, n_boot=50)
    seg = board["by_config_segment"]["rc1|agg_v1|p1|r1"]["tier2_escalation_analysis"]
    assert seg["escalated_questions_total"] == 2
    assert seg["escalated_unsubmitted_count"] == 1
    assert seg["missing_outcome_count"] == 1
    assert seg["missing_outcome_reasons"] == {"2": "unsubmitted_escalation_no_outcome_source"}
    assert seg["not_yet_evaluable_count"] == 0
    assert seg["itt_fallback_zero_count"] == 0
    itt = seg["intent_to_treat_log_score_diff"]
    assert itt["evaluatable_count"] == 1
    assert itt["complete_itt_coverage"] is False
    assert itt["conclusion_discipline"]["can_claim_superiority"] is False
    assert itt["conclusion_discipline"]["itt_incomplete_reasons"] == {"missing_outcome": 1, "escalated_unsubmitted": 1}
    # Six Tier 2 attempts, none with budget_cost / quota_weight: totals are incomplete, never 1.0 each
    assert seg["extra_tier2_attempts_used"] == 6
    assert seg["tier2_budget_units_consumed"] is None
    assert seg["tier2_budget_cost_missing_count"] == 6
    assert seg["log_score_gain_per_budget_unit"] is None
    # B1-R: the unsubmitted episode's Tier 2 cost is real, so a complete-case efficiency would overstate
    # the benefit; it is withheld and the subset sum is labeled as such.
    assert seg["tier2_efficiency_status"] == "incomplete_itt"
    assert seg["log_score_gain_per_tier2_attempt"] is None
    assert seg["total_log_score_gain_nats"] is None
    assert seg["evaluatable_subset_log_score_gain_nats"] == pytest.approx(math.log(0.8) - math.log(0.6))
    assert seg["observed_pair_log_score_diff"]["conclusion_discipline"]["can_claim_superiority"] is False
    assert seg["observed_pair_log_score_diff"]["conclusion_discipline"]["superiority_claim"] == (
        "none_observed_pairs_only_incomplete_itt"
    )
    # Tier 2 effect pairwise comparisons inherit the same gate (N7)
    seg_pw = board["by_config_segment"]["rc1|agg_v1|p1|r1"]["pairwise"]
    for comp in ("final_vs_tier1_aggregate", "final_vs_player_tier2:claude"):
        assert seg_pw[comp]["conclusion_discipline"]["can_claim_superiority"] is False
        assert seg_pw[comp]["log_score_diff"]["conclusion_discipline"]["itt_incomplete_reasons"] == {
            "missing_outcome": 1, "escalated_unsubmitted": 1,
        }
    # Ordinary (non-Tier-2) comparisons are not gated by D52
    assert "itt_incomplete_reasons" not in seg_pw["final_vs_player_tier1:claude"]["conclusion_discipline"]


def test_b2_final_vs_tier1_compares_only_escalated_and_b5_unadopted_escalation_never_flips_row(tmp_path: Path):
    data_dir = tmp_path / "data"
    parts = [_mini_question(q, p_t1=0.6) for q in range(10, 16)]
    parts += [_mini_question(20, p_t1=0.6, p_t2=0.8), _mini_question(21, p_t1=0.6, p_t2=0.3)]
    # Q10 also has an unadopted escalation candidate built from other Tier 1 attempts on the same snapshot
    stray = _mini_question(10, p_t1=0.4, p_t2=0.5, submitted=False)
    for a in stray["forecasts.jsonl"]:
        a["attempt_id"] += "_stray"
        a["input_attempt_ids"] = [x + "_stray" for x in a.get("input_attempt_ids") or []] or None
        if a["input_attempt_ids"] is None:
            a.pop("input_attempt_ids")
    stray_t1 = stray["aggregates.jsonl"][0]
    stray_t1["record_id"] += "_stray"
    stray_t1["input_attempt_ids"] = [x + "_stray" for x in stray_t1["input_attempt_ids"]]
    parts.append({"questions.jsonl": [], "forecasts.jsonl": stray["forecasts.jsonl"],
                  "aggregates.jsonl": [stray_t1], "submissions.jsonl": [], "resolutions.jsonl": []})
    _write_mini(data_dir, *parts)
    board = _REAL_EVALUATE(data_dir, as_of=_AS_OF, n_boot=50)
    q10 = next(q for q in board["questions"] if q["question_id"] == 10)
    assert q10["escalated"] is False  # B5: the stray candidate is not provenance-linked to the submitted series
    seg = board["by_config_segment"]["rc1|agg_v1|p1|r1"]
    pw = seg["pairwise"]["final_vs_tier1_aggregate"]
    assert pw["eligible_questions"] == 2
    assert pw["paired_count"] == 2
    # Mean over the 2 escalated questions only (Q20: 0.8 vs 0.6, Q21: 0.3 vs 0.6), not diluted by 6 zeros
    expected = ((math.log(0.8) - math.log(0.6)) + (math.log(0.3) - math.log(0.6))) / 2.0
    assert pw["log_score_diff"]["mean"] == pytest.approx(expected)
    # Ordinary comparisons still use all eight resolved questions
    assert seg["pairwise"]["final_vs_player_tier1:claude"]["paired_count"] == 8
    assert seg["series"]["player_tier2:claude"]["eligible_questions"] == 2
    d52 = seg["tier2_escalation_analysis"]
    assert d52["tier2_efficiency_status"] == "complete_itt"
    assert d52["total_log_score_gain_nats"] == pytest.approx(expected * 2)
    # ITT charges the stray Q10 escalation's 3 Tier 2 attempts (zero gain on what was actually submitted)
    # on top of Q20/Q21's 6, without flipping Q10's row or its pairwise membership.
    assert d52["itt_tier2_attempts"] == 9
    assert d52["log_score_gain_per_tier2_attempt"] == pytest.approx(expected * 2 / 9.0)
    assert "itt_incomplete_reasons" not in pw["conclusion_discipline"]


def test_c2_scores_the_frozen_submitted_payload_and_rejects_missing_or_mismatched(tmp_path: Path):
    opts = ["a", "b", "c"]
    unrounded = {"a": 0.3333333333, "b": 0.3333333333, "c": 0.3333333334}
    data_dir = tmp_path / "data"
    mc = _mini_question(30, qtype="multiple_choice", options=opts, mc_final=unrounded, outcome="c")
    payload = mc["submissions.jsonl"][0]["forecast_payload"]
    # submit.forecast_payload rounds the first K-1 options to 6 decimals and gives the remainder to the last
    assert payload[0]["probability_yes_per_category"] == {"a": 0.333333, "b": 0.333333, "c": 0.333334}
    tampered = _mini_question(31, p_t1=0.6)
    tampered["submissions.jsonl"][0]["forecast_payload"] = [{"question": 31, "probability_yes": 0.9}]
    missing = _mini_question(32, p_t1=0.6)
    del missing["submissions.jsonl"][0]["forecast_payload"]
    _write_mini(data_dir, mc, tampered, missing)
    board = _REAL_EVALUATE(data_dir, as_of=_AS_OF, n_boot=20)
    by_q = {q["question_id"]: q for q in board["questions"]}
    final_mc = by_q[30]["scores"]["final_aggregate"]["log_score"]
    assert final_mc == pytest.approx(math.log(0.333334), abs=1e-12)
    assert abs(final_mc - math.log(0.3333333334)) > 1e-7
    # Tier 1 aggregate was never sent, so it is scored as recorded
    assert by_q[30]["scores"]["tier1_aggregate"]["log_score"] == pytest.approx(math.log(0.3333333334), abs=1e-12)
    assert by_q[31]["scores"]["final_aggregate"] is None
    assert by_q[31]["scoring_errors"]["final_aggregate"] == "submitted_payload_mismatch"
    assert by_q[32]["scores"]["final_aggregate"] is None
    assert by_q[32]["scoring_errors"]["final_aggregate"] == "missing_submitted_forecast_payload"
    assert board["rejected_submission_counts"]["submitted_payload_mismatch"] == 1
    assert board["rejected_submission_counts"]["missing_submitted_forecast_payload"] == 1


def test_b3_b4_rankings_come_only_from_verified_per_tournament_observations(tmp_path: Path):
    data_dir = tmp_path / "data"
    q = _mini_question(40, p_t1=0.6)
    other = _mini_question(41, p_t1=0.6)
    for r in other["submissions.jsonl"] + other["questions.jsonl"]:
        r["tournament_id"] = 44444
    obs = [
        {"type": "ranking_observation", "ranking_observation_id": "rk1", "tournament_id": 33135,
         "observed_at_utc": "2026-11-06T10:00:00Z", "ranking_verified": True, "rank": 5, "matched_user_id": _BOT},
        {"type": "ranking_observation", "ranking_observation_id": "rk2", "tournament_id": 33135,
         "observed_at_utc": "2026-11-06T12:30:00Z", "ranking_verified": True, "rank": 3, "matched_user_id": _BOT},
        # After as_of: ignored
        {"type": "ranking_observation", "ranking_observation_id": "rk3", "tournament_id": 33135,
         "observed_at_utc": "2026-11-06T14:00:00Z", "ranking_verified": True, "rank": 1, "matched_user_id": _BOT},
        # Wrong identity for tournament 44444, and an unverified row: both ignored and counted
        {"type": "ranking_observation", "ranking_observation_id": "rk4", "tournament_id": 44444,
         "observed_at_utc": "2026-11-06T12:00:00Z", "ranking_verified": True, "rank": 2, "matched_user_id": 78},
        {"type": "ranking_observation", "ranking_observation_id": "rk5", "tournament_id": 44444,
         "observed_at_utc": "2026-11-06T12:00:00Z", "rank": 2, "matched_user_id": _BOT},
    ]
    _write_mini(data_dir, q, other, extra_res=obs)
    board = _REAL_EVALUATE(data_dir, as_of=_AS_OF, n_boot=20)
    off = board["official_scores"]
    assert set(off) >= {"official_rankings_by_tournament", "unverified_ranking_observations"}
    assert "official_ranking" not in off
    assert off["official_rankings_by_tournament"] == {
        "33135": {"tournament_id": 33135, "rank": 3, "matched_user_id": _BOT,
                  "observed_at_utc": "2026-11-06T12:30:00Z", "ranking_observation_id": "rk2"},
    }
    assert off["unverified_ranking_observations"] == 2
    md = SC.render_scoreboard_markdown(board)
    assert "verified own entry, per tournament" in md
    assert "CRPS/RPS" in md


def test_e6_overall_tier2_pairwise_is_gated_when_questions_fall_outside_the_single_segment(tmp_path: Path):
    data_dir = tmp_path / "data"
    q_ok = _mini_question(50, p_t1=0.6, p_t2=0.8)
    q_unknown = _mini_question(51, p_t1=0.6)
    for a in q_unknown["forecasts.jsonl"]:
        a.pop("prompt_version")  # no Tier 1 prompt provenance -> unsegmentable (never a literal "unknown" segment)
    _write_mini(data_dir, q_ok, q_unknown)
    board = _REAL_EVALUATE(data_dir, as_of=_AS_OF, n_boot=20)
    assert list(board["by_config_segment"]) == ["rc1|agg_v1|p1|r1"]
    assert board["overall"]["unsegmentable_question_ids"] == [51]
    seg = board["by_config_segment"]["rc1|agg_v1|p1|r1"]
    assert seg["tier2_escalation_analysis"]["tier2_efficiency_status"] == "complete_itt"
    assert "itt_incomplete_reasons" not in seg["pairwise"]["final_vs_tier1_aggregate"]["conclusion_discipline"]
    overall = board["overall"]
    assert overall["tier2_escalation_analysis"]["tier2_efficiency_status"] == "pooling_prohibited"
    gated = overall["pairwise"]["final_vs_tier1_aggregate"]["conclusion_discipline"]
    assert gated["can_claim_superiority"] is False
    assert gated["itt_incomplete_reasons"]["unsegmentable_questions_outside_segment"] == 1
    # The segment's own object is not mutated by the overall gate
    assert seg["pairwise"]["final_vs_tier1_aggregate"]["conclusion_discipline"] is not gated


def test_snapshotless_probe_submission_is_excluded_separately(tmp_path: Path):
    data_dir = tmp_path / "data"
    q = _mini_question(60, p_t1=0.6)
    q["submissions.jsonl"].append({"type": "submission", "status": "forecast_ok", "intent_id": "v15_1", "mode": "trial",
                                   "question_id": 46451, "question_snapshot_id": None, "tournament_id": 32977,
                                   "bot_id": _BOT, "at": _TS})
    _write_mini(data_dir, q)
    board = _REAL_EVALUATE(data_dir, as_of=_AS_OF, n_boot=10)
    assert board["submitted_questions_count"] == 2
    assert board["scorable_resolved_count"] == 1
    assert board["excluded_counts"]["submitted_without_snapshot_id"] == 1
    assert board["excluded_counts"]["no_resolution_event"] == 0
