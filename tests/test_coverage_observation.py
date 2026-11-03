import copy
from datetime import timedelta

import pytest

import coverage_sim as C
from flab.config import PLAYERS
from test_coverage_sim import at, question, replay, settings


def test_production_submit_margin_covers_poll_and_slow_submission(tmp_path, monkeypatch):
    cfg = settings()
    cfg["cycle_trigger"] = "after_completion"
    cfg["poll_s"] = 60
    cfg["submission"] = {key: 90 for key in cfg["submission"]}
    cfg["players"]["gpt"]["stops"] = [
        {"start": at(0), "end": at(10800), "reason": "quota_exhausted"}]
    current = replay(tmp_path / "current", [question()], cfg)
    assert current["questions"][0]["on_time"]
    legacy = copy.deepcopy(C.load_run_config())
    legacy["deadlines"]["submit_lead_minutes"] = 5
    monkeypatch.setattr(C, "load_run_config", lambda: legacy)
    prior = replay(tmp_path / "prior", [question()], cfg)
    assert prior["covered"] == 0
    assert prior["questions"][0]["reason"] == "late"


def test_visibility_delay_preserves_open_time_and_prevents_early_start(tmp_path):
    q = {**question(), "visible_at": at(1800)}
    result = replay(tmp_path, [q])
    assert result["questions"][0]["seen_at"] == at(1800)
    assert result["open_to_tier1_s"]["max"] == 1800
    assert q["open_time"] == at(0)


@pytest.mark.parametrize("extra", [{"visible_at": None}, {"visible_at": at(10800)},
                                  {"visibility_intervals": []}, {"unreplayable": True}])
def test_unknown_or_never_visible_stays_in_denominator(tmp_path, extra):
    result = replay(tmp_path, [{**question(), **extra}])
    assert result["denominator_supported"] == 1
    assert result["covered"] == 0
    assert not result["attempts"]


def test_visibility_loss_during_submission_does_not_count_as_success(tmp_path):
    q = {**question(), "visibility_intervals": [{"start": at(0), "end": at(63)}]}
    result = replay(tmp_path, [q])
    assert len(result["attempts"]) == 3
    assert result["covered"] == 0


def test_early_actual_close_rejects_submission_crossing_the_close(tmp_path):
    q = {**question(), "actual_close_time": at(63)}
    result = replay(tmp_path, [q])
    assert result["covered"] == 0
    assert q["scheduled_close_time"] == at(10800)


def test_eventual_early_close_does_not_give_scheduler_advance_knowledge(tmp_path):
    cfg = settings()
    cfg['players']['gpt']['stops'] = [{'start': at(0), 'end': at(10800), 'reason': 'quota_exhausted'}]
    q = {**question(), 'actual_close_time': at(2400)}
    result = replay(tmp_path/'early', [q], cfg)
    control = replay(tmp_path/'scheduled', [question()], cfg)
    assert control['covered'] == 1
    assert control['questions'][0]['submitted_at'] == at(9005)
    assert result['covered'] == 0
    assert result['questions'][0]['reason'] == 'late'


def test_background_repair_does_not_hide_submit_revision_or_discover_new_work(tmp_path, monkeypatch):
    cfg = settings()
    cfg['players']['gemini']['tier1'] = [120]
    cfg['players']['gpt']['tier1'] = [120]
    cfg['submission'] = {k: 90 for k in cfg['submission']}
    original = C.Simulation.start_attempt

    def delayed_repair(self, job, **kwargs):
        started = original(self, job, **kwargs)
        if started and job.question_id == 2 and job.player == 'claude' and self.active['claude']['attempt']['attempt_kind'] == 'initial':
            active = self.active['claude']
            active.update(finish=self.now + timedelta(seconds=140), repair=True, failed=False)
            active['attempt'].update(finished_at_utc=at(200), duration_s=140, sampled_s=140, outcome='format_required')
        return started

    monkeypatch.setattr(C.Simulation, 'start_attempt', delayed_repair)
    result = replay(tmp_path, [question(1, revisions=[{'time': at(180), 'structural': True}]),
                              question(2), question(3, opening=180)], cfg)
    first, _, new = result['questions']
    assert first['snapshot_generation'] == 1
    assert first['on_time'] and first['reason'] is None
    assert first['submitted_at'] == at(780)
    assert new['seen_at'] == at(450)


def test_carry_in_consumes_capacity_without_inflating_denominator(tmp_path):
    cfg = settings()
    for line in cfg["players"].values():
        line["attempt_cap"] = 1
    old = {**question(1, opening=-300), "workload_only": True}
    new = question(2, opening=600)
    result = replay(tmp_path, [old, new], cfg)
    assert result["denominator_all"] == 1
    assert result["carry_in_questions"][0]["on_time"]
    assert result["questions"][0]["question_id"] == 2
    assert result["covered"] == 0
    assert result["questions"][0]["reason"] == "budget_skipped"


def test_cannot_mark_in_window_question_as_carry_in():
    with pytest.raises(ValueError, match="carry-in"):
        C.validate(settings(), [{**question(), "workload_only": True}])


def test_background_work_does_not_extend_horizon_or_inflate_cohort_stats(tmp_path):
    cfg = settings()
    cfg['observation']['end'] = at(600)
    old = {**question(1, opening=-1000, closing=500000), 'workload_only': True}
    tail = {**question(3, opening=700, closing=500000), 'workload_only': True}
    result = replay(tmp_path, [old, question(2, opening=100, closing=3000), tail], cfg)
    assert len(result['carry_in_questions']) == len(result['tail_questions']) == 1
    assert result['denominator_all'] == 1
    assert result['peak_open_questions'] == 1
    assert result['open_to_tier1_s']['count'] == 1
    assert result['cycles'][-1]['started_at'] < at(3000)


def test_initial_usage_is_not_reset_by_new_simulation_id(tmp_path):
    cfg = settings()
    for line in cfg["players"].values():
        line.update(attempt_cap=2, initial_attempts_used=2)
    result = replay(tmp_path, cfg=cfg)
    assert result["covered"] == 0
    assert result["questions"][0]["reason"] == "budget_skipped"


def test_initial_usage_resets_only_at_registered_boundary(tmp_path):
    cfg = settings()
    for line in cfg["players"].values():
        line.update(attempt_cap=2, initial_attempts_used=2, reset_times=[at(1200)])
    result = replay(tmp_path, [question(1), question(2, opening=1800)], cfg)
    assert not result["questions"][0]["on_time"]
    assert result["questions"][1]["on_time"]


def test_outage_cancels_running_work_and_prevents_success(tmp_path):
    cfg = settings()
    cfg["cycle_trigger"] = "after_completion"
    cfg["unavailable_intervals"] = [{"start": at(20), "end": at(12000)}]
    result = replay(tmp_path, cfg=cfg)
    assert result["covered"] == 0
    assert result["cycles"][0]["status"] == "unavailable"
    assert all(a["outcome"] == "infrastructure_unavailable" for a in result["attempts"])
    assert all(a["finished_at_utc"] == at(20) for a in result["attempts"])


def test_no_work_starts_during_outage(tmp_path):
    cfg = settings()
    cfg["unavailable_intervals"] = [{"start": at(0), "end": at(1800)}]
    result = replay(tmp_path, cfg=cfg)
    assert result["questions"][0]["first_tier1_at"] >= at(1800)
    assert result["covered"] == 1


@pytest.mark.parametrize("field,value", [
    ("initial_attempts_used", -1), ("initial_attempts_used", True),
])
def test_invalid_initial_usage_rejected(field, value):
    cfg = settings()
    cfg["players"]["gpt"][field] = value
    with pytest.raises(ValueError, match=field):
        C.validate(cfg, [question()])


def test_overlapping_outages_and_visibility_rejected():
    overlap = [{"start": at(0), "end": at(100)}, {"start": at(50), "end": at(200)}]
    cfg = settings()
    cfg["unavailable_intervals"] = overlap
    with pytest.raises(ValueError, match="disjoint"):
        C.validate(cfg, [question()])
    with pytest.raises(ValueError, match="disjoint"):
        C.validate(settings(), [{**question(), "visibility_intervals": overlap}])
