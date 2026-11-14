from __future__ import annotations

import copy
import json
from datetime import timedelta

import pytest

import coverage_sim as CS
import scheduler as S
from flab.config import PLAYERS
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import parse_iso, to_iso
from test_forecast import NOW, SNAP


def at(seconds):
    return to_iso(NOW + timedelta(seconds=seconds))


def settings():
    return {"version": CS.VERSION, "sim_id": "synthetic", "schedule_file": "schedule.json",
            "observation": {"start": at(0), "end": at(10800)},
            "denominator": {"venues": ["main", "minibench"], "question_ids": "all"},
            "seed": 10, "repetitions": 1, "expected_new_questions": 0, "repair_reserve": 0,
            "urgent_slack_s": 120, "escalation_rate": 0, "startup_s": 0, "poll_s": 0,
            "submission": {"comment_s": 2, "precheck_s": 2, "submit_s": 1},
            "players": {p: {"tier1": [60], "tier2": [120], "repair": [10], "overhead_s": 0,
                "format_repair_rate": 0, "tier1_rerun_rate": 0, "tier2_rerun_rate": 0,
                "rerun_failure_rate": 0, "repair_failure_rate": 0, "attempt_cap": 1000,
                "reset_times": [], "stops": []} for p in PLAYERS}}


def question(qid=1, *, opening=0, closing=10800, venue="main", kind="binary", revisions=()):
    return {"question_id": qid, "venue": venue, "question_type": kind, "open_time": at(opening),
            "scheduled_close_time": at(closing), "revisions": list(revisions)}


def replay(tmp_path, questions=None, cfg=None):
    record = CS.register(tmp_path, cfg or settings(), questions if questions is not None else [question()], now=lambda: NOW)
    return CS.simulate(record)["trials"][0]


def test_completion_relative_trigger_includes_work_and_idle_gap(tmp_path):
    cfg = settings()
    cfg["cycle_trigger"] = "after_completion"
    result = replay(tmp_path, [question(), question(2, opening=300)], cfg)
    cycles = result["cycles"]
    assert result["overlap_skipped"] == 0
    assert len(cycles) > 1
    for first, second in zip(cycles, cycles[1:]):
        assert parse_iso(second["started_at"]) - parse_iso(first["finished_at"]) == timedelta(seconds=600)
    assert cycles[1]["started_at"] == at(665)
    assert result["questions"][1]["seen_at"] == at(665)


def test_completion_relative_trigger_includes_supervisor_cleanup(legacy_forecast_timeouts, tmp_path, monkeypatch):
    cfg = settings()
    cfg["cycle_trigger"] = "after_completion"
    cfg["startup_s"] = 5200
    result = replay(tmp_path, cfg=cfg)
    first, second = result["cycles"][:2]
    assert first["status"] == "timeout"
    assert first["duration_s"] == CS.engine_policy()["timing_limits"]["supervisor_timeout_s"]
    assert parse_iso(second["started_at"]) - parse_iso(first["finished_at"]) == timedelta(seconds=600)
    assert result["overlap_skipped"] == 0


def test_trigger_choice_is_validated_and_frozen(tmp_path):
    cfg = settings()
    cfg["cycle_trigger"] = "after_completion"
    CS.register(tmp_path, cfg, [question()], now=lambda: NOW)
    cfg["cycle_trigger"] = "fixed_grid"
    with pytest.raises(ValueError, match="cannot change"):
        CS.register(tmp_path, cfg, [question()], now=lambda: NOW)
    cfg["cycle_trigger"] = "unknown"
    with pytest.raises(ValueError, match="cycle_trigger"):
        CS.validate(cfg, [question()])


def test_empty_queue_single_question_parallel_and_on_time(tmp_path):
    result = replay(tmp_path)
    assert result["coverage_all"] == result["coverage_supported"] == 1
    assert result["supported_meets_90_percent"] is True
    assert result["questions"][0]["submitted_at"] == at(65)
    assert len(result["attempts"]) == 3
    assert {a["started_at_utc"] for a in result["attempts"]} == {at(0)}
    assert result["open_to_tier1_s"]["max"] == result["queue_wait_s"]["max"] == 0
    assert all(v == pytest.approx(60 / 10800) for v in result["utilization"].values())


def test_burst_queues_per_line_and_misses_before_deadline(tmp_path):
    cfg = settings()
    for line in cfg["players"].values():
        line["tier1"] = [400]
    result = replay(tmp_path, [question(i, closing=2400) for i in range(1, 7)], cfg)
    assert 0 < result["covered"] < 6
    assert result["queue_wait_s"]["max"] >= 400
    assert result["peak_simultaneous_opens"] == result["peak_open_questions"] == 6
    assert result["miss_reasons"]["timeout"] > 0
    for p in PLAYERS:
        attempts = [a for a in result["attempts"] if a["player"] == p]
        assert all(a["finished_at_utc"] <= b["started_at_utc"] for a, b in zip(attempts, attempts[1:]))


def test_tier2_p90_gate_strictly_before_deadline(tmp_path):
    cfg = settings()
    cfg["escalation_rate"] = 1
    for line in cfg["players"].values():
        line["tier2"] = [600]
    result = replay(tmp_path, [question(closing=2220)], cfg)
    assert result["questions"][0]["on_time"]
    assert not result["questions"][0]["tier2_started"]
    assert len(result["job_decisions"]) == 3
    assert all(d["outcome"] == "not_admitted" for d in result["job_decisions"])


def test_tier2_success_and_fallback_when_it_fails(tmp_path):
    cfg = settings()
    cfg["escalation_rate"] = 1
    result = replay(tmp_path / "success", cfg=cfg)
    assert result["questions"][0]["with_tier2"]
    assert len(result["attempts"]) == 6
    for line in cfg["players"].values():
        line["tier2_rerun_rate"] = line["rerun_failure_rate"] = 1
    result = replay(tmp_path / "failure", cfg=cfg)
    assert result["questions"][0]["on_time"]
    assert not result["questions"][0]["with_tier2"]
    assert result["questions"][0]["tier2_started"]


def test_long_cycle_skips_tick_and_defers_new_question(tmp_path):
    cfg = settings()
    cfg["escalation_rate"] = 1
    for line in cfg["players"].values():
        line["tier1"], line["tier2"] = [100], [1000]
    result = replay(tmp_path, [question(), question(2, opening=200)], cfg)
    second = result["questions"][1]
    assert second["seen_at"] == at(220)
    assert second["first_tier1_at"] == at(1200)
    assert result["overlap_skipped"] >= 1
    assert result["cycles_over_600_ratio"] > 0
    assert any(c["started_at"] == at(600) and c["status"] == "overlap_skipped" for c in result["cycles"])


def test_job_completion_refresh_sees_new_question_within_admission(tmp_path):
    result = replay(tmp_path, [question(), question(2, opening=30)])
    assert result["questions"][1]["seen_at"] == at(60)
    assert result["questions"][1]["first_tier1_at"] == at(65)


def test_idle_cycle_does_not_poll_until_next_tick(tmp_path):
    result = replay(tmp_path, [question(opening=20)])
    assert result["questions"][0]["seen_at"] == at(600)
    assert result["open_to_tier1_s"]["mean"] == 580


def test_format_repair_and_single_rerun_chain(tmp_path):
    cfg = settings()
    for line in cfg["players"].values():
        line["format_repair_rate"] = line["tier1_rerun_rate"] = 1
    result = replay(tmp_path, cfg=cfg)
    for p in PLAYERS:
        attempts = [a for a in result["attempts"] if a["player"] == p]
        assert [a["attempt_kind"] for a in attempts] == ["initial", "format_repair", "rerun", "format_repair"]
        assert attempts[-1]["outcome"] == "valid"
    assert result["coverage_supported"] == 1


def test_pending_repair_continues_next_cycle_without_new_base(tmp_path):
    cfg = settings()
    for line in cfg["players"].values():
        line["tier1"] = [550]
        line["format_repair_rate"] = 1
    result = replay(tmp_path, cfg=cfg)
    for p in PLAYERS:
        attempts = [a for a in result["attempts"] if a["player"] == p]
        assert [a["attempt_kind"] for a in attempts] == ["initial", "format_repair"]
        assert attempts[1]["started_at_utc"] == at(600)
    assert result["coverage_supported"] == 1


def test_timeout_has_only_one_rerun_per_snapshot(legacy_forecast_timeouts, tmp_path):
    cfg = settings()
    for line in cfg["players"].values():
        line["tier1"] = [900]
    result = replay(tmp_path, cfg=cfg)
    assert len(result["attempts"]) == 6
    assert all(a["outcome"] == "timeout" for a in result["attempts"])
    assert result["miss_reasons"] == {"timeout": 1}


def test_revision_with_time_invalidates_and_restarts_all_lines(tmp_path):
    result = replay(tmp_path, [question(revisions=[{"time": at(30), "structural": False}])])
    assert result["questions"][0]["revision_actions"][0]["action"] == "requeued"
    for p in PLAYERS:
        assert {a["generation"] for a in result["attempts"] if a["player"] == p} == {0, 1}
    assert result["questions"][0]["on_time"]
    assert result["questions"][0]["submitted_at"] == at(125)


@pytest.mark.parametrize("structural,expected", [(False, "text_fallback"), (True, "blocked_revision")])
def test_late_revision_fallback_or_block(tmp_path, structural, expected):
    cfg = settings()
    for line in cfg["players"].values():
        line["tier1"] = [400]
    result = replay(tmp_path, [question(closing=2400, revisions=[{"time": at(300), "structural": structural}])], cfg)
    row = result["questions"][0]
    assert row["revision_actions"][0]["action"] == expected
    assert row["on_time"] is not structural
    assert row["revision_fallback"] is not structural
    if structural:
        assert row["reason"] == "blocked_revision"
    assert len(result["attempts"]) == 3


def test_revision_after_submission_never_repeats_forecast(tmp_path):
    result = replay(tmp_path, [question(revisions=[{"time": at(500), "structural": True}])])
    assert len(result["attempts"]) == 3
    assert result["questions"][0]["revision_actions"][0]["action"] == "already_submitted"


@pytest.mark.parametrize("reason", ["quota_exhausted", "disabled", "preflight_blocked"])
def test_stopped_lines_miss_with_explicit_reason(tmp_path, reason):
    cfg = settings()
    for line in cfg["players"].values():
        line["stops"] = [{"start": at(0), "end": at(12000), "reason": reason}]
    result = replay(tmp_path, cfg=cfg)
    assert not result["attempts"]
    assert result["questions"][0]["reason"] == reason


def test_quota_stop_ends_at_explicit_reset(tmp_path):
    cfg = settings()
    for line in cfg["players"].values():
        line["stops"] = [{"start": at(0), "reason": "quota_exhausted"}]
        line["reset_times"] = [at(600)]
    result = replay(tmp_path, cfg=cfg)
    assert result["questions"][0]["first_tier1_at"] == at(600)
    assert result["questions"][0]["on_time"]


def test_attempt_caps_reset_and_tier2_protects_repair_capacity(tmp_path):
    cfg = settings()
    cfg["escalation_rate"] = 1
    cfg["repair_reserve"] = 1
    for line in cfg["players"].values():
        line["attempt_cap"] = 2
        line["reset_times"] = [at(600)]
    result = replay(tmp_path, [question(), question(2, opening=600)], cfg)
    assert result["covered"] == 2
    assert all(a["tier"] == 1 for a in result["attempts"])
    assert len(result["attempts"]) == 6


def test_budget_skipped_is_not_quota_exhausted(tmp_path):
    cfg = settings()
    for line in cfg["players"].values():
        line["attempt_cap"] = 0
    result = replay(tmp_path, cfg=cfg)
    assert result["miss_reasons"] == {"budget_skipped": 1}


def test_single_disabled_line_still_allows_aggregate(tmp_path):
    cfg = settings()
    cfg["players"]["gemini"]["stops"] = [{"start": at(0), "end": at(12000), "reason": "disabled"}]
    result = replay(tmp_path, cfg=cfg)
    assert result["covered"] == 1
    assert {a["player"] for a in result["attempts"]} == {"claude", "gpt"}


def test_unsupported_stays_in_all_denominator_and_window_is_half_open(tmp_path):
    questions = [question(), question(2, kind="numeric"), question(3, opening=-1), question(4, opening=10800, closing=21600)]
    result = replay(tmp_path, questions)
    assert result["denominator_all"] == 2 and result["denominator_supported"] == 1
    assert result["coverage_all"] == .5 and result["coverage_supported"] == 1
    assert result["supported_meets_90_percent"] is True
    assert result["questions"][1]["reason"] == "unsupported_type"


def test_fixed_venues_ids_and_empty_supported_denominator(tmp_path):
    cfg = settings()
    cfg["denominator"] = {"venues": ["minibench"], "question_ids": [2]}
    result = replay(tmp_path, [question(), question(2, venue="minibench", kind="discrete")], cfg)
    assert result["denominator_all"] == 1 and result["denominator_supported"] == 0
    assert result["coverage_supported"] is None and result["supported_meets_90_percent"] is None
    result = replay(tmp_path / "empty", [], settings())
    assert result["denominator_all"] == 0 and result["coverage_all"] is None


def test_tail_after_observation_end_is_not_censored(tmp_path):
    cfg = settings()
    cfg["observation"]["end"] = at(600)
    result = replay(tmp_path, [question(opening=590)], cfg)
    assert result["questions"][0]["submitted_at"] == at(665)
    assert result["coverage_supported"] == 1
    assert result["utilization"] == dict.fromkeys(PLAYERS, 0)


def test_submission_timeout_is_miss_not_success(tmp_path):
    cfg = settings()
    cfg["submission"]["submit_s"] = 400
    result = replay(tmp_path, cfg=cfg)
    assert result["miss_reasons"] == {"timeout": 1}
    assert result["questions"][0]["submitted_at"] is None


def test_submission_within_extended_bound_can_finish(tmp_path):
    cfg = settings()
    cfg["submission"]["submit_s"] = 200
    result = replay(tmp_path, cfg=cfg)
    assert result["covered"] == 1 and result["questions"][0]["submitted_at"] == at(264)


def test_submission_waits_for_cycle_and_can_be_late(tmp_path):
    cfg = settings()
    cfg["submission_policy"] = "cycle_end"
    cfg["escalation_rate"] = 1
    for line in cfg["players"].values():
        line["tier1"] = [100]
        line["tier2"] = [1200]
    result = replay(tmp_path, [question(closing=2150), question(2)], cfg)
    assert result["questions"][0]["reason"] == "late"
    assert result["questions"][1]["on_time"]


def test_supervisor_caps_hung_startup_and_accounts_overlap(tmp_path):
    cfg = settings()
    cfg["startup_s"] = 10000
    result = replay(tmp_path, cfg=cfg)
    timeout_cycles = [c for c in result["cycles"] if c["status"] == "timeout"]
    assert timeout_cycles
    assert all(c["duration_s"] == CS.engine_policy()["timing_limits"]["supervisor_timeout_s"] for c in timeout_cycles)
    assert result["overlap_skipped"] > 0
    assert not result["attempts"]


def test_frozen_settings_and_schedule_cannot_change(tmp_path):
    cfg, schedule = settings(), [question()]
    record = CS.register(tmp_path, cfg, schedule, now=lambda: NOW)
    before = (tmp_path / "coverage_sims.jsonl").read_bytes()
    assert CS.register(tmp_path, cfg, schedule) == record
    assert (tmp_path / "coverage_sims.jsonl").read_bytes() == before
    cfg["seed"] += 1
    with pytest.raises(ValueError, match="cannot change"):
        CS.register(tmp_path, cfg, schedule)
    cfg["seed"] -= 1
    schedule[0]["open_time"] = at(1)
    with pytest.raises(ValueError, match="cannot change"):
        CS.register(tmp_path, cfg, schedule)


def test_hash_mismatch_and_conflicting_duplicate_rejected(tmp_path):
    record = CS.register(tmp_path, settings(), [question()])
    tampered = copy.deepcopy(record)
    tampered["frozen"]["schedule"][0]["question_id"] = 10
    with pytest.raises(ValueError, match="hash mismatch"):
        CS.simulate(tampered)
    append_record(tmp_path / "coverage_sims.jsonl", tampered)
    with pytest.raises(ValueError, match="hash mismatch"):
        CS.run_registered(tmp_path, "synthetic")
    assert not (tmp_path / "coverage_sim_results.jsonl").exists()


def test_archived_results_block_before_simulation(tmp_path, monkeypatch):
    CS.register(tmp_path, settings(), [question()])
    (tmp_path / "coverage_sim_results.jsonl.archived.json").write_text("{}")
    monkeypatch.setattr(CS, "simulate", lambda _: pytest.fail("must not simulate archived history"))
    with pytest.raises(ValueError, match="restore them before appending"):
        CS.run_registered(tmp_path, "synthetic")
    assert not (tmp_path / "coverage_sim_results.jsonl").exists()


def test_archive_during_simulation_blocks_append(tmp_path, monkeypatch):
    CS.register(tmp_path, settings(), [question()])
    def simulated(_):
        (tmp_path / "coverage_sim_results.jsonl.archived.json").write_text("{}")
        return {}
    monkeypatch.setattr(CS, "simulate", simulated)
    with pytest.raises(ValueError, match="archived during simulation"):
        CS.run_registered(tmp_path, "synthetic")
    assert not (tmp_path / "coverage_sim_results.jsonl").exists()


def test_same_seed_reproducible_and_repetitions_have_quantiles(tmp_path):
    cfg = settings()
    cfg["repetitions"] = 3
    cfg["escalation_rate"] = .6
    for line in cfg["players"].values():
        line["tier1"] = [60, 130, 400]
        line["format_repair_rate"] = .3
        line["tier1_rerun_rate"] = .3
    record = CS.register(tmp_path, cfg, [question(i) for i in range(1, 5)])
    first = CS.run_registered(tmp_path, "synthetic", now=lambda: NOW)
    before = (tmp_path / "coverage_sim_results.jsonl").read_bytes()
    second = CS.run_registered(tmp_path, "synthetic", now=lambda: NOW)
    assert first == second == CS.simulate(record)
    assert len(first["trials"]) == 3
    assert first["summary"]["coverage_all"]["count"] == 3
    assert first["summary"]["coverage_all"]["p90"] is not None
    assert first["trials"][0] != first["trials"][1]
    assert (tmp_path / "coverage_sim_results.jsonl").read_bytes() == before + before
    assert len(read_complete(tmp_path / "coverage_sim_results.jsonl")) == 2


@pytest.mark.parametrize("urgent", [False, True])
def test_next_batch_matches_scheduler_jobs_in_same_state(tmp_path, monkeypatch, urgent):
    cfg = settings()
    closing = 2000 if urgent else 10800
    questions = [question(1), question(2, closing=closing, venue="minibench"), question(3, closing=10000)]
    record = CS.register(tmp_path, cfg, questions)
    sim = CS.Simulation(record["frozen"], cfg["seed"])
    sim.paused = set()
    for q in sim.questions.values():
        q["seen"] = NOW
    snaps = {q["question_id"]: {**SNAP, **q, "snapshot_id": f"s{q['question_id']}", "supported": True, "status": "open"} for q in questions}
    campaign = {"campaign_id": "comparison", "questions": [{"question_id": q["question_id"], "priority": q["venue"]} for q in questions],
                "urgent_slack_s": cfg["urgent_slack_s"]}
    scheduler = S.Scheduler(tmp_path / "scheduler", campaign, snapshots=lambda: snaps, now=lambda: NOW, continuous=True)
    monkeypatch.setattr(scheduler, "state", lambda: (sim.history, [], [], []))
    monkeypatch.setattr(S, "load_players_enabled", lambda: dict.fromkeys(PLAYERS, True))
    expected = scheduler.jobs(snaps, paused=set(), active=set())
    actual = sim.jobs()
    key = lambda j: (j.question_id, j.player, j.tier, j.urgent_override)
    assert list(map(key, actual)) == list(map(key, expected))
    assert actual[0].question_id == (2 if urgent else 3)


def test_cli_uses_frozen_schedule_even_when_source_changes(tmp_path, capsys):
    cfg = settings()
    source = tmp_path / "settings.json"
    schedule = tmp_path / "schedule.json"
    source.write_text(json.dumps(cfg))
    schedule.write_text(json.dumps([question()]))
    data = tmp_path / "data"
    assert CS.main(["--data-dir", str(data), "register", str(source)]) == 0
    registration = json.loads(capsys.readouterr().out)
    assert registration["schedule_sha256"] == hash_obj([question()])
    schedule.write_text("changed after registration")
    source.unlink()
    assert CS.main(["--data-dir", str(data), "run", cfg["sim_id"]]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["summary"]["coverage_supported"]["mean"] == 1
    assert sorted(p.name for p in data.iterdir()) == [".coverage_sim", "coverage_sim_results.jsonl", "coverage_sims.jsonl"]


@pytest.mark.parametrize("mutation", [
    lambda c: c["observation"].update(end=at(0)),
    lambda c: c.update(repetitions=0),
    lambda c: c.update(seed=True),
    lambda c: c.update(escalation_rate=1.1),
    lambda c: c["players"]["gpt"].update(tier1=[]),
    lambda c: c["players"]["gpt"].update(tier1=[float("nan")]),
    lambda c: c["players"]["gpt"].update(attempt_cap=-1),
    lambda c: c["players"]["gpt"].update(stops=[{"start": at(0), "reason": "quota_exhausted"}]),
    lambda c: c["denominator"].update(question_ids=[999]),
])
def test_bad_settings_rejected_before_registration(tmp_path, mutation):
    cfg = settings()
    mutation(cfg)
    with pytest.raises(ValueError):
        CS.register(tmp_path, cfg, [question()])
    assert not (tmp_path / "coverage_sims.jsonl").exists()


def test_cycle_job_cap_defers_remaining_work(tmp_path):
    cfg = settings()
    cfg["submission"] = dict.fromkeys(cfg["submission"], 0)
    for line in cfg["players"].values():
        line["tier1"] = [.01]
    result = replay(tmp_path, [question(i) for i in range(1, 62)], cfg)
    cycles = [c for c in result["cycles"] if c.get("jobs_started")]
    assert cycles[0]["jobs_started"] == 180
    assert cycles[1]["jobs_started"] == 3
    assert result["questions"][-1]["first_tier1_at"] == at(600)
    assert result["covered"] == 61


def test_supported_multiple_choice_counts_in_supported_denominator(tmp_path):
    result = replay(tmp_path, [question(kind="multiple_choice")])
    assert result["denominator_supported"] == result["covered"] == 1


def test_submission_budget_defers_unstarted_questions(tmp_path):
    cfg = settings()
    cfg["submission_policy"] = "cycle_end"
    cfg["submission"] = {"comment_s": 20, "precheck_s": 20, "submit_s": 20}
    result = replay(tmp_path, [question(i) for i in range(1, 7)], cfg)
    assert result["covered"] == 6
    assert result["questions"][-1]["submitted_at"] == at(1260)
    assert len(result["attempts"]) == 18


def test_changed_engine_policy_rejected(tmp_path, monkeypatch):
    record = CS.register(tmp_path, settings(), [question()])
    cfg = copy.deepcopy(CS.load_run_config())
    cfg["timeouts_s"]["tier1"] += 1
    monkeypatch.setattr(CS, "load_run_config", lambda: cfg)
    with pytest.raises(ValueError, match="engine policy changed"):
        CS.simulate(record)


@pytest.mark.parametrize("paused", [False, True])
def test_first_tier_global_gate_matches_scheduler_with_busy_or_paused_line(tmp_path, monkeypatch, paused):
    cfg = settings()
    record = CS.register(tmp_path, cfg, [question(), question(2)])
    sim = CS.Simulation(record["frozen"], cfg["seed"])
    sim.paused = {"gpt"} if paused else set()
    sim.active = {} if paused else {"gpt": {}}
    for q in sim.questions.values():
        q["seen"] = NOW
        q["candidate"] = True
        for p in PLAYERS:
            q["states"][p, 1].update(terminal=True, valid=True)
    sim.questions[2]["states"]["gpt", 1].update(terminal=False, valid=False)
    snaps = {qid: {**SNAP, **q, "snapshot_id": f"s{qid}", "supported": True, "status": "open"} for qid, q in sim.questions.items()}
    campaign = {"campaign_id": "comparison", "questions": [{"question_id": i, "priority": "main"} for i in (1, 2)],
                "urgent_slack_s": cfg["urgent_slack_s"]}
    scheduler = S.Scheduler(tmp_path / "scheduler", campaign, snapshots=lambda: snaps, now=lambda: NOW, continuous=True)
    monkeypatch.setattr(scheduler, "state", lambda: (sim.history, [], [], []))
    monkeypatch.setattr(scheduler, "_terminal", lambda qid, p, tier, *args: sim.questions[qid]["states"][p, tier]["terminal"])
    monkeypatch.setattr(S, "load_players_enabled", lambda: dict.fromkeys(PLAYERS, True))
    monkeypatch.setattr(S.E, "evaluate", lambda *a, **kw: {"escalation": {"candidate": True}})
    simulated = [(j.question_id, j.player, j.tier) for j in sim.jobs()]
    actual = [(j.question_id, j.player, j.tier) for j in scheduler.jobs(snaps, paused=sim.paused, active=set(sim.active))]
    expected = [(q, p, 2) for q in (1, 2) for p in ('claude', 'gemini')] if paused else []
    assert simulated == actual == expected
    sim.questions[2]["states"]["gpt", 1]["terminal"] = True
    actual = sim.jobs()
    expected = scheduler.jobs(snaps, paused=sim.paused, active=set(sim.active))
    key = lambda j: (j.question_id, j.player, j.tier)
    assert list(map(key, actual)) == list(map(key, expected))
    assert actual and all(j.tier == 2 for j in actual)


@pytest.mark.parametrize("schedule", [
    [question(), question()],
    [question(opening=10800)],
    [question(revisions=[{"time": at(60), "structural": False}, {"time": at(30), "structural": True}])],
    [question(revisions=[{"time": at(30), "structural": "false"}])],
])
def test_invalid_schedule_is_rejected(tmp_path, schedule):
    with pytest.raises(ValueError):
        CS.register(tmp_path, settings(), schedule)
    assert not (tmp_path / "coverage_sims.jsonl").exists()


@pytest.mark.parametrize("count", [1, 2, 4])
def test_during_schedule_submission_improves_or_preserves_coverage(tmp_path, count):
    cfg = settings()
    cfg["escalation_rate"] = 1
    for line in cfg["players"].values():
        line["tier1"], line["tier2"] = [100], [1200]
    schedule = [question(closing=2150), *(question(i) for i in range(2, count + 1))]
    new = replay(tmp_path / "new", schedule, cfg)
    cfg["submission_policy"] = "cycle_end"
    old = replay(tmp_path / "old", schedule, cfg)
    assert new["coverage_supported"] >= old["coverage_supported"]
    if count == 2:
        assert old["questions"][0]["reason"] == "late"
        assert new["questions"][0]["on_time"]
        assert new["coverage_supported"] > old["coverage_supported"]
    calls = [call for c in new["cycles"] for call in c.get("submit_calls", [])]
    assert any(call["phase"] == "on_finalized" for call in calls)
    assert any(call["phase"] == "cycle_end" for call in calls)


def test_simulated_submit_busy_skips_and_retries_later(tmp_path):
    cfg = settings()
    cfg["submit_busy_intervals"] = [{"start": at(0), "end": at(500)}]
    result = replay(tmp_path, cfg=cfg)
    first = result["cycles"][0]
    assert [c["status"] for c in first["submit_calls"]] == ["busy", "busy"]
    assert all(c["elapsed_s"] == 0 for c in first["submit_calls"])
    assert result["questions"][0]["submitted_at"] == at(605)
    assert result["covered"] == 1 and len(result["attempts"]) == 3


@pytest.mark.parametrize("change", [
    {"submission_policy": "unknown"},
    {"submit_busy_intervals": [{"start": at(1), "end": at(1)}]},
])
def test_submission_policy_validation(tmp_path, change):
    with pytest.raises(ValueError):
        replay(tmp_path, cfg={**settings(), **change})


@pytest.mark.parametrize('deadline', [300, 575, 610, 1190, 4175])
def test_idle_paused_single_question_is_submitted_without_other_work(tmp_path, deadline):
    cfg = settings()
    cfg['players']['gemini']['stops'] = [{'start': at(0), 'end': at(20000), 'reason': 'preflight_blocked'}]
    result = replay(tmp_path, [question(closing=deadline + 1800)], cfg)
    q, = result['questions']
    assert q['on_time']
    assert parse_iso(q['submitted_at']) <= NOW + timedelta(seconds=deadline + 60)
    assert len(result['attempts']) == 2


def test_paused_line_does_not_block_simulated_tier2(tmp_path):
    cfg = settings()
    cfg['escalation_rate'] = 1
    cfg['players']['gemini']['stops'] = [{'start': at(0), 'end': at(20000), 'reason': 'preflight_blocked'}]
    result = replay(tmp_path, [question(closing=2300)], cfg)
    assert result['questions'][0]['with_tier2']
    assert {(a['player'], a['tier']) for a in result['attempts']} == {('claude', 1), ('gpt', 1), ('claude', 2), ('gpt', 2)}


def test_idle_simulator_handoff_releases_before_next_tick(legacy_forecast_timeouts, tmp_path):
    cfg = settings()
    cfg['players']['gemini']['stops'] = [{'start': at(0), 'end': at(20000), 'reason': 'preflight_blocked'}]
    # A continuous stream of known deadlines keeps this otherwise idle worker alive
    # until its handoff. Each question already has a completed first-layer answer.
    deadlines = list(range(700, 4800, 500)) + [4771]
    record = CS.register(tmp_path, cfg, [question(i + 1, closing=d + 1800) for i, d in enumerate(deadlines)])
    sim = CS.Simulation(record['frozen'], cfg['seed'])
    for q in sim.questions.values():
        q['seen'] = NOW
        q['states']['claude', 1].update(terminal=True, valid=True)
        q['states']['gpt', 1].update(terminal=True, valid=True)
    sim.next_tick = NOW + timedelta(seconds=600)
    sim.run_cycle()
    cycle = sim.cycles[-1]
    assert cycle['handoff'] and cycle['finished_at'] == at(4770)
    assert sim.next_tick == NOW + timedelta(seconds=4800)
    assert not sim.questions[len(deadlines)]['sealed']
    sim.now = sim.next_tick
    sim.next_tick += timedelta(seconds=600)
    sim.run_cycle()
    assert sim.questions[len(deadlines)]['submitted'] <= NOW + timedelta(seconds=4771 + 60)
