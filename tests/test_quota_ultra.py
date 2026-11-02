"""M2-W9: offline process tables and quota fixtures, never real model calls."""
import json
import subprocess
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import acceptance
import autoload
import forecast as F
import m2_report
import quota
from adapters.base import AdapterRequest
from flab.config import load_quota_policy
from flab.jsonl import append_record, read_records
from flab.timeutil import to_iso
from test_budget import ledger, reserve, attempt
from test_reconcile import ctx
from test_forecast import NOW, POLICY, _qsnap, _good, env, _no_real_data

FIXTURES = Path(__file__).parent / "fixtures/gemini"


def ultra(weekly=.9, short=.8, **kwargs):
    snap = quota.parse_snapshot(FIXTURES / "usage_ultra.json", {"remaining_credits": 0})
    buckets = dict(snap.buckets)
    for key, value in ((quota.GEMINI_WEEKLY_BUCKET_ID, weekly), (quota.GEMINI_5H_BUCKET_ID, short)):
        buckets[key] = replace(buckets[key], remaining_fraction=value)
    return replace(snap, buckets=buckets, taken_at=to_iso(NOW), **kwargs)


def save_pair(root, aid, before, after):
    for when, snap in (("before", before), ("after", after)):
        append_record(root / "quota.jsonl", {"type": "quota_snapshot", "attempt_id": aid, "when": when, **snap.to_dict()})


def ultra_window(weekly=.9, short=.8, *, weekly_reset=None, short_reset=None, **kwargs):
    """Ultra snapshot whose reset windows contain NOW (the fixture's resets lie after NOW)."""
    snap = ultra(weekly, short, **kwargs)
    resets = {quota.GEMINI_WEEKLY_BUCKET_ID: weekly_reset or to_iso(NOW + timedelta(days=3)),
              quota.GEMINI_5H_BUCKET_ID: short_reset or to_iso(NOW + timedelta(hours=2))}
    return replace(snap, buckets={k: replace(b, reset_time=resets.get(k, b.reset_time)) for k, b in snap.buckets.items()})


def save_reading(root, snap, aid="reading", when="before"):
    append_record(root / "quota.jsonl", {"type": "quota_snapshot", "attempt_id": aid, "when": when, **snap.to_dict()})


def window_calls(path, n, *, player="gemini", started=None, tier=1):
    for i in range(n):
        append_record(path, {"type": "forecast_attempt", "attempt_id": f"{player}{tier}-{i}-{started}", "player": player,
                             "tier": tier, "started_at_utc": started or to_iso(NOW - timedelta(minutes=5))})


def test_layouts_and_bucket_selection_roundtrip():
    legacy = quota.parse_snapshot(FIXTURES / "slash_usage.json")
    old = quota.argon_buckets(legacy)
    assert old.layout == "legacy_argon" and old.weekly.id == quota.ARGON_BUCKET_ID and old.five_hour is None
    new = quota.argon_buckets(ultra().to_dict())
    assert new.layout == "ultra_shared" and new.weekly.id == quota.GEMINI_WEEKLY_BUCKET_ID
    assert new.five_hour.id == quota.GEMINI_5H_BUCKET_ID
    assert ultra().to_dict()["quota_layout"] == "ultra_shared"


@pytest.mark.parametrize("problem", ["missing", "legacy_missing", "extra", "empty_group", "duplicate", "group_name",
                                     "nan", "inf", "negative", "over", "bool"])
def test_unknown_layouts_fail_closed(problem):
    raw = json.loads((FIXTURES / ("slash_usage.json" if problem == "legacy_missing" else "usage_ultra.json")).read_text())
    groups = raw["command"]["data"]["groups"]
    if problem in ("missing", "legacy_missing"):
        groups[0]["buckets"].pop()
    elif problem == "extra":
        groups.append({"name": "Gemini Future", "buckets": [{"id": "future", "remaining_fraction": 1}]})
    elif problem == "empty_group":
        groups.append({"name": "Gemini Future", "buckets": []})
    elif problem == "duplicate":
        groups[0]["buckets"].append(groups[0]["buckets"][0])
    elif problem == "group_name":
        groups[0]["name"] = "Gemini Future"
    else:
        groups[0]["buckets"][0]["remaining_fraction"] = {"nan": float("nan"), "inf": float("inf"), "negative": -.1,
            "over": 1.1, "bool": True}[problem]
    snap = quota.parse_snapshot(raw, {"remaining_credits": 0})
    assert quota.argon_buckets(snap).layout == "unknown"
    assert quota.argon_buckets(quota.record_snapshot(snap)).layout == "unknown"


@pytest.mark.parametrize("before,after", [(_qsnap(.9), ultra(.8)), (ultra(.9), _qsnap(.8))])
def test_plan_switch_is_not_a_cost(before, after):
    assert quota.quota_drop(before, after) is None
    assert F.quota_model_check(before, after, SimpleNamespace(usage_raw={"log_model_check": "verified"})) == "undetermined"


@pytest.mark.parametrize("problem", ["reset", "no_reset", "invalid_reset", "increase", "stale", "interactive", "ide", "headless", "reversed"])
def test_bad_cost_pairs_are_excluded(problem):
    before, after = ultra(), ultra(.89, .79)
    if problem in ("reset", "no_reset", "invalid_reset", "increase"):
        buckets = dict(after.buckets)
        key = quota.GEMINI_WEEKLY_BUCKET_ID
        buckets[key] = replace(buckets[key], **({"remaining_fraction": .99} if problem == "increase" else
            {"reset_time": {"reset": "2026-11-10T00:00:00Z", "no_reset": None, "invalid_reset": "invalid"}[problem]}))
        after = replace(after, buckets=buckets)
    elif problem == "stale":
        after = replace(after, stale=True)
    elif problem == "reversed":
        after = replace(after, taken_at=to_iso(NOW - timedelta(seconds=1)))
    else:
        after = replace(after, concurrent_agy_kinds=(problem,))
    assert quota.quota_drop(before, after) is None


def test_window_estimate_uses_minimum_reading_and_window_calls(tmp_path):
    qlog, flog = tmp_path / "quota.jsonl", tmp_path / "estimates.jsonl"
    window_calls(flog, 6)
    window_calls(flog, 3, player="claude")                                       # other lines never count
    window_calls(flog, 2, started=to_iso(NOW - timedelta(days=9)))              # before this weekly window
    for weekly, short in ((.97, .95), (.98, .99), (.96, .94)):                  # non-monotonic reads
        save_reading(tmp_path, ultra_window(weekly, short))
    save_reading(tmp_path, ultra_window(.5, .5, weekly_reset=to_iso(NOW + timedelta(days=1)),
                                        short_reset=to_iso(NOW + timedelta(hours=1))))   # other reset window
    save_reading(tmp_path, _qsnap(.3))                                           # other layout
    save_reading(tmp_path, ultra_window(.1, .1, stale=True))                     # stale reads are ignored
    append_record(qlog, {"type": "quota_snapshot", "attempt_id": "late", "when": "after", "delayed": True,
                         **ultra_window(.1, .1).to_dict()})                     # so are delayed reads
    current = ultra_window(.97, .95)
    # weekly: (1 - .96) / 6 x 3; five-hour: (1 - .94) / 6 x 3
    assert F.argon_estimate(qlog, POLICY, flog, current=current) == pytest.approx((.02, 6))
    assert F.argon_estimate(qlog, POLICY, flog, current=current, period="five_hour") == pytest.approx((.03, 6))
    # Tier 2 is scaled by the token multiplier cap (default 3) and never exceeds the fallback.
    assert F.argon_estimate(qlog, POLICY, flog, current=current, tier=2) == pytest.approx((.05, 6))
    assert F.argon_estimate(qlog, {**POLICY, "token_multiplier_cap": 2}, flog, current=current, tier=2) == pytest.approx((.04, 6))
    assert F.argon_estimate(qlog, POLICY, flog) == (.05, 0)
    usage = quota.window_usage(read_records(flog), read_records(qlog), current=current)
    assert usage["readings"] == 4 and usage["min_remaining"] == .96 and usage["gemini_calls"] == 6
    assert usage["method"] == quota.WINDOW_ESTIMATE_VERSION and usage["includes_non_forecast_usage"]


@pytest.mark.parametrize("calls,readings", [(4, (.97, .96)), (6, (1.0,)), (0, (.9,))])
def test_window_estimate_falls_back_without_enough_calls_or_observed_usage(tmp_path, calls, readings):
    qlog, flog = tmp_path / "quota.jsonl", tmp_path / "estimates.jsonl"
    window_calls(flog, calls)
    for weekly in readings:
        save_reading(tmp_path, ultra_window(weekly))
    assert F.argon_estimate(qlog, POLICY, flog, current=ultra_window(max(readings))) == (.05, 0)


def test_window_estimate_rejects_unknown_or_stale_current_snapshot(tmp_path):
    window_calls(tmp_path / "f.jsonl", 6)
    assert quota.window_usage(read_records(tmp_path / "f.jsonl"), [], current=ultra_window(stale=True)) is None
    assert quota.window_usage([], [], current=ultra_window(weekly=float("nan"))) is None
    with pytest.raises(ValueError):
        quota.window_usage([], [], current=ultra_window(), period="daily")


@pytest.mark.parametrize("log_check,weekly,short,expected", [
    ("verified", .89, .79, "verified"), ("mismatch", .89, .79, "mismatch"),
    (None, .89, .79, "undetermined"), ("unverified", .89, .79, "undetermined"),
    ("verified", .9, .8, "undetermined"), ("verified", .89, .8, "undetermined"),
    ("verified", .91, .79, "undetermined"), ("mismatch", .9, .8, "mismatch"),
])
def test_shared_model_verification(log_check, weekly, short, expected):
    result = SimpleNamespace(usage_raw={"log_model_check": log_check})
    assert F.quota_model_check(ultra(), ultra(weekly, short), result) == expected


def test_model_check_rejects_other_group_changes_and_concurrency():
    before, after = ultra(), ultra(.89, .79)
    buckets = dict(after.buckets)
    key = quota.THIRD_PARTY_WEEKLY_BUCKET_ID
    buckets[key] = replace(buckets[key], remaining_fraction=.9)
    result = SimpleNamespace(usage_raw={"log_model_check": "verified"})
    assert F.quota_model_check(before, replace(after, buckets=buckets), result) == "undetermined"
    assert F.quota_model_check(before, replace(after, concurrent_agy_kinds=("ide",)), result) == "undetermined"


@pytest.mark.parametrize("command,kind", [("/usr/local/bin/agy -p secret prompt", "headless"),
    ("/usr/local/bin/agy", "interactive"), ("/Applications/Antigravity.app/Contents/MacOS/Electron", "ide"),
    ("/Applications/Antigravity.app/Contents/Resources/app/extensions/antigravity/bin/language_server_macos_arm", "ide"),
    ("/usr/bin/node /usr/local/bin/agy -p secret", "headless"), ("agy --prompt=secret", "headless"),
    ("/usr/bin/python script.py agy -p secret Antigravity.app", None)])
def test_process_classes(command, kind):
    counts, reason = quota.detect_agy_activity(ps_output=f"PID PPID COMMAND\n100 1 python\n200 1 {command}", current_pid=100)
    assert counts == ({kind: 1} if kind else {}) and reason is None
    assert "secret" not in str(counts)


def test_process_tree_excludes_all_descendants_and_counts_external_classes():
    counts, reason = quota.detect_agy_activity(ps_output="""PID PPID COMMAND
100 1 python
102 101 agy -p own grandchild
101 100 wrapper
1000 1 agy -p external
1001 1 agy -p external2
1002 1 agy
1003 1 /Applications/Antigravity.app/Contents/MacOS/Electron
""", current_pid=100)
    assert counts == {"headless": 2, "interactive": 1, "ide": 1} and reason is None


@pytest.mark.parametrize("failure", [OSError("secret"), subprocess.TimeoutExpired("ps", 5), subprocess.CalledProcessError(1, "ps")])
def test_ps_failure_is_headless_without_leaking_exception(monkeypatch, failure):
    def fail(*args, **kwargs):
        assert args[0] == ["/bin/ps", "-axo", "pid,ppid,command"]
        raise failure
    monkeypatch.setattr(quota.subprocess, "run", fail)
    assert quota.detect_agy_activity() == ({"headless": 1}, "ps_failed")


def test_snapshot_records_activity_before_and_after_query_without_commands(monkeypatch):
    scans = iter([({"interactive": 1}, None), ({"headless": 2, "ide": 1}, "ps_failed")])
    monkeypatch.setattr(quota, "detect_agy_activity", lambda: next(scans))
    snap = quota.snapshot(_run_cmd=lambda args: (FIXTURES / ("usage_ultra.json" if "/usage" in args else "slash_credits.json")).read_text())
    rec = snap.to_dict()
    assert rec["concurrent_agy_detected"] is True
    assert rec["concurrent_agy_kinds"] == ["headless", "ide", "interactive"]
    assert rec["concurrent_agy_counts"] == {"headless": 2, "ide": 1, "interactive": 1}
    assert quota.parse_snapshot(rec).to_dict() == rec
    assert rec["concurrent_agy_reason"] == "ps_failed" and "command" not in rec


@pytest.mark.parametrize("kind,blocked", [("headless", True), ("interactive", False), ("ide", False)])
def test_only_headless_blocks_admission(tmp_path, kind, blocked):
    before = ultra(concurrent_agy_kinds=(kind,), concurrent_agy_detected=kind == "headless")
    ctx = SimpleNamespace(quota_policy=POLICY, quota_log=tmp_path / "quota.jsonl", forecasts_log=tmp_path / "forecasts.jsonl", tier=1)
    assert bool(F.gemini_admission(ctx, before)[0]) is blocked
    assert bool(quota.manual_admission(before, POLICY, model="gemini-4-argon-high", history_root=tmp_path)[0]) is blocked


def test_forecast_wait_records_reset_without_starting_or_safety_stop(env, tmp_path):
    before = ultra(short=.01)
    summary, records, adapter = env([], player="gemini", quota_snapshot=lambda **kw: before, quota_policy=POLICY)
    assert adapter.ran == 0 and records == []
    assert not summary.get("notify_louis")
    row = read_records(tmp_path / "a/quota.jsonl")[-1]
    assert row["reason"] == "quota_5h_wait"
    assert row["quota_retry_at_utc"] == quota.argon_buckets(before).five_hour.reset_time
    assert summary["questions"][0]["outcome"] == "quota_5h_wait"


@pytest.mark.parametrize("kind", [None, "interactive", "ide", "headless"])
def test_forecast_cost_status_and_model_check_are_persisted(env, monkeypatch, kind):
    from test_forecast import FakeAdapter
    run = FakeAdapter.run
    def with_log(self, *args, **kwargs):
        result = run(self, *args, **kwargs)
        result.usage_raw["log_model_check"] = "verified"
        return result
    monkeypatch.setattr(FakeAdapter, "run", with_log)
    snaps = iter([ultra(), ultra(.89, .79, concurrent_agy_kinds=(kind,) if kind else (), concurrent_agy_detected=kind == "headless")])
    _, records, adapter = env([{"structured": _good()}], player="gemini", quota_snapshot=lambda **kw: next(snaps), quota_policy=POLICY)
    assert adapter.ran == 1
    assert records[0]["quota_cost_status"] == ("undetermined" if kind else "trusted")
    assert records[0]["model_check"] == ("undetermined" if kind else "verified")
    assert records[0]["quota_layout"] == "ultra_shared"


@pytest.mark.parametrize("gate", ["acceptance", "autoload", "budget"])
@pytest.mark.parametrize("problem", ["short", "weekly", "unknown"])
@pytest.mark.parametrize("weekly_cap", [.8, 1.0])
def test_each_gate_denies_before_model_call(tmp_path, monkeypatch, ledger, gate, problem, weekly_cap):
    ledger.policy['players']['gemini']['weekly_cap'] = weekly_cap
    monkeypatch.setattr(acceptance, 'load_quota_policy', lambda: ledger.policy)
    monkeypatch.setattr('flab.config.load_quota_policy', lambda: ledger.policy)
    before = ultra(weekly=1 - weekly_cap + .01 if problem == "weekly" else .9, short=.01 if problem == "short" else .8)
    if problem == "unknown":
        before = replace(before, buckets={})
    monkeypatch.setattr(quota, "snapshot", lambda **kw: before)
    reason = "quota_5h_wait" if problem == "short" else "quota_layout_unknown" if problem == "unknown" else "quota_exhausted"
    if gate == "acceptance":
        log = tmp_path / "acceptance.jsonl"
        monkeypatch.setattr(acceptance, "ACCEPTANCE_LOG", log)
        with pytest.raises(acceptance.AcceptancePaused, match=reason):
            acceptance._run(SimpleNamespace(run=lambda req: pytest.fail("model called")), "gemini", "tier1", "synthetic",
                tmp_path, tmp_path, {"model": "gemini-4-argon-high", "effort": "high"}, 60, run_id="r")
        row = read_records(log)[-1]
    elif gate == "autoload":
        log = tmp_path / "acceptance.jsonl"
        request = AdapterRequest(player="gemini", profile="tier1", prompt="synthetic", schema={}, workdir=tmp_path,
            raw_dir=tmp_path, model="gemini-4-argon-high", effort="high", timeout_s=60)
        with pytest.raises(ValueError, match=reason):
            autoload._start_call(request, "r", log)
        row = read_records(log)[-1]
    else:
        append_record(tmp_path / "quota.jsonl", {"type": "quota_snapshot", "attempt_id": "new", "when": "before", **before.to_dict()})
        row = reserve(ledger, player="gemini")
        if problem == "weekly":
            reason = "argon_personal_or_tier1_reserve"
        assert row["allowed"] is False
        assert not any(r["type"] == "budget_upgrade_stop" for r in read_records(ledger.log))
    assert row["reason"] == reason
    if problem == "short":
        assert row["quota_retry_at_utc"] == quota.argon_buckets(before).five_hour.reset_time


def window_history(root):
    for i in range(6):
        attempt(SimpleNamespace(root=root), aid=f"w{i}", player="gemini", qid=40 + i)
    save_reading(root, ultra_window(.98, .97))
    save_reading(root, _qsnap(.2))          # a legacy reading is another unit and never mixes in


def test_budget_uses_window_estimates_for_both_buckets(ledger):
    window_history(ledger.root)
    append_record(ledger.root / "quota.jsonl", {"attempt_id": "new", "when": "before", **ultra_window(.97, .94).to_dict()})
    result = reserve(ledger, player="gemini")
    # weekly (1 - .97) / 6 x 3 = .015; five-hour (1 - .94) / 6 x 3 = .03
    assert result["allowed"] and result["argon_estimate"] == pytest.approx(.015)
    assert result["argon_estimate_calls"] == 6 and result["argon_estimate_method"] == quota.WINDOW_ESTIMATE_VERSION
    assert result["quota_5h_estimate"] == pytest.approx(.03) and result["quota_5h_estimate_samples"] == 6


def test_manual_gate_uses_window_estimates(ledger):
    window_history(ledger.root)
    reason, info = quota.manual_admission(ultra_window(.97, .94), load_quota_policy(), model="gemini-4-argon-high",
                                          history_root=ledger.root)
    assert reason is None and info["estimate"] == pytest.approx(.015) and info["estimate_samples"] == 6
    assert info["quota_5h_estimate"] == pytest.approx(.03)
    # A nearly empty five-hour bucket raises the window estimate and waits for the reset.
    reason, info = quota.manual_admission(ultra_window(.97, .03), load_quota_policy(), model="gemini-4-argon-high",
                                          history_root=ledger.root)
    assert reason == "quota_5h_wait" and info["quota_5h_estimate"] == pytest.approx(.05)


def test_single_source_of_bucket_ids():
    forbidden = ("gemini-argon-wtu-weekly", "gemini-weekly", "gemini-5h", "ARGON_BUCKET_ID")
    for path in (Path(__file__).parent.parent / "src").rglob("*.py"):
        if path.name != "quota.py":
            assert not any(word in path.read_text() for word in forbidden), str(path)


def report_attempt(root, aid, before, after, status="trusted", qid=1):
    r = {"type": "forecast_attempt", "attempt_id": aid, "question_id": qid, "question_snapshot_id": f"q{qid}",
        "player": "gemini", "tier": 1, "model_check": "verified", "quota_cost_status": status,
        "attempt_kind": "initial", "usage": {"input_tokens": 10, "output_tokens": 10},
        "started_at_utc": to_iso(NOW), "finished_at_utc": to_iso(NOW + timedelta(seconds=1))}
    append_record(root / "forecasts.jsonl", r)
    save_pair(root, aid, before, after)


def test_report_uses_trusted_attempts_and_separates_incompatible_units(tmp_path):
    campaign = {"campaign_id": "report", "questions": [{"question_id": 1}, {"question_id": 2}]}
    report_attempt(tmp_path, "uncertain", ultra(), ultra(.6, .5), "undetermined")
    report = m2_report.build(tmp_path, campaign, gates={}, now=NOW)
    assert report["gemini_cost_evidence"]["legacy_trusted_attempts"] == 0
    assert report["undetermined_gemini_attempts"] == 1
    assert report["costs"]["gemini.tier1"]["groups"]["all"]["tokens"]["total"] is None
    report_attempt(tmp_path, "trusted", ultra(), ultra(.88, .77))
    report = m2_report.build(tmp_path, campaign, gates={}, now=NOW)
    assert report["gemini_cost_evidence"]["legacy_trusted_attempts"] == 1
    assert (report["trusted_gemini_attempts"], report["undetermined_gemini_attempts"]) == (1, 1)
    costs = report["costs"]["gemini.tier1"]
    assert costs["groups"]["all"]["argon_fraction"]["total"] == pytest.approx(.02)
    assert costs["groups"]["all"]["tokens"]["total"] == 20
    assert costs["groups"]["all"]["seconds"]["total"] == 1
    report_attempt(tmp_path, "legacy", _qsnap(.9), _qsnap(.85), qid=2)
    report_attempt(tmp_path, "cross", _qsnap(.9), ultra(.8), qid=2)
    report = m2_report.build(tmp_path, campaign, gates={}, now=NOW)
    assert (report["trusted_gemini_attempts"], report["undetermined_gemini_attempts"]) == (2, 2)
    costs = report["costs"]["gemini.tier1"]
    assert costs["groups"]["all"]["argon_fraction"]["total"] is None
    assert costs["quota_fraction_by_layout"]["ultra_shared"]["total"] == pytest.approx(.02)
    assert costs["quota_fraction_by_layout"]["legacy_argon"]["total"] == pytest.approx(.05)


def test_report_gemini_cost_gate_uses_window_estimate(tmp_path):
    campaign = {"campaign_id": "report", "questions": [{"question_id": 1}]}
    # The fixture's Ultra windows start after NOW, so these calls fall outside them: no estimate.
    report_attempt(tmp_path, "outside", ultra(), ultra(.95, .7), "undetermined")
    report = m2_report.build(tmp_path, campaign, gates={}, now=NOW)
    assert not report["checks"]["gemini_window_cost_estimate"] and "gemini_window_cost_estimate" in report["pending"]
    assert "trusted_gemini_costs" not in report["checks"]
    # Calls inside a window with inconsistent (rising) reads still give a window-level estimate.
    other = tmp_path / "inside"
    other.mkdir()
    report_attempt(other, "a", ultra_window(.96, .9), ultra_window(.98, .95), "undetermined")
    report_attempt(other, "b", ultra_window(.97, .93), ultra_window(.96, .9), "undetermined")
    report = m2_report.build(other, {"campaign_id": "inside", "questions": [{"question_id": 1}]}, gates={}, now=NOW)
    assert report["checks"]["gemini_window_cost_estimate"]
    evidence = report["gemini_cost_evidence"]
    assert evidence["decision"] == "D97" and evidence["per_call_cost"].startswith("unobservable")
    [window] = evidence["windows"]
    assert window["readings"] == 4 and window["gemini_calls"] == 2
    assert window["mean_per_call"] == pytest.approx(.02) and window["used_lower_bound"] == pytest.approx(.04)


def test_report_gate_needs_two_distinct_readings_in_the_window(tmp_path):
    # The before snapshot is also the `current` reading: it must not count twice.
    append_record(tmp_path / "forecasts.jsonl", {"type": "forecast_attempt", "attempt_id": "solo", "question_id": 1,
        "question_snapshot_id": "q1", "player": "gemini", "tier": 1, "model_check": "undetermined",
        "attempt_kind": "initial", "usage": {"input_tokens": 10, "output_tokens": 10},
        "started_at_utc": to_iso(NOW), "finished_at_utc": to_iso(NOW + timedelta(seconds=1))})
    save_reading(tmp_path, ultra_window(.97, .9), aid="solo")
    report = m2_report.build(tmp_path, {"campaign_id": "solo", "questions": [{"question_id": 1}]}, gates={}, now=NOW)
    [window] = report["gemini_cost_evidence"]["windows"]
    assert window["readings"] == 1 and not report["checks"]["gemini_window_cost_estimate"]
    snap = ultra_window(.97, .9)
    rows = [{"attempt_id": "x", "when": "before", **snap.to_dict()}] * 3
    assert quota.window_usage([], rows, current=rows[0])["readings"] == 1


def test_forecast_unknown_snapshot_records_fixed_reason_without_nan(env, tmp_path):
    summary, records, adapter = env([], player="gemini", quota_snapshot=lambda **kw: ultra(weekly=float("nan")), quota_policy=POLICY)
    assert adapter.ran == 0 and not records
    assert summary["line_stopped"] == quota.UNKNOWN_LAYOUT_REASON
    rows = read_records(tmp_path / "a/quota.jsonl")
    assert rows[-1]["reason"] == quota.UNKNOWN_LAYOUT_REASON
    assert rows[0]["buckets"][quota.GEMINI_WEEKLY_BUCKET_ID]["remaining_fraction"] is None


@pytest.mark.parametrize("layout", ["legacy", "ultra"])
@pytest.mark.parametrize("gate", ["acceptance", "autoload"])
def test_manual_entrypoints_accept_both_layouts(tmp_path, monkeypatch, layout, gate):
    before = _qsnap(.9) if layout == "legacy" else ultra()
    monkeypatch.setattr(quota, "snapshot", lambda **kw: before)
    log = tmp_path / "acceptance.jsonl"
    if gate == "acceptance":
        monkeypatch.setattr(acceptance, "ACCEPTANCE_LOG", log)
        result = SimpleNamespace(completed=True, error_type=None, usage={}, run=None)
        observed, _ = acceptance._run(SimpleNamespace(run=lambda req: result), "gemini", "tier1", "synthetic",
            tmp_path, tmp_path, {"model": "gemini-4-argon-high", "effort": "high"}, 60, run_id="r")
        assert observed is result
    else:
        request = AdapterRequest(player="gemini", profile="tier1", prompt="synthetic", schema={}, workdir=tmp_path,
            raw_dir=tmp_path, model="gemini-4-argon-high", effort="high", timeout_s=60)
        autoload._start_call(request, "r", log)
    assert sum(r["type"] == "acceptance_call_started" for r in read_records(log)) == 1


@pytest.mark.parametrize('weekly_cap,remaining', [(.8, .34), (1.0, .14)])
def test_budget_reserves_tier1_capacity_in_shared_weekly_units(ledger, weekly_cap, remaining):
    ledger.policy['players']['gemini']['weekly_cap'] = weekly_cap
    attempt(ledger, player="gemini")
    save_pair(ledger.root, "old", ultra(), ultra(.85, .79))
    append_record(ledger.root / "quota.jsonl", {"attempt_id": "new", "when": "before", **ultra(weekly=remaining).to_dict()})
    decision = reserve(ledger, player="gemini", tier=2, pending=2)
    assert not decision["allowed"] and decision["reason"] == "argon_personal_or_tier1_reserve"
    assert decision["protected_attempts"] == 2


def test_budget_legacy_breaker_does_not_block_new_shared_plan(ledger):
    attempt(ledger, player="gemini")
    append_record(ledger.log, {"type": "budget_upgrade_stop", "player": "gemini", "reason": "weekly_tier2_argon_breaker",
        "until": quota.argon_buckets(ultra()).weekly.reset_time})
    append_record(ledger.root / "quota.jsonl", {"attempt_id": "new", "when": "before", **ultra().to_dict()})
    decision = reserve(ledger, player="gemini", tier=2)
    assert decision["allowed"]


def test_scheduler_wait_persists_across_restart_until_reset(tmp_path, monkeypatch):
    import scheduler as S
    from test_scheduler import campaign
    from test_forecast import SNAP
    # No model or temporary execution directory is needed: denial precedes both.
    snap = {**SNAP, "question_id": 100, "post_id": 100, "snapshot_id": "q100"}
    clock = [NOW]
    obj = S.Scheduler(tmp_path, campaign(1), snapshots=lambda: {100: snap},
        adapter_loader=lambda p: SimpleNamespace(run=lambda req: pytest.fail("model called")),
        preflight=lambda p, t, a: F.Preflight(p, tier=t), now=lambda: clock[0],
        acceptance_log=tmp_path / "acceptance.jsonl", context_extra=lambda p: {"quota_snapshot": lambda **kw: ultra(short=.01)})
    job = next(j for j in obj.jobs({100: snap}, paused=set(), active=set()) if j.player == "gemini")
    result = obj.execute(job)
    assert result["outcome"] == "quota_5h_wait" and not result["notify_louis"]
    assert result["quota_retry_at_utc"] == quota.argon_buckets(ultra()).five_hour.reset_time
    assert not any(j.player == "gemini" for j in obj.jobs({100: snap}, paused=set(), active=set()))
    clock[0] = NOW + timedelta(days=1)
    assert any(j.player == "gemini" for j in obj.jobs({100: snap}, paused=set(), active=set()))


def test_unknown_layout_cannot_verify_even_with_legacy_bucket_movement():
    before, after = _qsnap(.9), _qsnap(.89)
    buckets = dict(after.buckets)
    buckets[quota.GEMINI_WEEKLY_BUCKET_ID] = replace(buckets[quota.GEMINI_WEEKLY_BUCKET_ID], group_name="Unknown Gemini Group")
    after = replace(after, buckets=buckets)
    assert F.quota_model_check(before, after, SimpleNamespace(usage_raw={})) == "undetermined"


def test_reconciliation_preserves_budget_five_hour_wait(ctx):
    import reconcile as R
    from test_reconcile import SNAP
    ctx.player = "gemini"
    ctx.preflight = F.Preflight("gemini", tier=2)
    ctx.quota_policy = POLICY
    ctx.quota_snapshot = lambda **kw: ultra()
    retry = quota.argon_buckets(ultra()).five_hour.reset_time
    result = R.run(ctx, SNAP, admit=lambda req: {"allowed": False, "reason": "quota_5h_wait", "quota_retry_at_utc": retry})
    assert result["outcome"] == "quota_5h_wait" and result["quota_retry_at_utc"] == retry
    assert not result["notify_louis"] and not ctx.adapter.requests


def idle_pair():
    before, after = ultra(), replace(ultra(.89994, .79977), taken_at=to_iso(NOW+timedelta(seconds=144)))
    def other(snap):
        buckets=dict(snap.buckets)
        for key, seconds in ((quota.THIRD_PARTY_WEEKLY_BUCKET_ID,7*86400),(quota.THIRD_PARTY_5H_BUCKET_ID,5*3600)):
            buckets[key]=replace(buckets[key],remaining_fraction=1.0,
                reset_time=to_iso(quota._parse_iso_safe(snap.taken_at)+timedelta(seconds=seconds-3)))
        return replace(snap,buckets=buckets)
    return other(before),other(after)


def test_idle_full_other_groups_moving_future_reset_does_not_hide_gemini_use():
    before,after=idle_pair()
    assert quota.shared_group_consumed(before,after)
    result=SimpleNamespace(usage_raw={'log_model_check':'verified'})
    assert F.quota_model_check(before,after,result)=='verified'
    assert quota.quota_drop(before,after)==pytest.approx(.00006)
    # No ID evidence cannot be upgraded by quota shape alone.
    assert F.quota_model_check(before,after,SimpleNamespace(usage_raw={}))=='undetermined'


@pytest.mark.parametrize('problem',['used_before','used_both','decrease','increase','missing_timestamp','reversed',
    'missing_reset','invalid_reset','crossed_reset','wrong_window','far_future','reset_backwards','gemini_reset',
    'gemini_zero','concurrent','stale','missing_bucket'])
def test_idle_reset_exception_remains_narrow(problem):
    before,after=idle_pair()
    a,b=dict(before.buckets),dict(after.buckets)
    key=quota.THIRD_PARTY_WEEKLY_BUCKET_ID
    if problem=='used_before': a[key]=replace(a[key],remaining_fraction=.99)
    elif problem=='used_both':
        a[key]=replace(a[key],remaining_fraction=.99);b[key]=replace(b[key],remaining_fraction=.99)
    elif problem=='decrease': b[key]=replace(b[key],remaining_fraction=.999999999)
    elif problem=='increase': a[key]=replace(a[key],remaining_fraction=.999999999)
    elif problem=='missing_timestamp': before=replace(before,taken_at=None)
    elif problem=='reversed': after=replace(after,taken_at=to_iso(NOW-timedelta(seconds=1)))
    elif problem=='missing_reset': b[key]=replace(b[key],reset_time=None)
    elif problem=='invalid_reset': b[key]=replace(b[key],reset_time='bad')
    elif problem=='crossed_reset': a[key]=replace(a[key],reset_time=to_iso(NOW+timedelta(seconds=20)))
    elif problem=='wrong_window': b[key]=replace(b[key],reset_time=to_iso(NOW+timedelta(hours=5,seconds=141)))
    elif problem=='far_future': b[key]=replace(b[key],reset_time=to_iso(NOW+timedelta(days=30)))
    elif problem=='reset_backwards': b[key]=replace(b[key],reset_time=to_iso(NOW+timedelta(days=7,seconds=-10)))
    elif problem=='gemini_reset':
        k=quota.GEMINI_WEEKLY_BUCKET_ID;b[k]=replace(b[k],reset_time=to_iso(NOW+timedelta(days=8)))
    elif problem=='gemini_zero':
        k=quota.GEMINI_5H_BUCKET_ID;b[k]=replace(b[k],remaining_fraction=a[k].remaining_fraction)
    elif problem=='concurrent': after=replace(after,concurrent_agy_kinds=('ide',))
    elif problem=='stale': after=replace(after,stale=True)
    elif problem=='missing_bucket': b.pop(key)
    before,after=replace(before,buckets=a),replace(after,buckets=b)
    assert not quota.shared_group_consumed(before,after)
    assert F.quota_model_check(before,after,SimpleNamespace(usage_raw={'log_model_check':'verified'}))=='undetermined'


def test_idle_query_without_model_use_never_proves_model_or_cost():
    before,after=idle_pair();b=dict(after.buckets)
    for key in (quota.GEMINI_WEEKLY_BUCKET_ID,quota.GEMINI_5H_BUCKET_ID):
        b[key]=replace(b[key],remaining_fraction=before.buckets[key].remaining_fraction)
    assert not quota.shared_group_consumed(before,replace(after,buckets=b))


def test_model_mismatch_still_dominates_idle_reset():
    before,after=idle_pair()
    assert F.quota_model_check(before,after,SimpleNamespace(usage_raw={'log_model_check':'mismatch'}))=='mismatch'
