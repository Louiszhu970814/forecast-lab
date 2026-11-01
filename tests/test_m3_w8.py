"""M3-W8 idle deadlines and missing-line regression tests; synthetic adapters only."""
import json
from datetime import timedelta

import pytest

import forecast as F
import scheduler as S
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import to_iso, parse_iso
from test_forecast import NOW
from test_scheduler import environment
from test_m3_integration import integrated, rows, offers
from test_aggregate import answer as aggregate_answer


def answer(*args, **kwargs):
    return aggregate_answer(*args, finished_at_utc=to_iso(NOW), **kwargs)


@pytest.mark.parametrize('stop_submit', [False, True])
def test_single_idle_question_finalizes_at_deadline_without_background_job(integrated, stop_submit):
    e = integrated
    (e.config / 'players_enabled.json').write_text(json.dumps(dict(claude=True, gemini=False, gpt=True)))
    e.post['question']['scheduled_close_time'] = to_iso(NOW + timedelta(seconds=1800))
    if stop_submit:
        (e.config / 'STOP_SUBMIT').touch()
    sleeps = []
    def sleep(seconds):
        assert not e.tracker['active']  # no unrelated long model call keeps the cycle alive
        assert 0 < seconds <= 60
        sleeps.append(seconds)
        e.clock[0] += timedelta(seconds=seconds)
    def pf(player, tier, adapter):
        return F.Preflight(player, tier=tier, problems=['paused'] if player == 'claude' else [])
    result = e.run(preflight=pf, sleep=sleep)
    deadline = NOW + timedelta(seconds=300)
    finals = [r for r in rows(e, 'aggregates') if r.get('completion_status') == 'complete_local']
    assert finals and sleeps and len(e.tracker['calls']) == 1
    assert all(0 <= (parse_iso(r['created_at_utc']) - deadline).total_seconds() <= 60 for r in finals)
    assert result['steps']['on_finalized']['count'] == 1
    if stop_submit:
        assert not e.transport.writes and not offers(e)
    else:
        assert [w[0] for w in e.transport.writes] == ['forecast', 'comment']
        assert all(parse_iso(r['at']) < NOW + timedelta(seconds=600) for r in rows(e) if r['status'] == 'forecast_ok')


@pytest.mark.parametrize('continuous', [False, True])
@pytest.mark.parametrize('blocker', ['paused', 'quota_5h_wait', 'quota_weekly_wait', 'preflight_blocked', 'line_stopped'])
def test_missing_line_only_releases_continuous_tier2(environment, continuous, blocker):
    obj, snaps, _, _ = environment(count=1, continuous=continuous)
    snap = snaps[100]
    for player, probability in [('claude', .2), ('gpt', .8)]:
        append_record(obj.root / 'forecasts.jsonl', answer(player, snapshot=snap, p=probability, aid=player))
    paused = set()
    if blocker in ('paused', 'preflight_blocked'):
        paused.add('gemini')
    elif blocker == 'line_stopped':
        append_record(obj.root / 'line_status.jsonl', {'type': 'line_stop', 'player': 'gemini', 'reason': 'synthetic', 'at': to_iso(NOW)})
    else:
        append_record(obj.jobs_log, {'type': 'scheduler_job_finished', 'player': 'gemini', 'question_id': 100,
            'question_snapshot_id': snap['snapshot_id'], 'tier': 1, 'outcome': blocker,
            'quota_retry_at_utc': to_iso(NOW + timedelta(hours=1))})
    jobs = obj.jobs(snaps, paused=paused, active=set())
    if continuous:
        assert {(j.player, j.tier) for j in jobs} == {('claude', 2), ('gpt', 2)}
    else:
        assert all(j.tier == 1 for j in jobs)


def test_resumed_missing_line_recovers_tier1_priority(environment):
    obj, snaps, _, _ = environment(count=2, continuous=True)
    for snap in snaps.values():
        for player, probability in [('claude', .2), ('gpt', .8)]:
            append_record(obj.root / 'forecasts.jsonl', answer(player, snapshot=snap, p=probability,
                          aid=f'{player}{snap["question_id"]}'))
    assert all(j.tier == 2 for j in obj.jobs(snaps, paused={'gemini'}, active=set()))
    assert {(j.question_id, j.player, j.tier) for j in obj.jobs(snaps, paused=set(), active=set())} == {
        (100, 'gemini', 1), (101, 'gemini', 1)}
    assert not obj.jobs(snaps, paused=set(), active={'gemini'})


@pytest.mark.parametrize('outcome', ['quota_5h_wait', 'quota_weekly_wait'])
def test_wait_retry_is_read_after_restart_and_expires(environment, outcome):
    obj, snaps, _, _ = environment(count=1, continuous=True)
    retry = NOW + timedelta(minutes=1)
    append_record(obj.jobs_log, {'type': 'scheduler_job_finished', 'player': 'gemini', 'question_id': 100,
        'question_snapshot_id': snaps[100]['snapshot_id'], 'tier': 1, 'outcome': outcome,
        'quota_retry_at_utc': to_iso(retry)})
    assert 'gemini' not in {j.player for j in obj.jobs(snaps, paused=set(), active=set())}
    obj.now = lambda: retry
    assert ('gemini', 1) in {(j.player, j.tier) for j in obj.jobs(snaps, paused=set(), active=set())}


def test_weekly_wait_scheduler_records_no_line_stop(environment, monkeypatch):
    obj, snaps, _, _ = environment(count=1, continuous=True)
    job = next(j for j in obj.jobs(snaps, paused=set(), active=set()) if j.player == 'gemini')
    retry = to_iso(NOW + timedelta(days=1))
    def denied(*args):
        raise F.LineStop('weekly reserve', quota_retry_at_utc=retry, quota_wait='quota_weekly_wait')
    monkeypatch.setattr(F, 'forecast_question', denied)
    result = obj.execute(job)
    assert result['outcome'] == 'quota_weekly_wait' and result['quota_retry_at_utc'] == retry
    assert not result['notify_louis'] and not read_complete(obj.root / 'line_status.jsonl')


def test_handoff_does_not_start_slow_tail(setup_cycle, monkeypatch):
    # Fixture imported below: no calls to the network or real model CLIs.
    run, current, clock, enabled, calls, _, _ = setup_cycle
    enabled['claude'] = True
    import cycle as C
    cutoff = C.timing_limits(F.load_run_config())['handoff_s']
    current[0]['scheduled_close_time'] = to_iso(NOW + timedelta(seconds=cutoff + 40 + 1500))
    def pf(player, tier, adapter):
        return F.Preflight(player, tier=tier, problems=['paused'] if player == 'claude' else [])
    # Start just before the last handoff, with a previously accumulated answer.
    original = S.Scheduler.run
    def run_at_limit(self, **kw):
        clock[0] = NOW + timedelta(seconds=cutoff - 10)
        return original(self, **kw)
    monkeypatch.setattr(S.Scheduler, 'run', run_at_limit)
    result = run(preflight=pf)
    assert result['steps']['scheduler']['handoff']
    assert clock[0] == NOW + timedelta(seconds=cutoff)
    assert result['steps']['submit']['reason'] == 'launchd handoff'
    assert 'submit' not in calls


from test_cycle import setup_cycle


@pytest.mark.parametrize('offset', [1, 29, 40])
def test_cycle_handoff_to_next_tick_preserves_deadline(integrated, offset):
    import cycle as C
    e = integrated
    cutoff = C.timing_limits(F.load_run_config())['handoff_s']
    deadline = NOW + timedelta(seconds=cutoff + offset)
    (e.config / 'players_enabled.json').write_text(json.dumps(dict(claude=True, gemini=False, gpt=True)))
    e.post['question']['scheduled_close_time'] = to_iso(deadline + timedelta(minutes=25))
    def pf(player, tier, adapter):
        return F.Preflight(player, tier=tier, problems=['paused'] if player == 'claude' else [])
    e.run(preflight=pf)
    assert len(e.tracker['calls']) == 1 and not offers(e)
    e.clock[0] = NOW + timedelta(seconds=cutoff - 10)
    rec = e.run(preflight=pf, started_at=NOW)
    assert rec['steps']['scheduler']['handoff'] and not offers(e)
    e.clock[0] = NOW + timedelta(seconds=cutoff + C.SAFETY_S)
    e.run(preflight=pf)
    assert [w[0] for w in e.transport.writes] == ['forecast', 'comment']
    offer, = offers(e)
    assert 0 <= (parse_iso(offer['created_at_utc']) - deadline).total_seconds() <= 60


@pytest.mark.parametrize('fault', [None, 'missing_alias', 'missing_metadata', 'duplicate'])
def test_shared_official_discovery_includes_all_current_minibenches(fault):
    import continuous as Q
    from flab.config import load_tournaments
    import copy
    static = load_tournaments()
    original = copy.deepcopy(static)
    mini = [{'id': 33122, 'slug': 'minibench', 'is_ongoing': False, 'start_date': to_iso(NOW + timedelta(days=1))},
            {'id': 33123, 'slug': 'previous', 'is_ongoing': True},
            {'id': 33124, 'slug': 'old', 'is_ongoing': False, 'start_date': to_iso(NOW - timedelta(days=10))}]
    if fault == 'missing_alias':
        mini.pop(0)
    elif fault == 'missing_metadata':
        mini[-1].pop('start_date')
    elif fault == 'duplicate':
        mini.append(copy.deepcopy(mini[0]))
    class Client:
        def get(self, path):
            return {'projects/tournaments/minibench/': {'id': 33122, 'slug': 'minibench'},
                'projects/tournaments/': [{'id': 33108, 'name': 'Metaculus Cup November',
                    'is_ongoing': True, 'start_date': to_iso(NOW)}],
                'projects/site_main/': {'id': 144, 'type': 'site_main'},
                'projects/minibenches/': mini}[path]
    if fault:
        with pytest.raises(F.PreflightError):
            Q.current_tournaments(Client(), configured=static, now=lambda: NOW)
    else:
        cfg = Q.current_tournaments(Client(), configured=static, now=lambda: NOW)
        assert cfg['excluded_project_ids'] == [33121, 33122, 33123]
        assert cfg['tournaments']['metaculus_cup']['id'] == 33108
        assert cfg['tournaments']['main_site_questions']['id'] == 144
    assert static == original
