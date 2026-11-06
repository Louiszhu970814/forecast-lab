import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

import budget as B
import budget_handoff as H
import continuous as Q
from flab.config import load_quota_policy
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import parse_iso, to_iso
from test_cycle import policy
from test_forecast import NOW


def campaigns(root, *, cap=40):
    old = {**policy(), "version": "continuous_v8", "campaign_id": "old-trial",
           "budget_window_start": to_iso(NOW - timedelta(hours=12)),
           "budget_window_end": to_iso(NOW + timedelta(hours=12)),
           "weekly_attempt_caps": dict.fromkeys(("claude", "gemini", "gpt"), cap)}
    Q.register(root, old, now=lambda: NOW - timedelta(hours=1))
    new = {**copy.deepcopy(old), "version": "continuous_v9", "campaign_id": "early-mini",
           "venues": ["minibench"], "budget_window_start": to_iso(NOW),
           "budget_window_end": to_iso(NOW + timedelta(days=7)),
           "budget_handoff": {"version": H.LEGACY_VERSION, "source_campaign_id": old["campaign_id"],
                              "source_sha256": hash_obj(old),
                              "accounting_start": old["budget_window_start"],
                              "authorization": "PLAN D118"}}
    return old, new


def reserve(root, campaign, *, now=NOW, acceptance=None):
    budget = B.Budget(root, campaign, load_quota_policy(), "new-run", now=lambda: now,
                      acceptance_log=acceptance)
    return budget.reserve({"player": "claude", "tier": 1, "question_id": 7, "attempt_id": "new"},
                          pending_tier1=0, expected_new=0, repair_reserve=0)


def finished(root, aid, when, *, start=True, result=True):
    record = {"type": "forecast_attempt", "attempt_id": aid, "player": "claude", "tier": 1,
              "question_id": 100, "attempt_kind": "initial", "started_at_utc": to_iso(when),
              "finished_at_utc": to_iso(when + timedelta(seconds=1))}
    if start:
        append_record(root / "forecast_attempt_starts.jsonl", record)
    if result:
        append_record(root / "forecasts.jsonl", record)


def test_handoff_keeps_original_record_and_has_one_idempotent_successor(tmp_path):
    old, new = campaigns(tmp_path)
    original = (tmp_path / "continuous_campaigns.jsonl").read_bytes()
    Q.register(tmp_path, new, now=lambda: NOW)
    once = (tmp_path / "continuous_campaigns.jsonl").read_bytes()
    assert once.startswith(original)
    Q.register(tmp_path, new, now=lambda: NOW + timedelta(days=2))
    assert (tmp_path / "continuous_campaigns.jsonl").read_bytes() == once
    with pytest.raises(ValueError, match="superseded"):
        Q.register(tmp_path, old, now=lambda: NOW)
    with pytest.raises(ValueError, match="superseded"):
        reserve(tmp_path, old)


def test_old_and_new_attempts_and_acceptance_use_one_allowance(tmp_path):
    _, new = campaigns(tmp_path, cap=4)
    Q.register(tmp_path, new, now=lambda: NOW)
    finished(tmp_path, "before-source", NOW - timedelta(days=1))
    finished(tmp_path, "old-complete", NOW - timedelta(hours=1))
    finished(tmp_path, "legacy-no-start", NOW - timedelta(hours=2), start=False)
    finished(tmp_path, "new-complete", NOW + timedelta(minutes=1))
    acceptance = tmp_path / "acceptance.jsonl"
    append_record(acceptance, {"type": "acceptance_call_started", "attempt_id": "accept",
                              "run_id": "accept-run", "player": "claude",
                              "started_at": to_iso(NOW - timedelta(minutes=20))})
    result = reserve(tmp_path, {**new, "questions": [{"question_id": 7}]},
                     now=NOW + timedelta(hours=1), acceptance=acceptance)
    assert result["weekly_attempts_used"] == 4
    assert not result["allowed"] and result["reason"] == "weekly_attempt_capacity_reserved_or_exhausted"


def test_handoff_does_not_clear_an_unresolved_old_attempt(tmp_path):
    _, new = campaigns(tmp_path)
    Q.register(tmp_path, new, now=lambda: NOW)
    finished(tmp_path, "unresolved", NOW - timedelta(hours=1), result=False)
    result = reserve(tmp_path, new)
    assert not result["allowed"] and result["reason"] == "unresolved_or_running_attempt"
    assert result["weekly_attempts_used"] == 1


def test_source_costs_still_count_after_source_window_ends(tmp_path):
    _, new = campaigns(tmp_path, cap=1)
    Q.register(tmp_path, new, now=lambda: NOW)
    finished(tmp_path, "old", NOW - timedelta(hours=1))
    result = reserve(tmp_path, new, now=NOW + timedelta(days=1))
    assert not result["allowed"] and result["weekly_attempts_used"] == 1


@pytest.mark.parametrize("change", ["missing-source", "hash", "start", "authorization", "version",
                                    "extra", "self", "before-source", "source-end", "rollover"])
def test_invalid_handoff_cannot_register_or_spend(tmp_path, change):
    old, new = campaigns(tmp_path)
    h = new["budget_handoff"]
    if change == "missing-source": h["source_campaign_id"] = "missing"
    elif change == "hash": h["source_sha256"] = "changed"
    elif change == "start": h["accounting_start"] = to_iso(NOW)
    elif change == "authorization": h["authorization"] = "not authorized"
    elif change == "version": new["version"] = "continuous_v8"
    elif change == "extra": h["reset_usage"] = True
    elif change == "self": new["campaign_id"] = old["campaign_id"]
    elif change == "before-source": new["budget_window_start"] = old["budget_window_start"]
    elif change == "source-end": new["budget_window_start"] = old["budget_window_end"]
    else: new["rollover"] = {"version": Q.ROLLOVER_VERSION, "windows": 2}
    original = (tmp_path / "continuous_campaigns.jsonl").read_bytes()
    observed = parse_iso(old["budget_window_end"]) if change == "source-end" else NOW
    with pytest.raises(ValueError): Q.active_campaign(tmp_path, new, now=lambda: observed)
    with pytest.raises(ValueError): reserve(tmp_path, new, now=observed)
    assert (tmp_path / "continuous_campaigns.jsonl").read_bytes() == original
    assert not read_complete(tmp_path / "budget_events.jsonl")


def test_valid_but_unregistered_handoff_cannot_spend(tmp_path):
    _, new = campaigns(tmp_path)
    with pytest.raises(ValueError, match="registered"):
        reserve(tmp_path, new)


def test_first_registration_after_source_expiry_is_refused(tmp_path):
    old, new = campaigns(tmp_path)
    with pytest.raises(ValueError, match="still be active"):
        Q.register(tmp_path, new, now=lambda: parse_iso(old["budget_window_end"]))
    assert len(read_complete(tmp_path / "continuous_campaigns.jsonl")) == 1


def test_second_successor_or_chained_handoff_cannot_reset_usage(tmp_path):
    _, new = campaigns(tmp_path)
    Q.register(tmp_path, new, now=lambda: NOW)
    with pytest.raises(ValueError, match="successor"):
        Q.register(tmp_path, {**new, "campaign_id": "renamed"}, now=lambda: NOW)
    chained = copy.deepcopy(new)
    chained.update(campaign_id="chained", budget_window_start=to_iso(NOW + timedelta(hours=1)))
    chained["budget_handoff"].update(source_campaign_id=new["campaign_id"],
                                    source_sha256=hash_obj(new), accounting_start=new["budget_window_start"])
    with pytest.raises(ValueError, match="source"):
        Q.register(tmp_path, chained, now=lambda: NOW + timedelta(hours=1))


def test_handoff_may_overlap_only_the_exact_source(tmp_path):
    old, new = campaigns(tmp_path)
    unrelated = {**old, "campaign_id": "later",
                 "budget_window_start": old["budget_window_end"],
                 "budget_window_end": to_iso(NOW + timedelta(days=2))}
    Q.register(tmp_path, unrelated, now=lambda: NOW + timedelta(days=1))
    with pytest.raises(ValueError, match="overlap"):
        Q.register(tmp_path, new, now=lambda: NOW)


@pytest.mark.parametrize("change", ["cap", "time", "venue", "drop-handoff"])
def test_registered_successor_cannot_drift(tmp_path, change):
    _, new = campaigns(tmp_path)
    Q.register(tmp_path, new, now=lambda: NOW)
    changed = copy.deepcopy(new)
    if change == "cap": changed["weekly_attempt_caps"]["claude"] += 1
    elif change == "time":
        changed["budget_window_start"] = to_iso(NOW - timedelta(minutes=1))
        changed["budget_window_end"] = to_iso(NOW + timedelta(days=7) - timedelta(minutes=1))
    elif change == "venue": changed["venues"] = ["main"]
    else: changed.pop("budget_handoff")
    with pytest.raises(ValueError, match="cannot change"):
        Q.register(tmp_path, changed, now=lambda: NOW)
    # A forged runtime that drops handoff must not evade source accounting either.
    with pytest.raises(ValueError):
        reserve(tmp_path, changed)


def test_handoff_registration_is_atomic_under_competing_successors(tmp_path):
    _, new = campaigns(tmp_path)
    def attempt(i):
        candidate = {**new, "campaign_id": "one" if i % 2 else "two"}
        try:
            Q.register(tmp_path, candidate, now=lambda: NOW)
            return candidate["campaign_id"]
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(8)))
    assert len({r for r in results if r is not None}) == 1
    assert len(read_complete(tmp_path / "continuous_campaigns.jsonl")) == 2


def test_successor_remains_bounded_by_seven_days(tmp_path):
    _, new = campaigns(tmp_path)
    new["budget_window_end"] = to_iso(NOW + timedelta(days=7, seconds=1))
    with pytest.raises(ValueError, match="seven days"):
        Q.register(tmp_path, new, now=lambda: NOW)
