from datetime import timedelta

import pytest

import budget as B
import quota
from flab.config import load_quota_policy, load_run_config
from flab.jsonl import append_record, read_records
from flab.timeutil import to_iso
from test_forecast import NOW


@pytest.fixture
def ledger(tmp_path):
    campaign = {"campaign_id": "test", "budget_window_start": to_iso(NOW - timedelta(days=1)),
                "budget_window_end": to_iso(NOW + timedelta(days=6)), "weekly_attempt_caps": dict.fromkeys(("claude", "gemini", "gpt"), 40)}
    return B.Budget(tmp_path, campaign, load_quota_policy(), "current", now=lambda: NOW)


def reserve(ledger, tier=1, player="claude", aid="new", qid=7, **kw):
    return ledger.reserve({"player": player, "tier": tier, "question_id": qid, "attempt_id": aid},
                          pending_tier1=kw.get("pending", 0), expected_new=kw.get("incoming", 0), repair_reserve=kw.get("repair", 0))


def attempt(ledger, aid="old", tier=1, player="claude", qid=7, total=30):
    r = {"type": "forecast_attempt", "attempt_id": aid, "tier": tier, "player": player, "question_id": qid,
         "attempt_kind": "initial", "model_check": "verified", "started_at_utc": to_iso(NOW - timedelta(minutes=5)),
         "finished_at_utc": to_iso(NOW - timedelta(minutes=4)),
         "usage": {"input_tokens": total // 2, "output_tokens": total - total // 2} if total is not None else {}}
    append_record(ledger.root / "forecasts.jsonl", r)
    return r


def snapshot(ledger, aid="new", when="before", remaining=.9, reset=None, **kw):
    r = {"type": "quota_snapshot", "attempt_id": aid, "when": when, "taken_at": to_iso(NOW), "remaining_credits": 0,
         "buckets": {quota.ARGON_BUCKET_ID: {"remaining_fraction": remaining, "reset_time": reset or to_iso(NOW + timedelta(days=3))}}, **kw}
    append_record(ledger.root / "quota.jsonl", r)


def test_unmeasured_tier1_can_run_with_an_explicit_cap_but_tier2_cannot(ledger):
    assert reserve(ledger)["allowed"]
    ledger.outcome("new", False)
    assert reserve(ledger, tier=2, aid="two")["reason"] == "tier1_token_baseline_unmeasured"


def test_start_and_acceptance_count_toward_weekly_cap_without_double_counting(ledger):
    r = attempt(ledger)
    append_record(ledger.root / "forecast_attempt_starts.jsonl", r)
    acceptance = ledger.root / "acceptance.jsonl"
    ledger.acceptance_log = acceptance
    append_record(acceptance, {"type": "acceptance_result", "player": "claude", "finished_at": to_iso(NOW)})
    ledger.campaign["weekly_attempt_caps"]["claude"] = 4
    result = reserve(ledger)
    assert not result["allowed"] and result["weekly_attempts_used"] == 4


def test_protected_first_tier_and_future_and_repair_capacity_precedes_tier2(ledger):
    attempt(ledger)
    ledger.campaign["weekly_attempt_caps"]["claude"] = 5
    decision = reserve(ledger, tier=2, pending=2, incoming=1, repair=1)
    assert not decision["allowed"] and decision["protected_attempts"] == 4
    assert reserve(ledger, aid="first")["allowed"]


def test_reservation_is_exclusive_and_can_be_released(ledger):
    assert reserve(ledger)["allowed"]
    assert not reserve(ledger, aid="other")["allowed"]
    ledger.outcome("new", False)
    assert reserve(ledger, aid="third")["allowed"]
    with pytest.raises(ValueError, match="already"):
        reserve(ledger, aid="third")


def test_restart_releases_only_prior_no_start_reservation(ledger):
    assert reserve(ledger)["allowed"]
    ledger.run_id = "restarted"
    ledger.recover()
    assert reserve(ledger, aid="second")["allowed"]
    append_record(ledger.root / "forecast_attempt_starts.jsonl", {"attempt_id": "second", "player": "claude",
                  "started_at_utc": to_iso(NOW)})
    ledger.run_id = "again"
    ledger.recover()
    assert not reserve(ledger, aid="third")["allowed"]


@pytest.mark.parametrize("total", [100, None])
def test_token_overrun_or_missing_usage_halts_upgrades_for_window(ledger, total):
    attempt(ledger)
    attempt(ledger, aid="deep", tier=2, total=total)
    assert reserve(ledger, tier=2, aid="next", qid=8)["reason"] == "weekly_tier2_token_breaker"
    # Later higher baselines must not erase the observed stop.
    for i in range(5):
        attempt(ledger, aid=f"later{i}", qid=10 + i, total=1000)
    assert reserve(ledger, tier=2, aid="still", qid=9)["reason"] == "weekly_tier2_token_breaker"


def test_per_question_token_estimate_reserves_remaining_capacity(ledger):
    attempt(ledger)
    attempt(ledger, aid="deep", tier=2, total=60)
    assert reserve(ledger, tier=2)["reason"] == "question_tier2_token_capacity"
    assert reserve(ledger, tier=2, aid="different", qid=8)["allowed"]


@pytest.mark.parametrize("change", ["missing", "credits", "stale", "old", "reset", "nan"])
def test_gemini_requires_trustworthy_current_usage(ledger, change):
    kw = {}
    if change == "credits": kw["remaining_credits"] = 1
    if change == "stale": kw["stale"] = True
    if change == "old": kw["taken_at"] = to_iso(NOW - timedelta(minutes=2))
    if change != "missing":
        snapshot(ledger, remaining="bad" if change == "nan" else .9,
                 reset="not a date" if change == "reset" else None, **kw)
    assert not reserve(ledger, player="gemini")["allowed"]


def test_gemini_respects_personal_reserve_and_forecast_costs(ledger):
    snapshot(ledger, remaining=.44)
    assert reserve(ledger, player="gemini")["reason"] == "argon_personal_or_tier1_reserve"
    snapshot(ledger, aid="next", remaining=.7)
    assert reserve(ledger, player="gemini", aid="next")["allowed"]


def test_gemini_tier2_overrun_is_a_persistent_reset_bound_breaker(ledger):
    attempt(ledger, player="gemini")
    attempt(ledger, aid="deep", tier=2, player="gemini", total=30)
    snapshot(ledger, aid="deep", remaining=.9)
    snapshot(ledger, aid="deep", when="after", remaining=.84)
    snapshot(ledger, remaining=.84)
    decision = reserve(ledger, tier=2, player="gemini", qid=8)
    assert decision["reason"] == "weekly_tier2_argon_breaker"
    event = next(r for r in read_records(ledger.log) if r["type"] == "budget_upgrade_stop")
    assert event["until"] == to_iso(NOW + timedelta(days=3))


def test_completion_estimate_uses_same_line_same_layer_and_full_cap_when_unknown(ledger):
    records = [attempt(ledger)]
    cfg = load_run_config()
    assert B.duration_estimate(records, "claude", 1, cfg) == 120
    assert B.duration_estimate(records, "claude", 2, cfg) == 1260
    assert B.duration_estimate(records, "gemini", 1, cfg) == 720


def test_detail_tokens_are_not_double_counted():
    assert B.tokens({"usage": {"input_tokens": 100, "output_tokens": 50, "reasoning_tokens": 30, "cached_tokens": 80}}) == 150
    assert B.tokens({"usage": {"input_tokens": 100, "output_tokens": None}}) is None


def test_acceptance_started_calls_count_even_when_interrupted_without_double_count(ledger):
    ledger.acceptance_log = ledger.root / "acceptance.jsonl"
    append_record(ledger.acceptance_log, {"type": "acceptance_call_started", "player": "claude", "run_id": "ok",
        "attempt_id": "one", "started_at": to_iso(NOW)})
    append_record(ledger.acceptance_log, {"type": "acceptance_result", "player": "claude", "run_id": "ok", "finished_at": to_iso(NOW)})
    append_record(ledger.acceptance_log, {"type": "acceptance_call_started", "player": "claude", "run_id": "interrupted",
        "attempt_id": "two", "started_at": to_iso(NOW)})
    assert reserve(ledger)["weekly_attempts_used"] == 2
