from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import copy

import pytest

import continuous as Q
import budget as B
import forecast as F
from flab.config import load_quota_policy
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import parse_iso, to_iso
from test_forecast import NOW
from test_cycle import policy, setup_cycle, snapshot
from test_scheduler import environment


def template():
    return {**policy(), 'rollover': {'version': Q.ROLLOVER_VERSION, 'windows': 3}}


def test_week_boundary_is_anchored_and_replay_does_not_reset_history(tmp_path):
    cfg = template()
    boundary = parse_iso(cfg['budget_window_end'])
    first = Q.active_campaign(tmp_path, cfg, now=lambda: boundary - timedelta(milliseconds=1))
    assert first['window_index'] == 0
    assert Q.active_campaign(tmp_path, cfg, now=lambda: NOW) == first
    second = Q.active_campaign(tmp_path, cfg, now=lambda: boundary)
    assert second['window_index'] == 1 and second['budget_window_start'] == first['budget_window_end']
    assert second['weekly_attempt_caps'] == first['weekly_attempt_caps']
    assert Q.active_campaign(tmp_path, cfg, now=lambda: boundary) == second
    assert len(read_complete(tmp_path/'continuous_campaigns.jsonl')) == 2
    assert len(read_complete(tmp_path/'continuous_series.jsonl')) == 1
    assert cfg == template()


def test_missed_weeks_are_not_backfilled_or_carried_as_extra_capacity(tmp_path):
    cfg = template()
    third_start = parse_iso(cfg['budget_window_start']) + timedelta(days=14)
    active = Q.active_campaign(tmp_path, cfg, now=lambda: third_start)
    assert active['window_index'] == 2
    assert active['weekly_attempt_caps'] == cfg['weekly_attempt_caps']
    assert len(read_complete(tmp_path/'continuous_campaigns.jsonl')) == 1
    with pytest.raises(ValueError, match='inactive'):
        Q.active_campaign(tmp_path, cfg, now=lambda: third_start + timedelta(days=7))
    with pytest.raises(ValueError, match='earlier window'):
        Q.active_campaign(tmp_path, cfg, now=lambda: NOW)


@pytest.mark.parametrize('setting', [None, {}, {'version': Q.ROLLOVER_VERSION, 'windows': True},
    {'version': Q.ROLLOVER_VERSION, 'windows': 0}, {'version': Q.ROLLOVER_VERSION, 'windows': 2.5},
    {'version': 'unknown', 'windows': 2}, {'version': Q.ROLLOVER_VERSION, 'windows': 2, 'cap_multiplier': 2}])
def test_invalid_rollover_never_registers(tmp_path, setting):
    cfg = {**template(), 'rollover': setting}
    with pytest.raises(ValueError):
        Q.active_campaign(tmp_path, cfg, now=lambda: NOW)
    assert not read_complete(tmp_path/'continuous_campaigns.jsonl')


def test_anchor_and_future_start_fail_closed(tmp_path):
    cfg = template()
    cfg['budget_window_end'] = to_iso(NOW + timedelta(days=1))
    with pytest.raises(ValueError, match='exactly seven'):
        Q.active_campaign(tmp_path, cfg, now=lambda: NOW)
    cfg = template()
    with pytest.raises(ValueError, match='inactive'):
        Q.active_campaign(tmp_path, cfg, now=lambda: parse_iso(cfg['budget_window_start']) - timedelta(seconds=1))


@pytest.mark.parametrize('field,value', [('weekly_attempt_caps', dict.fromkeys(('gpt','claude','gemini'), 200)),
    ('venues', ['main_site_questions']), ('rollover', {'version': Q.ROLLOVER_VERSION, 'windows': 4}),
    ('quota_policy_sha256', 'changed')])
def test_template_is_frozen_across_weeks(tmp_path, field, value):
    cfg = template()
    Q.active_campaign(tmp_path, cfg, now=lambda: NOW)
    changed = {**cfg, field: value}
    with pytest.raises(ValueError, match='cannot change'):
        Q.active_campaign(tmp_path, changed, now=lambda: NOW + timedelta(days=7))
    assert len(read_complete(tmp_path/'continuous_campaigns.jsonl')) == 1


def test_configuration_drift_stops_rollover(tmp_path, monkeypatch):
    cfg = template()
    Q.active_campaign(tmp_path, cfg, now=lambda: NOW)
    quota = copy.deepcopy(Q.load_quota_policy())
    quota['players']['gpt']['weekly_attempts_cap'] = 99
    monkeypatch.setattr(Q, 'load_quota_policy', lambda: quota)
    with pytest.raises(ValueError):
        Q.active_campaign(tmp_path, cfg, now=lambda: NOW + timedelta(days=7))
    assert len(read_complete(tmp_path/'continuous_campaigns.jsonl')) == 1


def test_renaming_or_moving_anchor_cannot_create_overlapping_allowance(tmp_path):
    cfg = template()
    Q.active_campaign(tmp_path, cfg, now=lambda: NOW)
    changed = {**cfg, 'campaign_id': 'other'}
    with pytest.raises(ValueError, match='overlap'):
        Q.active_campaign(tmp_path, changed, now=lambda: NOW)
    changed['budget_window_start'] = to_iso(NOW)
    changed['budget_window_end'] = to_iso(NOW + timedelta(days=7))
    with pytest.raises(ValueError, match='overlap'):
        Q.active_campaign(tmp_path, changed, now=lambda: NOW)
    assert len(read_complete(tmp_path/'continuous_series.jsonl')) == 1


def test_parallel_registration_is_idempotent(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: Q.active_campaign(tmp_path, template(), now=lambda: NOW), range(8)))
    assert all(r == results[0] for r in results)
    assert len(read_complete(tmp_path/'continuous_campaigns.jsonl')) == 1
    assert len(read_complete(tmp_path/'continuous_series.jsonl')) == 1


def test_crash_after_window_before_template_row_recovers_without_reset(tmp_path, monkeypatch):
    real = Q.append_record
    def fail(path, row):
        if path.name == 'continuous_series.jsonl':
            raise OSError('synthetic disk failure')
        return real(path, row)
    monkeypatch.setattr(Q, 'append_record', fail)
    with pytest.raises(OSError):
        Q.active_campaign(tmp_path, template(), now=lambda: NOW)
    monkeypatch.setattr(Q, 'append_record', real)
    changed = template()
    changed['weekly_attempt_caps']['gpt'] += 1
    with pytest.raises(ValueError, match='cannot change'):
        Q.active_campaign(tmp_path, changed, now=lambda: NOW)
    Q.active_campaign(tmp_path, template(), now=lambda: NOW)
    assert len(read_complete(tmp_path/'continuous_campaigns.jsonl')) == 1
    assert len(read_complete(tmp_path/'continuous_series.jsonl')) == 1


def test_new_window_counts_all_current_attempts_and_acceptance_not_prior_week(tmp_path):
    cfg = template()
    cfg['weekly_attempt_caps'] = dict.fromkeys(('claude','gpt','gemini'), 2)
    boundary = parse_iso(cfg['budget_window_end'])
    Q.active_campaign(tmp_path, cfg, now=lambda: NOW)
    active = Q.active_campaign(tmp_path, cfg, now=lambda: boundary)
    for aid, when in [('old', boundary-timedelta(seconds=1)), ('current', boundary)]:
        record = {'type':'forecast_attempt', 'attempt_id':aid, 'player':'claude', 'tier':1,
            'question_id':7, 'attempt_kind':'initial', 'started_at_utc':to_iso(when), 'finished_at_utc':to_iso(when)}
        append_record(tmp_path/'forecast_attempt_starts.jsonl', record)
        append_record(tmp_path/'forecasts.jsonl', record)
    acceptance = tmp_path/'acceptance.jsonl'
    append_record(acceptance, {'type':'acceptance_call_started','attempt_id':'accept','run_id':'accept-run',
        'player':'claude','started_at':to_iso(boundary)})
    budget = B.Budget(tmp_path, active, load_quota_policy(), 'new', now=lambda: boundary, acceptance_log=acceptance)
    decision = budget.reserve({'player':'claude','tier':1,'question_id':8,'attempt_id':'fresh'},
        pending_tier1=0, expected_new=0, repair_reserve=0)
    assert not decision['allowed'] and decision['weekly_attempts_used'] == 2
    assert len(read_complete(tmp_path/'forecasts.jsonl')) == 2


@pytest.mark.parametrize('stop_submit', [False, True])
@pytest.mark.parametrize('reenable_player', [False, True])
def test_cycle_rolls_over_without_unsealing_unchanged_snapshot(setup_cycle, monkeypatch, tmp_path, stop_submit, reenable_player):
    run, current, clock, enabled, calls, tracker, project = setup_cycle
    current[0]['scheduled_close_time'] = to_iso(NOW + timedelta(days=30))
    if stop_submit:
        (project / 'config' / 'STOP_SUBMIT').touch()
    original = Q.active_campaign
    cfg = template()
    monkeypatch.setattr(Q, 'active_campaign', lambda root, ignored, **kw: original(root, cfg, **kw))
    first = run()
    count = len(tracker['calls'])
    assert count == 1
    # Submit stub intentionally creates no remote intent. Snapshot remains in queue.
    assert not read_complete(tmp_path / 'submissions.jsonl')
    before = {name: (tmp_path/name).read_bytes() for name in ('aggregates.jsonl','comments.jsonl')}
    import scheduler as S
    real_finalize = S.Scheduler.finalize
    callbacks = []
    def finalize(obj, *a, **kw):
        original_callback = obj.on_finalized
        def on_ready(qids):
            callbacks.extend(qids)
            if original_callback:
                original_callback(qids)
        obj.on_finalized = on_ready
        try:
            return real_finalize(obj, *a, **kw)
        finally:
            obj.on_finalized = original_callback
    monkeypatch.setattr(S.Scheduler, 'finalize', finalize)
    if reenable_player:
        enabled['claude'] = True
    clock[0] = parse_iso(cfg['budget_window_end'])
    second = run()
    assert first['steps']['policy']['campaign_id'] != second['steps']['policy']['campaign_id']
    assert len(tracker['calls']) == count
    assert second['new_question_ids'] == []
    assert not callbacks
    assert all((tmp_path/name).read_bytes() == content for name, content in before.items())
    assert second['status'] == 'complete'


def test_new_budget_wait_uses_fresh_week_capacity_without_reopening_sealed_answer(setup_cycle, monkeypatch, tmp_path):
    run, current, clock, _, _, tracker, _ = setup_cycle
    current[0]['scheduled_close_time'] = to_iso(NOW + timedelta(days=30))
    cfg = template()
    cfg['weekly_attempt_caps']['gpt'] = 1
    original = Q.active_campaign
    monkeypatch.setattr(Q, 'active_campaign', lambda root, ignored, **kw: original(root, cfg, **kw))
    run()
    current.append(snapshot(101, scheduled_close_time=to_iso(NOW + timedelta(days=30))))
    run()
    assert len(tracker['calls']) == 1
    skipped = [r for r in read_complete(tmp_path/'aggregates.jsonl')
        if r.get('question_id') == 101 and r.get('completion_status') == 'complete_local']
    assert not skipped
    sealed_before = [r for r in read_complete(tmp_path/'aggregates.jsonl')
                     if r.get('question_id') == 100 and r.get('completion_status') == 'complete_local']
    # D121 temporary waits remain open; already sealed question 100 stays sealed.
    clock[0] = parse_iso(cfg['budget_window_end'])
    current.append(snapshot(102, scheduled_close_time=to_iso(NOW + timedelta(days=30))))
    run()
    assert len(tracker['calls']) == 2
    assert [r for r in read_complete(tmp_path/'aggregates.jsonl')
            if r.get('question_id') == 100 and r.get('completion_status') == 'complete_local'] == sealed_before
    assert len([r for r in read_complete(tmp_path/'aggregates.jsonl')
                if r.get('question_id') == 101 and r.get('completion_status') == 'complete_local'
                and not r.get('submission_eligible')]) == 1  # publication has its separate offer record
    assert {r['question_id'] for r in read_complete(tmp_path/'forecasts.jsonl')} == {100, 101}


@pytest.mark.parametrize('kind', ['start', 'reservation'])
def test_budget_directly_blocks_prior_window_unresolved_work(tmp_path, kind):
    cfg = template()
    active = Q.active_campaign(tmp_path, cfg, now=lambda: NOW + timedelta(days=7))
    if kind == 'start':
        append_record(tmp_path/'forecast_attempt_starts.jsonl', {'attempt_id':'unresolved',
            'player':'claude','started_at_utc':to_iso(NOW)})
    else:
        append_record(tmp_path/'budget_events.jsonl', {'type':'budget_reserved','attempt_id':'unresolved',
            'player':'claude','at':to_iso(NOW),'run_id':'old'})
    budget = B.Budget(tmp_path, active, load_quota_policy(), 'new', now=lambda: NOW + timedelta(days=7),
        acceptance_log=tmp_path/'acceptance.jsonl')
    result = budget.reserve({'player':'claude','tier':1,'question_id':8,'attempt_id':'fresh'},
        pending_tier1=0, expected_new=0, repair_reserve=0)
    assert not result['allowed'] and result['reason'] == 'unresolved_or_running_attempt'
    assert result['weekly_attempts_used'] == 0


@pytest.mark.usefixtures("legacy_stops")  # pre-D149 stop rule
def test_old_unresolved_start_still_blocks_line_after_rollover(tmp_path):
    cfg = template()
    append_record(tmp_path/'forecast_attempt_starts.jsonl', {'attempt_id':'unknown', 'player':'claude',
        'started_at_utc':to_iso(NOW)})
    Q.active_campaign(tmp_path, cfg, now=lambda: NOW + timedelta(days=7))
    stop = F.line_stopped(tmp_path/'line_status.jsonl', 'claude', tmp_path/'forecasts.jsonl')
    assert stop and 'unknown' in stop['reason']


def test_legacy_single_window_does_not_gain_implicit_rollover(tmp_path):
    cfg = policy()
    assert Q.active_campaign(tmp_path, cfg, now=lambda: NOW) == cfg
    with pytest.raises(ValueError, match='active'):
        Q.active_campaign(tmp_path, cfg, now=lambda: NOW + timedelta(days=7))
    assert not read_complete(tmp_path/'continuous_series.jsonl')
