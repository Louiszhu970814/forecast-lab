from __future__ import annotations

import copy
import threading
import time
from datetime import timedelta

import pytest

import forecast as F
import scheduler as S
from adapters.base import AdapterResult
from flab.config import load_quota_policy, load_run_config
from flab.ids import hash_obj
from flab.jsonl import append_record, read_records
from flab.timeutil import to_iso
from test_forecast import NOW, SNAP, _good, _qsnap


def campaign(count=2):
    return {"version": "m2_campaign_v1", "campaign_id": "synthetic", "questions": [
        {"question_id": 100 + i, "ground": "bot_testing_area", "priority": "main"} for i in range(count)],
        "budget_window_start": to_iso(NOW - timedelta(days=1)), "budget_window_end": to_iso(NOW + timedelta(days=6)),
        "weekly_attempt_caps": dict.fromkeys(("claude", "gemini", "gpt"), 100),
        "expected_new_questions": 0, "repair_reserve": 1, "urgent_slack_s": 120,
        "run_config_sha256": hash_obj(load_run_config()), "quota_policy_sha256": hash_obj(load_quota_policy())}


class Adapter:
    def __init__(self, player, tracker):
        self.player, self.tracker = player, tracker

    def run(self, req, **kw):
        with self.tracker["lock"]:
            assert self.player not in self.tracker["active"]
            self.tracker["active"].add(self.player)
            self.tracker["max"] = max(self.tracker["max"], len(self.tracker["active"]))
            self.tracker["calls"].append((self.player, req.profile, req.schema["properties"]["question_id"]["const"]))
        time.sleep(.005)
        qid = req.schema["properties"]["question_id"]["const"]
        probability = {"claude": .2, "gemini": .8, "gpt": .5}[self.player] if req.profile == "tier1" else .5
        output = _good(question_id=qid, probability=probability, change_reason="The verified evidence resolves the dispute." if req.profile == "tier2" else None)
        (req.raw_dir / "stdout.jsonl").write_text("{}\n")
        with self.tracker["lock"]:
            self.tracker["active"].remove(self.player)
        return AdapterResult(argv=["fake"], run=None, cli_version="fake", completed=True, final_text=None,
            structured=output, reported_model_id=req.model, model_check="verified", usage_raw={},
            usage={"input_tokens": 20, "output_tokens": 10, "reasoning_tokens": None, "cached_tokens": None})

    def apply_quota_model_check(self, result, check):
        result.model_check = check


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(F.quota, "judge", lambda a, b: "verified")
    monkeypatch.setattr(S, "load_players_enabled", lambda: dict.fromkeys(("claude", "gemini", "gpt"), True))
    tracker = {"lock": threading.Lock(), "active": set(), "max": 0, "calls": []}
    counter = {"quota": 0, "refresh": 0}
    def make(count=2, **kw):
        cfg = campaign(count)
        snaps = {q["question_id"]: {**SNAP, "question_id": q["question_id"], "post_id": q["question_id"],
            "snapshot_id": f"snap_{q['question_id']}"} for q in cfg["questions"]}
        def provider():
            counter["refresh"] += 1
            return copy.deepcopy(snaps)
        def quota(**_):
            counter["quota"] += 1
            q = _qsnap(.9 - counter["quota"] * .001)
            from dataclasses import replace
            from quota import BucketQuota, ARGON_BUCKET_ID
            return replace(q, taken_at=to_iso(NOW), buckets={**q.buckets, ARGON_BUCKET_ID:
                BucketQuota(ARGON_BUCKET_ID, .9 - counter["quota"] * .001, reset_time=to_iso(NOW + timedelta(days=3)))})
        def extra(player):
            return {"quota_snapshot": quota}
        obj = S.Scheduler(tmp_path, cfg, snapshots=provider, adapter_loader=lambda p: Adapter(p, tracker),
            preflight=kw.pop("preflight", lambda p, t, a: F.Preflight(p, tier=t)),
            context_extra=extra, now=lambda: NOW, acceptance_log=tmp_path / "acceptance.jsonl", **kw)
        return obj, snaps, tracker, counter
    return make


def test_three_lines_parallel_breadth_first_then_tier2_and_comments(environment):
    scheduler, _, tracker, counter = environment()
    result = scheduler.run()
    assert result["jobs_completed"] == 12 and result["paused_players"] == []
    assert tracker["max"] == 3
    assert [c[1] for c in tracker["calls"][:6]] == ["tier1"] * 6
    assert [c[1] for c in tracker["calls"][6:]] == ["tier2"] * 6
    assert counter["refresh"] >= 12
    finals = [r for r in read_records(scheduler.root / "aggregates.jsonl") if r.get("completion_status") == "complete_local"]
    assert len(finals) == 2 and all(not r["submission_eligible"] for r in finals)
    comments = read_records(scheduler.root / "comments.jsonl")
    assert len(comments) == 2 and all(r["type"] == "comment" for r in comments)
    assert all(len(r["text"]) <= 3000 for r in comments)


def test_restart_of_completed_campaign_never_calls_models_again(environment):
    scheduler, _, tracker, _ = environment()
    scheduler.run()
    previous = len(tracker["calls"])
    result = scheduler.run()
    assert result["jobs_completed"] == 0 and len(tracker["calls"]) == previous


def test_preflight_blockers_stop_before_model_calls_and_do_not_grant_waivers(environment):
    scheduler, _, tracker, _ = environment(preflight=lambda p, t, a: F.Preflight(p, tier=t, problems=["baseline not approved"]))
    result = scheduler.run()
    assert set(result["paused_players"]) == {"claude", "gemini", "gpt"} and not tracker["calls"]


def test_no_high_acceptance_keeps_campaign_incomplete(environment):
    scheduler, _, tracker, _ = environment(preflight=lambda p, t, a: F.Preflight(p, tier=t, problems=["high unaccepted"] if t == 2 else []))
    result = scheduler.run()
    assert len(tracker["calls"]) == 6 and len(result["paused_players"]) == 3
    assert not (scheduler.root / "comments.jsonl").exists()


def test_registration_precedes_results_and_cannot_be_rewritten(environment):
    scheduler, _, _, _ = environment()
    S.register(scheduler.root, scheduler.campaign)
    modified = copy.deepcopy(scheduler.campaign)
    modified["weekly_attempt_caps"]["gpt"] += 1
    with pytest.raises(ValueError, match="cannot be changed"):
        S.register(scheduler.root, modified)


def test_new_campaign_cannot_be_selected_after_outputs_exist(environment):
    scheduler, _, _, _ = environment()
    append_record(scheduler.root / "forecasts.jsonl", {"question_id": 100})
    with pytest.raises(ValueError, match="precede forecasts"):
        S.register(scheduler.root, scheduler.campaign)


def test_attempt_cap_records_skips_without_blocking_other_lines(environment):
    scheduler, _, tracker, _ = environment()
    scheduler.campaign["weekly_attempt_caps"]["claude"] = 1
    result = scheduler.run()
    assert "claude" in result["paused_players"]
    assert result["jobs_completed"] == 10
    assert sum(p == "claude" for p, _, _ in tracker["calls"]) == 1
    assert sum(p == "gpt" for p, _, _ in tracker["calls"]) == 4
    assert any(r["outcome"] == "budget_wait" for r in read_records(scheduler.jobs_log))
    # The next round can account for Claude's still-pending tier-2 admission;
    # it also waits without spending a model call, then no further jobs are ready.
    count = len(tracker["calls"])
    assert scheduler.run()["jobs_completed"] == 1
    assert len(tracker["calls"]) == count
    assert scheduler.run()["jobs_completed"] == 0
    # D121: new temporary waits are not archived skips; unattempted questions
    # remain in the coverage denominator until a later decision or deadline.
    import m2_report
    line = m2_report.build(scheduler.root, scheduler.campaign, gates={})["lines"]["claude"]
    skipped = line["budget_skipped_tier1_questions"]
    assert not skipped and line["denominator"] == line["campaign_questions"]
    assert line["valid_fraction"] == line["selected_tier1_questions"] / line["denominator"]
    assert line["coverage_fraction"] == line["selected_tier1_questions"] / line["campaign_questions"]
    assert line["denominator"] == line["campaign_questions"]


def test_budget_skip_denominator_counts_started_and_invalid_attempts(tmp_path):
    import m2_report
    campaign = {"campaign_id": "d80", "questions": [{"question_id": q} for q in (1, 2, 3, 4)]}
    def job(q, outcome, campaign_id="d80"):
        append_record(tmp_path / "scheduler_jobs.jsonl", {"type": "scheduler_job_finished", "campaign_id": campaign_id,
                      "player": "gemini", "tier": 1, "question_id": q, "outcome": outcome})
    job(1, "budget_skipped")                       # never run: excluded
    job(2, "budget_skipped")                       # skipped, then started without a result: counted
    append_record(tmp_path / "forecast_attempt_starts.jsonl", {"player": "gemini", "tier": 1, "question_id": 2,
                  "attempt_id": "a2", "started_at_utc": "2026-11-01T00:00:00Z"})
    job(3, "budget_skipped")                       # an invalid attempt exists: counted
    append_record(tmp_path / "forecasts.jsonl", {"type": "forecast_attempt", "attempt_id": "a3", "question_id": 3,
                  "question_snapshot_id": "qs3", "prev_attempt_id": None, "player": "gemini", "tier": 1,
                  "validation_status": "schema_failed", "attempt_kind": "initial",
                  "started_at_utc": "2026-11-01T00:00:00Z", "finished_at_utc": "2026-11-01T00:01:00Z"})
    job(4, "selected", campaign_id="other")        # another campaign's skip does not apply
    job(4, "budget_skipped", campaign_id="other")
    line = m2_report.build(tmp_path, campaign, gates={})["lines"]["gemini"]
    assert line["budget_skipped_tier1_questions"] == [1]
    assert line["denominator"] == 3 and line["campaign_questions"] == 4
    job(1, "selected")                             # a later run supersedes the skip
    append_record(tmp_path / "forecast_attempt_starts.jsonl", {"player": "gemini", "tier": 1, "question_id": 1,
                  "attempt_id": "a1", "started_at_utc": "2026-11-01T01:00:00Z"})
    assert m2_report.build(tmp_path, campaign, gates={})["lines"]["gemini"]["denominator"] == 4


def test_priority_edf_and_urgent_minibench_override():
    def job(qid, priority, minutes, tier=1):
        return S.Job("claude", tier, qid, {}, priority, NOW + timedelta(minutes=minutes), 600)
    main, mini = job(1, "main", 40), job(2, "minibench", 11)
    result = S.order_jobs([main, mini], now=NOW, urgent_slack_s=120)
    assert result[0].question_id == 2 and result[0].urgent_override
    near_main = job(3, "main", 12)
    result = S.order_jobs([main, mini, near_main], now=NOW, urgent_slack_s=120)
    assert result[0].question_id == 3 and not result[0].urgent_override
    assert S.order_jobs([job(4, "main", 5, tier=2), main], now=NOW, urgent_slack_s=120)[0].tier == 1


def test_max_jobs_bounds_one_invocation_and_resume_can_continue(environment):
    scheduler, _, tracker, _ = environment()
    assert scheduler.run(max_jobs=2)["jobs_completed"] == 2
    assert len(tracker["calls"]) == 2
    assert scheduler.run()["jobs_completed"] == 10


def test_live_mode_is_refused(environment, monkeypatch):
    scheduler, _, tracker, _ = environment()
    monkeypatch.setattr(S, "load_submit_mode", lambda: {"mode": "live"})
    with pytest.raises(F.PreflightError):
        scheduler.run()
    assert not tracker["calls"]


def test_restart_recovers_comment_after_aggregate_was_written(environment, monkeypatch):
    obj, _, tracker, _ = environment(count=1)
    original = obj._comment
    monkeypatch.setattr(obj, "_comment", lambda *a: None)
    obj.run()
    calls = len(tracker["calls"])
    monkeypatch.setattr(obj, "_comment", original)
    assert obj.run()["jobs_completed"] == 0
    assert len(tracker["calls"]) == calls
    assert len(read_records(obj.root / "comments.jsonl")) == 1


def test_twenty_question_report_is_not_real_acceptance(environment):
    import m2_report
    obj, snaps, tracker, _ = environment(count=20)
    for snap in snaps.values():
        append_record(obj.root / "questions.jsonl", {"type": "question_snapshot", **snap})
    result = obj.run()
    assert result["jobs_completed"] == 120
    report = m2_report.build(obj.root, obj.campaign, gates={})
    assert report["checks"]["at_least_20_questions"]
    assert report["checks"]["all_three_tier1_at_least_90_percent"]
    assert report["checks"]["all_questions_finalized_and_commented"]
    assert not report["m2_complete"]
    assert "baseline_and_autoload_approved" in report["pending"]
    assert "all_cli_quota_exhaustion_observed" not in report["pending"]
    assert not report["quota_exhaustion_evidence"]["all_cli_quota_exhaustion_observed"]
    assert set(report["quota_exhaustion_evidence"]["unobserved_players"]) == {"claude", "gemini", "gpt"}
    assert report["costs"]["claude.tier1"]["groups"]["all"]["tokens"]["total"] == 600
    assert report["costs"]["gemini.tier2"]["groups"]["all"]["argon_fraction"]["total"] == pytest.approx(.02)
    assert "probability" not in str(report) and "The verified evidence" not in str(report)
    # Failed repair consumes native tokens and belongs in the per-question total.
    rows = list(read_records(obj.root / "forecasts.jsonl"))
    extra = {**next(r for r in rows if r["player"] == "claude" and r["tier"] == 1),
             "attempt_id": "failed_repair_cost", "record_id": "failed_repair_record", "attempt_kind": "format_repair",
             "validation_status": "invalid", "error_type": "parse_failed"}
    append_record(obj.root / "forecasts.jsonl", extra)
    updated = m2_report.build(obj.root, obj.campaign, gates={})
    assert updated["costs"]["claude.tier1"]["groups"]["all"]["tokens"]["total"] == 630
    append_record(obj.root / "audits.jsonl", {"type": "audit_update", "audit_event_id": "late-m2-test",
        "attempt_id": rows[0]["attempt_id"], "audited_at": to_iso(NOW), "audit_flags": ["leak_confirmed"], "audit_reasons": ["synthetic"]})
    late = m2_report.build(obj.root, obj.campaign, gates={})
    assert late["checks"]["all_three_tier1_at_least_90_percent"]
    assert not late["checks"]["all_questions_finalized_and_commented"]


def test_midrun_config_change_stops_before_cli(environment, monkeypatch):
    obj, snaps, tracker, _ = environment(count=1)
    job = obj.jobs(snaps, paused=set(), active=set())[0]
    changed = copy.deepcopy(obj.cfg)
    changed["timeouts_s"]["tier1"] += 1
    monkeypatch.setattr(S, "load_run_config", lambda: changed)
    result = obj.execute(job)
    assert result["outcome"] == "line_stopped" and "configuration changed" in result["reason"]
    assert not tracker["calls"]


def _approved_report_gates():
    # Synthetic report-policy fixture only; never production acceptance evidence.
    return {"baseline_approved_id":"synthetic-approval","autoload_passed":True,
        "production_acceptance":{f"{p}.tier{t}":{"ok":True} for p in ("claude","gemini","gpt") for t in (1,2)},
        "acceptance_quota_error_players":[]}


def _completed_report_campaign(environment):
    obj,snaps,tracker,_=environment(count=20)
    for snap in snaps.values():append_record(obj.root/"questions.jsonl",{"type":"question_snapshot",**snap})
    obj.run()
    append_record(obj.root/"campaign_safety.jsonl", {"campaign_id":obj.campaign["campaign_id"],"synthetic":True})
    return obj,tracker


def test_passive_v5_does_not_block_complete_report_or_change_ledgers(environment):
    import m2_report
    obj,tracker=_completed_report_campaign(environment)
    before={p:p.read_bytes() for p in obj.root.glob("*.jsonl")};calls=list(tracker["calls"])
    report=m2_report.build(obj.root,obj.campaign,gates=_approved_report_gates())
    assert report["m2_complete"] and report["pending"]==[]
    assert "all_cli_quota_exhaustion_observed" not in report["checks"]
    evidence=report["quota_exhaustion_evidence"]
    assert evidence["policy"]=="passive_observation_v1" and evidence["decision"]=="D89"
    assert evidence["blocks_m2"] is False and evidence["all_cli_quota_exhaustion_observed"] is False
    assert not evidence["observed_players"] and len(evidence["unobserved_players"])==3
    assert all(not line["quota_exhaustion_observed"] for line in report["lines"].values())
    assert {p:p.read_bytes() for p in obj.root.glob("*.jsonl")}==before and tracker["calls"]==calls


@pytest.mark.parametrize("gap",["gemini_window_cost_estimate","production_acceptance_current","no_unknown_started_attempts"])
def test_passive_v5_preserves_other_blocking_gates(environment,monkeypatch,gap):
    import m2_report
    obj,_=_completed_report_campaign(environment);gates=_approved_report_gates()
    if gap=="gemini_window_cost_estimate":monkeypatch.setattr(m2_report.quota,"window_usage",lambda *args,**kwargs:None)
    elif gap=="production_acceptance_current":gates["production_acceptance"]["gpt.tier2"]["ok"]=False
    else:append_record(obj.root/"forecast_attempt_starts.jsonl",{"attempt_id":"unknown-start","question_id":100,"player":"gpt","tier":2})
    report=m2_report.build(obj.root,obj.campaign,gates=gates)
    assert not report["m2_complete"] and gap in report["pending"]
    assert not report["quota_exhaustion_evidence"]["blocks_m2"]


@pytest.mark.parametrize("observed",[[],["claude"],["claude","gemini","gpt"]])
def test_passive_v5_reports_observed_and_missing_without_inventing_evidence(tmp_path,observed):
    import m2_report
    report=m2_report.build(tmp_path,{"campaign_id":"v5-synthetic","questions":[{"question_id":100}]},
        gates={"acceptance_quota_error_players":observed})
    evidence=report["quota_exhaustion_evidence"]
    assert set(evidence["observed_players"])==set(observed)
    assert set(evidence["unobserved_players"])=={"claude","gemini","gpt"}-set(observed)
    assert evidence["all_cli_quota_exhaustion_observed"]==(len(observed)==3)
    assert all(line["quota_exhaustion_observed"]==(p in observed) for p,line in report["lines"].items())


def test_passive_v5_keeps_runtime_quota_errors_as_observed_evidence(tmp_path):
    import m2_report
    append_record(tmp_path/"forecasts.jsonl",{"type":"forecast_attempt","attempt_id":"quota-failure",
        "question_id":100,"question_snapshot_id":"qs100","prev_attempt_id":None,"player":"gemini","tier":1,
        "validation_status":"invalid","attempt_kind":"initial","error_type":"quota_exhausted",
        "started_at_utc":to_iso(NOW),"finished_at_utc":to_iso(NOW)})
    report=m2_report.build(tmp_path,{"campaign_id":"v5-synthetic","questions":[{"question_id":100}]},gates={})
    assert report["quota_exhaustion_evidence"]["observed_players"]==["gemini"]
    assert report["lines"]["gemini"]["errors"]=={"quota_exhausted":1}
