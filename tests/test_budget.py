from datetime import timedelta

import pytest

import budget as B
import quota
from flab.config import load_quota_policy, load_run_config
from flab.jsonl import append_record, read_records
from flab.timeutil import to_iso
from test_forecast import NOW, _qsnap


@pytest.fixture
def ledger(tmp_path):
    campaign = {"campaign_id": "test", "budget_window_start": to_iso(NOW - timedelta(days=1)),
                "budget_window_end": to_iso(NOW + timedelta(days=6)), "weekly_attempt_caps": dict.fromkeys(("claude", "gemini", "gpt"), 40)}
    return B.Budget(tmp_path, campaign, load_quota_policy(), "current", now=lambda: NOW)


def reserve(ledger, tier=1, player="claude", aid="new", qid=7, **kw):
    return ledger.reserve({"player": player, "tier": tier, "question_id": qid, "attempt_id": aid},
                          pending_tier1=kw.get("pending", 0), expected_new=kw.get("incoming", 0), repair_reserve=kw.get("repair", 0))


def attempt(ledger, aid="old", tier=1, player="claude", qid=7, total=30, started=None):
    started = started or NOW - timedelta(minutes=5)
    r = {"type": "forecast_attempt", "attempt_id": aid, "tier": tier, "player": player, "question_id": qid,
         "attempt_kind": "initial", "model_check": "verified", "started_at_utc": to_iso(started),
         "finished_at_utc": to_iso(started + timedelta(minutes=1)),
         "usage": {"input_tokens": total // 2, "output_tokens": total - total // 2} if total is not None else {}}
    append_record(ledger.root / "forecasts.jsonl", r)
    return r


def snapshot(ledger, aid="new", when="before", remaining=.9, reset=None, **kw):
    r = {"type": "quota_snapshot", "attempt_id": aid, "when": when, "taken_at": to_iso(NOW), "remaining_credits": 0,
         "buckets": {**_qsnap(.9).to_dict()["buckets"], quota.ARGON_BUCKET_ID: {"remaining_fraction": remaining, "reset_time": reset or to_iso(NOW + timedelta(days=3))}}, **kw}
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
def test_old_outputs_are_not_rejudged_by_new_policy(ledger, total):
    attempt(ledger)
    attempt(ledger, aid="deep", tier=2, total=total)
    assert reserve(ledger, tier=2, aid="next", qid=8)["allowed"]
    ledger.outcome("next", False)
    # Old question usage still constrains its own capacity.
    assert reserve(ledger, tier=2, aid="own", qid=7)["reason"] == "question_tier2_token_capacity"
    for i in range(5):
        attempt(ledger, aid=f"later{i}", qid=10 + i, total=1000)
    assert reserve(ledger, tier=2, aid="still", qid=9)["allowed"]
    assert not any(r["type"] == "budget_upgrade_stop" for r in read_records(ledger.log))


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


@pytest.mark.parametrize('weekly_cap,remaining', [(.8, .24), (1.0, .04)])
def test_gemini_respects_personal_reserve_and_forecast_costs(ledger, weekly_cap, remaining):
    ledger.policy['players']['gemini']['weekly_cap'] = weekly_cap
    snapshot(ledger, remaining=remaining)
    assert reserve(ledger, player="gemini")["reason"] == "argon_personal_or_tier1_reserve"
    snapshot(ledger, aid="next", remaining=.7)
    assert reserve(ledger, player="gemini", aid="next")["allowed"]


@pytest.mark.parametrize("after", [.84, .95])
def test_gemini_tier2_quota_deltas_no_longer_trip_a_breaker(ledger, after):
    # D97: a large or negative before/after delta is not a cost and never stops upgrades.
    attempt(ledger, player="gemini")
    attempt(ledger, aid="deep", tier=2, player="gemini", total=30)
    snapshot(ledger, aid="deep", remaining=.9)
    snapshot(ledger, aid="deep", when="after", remaining=after)
    snapshot(ledger, remaining=.84)
    decision = reserve(ledger, tier=2, player="gemini", qid=8)
    assert decision["allowed"], decision["reason"]
    assert decision["argon_estimate_method"] == quota.WINDOW_ESTIMATE_VERSION
    assert not any(r["type"] == "budget_upgrade_stop" for r in read_records(ledger.log))


def test_gemini_old_token_overrun_does_not_create_new_line_stop(ledger):
    attempt(ledger, player="gemini")
    attempt(ledger, aid="deep", tier=2, player="gemini", total=100)
    snapshot(ledger, remaining=.9)
    decision = reserve(ledger, tier=2, player="gemini", qid=8)
    assert decision["allowed"]
    assert not any(r["type"] == "budget_upgrade_stop" for r in read_records(ledger.log))


def test_persisted_argon_breaker_is_honoured_until_it_expires(ledger):
    attempt(ledger, player="gemini")
    reset = to_iso(NOW + timedelta(days=3))
    append_record(ledger.log, {"type": "budget_upgrade_stop", "player": "gemini", "reason": "weekly_tier2_argon_breaker",
                               "until": reset, "at": to_iso(NOW - timedelta(hours=1)), "quota_layout": "legacy_argon"})
    snapshot(ledger, remaining=.9, reset=reset)
    assert reserve(ledger, tier=2, player="gemini", qid=8)["reason"] == "weekly_tier2_argon_breaker"
    # A later reset window is a different breaker scope: the old stop no longer applies.
    snapshot(ledger, aid="later", remaining=.9, reset=to_iso(NOW + timedelta(days=4)))
    assert reserve(ledger, tier=2, player="gemini", aid="later", qid=9)["allowed"]


def test_gemini_reserve_uses_window_estimate_instead_of_fixed_fallback(ledger):
    # Twenty pending tier-1 jobs at the old 0.05 fallback exceed the bucket (0.97 - 0.05 - 1.0 < 0).
    # Window usage 0.03 over 7 calls is ~0.0043 per call: tier 1 x3, tier 2 x3 x3 (token cap).
    for i in range(6):
        attempt(ledger, aid=f"w{i}", player="gemini", qid=20 + i)
    attempt(ledger, aid="deep", tier=2, player="gemini", qid=30)
    snapshot(ledger, aid="w0", remaining=.98)
    snapshot(ledger, remaining=.97)
    decision = reserve(ledger, tier=2, player="gemini", qid=8, pending=20)
    assert decision["allowed"], decision["reason"]
    assert decision["argon_estimate"] == pytest.approx(.03 / 7 * 3 * 3)
    assert decision["argon_estimate_calls"] == 7
    # Without enough window calls the old fallback still applies and reserves the line.
    fresh = B.Budget(ledger.root / "fresh", ledger.campaign, ledger.policy, "fresh", now=lambda: NOW)
    fresh.root.mkdir()
    attempt(fresh, player="gemini")
    attempt(fresh, aid="deep", tier=2, player="gemini", qid=30)
    snapshot(fresh, remaining=.97)
    decision = reserve(fresh, tier=2, player="gemini", qid=8, pending=20)
    assert decision["allowed"] and decision["protected_quota"] == .05


def test_completion_estimate_uses_same_line_same_layer_and_full_cap_when_unknown(ledger):
    records = [attempt(ledger)]
    cfg = load_run_config()
    assert B.duration_estimate(records, "claude", 1, cfg) == 120
    assert B.duration_estimate(records, "claude", 2, cfg) == cfg["timeouts_s"]["tier2"] + 60
    assert B.duration_estimate(records, "gemini", 1, cfg) == cfg["timeouts_s"]["tier1"] + 120


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
